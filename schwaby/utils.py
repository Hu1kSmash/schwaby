'''Implements additional functionality beyond what's implemented in the client
module.'''

import re
import time

# The class an HTTP error status raises, under a name this library owns.
#
# `raise_for_status()` on a response from any client call raises
# `httpx2.HTTPStatusError`. That package shares no exception hierarchy with
# `httpx`, so a handler written against the other one never runs -- no error,
# the `except` simply does not match, and nothing reports that it didn't. A
# consumer was left probing which HTTP package happened to be installed in
# order to know what to catch. Measured: `httpx2` is the only HTTP dependency
# declared, `httpx` is not installed alongside it, and a 429 driven through the
# real session stack raises this class. Re-exported rather than subclassed, so
# `isinstance` against the original still holds.
from httpx2 import HTTPStatusError


def class_fullname(o):
    return o.__module__ + '.' + o.__name__


class EnumEnforcer:
    def __init__(self, enforce_enums):
        self.enforce_enums = enforce_enums

    def type_error(self, value, required_enum_type):
        possible_members_message = ''

        if isinstance(value, str):
            possible_members = []
            for member in required_enum_type.__members__:
                fullname = class_fullname(required_enum_type) + '.' + member
                if value in fullname:
                    possible_members.append(fullname)

            # Oxford comma insertion
            if possible_members:
                possible_members_message = 'Did you mean ' + ', '.join(
                    possible_members[:-2] + [' or '.join(
                        possible_members[-2:])]) + '? '

        raise ValueError(
            ('expected type "{}", got type "{}". {}(initialize with ' +
             'enforce_enums=False to disable this checking)').format(
                required_enum_type.__name__,
                type(value).__name__,
                possible_members_message))

    def convert_enum(self, value, required_enum_type):
        if value is None:
            return None

        if isinstance(value, required_enum_type):
            return value.value
        elif self.enforce_enums:
            self.type_error(value, required_enum_type)
        else:
            return value

    def convert_enum_iterable(self, iterable, required_enum_type):
        if iterable is None:
            return None

        if isinstance(iterable, required_enum_type):
            return [iterable.value]

        values = []
        for value in iterable:
            if isinstance(value, required_enum_type):
                values.append(value.value)
            elif self.enforce_enums:
                self.type_error(value, required_enum_type)
            else:
                values.append(value)
        return values

    def set_enforce_enums(self, enforce_enums):
        self.enforce_enums = enforce_enums


def _describe_error(response):
    '''Schwab's own words for a rejection, as a suffix, or the empty string.

    Bounded, because this ends up in an exception message and from there in
    somebody's logs: an unbounded body would put an entire order echo on one
    line. The whole response stays reachable as the exception's ``.response``
    for anyone who needs the rest.

    Anything unexpected yields no suffix rather than an error of its own. This
    runs on the failure path, and a formatter that raises there would replace a
    useful exception with a useless one.
    '''
    try:
        body = response.json()
        if not isinstance(body, dict):
            return ''
        parts = []
        message = body.get('message')
        if isinstance(message, str) and message.strip():
            parts.append(message.strip())
        errors = body.get('errors')
        if isinstance(errors, (list, tuple)):
            parts.extend(e.strip() for e in errors
                         if isinstance(e, str) and e.strip())
        if not parts:
            return ''
        detail = '; '.join(parts)
        if len(detail) > _ERROR_DETAIL_LIMIT:
            detail = detail[:_ERROR_DETAIL_LIMIT] + '... (truncated, see .response)'
        return ': ' + detail
    except Exception:
        return ''


_ERROR_DETAIL_LIMIT = 500


def _rebuild_exception(cls, args, state):
    '''Reconstructs an exception without going through its ``__init__``.

    Module level because ``__reduce__`` has to name something picklable.
    '''
    exc = cls.__new__(cls)
    BaseException.__init__(exc, *args)
    exc.__dict__.update(state)
    return exc


class SchwabError(Exception):
    '''Base class for every exception this library raises.

    Every exception class this library *defines* inherits it, so
    ``except SchwabError`` is one name for all of them.

    It is **not** everything the library can raise. Argument validation still
    raises builtin ``ValueError`` in about thirty places --- a negative
    quantity, a float price, a strike finer than the symbol format carries ---
    and those are ordinary "you passed a bad value" errors that a builtin
    describes correctly. Catch ``ValueError`` for those. What this class buys
    you is the ability to catch the library's own failures *without* also
    catching every ``int()`` and ``float()`` in the same block.

    Two of the order exceptions additionally inherit ``ValueError``, because
    they did before this class existed and code catching them that way still
    works. :class:`OrderIdNotFoundError` deliberately does not: see its own
    documentation.
    '''

    def __reduce__(self):
        # Exceptions here take the thing they are about as a leading positional
        # argument -- a response, an order id, a raw frame -- and pass only the
        # message up to BaseException. The default __reduce__ reconstructs by
        # calling __init__ with self.args, which is just the message, so a copy
        # or a pickle either raised TypeError for the missing positionals or,
        # worse, succeeded with the message bound to the response and the
        # message itself lost.
        #
        # Nothing in this library moves an exception across a boundary --
        # checked: the login flow's queue carries flask.request.url and
        # RedirectServerExitedError is constructed in the parent. This is for
        # callers who do, a ProcessPoolExecutor over placements being the
        # obvious one, where the exception saying an order is live on the wrong
        # account is the last that should arrive as a TypeError about argument
        # counts.
        #
        # _rebuild_exception bypasses __init__ entirely -- __new__, then
        # BaseException.__init__ for the message, then __dict__. That handles
        # every signature below uniformly, including keyword-only ones, and
        # needs no per-class __reduce__. The cost is that any invariant a
        # subclass establishes in __init__ beyond plain assignment -- coercing
        # an id to int, normalising a hash -- is skipped on reconstruction. No
        # subclass does that today; one that starts to needs its own
        # __reduce__ or a __setstate__.
        return (_rebuild_exception, (type(self), self.args, dict(self.__dict__)))



class UnsuccessfulOrderException(SchwabError, ValueError):
    '''
    Raised by :meth:`Utils.extract_order_id` when attempting to extract an
    order ID from a :meth:`Client.place_order` response that was not successful.

    The rejected response is on the exception as ``.response``, and Schwab's own
    explanation is in the message when there was one. Schwab types an error body
    as ``{"message": ..., "errors": [...]}``, and that text is the only place
    the reason for a rejection appears -- a status code alone does not
    distinguish a malformed order from one the account cannot afford.
    '''

    def __init__(self, response, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.response = response


class OrderIdNotFoundError(SchwabError):
    '''
    Raised by :meth:`Utils.extract_order_id` when Schwab accepted the order but
    the response did not yield an order ID.

    **The order may be live.** Schwab returned a success status, so the order
    was very likely placed; what is missing is the handle you would use to
    watch or cancel it. Treat this as "go and look", not as "nothing
    happened" --- :meth:`Client.get_orders_for_account` over a recent time
    window will find it.

    ``.response`` is the ``place_order`` response. ``.location`` is the raw
    ``Location`` header, or ``None`` when there was not one.

    **This deliberately does not inherit** ``ValueError``, unlike its two
    siblings. ``except ValueError`` is the idiom people reach for around
    ``int()`` and ``float()``, and order specs coerce exactly those a few lines
    from the call --- so inheriting it would let the one exception here that
    means *an order may be live and untracked* be swallowed by a block aimed at
    parsing. It is a :class:`SchwabError` and nothing else.

    ``ValueError`` also says the caller passed a bad argument, and they did
    not. This is remote state.
    '''

    def __init__(self, response, location, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.response = response
        self.location = location


class MissingLocationHeaderError(OrderIdNotFoundError):
    '''The response carried no ``Location`` header at all.

    Catch :class:`OrderIdNotFoundError` unless you specifically need to tell
    this apart from :class:`UnrecognizedLocationError`.
    '''


class UnrecognizedLocationError(OrderIdNotFoundError):
    '''The ``Location`` header was present and did not look like an order URL.

    Most likely Schwab changed the format. The header is on the exception as
    ``.location`` --- please include it in a bug report.
    '''


class AccountHashMismatchException(SchwabError, ValueError):
    '''
    Raised by :meth:`Utils.extract_order_id` when the response belongs to a
    different account than the one this :class:`Utils` was built with.

    **The order is live.** Not "may be": this is raised after the response came
    back successful *and* a valid order ID was parsed out of it, so Schwab
    placed something. The mismatch is in your wiring, and the consequence is an
    order on an account you were not expecting to trade.

    So everything needed to go and cancel it is on the exception --- read it,
    do not re-derive it from the message:

    * ``.order_id`` --- the order Schwab created
    * ``.account_hash`` --- the account it was placed on, as Schwab reported it
    * ``.expected_account_hash`` --- the one this :class:`Utils` was built
      with. ``None`` if the exception was raised by something other than
      :meth:`Utils.extract_order_id`, so compare it before trusting it
    * ``.response`` --- the whole ``place_order`` response

    The two hashes are both here on purpose. A handler far from the call site
    has no ``Utils`` left to ask, and reaching only the one Schwab named leaves
    it interpolating the other out of the message text.

    .. warning::

       This inherits ``ValueError``, as it did before :class:`SchwabError`
       existed, so a broad ``except ValueError`` around order building will
       swallow it. Given what it means, narrow that.
    '''

    # expected_account_hash is keyword-only, and that is not stylistic. Added
    # ahead of *args it took the slot the message had been passed in, so the
    # four-argument call that already existed bound the message to it and left
    # self.args empty -- an exception whose str() is '' on the one failure that
    # means an order is live on the wrong account. That is the same
    # succeeded-while-losing-the-message defect this class's __reduce__ exists
    # to fix, through a different door. UnusableMessage and TokenRefreshError
    # in this file already take their extras keyword-only for this reason.
    def __init__(self, response, order_id, account_hash, *args,
                 expected_account_hash=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.response = response
        self.order_id = order_id
        self.account_hash = account_hash
        self.expected_account_hash = expected_account_hash


def _type_name(value):
    '''A value's type name for a message, read through type's own descriptor
    so that a metaclass cannot raise inside the message.'''
    try:
        return str.__str__(type.__dict__['__name__'].__get__(type(value)))
    except Exception:
        return 'object'


class AccountNumberNotFoundError(SchwabError):
    '''
    Raised by :func:`find_account_hash` when no account in the response has the
    account number asked for.

    This is an ordinary answer rather than a fault: the token does not cover
    that account. Its message names neither account number, so it is safe to
    log.
    '''


class UnusableAccountNumbersError(SchwabError, ValueError):
    '''
    Raised by :func:`find_account_hash` when the account list is not the shape
    of the JSON body :meth:`Client.get_account_numbers
    <schwaby.client.Client.get_account_numbers>` responds with, or lists the
    account number more than once.

    Either way the answer cannot be trusted to name one account, so no hash is
    returned. Its message names neither account number.
    '''


def find_account_hash(account_numbers, account_number):
    '''
    Returns the account hash for ``account_number``, which is what every
    account-specific call takes in place of the account number.

    ``account_numbers`` is the parsed JSON body of the response from
    :meth:`Client.get_account_numbers
    <schwaby.client.Client.get_account_numbers>`, that is
    ``client.get_account_numbers().json()``. This makes no request, so it
    serves the synchronous and asynchronous clients alike.

    A token can cover several accounts and Schwab documents no order for the
    list, so taking the first entry's ``hashValue`` picks an account rather
    than finding one.

    :param account_numbers: The parsed ``get_account_numbers()`` response
                            body, a list of
                            ``{"accountNumber": ..., "hashValue": ...}``
                            objects.
    :param account_number: The account number, as a ``str`` --- Schwab's schema
                           types it as a string. Anything else is refused
                           rather than converted, because converting a number
                           drops any leading zero and then matches nothing.
    :raises TypeError: ``account_number`` is not a ``str``.
    :raises ValueError: ``account_number`` is not ASCII digits.
    :raises AccountNumberNotFoundError: No account has that number.
    :raises UnusableAccountNumbersError: The list is not that shape, or has
                                         that number more than once.
    '''
    # Types are checked through type() rather than isinstance, which a class
    # can satisfy by faking __class__.
    if not issubclass(type(account_number), str):
        raise TypeError(
                'account_number must be a str, as Schwab types it, not a '
                '{}'.format(_type_name(account_number)))
    account_number = str.__str__(account_number)
    # Schwab's account numbers are ASCII digits. Anything else -- a newline or
    # a byte order mark kept from a file, an unset environment variable, a
    # hyphen -- would read as an account the token does not cover, and that
    # sends a caller with a typo off to log in again.
    if not (account_number.isascii() and account_number.isdigit()):
        raise ValueError(
                'account_number must be ASCII digits, and this one is empty '
                'or has other characters in it')

    if not issubclass(type(account_numbers), list):
        raise UnusableAccountNumbersError(
                'expected the list get_account_numbers().json() returns, not '
                'a {}'.format(_type_name(account_numbers)))

    # Read through the built-in types' own methods, and reduce every value to
    # a plain str, so that what is checked is what is matched and returned. A
    # subclass could otherwise answer the check one way and the match another.
    malformed = ('an entry is not an accountNumber and hashValue pair of '
                 'strings')
    entries = list.__getitem__(account_numbers, slice(None))
    hashes = []
    for entry in entries:
        if not issubclass(type(entry), dict):
            raise UnusableAccountNumbersError(malformed)
        number = dict.get(entry, 'accountNumber')
        hash_value = dict.get(entry, 'hashValue')
        if not (issubclass(type(number), str)
                and issubclass(type(hash_value), str)):
            raise UnusableAccountNumbersError(malformed)
        number = str.__str__(number)
        hash_value = str.__str__(hash_value)
        if not hash_value:
            raise UnusableAccountNumbersError(malformed)
        if number == account_number:
            hashes.append(hash_value)

    if not hashes:
        raise AccountNumberNotFoundError(
                'none of the {} accounts listed has that account '
                'number'.format(len(entries)))
    if len(hashes) > 1:
        raise UnusableAccountNumbersError(
                'that account number is listed {} times'.format(len(hashes)))
    return hashes[0]


#: The furthest off an expiry may be. Schwab documents a 30-minute access
#: token and a refresh token that lasts seven days, so an expiry beyond that is
#: not one Schwab issued in seconds: an ``expires_in`` sent in milliseconds,
#: read as seconds, is 20.8 days. authlib would not refresh such a token until
#: then, and every call in between fails with a 401 and no exception. Refused,
#: it fails loudly on the first refresh instead.
_LONGEST_EXPIRY = 7 * 24 * 60 * 60


def _expiry_authlib_acts_on(expires_at):
    '''Whether authlib will refresh a token carrying this ``expires_at`` while
    it is still of use.

    authlib's ``OAuth2Token.is_expired`` checks one only when it is an int, and
    refreshes only once it is reached, so one further off than
    ``_LONGEST_EXPIRY`` is refused. The check on a token response and the
    classification of a stored token both ask this, so that they cannot
    disagree. They did: a response with a milliseconds expiry was refused as
    never reached, while a stored token with the same expiry was reported as
    retryable on every call.
    '''
    return (isinstance(expires_at, int)
            and expires_at <= time.time() + _LONGEST_EXPIRY)


class TokenRefreshError(SchwabError):
    '''
    Raised when a refresh of the OAuth token fails: Schwab rejects it, the
    token endpoint answers with something that is not a usable token, or the
    stored token cannot be refreshed at all.

    Every request refreshes the token first if it is close to expiring, so this
    surfaces from an ordinary call rather than from anything token-shaped. It
    exists so that an unattended application can catch a failure to refresh
    without importing ``authlib`` and catching an exception type this library
    never mentions. The original error is preserved as ``__cause__``.

    ``token_age`` is the number of seconds since the token was originally
    authorized, or ``None`` if this client was built without token metadata.
    Schwab documents a refresh token as valid for seven days after creation,
    and refreshing does not extend that, so a ``token_age`` past 604800 is past
    its documented term. Schwab has not held exactly to that term, so the age
    alone does not make a failure terminal -- ``refresh_token_invalid`` says
    that -- but past seven days a retryable failure's message says to alert
    someone.

    ``refresh_token_invalid`` is ``True`` when no amount of retrying will
    produce a working token, and only the full authorization_code flow -- which
    needs a human at a browser -- will help. That covers two cases: Schwab
    saying the refresh token is invalid, expired or revoked, and a stored token
    that cannot be used and will not change on its own, which the underlying
    OAuth library reports without contacting Schwab at all -- one with no
    refresh token, or one it cannot send and will never replace by itself: its
    expiry is missing, cannot be read as an int, or is more than seven days
    off, or there is no refresh token to refresh it with.

    A stored token it cannot send that *will* be refreshed is ``False``,
    although nothing was sent: it is replaced as its expiry nears.

    It is ``False`` for everything else, *including* failures this library did
    not recognize -- the conservative direction, since treating a recoverable
    failure as terminal would stop an application which only needed to try
    again. Seen once: Schwab answered a refresh with ``unsupported_token_type``
    and no nested ``invalid_grant``, and it recovered without a new login.

    Not every failed refresh is one of these. A server error from the token
    endpoint raises :class:`~schwaby.utils.HTTPStatusError`, a dropped
    connection raises one of ``httpx2``'s transport errors, and a response
    that is not JSON at all raises a ``ValueError``, usually
    ``json.JSONDecodeError``.
    '''

    def __init__(self, message, *, token_age=None, refresh_token_invalid=False):
        super().__init__(message)

        #: Seconds since the token was originally authorized, or ``None``.
        self.token_age = token_age

        #: ``True`` when no retry can produce a working token -- Schwab
        #: rejected the refresh token, or the stored token cannot be used and
        #: will not change on its own -- and only a new login flow will help.
        #: ``False`` when the failure may be transient, or was not recognized.
        self.refresh_token_invalid = refresh_token_invalid


class LazyLog:
    'Helper to defer evaluation of expensive variables in log messages'
    def __init__(self, func):
        self.func = func
    def __str__(self):
        return self.func()


class Utils(EnumEnforcer):
    '''Helper for placing orders on equities. Provides easy-to-use
    implementations for common tasks such as market and limit orders.'''

    def __init__(self, client, account_hash):
        '''Creates a new ``Utils`` instance. For convenience, this object
        assumes the user wants to work with a single account hash at a time.'''
        super().__init__(True)

        self.client = client
        self.account_hash = account_hash

    def set_account_hash(self, account_hash):
        '''Set the account hash used by this ``Utils`` instance.'''
        self.account_hash = account_hash

    def extract_order_id(self, place_order_response):
        '''Extracts the order ID from a response returned by
        :meth:`Client.place_order() <schwaby.client.Client.place_order>`.

        Every outcome other than success raises, and each one raises something
        different, because they call for different handling:

        * :class:`UnsuccessfulOrderException` --- Schwab rejected the order.
          Its own explanation is in the message and the whole response is on
          the exception. Nothing was placed.
        * :class:`MissingLocationHeaderError` and
          :class:`UnrecognizedLocationError` --- Schwab accepted the order and
          the ID could not be read. **The order may be live.** Both subclass
          :class:`OrderIdNotFoundError`, so catch that unless you need to tell
          them apart.
        * :class:`AccountHashMismatchException` --- the response belongs to a
          different account than this :class:`Utils` was built with. **The
          order is live**, on the account Schwab named; the exception carries
          its ID.

        :param place_order_response: Response from
                                     :meth:`Client.place_order()
                                     <schwaby.client.Client.place_order>`.
        '''
        if place_order_response.is_error:
            raise UnsuccessfulOrderException(
                    place_order_response,
                    'order not successful: status {}{}'.format(
                        place_order_response.status_code,
                        _describe_error(place_order_response)))

        try:
            location = place_order_response.headers['Location']
        except KeyError:
            # `from None` because the chained "During handling of the above
            # exception" opens the traceback with KeyError: 'Location', which
            # in a log capture reads as the library crashing on a dict lookup
            # -- the opposite of what the message below is trying to say.
            raise MissingLocationHeaderError(
                    place_order_response, None,
                    'order was accepted but the response carried no Location '
                    'header, so it has no order ID to return. The order may be '
                    'live: check get_orders_for_account.') from None

        m = re.match(
                r'https://api.schwabapi.com/trader/v1/accounts/(\w+)/orders/(\d+)',
                location)

        if m is None:
            raise UnrecognizedLocationError(
                    place_order_response, location,
                    'order was accepted but its Location header does not look '
                    'like an order URL, so it has no order ID to return. The '
                    'order may be live: check get_orders_for_account. Header '
                    'was: {!r}'.format(location))
        account_hash, order_id = m.group(1), int(m.group(2))

        if str(account_hash) != str(self.account_hash):
            raise AccountHashMismatchException(
                place_order_response, order_id, account_hash,
                'order {} was placed on account {!r}, but this Utils was built '
                'with {!r}. The order is live on the account Schwab named; its '
                'id is on this exception as .order_id.'.format(
                    order_id, account_hash, self.account_hash),
                expected_account_hash=self.account_hash)

        return order_id
