from authlib.common.errors import AuthlibBaseError
from authlib.integrations.base_client.errors import OAuthError
from authlib.integrations.httpx_client import AsyncOAuth2Client, OAuth2Client
from authlib.oauth2.rfc6749 import OAuth2Token

import collections
import collections.abc
import contextlib
import decimal
import httpx2
import importlib
import json
import logging
import math
import numbers
import os
import queue
import sys
import tempfile
import time
import urllib
import urllib.parse
import warnings
import webbrowser

from schwaby.client import AsyncClient, Client
from schwaby.utils import (LoginExchangeError, SchwabError,
        _expiry_authlib_acts_on, _refusal_text, _venue_text)
from schwaby.debug import register_redactions


TOKEN_ENDPOINT = 'https://api.schwabapi.com/v1/oauth/token'


def get_logger():
    return logging.getLogger(__name__)


# The token grants full access to the account it was issued for, so it is
# written readable only by its owner.
TOKEN_FILE_MODE = 0o600


def __write_token_file(token_path, token):
    '''
    Serializes ``token`` to ``token_path``, atomically and readable only by the
    current user.

    The token is written to a temporary file in the same directory and then
    renamed over the destination. ``os.replace`` is atomic within a filesystem,
    so a reader either sees the complete previous token or the complete new one,
    and a process which dies partway through leaves the existing token intact.
    Writing in place would truncate the file first, so an interrupted write --
    a machine losing power, a supervisor killing a long-running process during a
    refresh -- would leave a partial file that cannot be parsed on the next
    start, and recovering from that requires a fresh interactive login.
    '''
    # Renaming onto a symlink replaces the link itself rather than writing
    # through it, which would silently orphan whatever the caller pointed it at.
    # Resolve first so a linked token file keeps working as it did.
    token_path = os.path.realpath(token_path)

    directory = os.path.dirname(os.path.abspath(token_path)) or '.'

    # The temporary file must share a filesystem with the destination for the
    # rename to be atomic, so it is created alongside it rather than in /tmp.
    fd, tmp_path = tempfile.mkstemp(
            dir=directory, prefix='.schwab-py-token', suffix='.tmp')

    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(token, f)
            # Flush all the way to disk before the rename, so that a crash
            # cannot leave the destination pointing at an empty file.
            f.flush()
            os.fsync(f.fileno())

        os.chmod(tmp_path, TOKEN_FILE_MODE)
        os.replace(tmp_path, token_path)

        # The rename is atomic, but the directory entry recording it is only
        # durable once the directory itself is flushed. Not every platform
        # permits opening a directory, so this is best effort: failing to sync
        # here can only mean the previous token survives a crash, which is a
        # great deal better than neither surviving.
        try:
            dir_fd = os.open(directory, os.O_RDONLY)
        except OSError:  # pragma: no cover
            pass
        else:
            try:
                os.fsync(dir_fd)
            except OSError:  # pragma: no cover
                pass
            finally:
                os.close(dir_fd)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:  # pragma: no cover
            pass
        raise

    __sweep_stale_token_temp_files(directory)


#: How long a temporary token file must go untouched before a later write will
#: remove it.
#:
#: A write takes about a millisecond, so this is roughly a 300,000x margin. The
#: margin is the whole safety mechanism: if a write ever did stall for longer
#: than this, a *different* process sweeping the directory would delete the
#: file out from under it, and that write would fail with FileNotFoundError
#: from os.replace. Demonstrated by stalling a write deliberately -- the token
#: file is left valid and holding the other process's version, so the cost is a
#: lost token update rather than a damaged file, but it is a real edge and the
#: margin is what keeps it closed.
#:
#: Reaching it needs two processes writing the same token file, which this
#: library already tells you not to do, *and* a write stalled for five minutes,
#: at which point the filesystem has bigger problems.
#:
#: One way to reach it without a stall: the age is the file's mtime measured
#: against this machine's clock. On a network-mounted token directory the mtime
#: comes from the server and the comparison does not, so a clock skew larger
#: than this makes every temporary file there look ancient, including one
#: another process is writing right now. Keeping the token file on local disk
#: avoids the question entirely, and is a good idea for a credential anyway.
TOKEN_TEMP_FILE_MAX_AGE = 300.0


def __sweep_stale_token_temp_files(directory):
    '''Removes temporary token files abandoned by a process which was killed.

    The cleanup above runs on the way out of an exception, which a SIGKILL does
    not give anybody. What is left behind is not an empty file: it is a
    complete, readable copy of the token, holding a refresh token which stays
    valid for the rest of its seven days. Nothing else ever removes them, so a
    process which crashes repeatedly leaves a growing pile of live credentials
    next to the token file.

    Only files matching the name this module gives its own temporaries are
    considered, and only once they are old enough that they cannot be a write
    in progress -- another process may be partway through one right now, and
    deleting its temporary file would break it, which is measurable and not
    hypothetical. See ``TOKEN_TEMP_FILE_MAX_AGE`` for what that margin costs
    and why it is set where it is. The token directory is often the user's own,
    so anything else found there is none of our business.

    This runs after the rename rather than before it, so a write never sweeps
    its own temporary file: by the time this is reached, that file has already
    become the token.

    Failures are ignored throughout. This is tidying, and it must never be the
    reason a token write appears to fail.
    '''
    try:
        names = os.listdir(directory)
    except OSError:  # pragma: no cover
        return

    cutoff = time.time() - TOKEN_TEMP_FILE_MAX_AGE

    for name in names:
        if not (name.startswith('.schwab-py-token') and name.endswith('.tmp')):
            continue

        path = os.path.join(directory, name)
        try:
            if os.path.getmtime(path) < cutoff:
                os.unlink(path)
        except OSError:  # pragma: no cover
            # Vanished under us, or not ours to delete. Either way, fine.
            pass


def _is_usable_token(token):
    '''Whether a token response can replace the stored token.

    What the session needs to go on using a token: a string access token, the
    ``bearer`` type it knows how to send, and an expiry authlib acts on.
    authlib builds that from an ``expires_at`` it can read, or else from
    ``expires_in``, and checks it only when the result is an int. Without one
    the token is never refreshed, and every call fails once it lapses. It is
    bounded too, by the seven days a refresh token lasts: an ``expires_in``
    sent in milliseconds is 20.8 days read as seconds, and the token would fail
    every call until then. An expiry already past, or inside the session's
    leeway, is accepted -- that token is refreshed on every call, which
    works, and refusing it would not stop the refreshes.

    A ``refresh_token`` may be absent, and authlib then keeps the stored one;
    present, it replaces the stored one, so an empty value would erase a
    refresh token that still works.

    Nothing else is required. A stricter test would refuse a real token over
    a field Schwab might omit, and a refused refresh stops an application that
    had a working token a moment ago.
    '''
    try:
        access_token = token.get('access_token')
        token_type = token.get('token_type')
        if not (isinstance(access_token, str) and access_token
                and isinstance(token_type, str)
                and token_type.lower() == 'bearer'):
            return False
        # Built the way authlib will build it. Whatever authlib would raise on
        # here -- expires_in 'abc', say -- refuses the token.
        expires_at = OAuth2Token(dict(token)).get('expires_at')
        if not _expiry_authlib_acts_on(expires_at):
            return False
        if 'refresh_token' in token:
            refresh_token = token['refresh_token']
            return isinstance(refresh_token, str) and bool(refresh_token)
        return True
    except Exception:
        return False


def _refuse_unusable_token_response(response):
    '''Refuses a token response that is not a usable token, before it is
    kept.

    authlib takes any JSON object without an ``error`` key as the new token,
    sets it on the session and then writes it; JSON that is not an object it
    sets on the session and then fails on. Measured with a mocked token
    endpoint: ``{"message": "Unauthorized"}`` was written to the token file,
    and a list or a string was kept in memory, so every call after either
    failed without contacting Schwab -- until a new login, or a restart.

    Registered as authlib's ``refresh_token_response`` compliance hook, which
    sees the response before it is parsed, so nothing unusable is stored. A
    server error and a body that is not JSON are left to authlib, which raises
    on both without storing anything, and so is a JSON object carrying
    ``error``, which authlib reports as the rejection it is.

    Also registered as ``access_token_response`` on the client that exchanges
    a login's code, whose token was written the same way.
    '''
    if response.status_code >= 500:
        return response
    try:
        body = response.json()
    except ValueError:
        return response
    if isinstance(body, dict) and ('error' in body or _is_usable_token(body)):
        return response
    refusal = OAuthError(
            error='unusable_token_response',
            description='the token endpoint answered with something that is '
                        'not a usable token, so it was not stored: it needs a '
                        'non-empty string access token, the bearer type, an '
                        'expiry no more than seven days away, and no refresh '
                        'token that is empty or not a string')
    # Marked, so a login can tell this library's description from text an
    # endpoint sends with the same code. Not a subclass: a refresh reports it
    # as TokenRefreshError's cause, and the docs are searched by the line its
    # traceback prints.
    refusal._described_by_this_library = True
    raise refusal


def _stored_token_for_session(token):
    '''The stored token, in the shape authlib needs to send and refresh it.

    Nothing checked it before a session was built on it, and authlib fails on
    a wrong shape with something other than ``OAuthError``, which gets past
    the translation into ``TokenRefreshError``: a mapping that is not a dict
    has no ``is_expired``, a ``token_type`` that is not a string has no
    ``lower``, and a refresh token that is not a string is sent as whatever
    ``bytes()`` makes of it, or raises. A token this library wrote has none
    of these shapes.
    '''
    advice = ('If it came from a token file, delete the file and create a '
              'new one.')
    if not issubclass(type(token), collections.abc.Mapping):
        raise ValueError(
                'The token\'s "token" entry is not a JSON object, so it is '
                'not a token this library wrote. ' + advice)
    # A copy, and a real dict: authlib converts only a dict into its token
    # type, and nothing below should reach the caller's own object.
    token = dict(token)
    for key in ('token_type', 'refresh_token'):
        if key not in token:
            continue
        if token[key] is None:
            # A nullable field in a token store. authlib reads an absent key
            # the way a null means it: the bearer type, and no refresh token.
            del token[key]
        elif not issubclass(type(token[key]), str):
            raise ValueError(
                    'The token\'s {} is not a string, so the token cannot be '
                    'sent or refreshed. {}'.format(key, advice))

    # authlib refreshes a token only once an expiry it reads as an int is near.
    # One with no such expiry -- none at all, an ISO date or a float in a
    # string -- is never refreshed. One counted from expires_in is counted
    # afresh at every build, so a process restarting more often than that
    # never refreshes either. And one further off than the seven days a
    # refresh token lasts, stored in milliseconds say, is not reached while it
    # is of use. Each goes on sending a lapsed token, and every call fails with
    # a 401 and no exception. When the token was issued is unknown, so with a
    # refresh token to refresh it, its expiry is taken as now and the first
    # call refreshes it.
    if token.get('refresh_token') and _expiry_needs_refreshing_first(token):
        token['expires_at'] = int(time.time())
    return token


def _expiry_needs_refreshing_first(token):
    '''Whether authlib would not refresh this token while its expiry is of use:
    ``expires_at`` is missing, null or not readable as an int, so authlib
    counts ``expires_in`` from the build or has no expiry at all; or it is
    further off than the bound on an expiry. A zero expiry authlib already
    treats as lapsed, and refreshes itself.'''
    expires_at = token.get('expires_at')
    if expires_at is None:
        return True
    try:
        expires_at = int(expires_at)
    except ValueError:
        return True
    except Exception:
        # authlib raises on this itself, and it is left to do so.
        return False
    return not _expiry_authlib_acts_on(expires_at)


def _new_session(session_class, api_key, app_secret, token, update_token):
    '''The refreshing session every client is built on.

    One builder, because there were two: the check on refresh responses was
    first added where token files and access functions build their session,
    and a client returned by a login flow built its own and kept storing
    whatever the token endpoint returned.
    '''
    session = session_class(api_key,
                            client_secret=app_secret,
                            token=token,
                            token_endpoint=TOKEN_ENDPOINT,
                            update_token=update_token,
                            leeway=300)
    session.register_compliance_hook(
            'refresh_token_response', _refuse_unusable_token_response)
    return session


def __make_update_token_func(token_path):
    def update_token(t, *args, **kwargs):
        get_logger().info('Updating token to file %s', token_path)

        __write_token_file(token_path, t)
    return update_token



def __normalize_credential(value, name):
    '''
    Strips surrounding whitespace from an app key or secret, warning when there
    was any.

    Copy-pasting a key out of the developer console picks up a trailing space or
    newline easily, and Schwab does not treat the result consistently -- some
    endpoints tolerate it and some reject it, so the symptom is an intermittent
    authentication failure a long way from its cause.
    '''
    if not isinstance(value, str):
        return value

    stripped = value.strip()
    if stripped != value:
        warnings.warn(
                '{} had surrounding whitespace, which has been stripped. This '
                'is usually a copy-paste artifact. Schwab does not handle it '
                'consistently, so it is worth correcting at the '
                'source.'.format(name),
                stacklevel=3)
    return stripped

def _is_finite(number):
    '''Whether a real number is finite when read as a float, which a Decimal
    and a numpy scalar both can be. An int too large for a float is refused
    along with infinity; no timestamp comes near that size.'''
    try:
        return math.isfinite(number)
    except (ValueError, OverflowError):
        # A signalling NaN raises rather than converting.
        return False


def __token_loader(token_path, level=logging.INFO):
    def load_token():
        get_logger().log(level, 'Loading token from file %s', token_path)

        with open(token_path, 'rb') as f:
            try:
                return json.load(f)
            except RecursionError:
                # Nesting past the interpreter's depth is not a token this
                # library wrote, and fails like any other file that is not.
                raise ValueError(
                        'The token file is nested too deeply to be a token '
                        'this library wrote. Delete it and create a new '
                        'one.') from None
    return load_token


class TokenMetadata:
    '''
    Provides the functionality required to maintain and update our view of the
    token's metadata.
    '''
    def __init__(self, token, creation_timestamp, unwrapped_token_write_func):
        '''
        :param token: The token to wrap in metadata
        :param creation_timestamp: Timestamp at which this token was initially 
                                   created. Notably, this timestamp does not 
                                   change when the token is updated.
        :unwrapped_token_write_func: Function that accepts a non-metadata
                                     wrapped token and writes it to disk or 
                                     other persistent storage.
        '''

        self.creation_timestamp = creation_timestamp

        # The token write function is ultimately stored in the session. When we
        # get a new token we immediately wrap it in a new sesssion. We hold on
        # to the unwrapped token writer function to allow us to inject the
        # appropriate write function.
        self.unwrapped_token_write_func = unwrapped_token_write_func

        # The current token. Updated whenever the wrapped token update function 
        # is called.
        self.token = token

    @classmethod
    def from_loaded_token(cls, token, unwrapped_token_write_func):
        '''
        Returns a new ``TokenMetadata`` object extracted from the metadata of
        the loaded token object.

        A token predating the metadata wrapper is rejected rather than adapted.
        Its creation timestamp is what decides whether the refresh token is
        still inside Schwab's seven-day window, and there is no honest value to
        invent for it -- guessing would either refuse a usable token or keep
        presenting a dead one.
        '''
        # json.load returns whatever the file held, and a token_read_func
        # may hand back any mapping. Anything else -- a list, a string, null
        # -- would fail below with an error about indexing, which says nothing
        # about the token.
        # Types through type(): a class can fake __class__ for isinstance,
        # and then fails below with an error that says nothing about tokens.
        if not issubclass(type(token), collections.abc.Mapping):
            raise ValueError(
                    'The token is not a JSON object, so it is not a token '
                    'this library wrote. If it came from a token file, delete '
                    'the file and create a new one.')
        if 'creation_timestamp' not in token:
            raise ValueError(
                    'WARNING: The token format has changed since this token '+
                    'was created. Please delete it and create a new one.')

        # The creation time decides how much of the seven days is left, so a
        # value that is not a finite number is refused here, rather than
        # failing later inside token_age() with an error about subtraction.
        # Any real number works there, including the Decimal a token store
        # such as DynamoDB hands back.
        creation_timestamp = token['creation_timestamp']
        if (issubclass(type(creation_timestamp), bool)
                or not issubclass(type(creation_timestamp),
                                  (numbers.Real, decimal.Decimal))
                or not _is_finite(creation_timestamp)):
            raise ValueError(
                    'The token\'s creation_timestamp is not a finite number '
                    'a float can hold, so its age cannot be known. If it came '
                    'from a token file, delete the file and create a new '
                    'one.')
        if 'token' not in token:
            raise ValueError(
                    'The token has no "token" entry, so it is not a token '
                    'this library wrote. If it came from a token file, delete '
                    'the file and create a new one.')

        return TokenMetadata(
                token['token'],
                creation_timestamp,
                unwrapped_token_write_func)

    def token_age(self):
        '''Returns the number of second elapsed since this token was initially 
        created.'''
        return int(time.time()) - self.creation_timestamp

    def wrapped_token_write_func(self):
        '''
        Returns a version of the unwrapped write function which wraps the token 
        in metadata and updates our view on the most recent token.
        '''
        def wrapped_token_write_func(token, *args, **kwargs):
            # If the write function is going to raise an exception, let it do so 
            # here before we update our reference to the current token.
            ret = self.unwrapped_token_write_func(
                self.wrap_token_in_metadata(token), *args, **kwargs)

            self.token = token

            return ret

        return wrapped_token_write_func

    def wrap_token_in_metadata(self, token):
        return {
            'creation_timestamp': self.creation_timestamp,
            'token': token,
        }


def token_file_age(token_path):
    '''Returns the number of seconds since the token in ``token_path`` was
    created by a login, without building a client.

    This is the number :meth:`Client.token_age
    <schwaby.client.Client.token_age>` reports, read through the same code, for
    a monitor or a scheduled job that only needs to know how much of the
    refresh token's seven days is left.

    It reads the ``creation_timestamp`` this library stores in the file, not
    the file's modification time. The file is rewritten every time the access
    token is refreshed, so its modification time says when that last happened,
    not when the seven days began.

    :param token_path: Path to a token file this library wrote.
    :raises ValueError: The file is not a token in the format this library
                        writes: not JSON, nested too deeply to read, not a
                        JSON object, written before the creation timestamp
                        was stored, without a ``token`` entry, or with a
                        creation timestamp that is not a finite number a
                        float can hold.
    :raises OSError: The file cannot be read.
    '''
    # A monitor reads the age often, so this read logs at DEBUG. At INFO it
    # would bury the line a client writes when it actually loads the token.
    return TokenMetadata.from_loaded_token(
            __token_loader(token_path, logging.DEBUG)(), None).token_age()


################################################################################
# client_from_login_flow


# The login flow probes this path to decide whether the thing listening on the
# callback port is its own server. One constant rather than two literals: the
# route and the probe are 200 lines apart, and a divergence between them does
# not fail -- it makes the probe miss its own server and time out.
#
# The value matters as much as the sharing. It was `/schwab-py-internal/status`
# through 3.0.3, which is the path `schwab-py` serves for exactly the same
# purpose -- so a `schwab-py` login flow already holding the port answers this
# with 200, the guard below reads that as "our server is up", and the browser
# delivers the authorization code into the other project's queue. That was
# unreachable while the two could not be installed together and is merely
# unlikely now, which is the wrong direction for a check whose whole job is to
# refuse a stranger.
INTERNAL_STATUS_PATH = '/schwaby-internal/status'


# This runs in a separate process and is invisible to coverage
def __run_client_from_login_flow_server(
        q, callback_port, callback_path):  # pragma: no cover
    '''Helper server for intercepting redirects to the callback URL. See
    client_from_login_flow for details.'''

    # Imported here rather than at module scope: this is the only entry point
    # that runs a callback server, and a process which loads its token from a
    # file should not pay for a web framework at import time.
    import flask

    app = flask.Flask(__name__)

    @app.route(callback_path)
    def handle_token():
        q.put(flask.request.url)
        return 'schwaby callback received! You may now close this window/tab.'

    @app.route(INTERNAL_STATUS_PATH)
    def status():
        return 'running'

    if callback_port == 443:
        return

    # Wrap this call in some hackery to suppress the flask startup messages
    with open(os.devnull, 'w') as devnull:
        import logging
        log = logging.getLogger('werkzeug')
        log.setLevel(logging.ERROR)

        old_stdout = sys.stdout
        sys.stdout = devnull
        app.run(port=callback_port, ssl_context='adhoc')
        sys.stdout = old_stdout


class RedirectTimeoutError(SchwabError):
    pass

class RedirectServerExitedError(SchwabError):
    pass

# Capture the real time.time so that we can use it in server initialization
# while simultaneously mocking it in testing
__TIME_TIME = time.time

#: Seconds to wait for the local callback server to start answering before
#: giving up. Generous: it only has to bind a port and start a thread, but a
#: loaded machine can take a moment.
SERVER_STARTUP_TIMEOUT = 30.0

def client_from_login_flow(api_key, app_secret, callback_url, token_path,
                           asyncio=False, enforce_enums=False, 
                           token_write_func=None, callback_timeout=300.0,
                           interactive=True, requested_browser=None):
    '''
    Open a web browser to perform an OAuth webapp login flow and creates a 
    client wrapped around the resulting token. The client will be configured to 
    refresh the token as necessary, writing each updated version to 
    ``token_path``.

    The callback server below needs ``flask``, ``multiprocess`` and ``psutil``.
    They are ordinary dependencies, so a plain ``pip install schwaby`` has them.

    **Important Note:** this method starts a server on the port in your
    callback URL, and Schwab sends your login data to it in the request query.
    *Anyone who receives that request can act on your account as though they
    were you.* The server uses a self-signed certificate, which your browser
    will warn about; that protects the data in transit and does nothing about
    who is listening on the port.
    Only ``127.0.0.1`` is allowed as a host, and the port should be above
    ``1024``. Most users should use ``https://127.0.0.1:8182``. See
    :ref:`callback_url_advisory` for the rest, including what to do if your app
    is registered with a callback URL this will not accept.

    :param api_key: Your Schwab application's app key.
    :param app_secret: Application secret provided upon :ref:`app approval 
                       <approved_pending>`.
    :param callback_url: Your Schwab application's callback URL. Note this must
                         *exactly* match the value you've entered in your
                         application configuration, otherwise login will fail
                         with a security error. Be sure to check case and 
                         trailing slashes. :ref:`See the above note for
                         important information about setting your callback URL.
                         <callback_url_advisory>`
    :param token_path: Path to which the new token will be written. If the token
                       file already exists, it will be overwritten with a new
                       one. Updated tokens will be written to this path as well.
    :param asyncio: If set to ``True``, this will enable async support allowing
                    the client to be used in an async environment. Defaults to
                    ``False``
    :param enforce_enums: Set it to ``False`` to disable the enum checks on ALL
                          the client methods. Only do it if you know you really
                          need it. For most users, it is advised to use enums
                          to avoid errors.
    :param token_write_func: Function that writes the token on update. Will be
                             called whenever the token is updated, such as when
                             it is refreshed. See the above-mentioned example 
                             for what parameters this method takes.
    :param callback_timeout: How long to wait for a callback from the server 
                             before giving up, in seconds. Wait forever if set
                             to zero or ``None``.
    :param interactive: Require user input before starting the browser.
    :param requested_browser: Name of the browser to attempt to open. This 
                              function uses the standard ``webbrowser`` library 
                              under the hood, so you can find a table of valid 
                              values
                              `here <https://docs.python.org/3/library/webbrowser.html#webbrowser.register>`__
    '''
    api_key = __normalize_credential(api_key, 'api_key')
    app_secret = __normalize_credential(app_secret, 'app_secret')

    if callback_timeout is None:
        callback_timeout = 0
    if callback_timeout < 0:
        raise ValueError('callback_timeout must be positive')

    # Start the server
    parsed = urllib.parse.urlparse(callback_url)

    if parsed.hostname != '127.0.0.1':
        # Documented under "Callback URL Requirements", which is where the
        # message below links. A test asserts the anchor exists in the source
        # that builds that page.
        raise ValueError(
                ('Disallowed hostname {}. client_from_login_flow only allows '+
                 'callback URLs with hostname 127.0.0.1. See here for ' +
                 'more information: https://schwaby.readthedocs.io/en/' +
                 'stable/auth.html#callback-url-requirements').format(
                     parsed.hostname))

    callback_port = parsed.port if parsed.port else 443
    callback_path = parsed.path if parsed.path else '/'

    # Imported here rather than at module scope: this is the only entry point
    # that runs a local callback server, and a process which loads its token
    # from a file should not pay for a web framework and a process manager it
    # will never call.
    import multiprocess
    import psutil

    # flask is imported by the server, which runs in a child process -- so an
    # ImportError there would surface as child stderr and the parent would
    # report RedirectServerExitedError, blaming the callback port for a broken
    # install. Imported here, in the parent, where it can actually be raised.
    #
    # import_module rather than `import flask`, which pyflakes reports as an
    # unused import -- it does not honour flake8's noqa, and `python -m
    # pyflakes schwaby/` is the documented way to find dead code here. A lint
    # channel with one permanent entry in it is one nobody reads.
    importlib.import_module('flask')

    # cryptography for the same reason, one step further out: app.run below
    # passes ssl_context='adhoc', and werkzeug builds that certificate with
    # `from cryptography import x509`. Absent, werkzeug raises a TypeError
    # inside the child, which reaches the parent as a dead server rather than
    # as a missing package.
    importlib.import_module('cryptography')

    output_queue = multiprocess.Queue()

    server = multiprocess.Process(
            target=__run_client_from_login_flow_server,
            args=(output_queue, callback_port, callback_path))

    # Context manager to kill the server upon completion
    @contextlib.contextmanager
    def callback_server():
        server.start()

        try:
            yield
        finally:
            try:
                psutil.Process(server.pid).kill()
            except psutil.NoSuchProcess:
                pass

    with callback_server():
        # Wait until the server successfully starts. Bounded, because a server
        # which comes up but never answers would otherwise leave this spinning
        # with nothing on screen to say why.
        startup_deadline = __TIME_TIME() + SERVER_STARTUP_TIMEOUT

        while True:
            # Check if the server is still alive
            if server.exitcode is not None:
                # RedirectServerExitedError is documented in docs/util.rst
                # under Exceptions. The parent-side imports above exist so that
                # a broken install does not arrive here wearing this message.
                raise RedirectServerExitedError(
                        'Redirect server exited. Are you attempting to use a ' +
                        'callback URL without a port number specified?')

            if __TIME_TIME() >= startup_deadline:
                raise RedirectServerExitedError(
                        ('Redirect server did not become ready within {} '
                         'seconds. It is running, but not answering on port '
                         '{}.').format(SERVER_STARTUP_TIMEOUT, callback_port))

            # Attempt to send a request to the server
            try:
                # verify=False because the callback server presents a
                # self-signed certificate. httpx2 says nothing about that; the
                # suppression which used to sit here was for a urllib3 warning
                # that never reached this code.
                resp = httpx2.get(
                        'https://127.0.0.1:{}{}'.format(
                            callback_port, INTERNAL_STATUS_PATH),
                        verify=False)
            except (httpx2.ConnectError, httpx2.ConnectTimeout):
                # Not listening yet. Which of the two you get depends on the
                # host: a port nothing is bound to is normally refused, giving
                # ConnectError, but where the attempt is dropped rather than
                # rejected -- a firewall, or macOS's behaviour on some
                # configurations -- it times out instead. ConnectTimeout is a
                # sibling of ConnectError, not a subclass, so catching only the
                # latter let that case escape and take down the whole login
                # flow while the server was still starting.
                pass
            else:
                # It answered. Anything other than success means the port is
                # occupied by something which is not our callback server, and
                # continuing would hand the login redirect to a stranger.
                if resp.status_code == httpx2.codes.OK:
                    break

                raise RedirectServerExitedError(
                        ('Something other than the schwaby callback server '
                         'is listening on port {}: it answered the status '
                         'check with HTTP {}. Refusing to start a login flow '
                         'which would send your authorization code to '
                         'it.').format(callback_port, resp.status_code))

            time.sleep(0.1)

        # Open the browser
        auth_context = get_auth_context(api_key, callback_url)

        print()
        print('***********************************************************************')
        print()
        print('This is the browser-assisted login and token creation flow for')
        print('schwaby. This flow automatically opens the login page on your')
        print('browser, captures the resulting OAuth callback, and creates a token')
        print('using the result. The authorization URL is:')
        print()
        print('>>', auth_context.authorization_url)
        print()
        print('IMPORTANT: Your browser will give you a security warning about an')
        print('invalid certificate prior to issuing the redirect. This is because')
        print('schwaby has started a server on your machine to receive the OAuth')
        print('redirect using a self-signed SSL certificate. You can ignore that')
        print('warning, but make sure to first check that the URL matches your')
        print('callback URL, ignoring URL parameters. As a reminder, your callback URL')
        print('is:')
        print()
        print('>>',callback_url)
        print()
        print('See here to learn more about self-signed SSL certificates:')
        print('https://schwaby.readthedocs.io/en/stable/auth.html'
              '#browser-warnings-about-invalid-self-signed-certificates')
        print()
        print('If you encounter any issues, see here for troubleshooting:')
        print('https://schwaby.readthedocs.io/en/stable/auth.html#troubleshooting')
        print('***********************************************************************')
        print()

        if interactive:
            input('Press ENTER to open the browser. Note you can call ' +
                  'this method with interactive=False to skip this input.')

        controller = webbrowser.get(requested_browser)
        controller.open(auth_context.authorization_url)

        # Wait for a response
        now = __TIME_TIME()
        timeout_time = now + callback_timeout
        received_url = None
        while True:
            now = __TIME_TIME()
            if now >= timeout_time:
                if callback_timeout == 0:
                    # XXX: We're detecting a test environment here to avoid an 
                    #      infinite sleep. Surely there must be a better way to do 
                    #      this...
                    if __TIME_TIME != time.time:  # pragma: no cover
                        raise ValueError('endless wait requested')
                else:
                    break

            # Attempt to fetch from the queue
            try:
                received_url = output_queue.get(
                        timeout=min(timeout_time - now, 0.1))
                break
            except queue.Empty:
                pass

        if not received_url:
            raise RedirectTimeoutError(
                    'Timed out waiting for a post-authorization callback. You '+
                    'can set a longer timeout by passing a value of ' +
                    'callback_timeout to client_from_login_flow.')

        token_write_func = (
            __make_update_token_func(token_path) if token_write_func is None
            else token_write_func)

        return client_from_received_url(
                api_key, app_secret, auth_context, received_url, 
                token_write_func, asyncio, enforce_enums)


################################################################################
# client_from_token_path


def client_from_token_file(token_path, api_key, app_secret, asyncio=False,
                           enforce_enums=True):
    '''
    Returns a session from an existing token file. The session will perform
    an auth refresh as needed. It will also update the token on disk whenever
    appropriate.

    :param token_path: Path to an existing token. Updated tokens will be written
                       to this path. If you do not yet have a token, use
                       :func:`~schwaby.auth.client_from_login_flow` or
                       :func:`~schwaby.auth.easy_client` to create one.
    :param api_key: Your Schwab application's app key.
    :param app_secret: Application secret. Provided upon :ref:`app approval 
                       <approved_pending>`.
    :param asyncio: If set to ``True``, this will enable async support allowing
                    the client to be used in an async environment. Defaults to
                    ``False``
    :param enforce_enums: Set it to ``False`` to disable the enum checks on ALL
                          the client methods. Only do it if you know you really
                          need it. For most users, it is advised to use enums
                          to avoid errors.
    '''
    api_key = __normalize_credential(api_key, 'api_key')
    app_secret = __normalize_credential(app_secret, 'app_secret')

    load = __token_loader(token_path)

    return client_from_access_functions(
        api_key, app_secret, load, __make_update_token_func(token_path),
        asyncio=asyncio, enforce_enums=enforce_enums)


################################################################################
# client_from_manual_flow


def client_from_manual_flow(api_key, app_secret, callback_url, token_path,
                            asyncio=False, token_write_func=None,
                            enforce_enums=True):
    '''
    Walks the user through performing an OAuth login flow by manually
    copy-pasting URLs, and returns a client wrapped around the resulting token.
    The client will be configured to refresh the token as necessary, writing
    each updated version to ``token_path``.

    :param api_key: Your Schwab application's app key.
    :param app_secret: Application secret provided upon :ref:`app approval 
                       <approved_pending>`.
    :param callback_url: Your Schwab application's callback URL. Note this must
                         *exactly* match the value you've entered in your
                         application configuration, otherwise login will fail
                         with a security error. Be sure to check case and 
                         trailing slashes.
    :param token_path: Path to which the new token will be written. If the token
                       file already exists, it will be overwritten with a new
                       one. Updated tokens will be written to this path as well.
    :param asyncio: If set to ``True``, this will enable async support allowing
                    the client to be used in an async environment. Defaults to
                    ``False``
    :param enforce_enums: Set it to ``False`` to disable the enum checks on ALL
                          the client methods. Only do it if you know you really
                          need it. For most users, it is advised to use enums
                          to avoid errors.
    '''
    api_key = __normalize_credential(api_key, 'api_key')
    app_secret = __normalize_credential(app_secret, 'app_secret')
    get_logger().info('Creating new token with callback URL \'%s\' ' +
                       'and token path \'%s\'', callback_url, token_path)

    auth_context = get_auth_context(api_key, callback_url)

    print('\n**************************************************************\n')
    print('This is the manual login and token creation flow for schwaby.')
    print('Please follow these instructions exactly:')
    print()
    print(' 1. Open the following link by copy-pasting it into the browser')
    print('    of your choice:')
    print()
    print('        ' + auth_context.authorization_url)
    print()
    print(' 2. Log in with your account credentials. You may be asked to')
    print('    perform two-factor authentication using text messaging or')
    print('    another method, as well as whether to trust the browser.')
    print()
    print(' 3. When asked whether to allow your app access to your account,')
    print('    select "Allow".')
    print()
    print(' 4. Your browser should be redirected to your callback URI. Copy')
    print('    the ENTIRE address, paste it into the following prompt, and press')
    print('    Enter/Return.')
    print()
    print('If you encounter any issues, see here for troubleshooting:')
    print('https://schwaby.readthedocs.io/en/stable/auth.html#troubleshooting')
    print('\n**************************************************************\n')

    if callback_url.startswith('http://'):
        print(('WARNING: Your callback URL ({}) will transmit data over HTTP, ' +
               'which is a potentially severe security vulnerability. ' +
               'Please go to your app\'s configuration with Schwab ' +
               'and update your callback URL to begin with \'https\' ' +
               'to stop seeing this message.').format(callback_url))

    received_url = input('Redirect URL> ').strip()

    token_write_func = (
        __make_update_token_func(token_path) if token_write_func is None
        else token_write_func)

    return client_from_received_url(
            api_key, app_secret, auth_context, received_url, token_write_func, 
            asyncio, enforce_enums)


################################################################################
# client_from_access_functions


def client_from_access_functions(api_key, app_secret, token_read_func,
                                 token_write_func, asyncio=False,
                                 enforce_enums=True):
    '''
    Returns a session from an existing token file, using the accessor methods to
    read and write the token. This is an advanced method for users who do not
    have access to a standard writable filesystem, such as users of AWS Lambda
    and other serverless products who must persist token updates on
    non-filesystem places, such as S3. 99.9% of users should not use this
    function.

    Users are free to customize how they represent the token file. In theory,
    since they have direct access to the token, they can get creative about how
    they store it and fetch it. In practice, it is *highly* recommended to
    simply accept the token object and use ``json`` to serialize and
    deserialize it, without inspecting it in any way.

    The two functions have specific signatures:

    .. code-block:: python

      def token_read_func():
          # Returns whatever token_write_func last stored, as the object it was
          # given. Called once, when the client is created.
          ...

      def token_write_func(token, *args, **kwargs):
          # Stores the token. Called whenever it is refreshed. The extra
          # arguments come from the underlying OAuth session and can be
          # ignored, but they have to be accepted.
          ...

    Store and return the token object as it is given to you. It is a plain
    ``dict``, so ``json`` is enough, and inspecting or reshaping it is a good
    way to end up with a token this library cannot load.

    :param api_key: Your Schwab application's app key.
    :param app_secret: Application secret. Provided upon :ref:`app approval 
                       <approved_pending>`.
    :param token_read_func: Function that takes no arguments and returns a token
                            object.
    :param token_write_func: Function that writes the token on update. Will be
                             called whenever the token is updated, such as when
                             it is refreshed. See the above-mentioned example 
                             for what parameters this method takes.
    :param asyncio: If set to ``True``, this will enable async support allowing
                    the client to be used in an async environment. Defaults to
                    ``False``
    :param enforce_enums: Set it to ``False`` to disable the enum checks on ALL
                          the client methods. Only do it if you know you really
                          need it. For most users, it is advised to use enums
                          to avoid errors.
    '''
    api_key = __normalize_credential(api_key, 'api_key')
    app_secret = __normalize_credential(app_secret, 'app_secret')
    token = token_read_func()

    # Extract metadata and unpack the token, if necessary
    metadata = TokenMetadata.from_loaded_token(token, token_write_func)
    token = _stored_token_for_session(metadata.token)

    # Don't emit token details in debug logs. The walk recurses once per level,
    # so a token nested past the interpreter's depth -- which json can still
    # read -- is refused like any other token this library did not write.
    try:
        register_redactions(token)
    except RecursionError:
        raise ValueError(
                'The token is nested too deeply to be a token this library '
                'wrote. If it came from a token file, delete the file and '
                'create a new one.') from None

    wrapped_token_write_func = metadata.wrapped_token_write_func()
    if asyncio:
        # Not `# pragma: no cover`, which is what this said. The exclusion
        # was honest when nothing reached it, and it meant the one line that
        # actually writes the token on the async path was hidden from
        # measurement as well as untested -- so a write that quietly did
        # nothing here would have shown up as neither a failure nor a gap.
        async def oauth_client_update_token(t, *args, **kwargs):
            wrapped_token_write_func(t, *args, **kwargs)
        session_class = AsyncOAuth2Client
        client_class = AsyncClient
    else:
        oauth_client_update_token = wrapped_token_write_func
        session_class = OAuth2Client
        client_class = Client

    return client_class(
        api_key,
        _new_session(session_class, api_key, app_secret, token,
                     oauth_client_update_token),
        token_metadata=metadata,
        enforce_enums=enforce_enums)


################################################################################
# Tools for incorporating token generation into webapp workflows


AuthContext = collections.namedtuple(
        'AuthContext', ['callback_url', 'authorization_url', 'state'])
AuthContext.__doc__ = '''The half of a login that has to survive between two
requests: the ``callback_url`` the flow was started with, the
``authorization_url`` to send the user to, and the OAuth ``state`` that ties
the two together.

It is a plain :class:`collections.namedtuple` of strings so that it can be
serialised into a session, a cookie or a database row. That is deliberate --
the ``OAuth2Client`` it came from cannot be, which is why
:func:`client_from_received_url` rebuilds one rather than being handed it.'''


def get_auth_context(api_key, callback_url, state=None):
    '''
    Start a login without opening a browser, for when the callback lands
    somewhere this process cannot listen.

    :func:`client_from_login_flow` runs a callback server on ``127.0.0.1`` and
    blocks until the user finishes. That is wrong for a web application, where
    the redirect arrives as an ordinary request to a server you already have,
    in a different process from the one that started the login and possibly on
    a different machine. Use this and :func:`client_from_received_url` instead:

    1. Call this to get an :class:`AuthContext`.
    2. Send the user to ``context.authorization_url``, and store the context
       somewhere you can retrieve it in the next request.
    3. When Schwab redirects back, pass the full URL it was received at,
       together with the stored context, to :func:`client_from_received_url`.

    :param api_key: Your Schwab application's app key.
    :param app_secret: Not required here. It is needed in step 3.
    :param callback_url: Your application's callback URL, exactly as registered
                         with Schwab. Unlike :func:`client_from_login_flow`,
                         this is not restricted to ``127.0.0.1`` -- nothing
                         here listens on it, so it can be a real host you
                         control. Everything in
                         :ref:`callback_url_advisory` about who can read that
                         request still applies, and applies more, because the
                         request now crosses a network.
    :param state: An opaque value echoed back by Schwab, which
                  :func:`client_from_received_url` checks. Leave it unset to
                  have one generated. Set it only if you have your own scheme
                  for tying a redirect to the session that started it.
    '''
    api_key = __normalize_credential(api_key, 'api_key')
    oauth = OAuth2Client(api_key, redirect_uri=callback_url)
    authorization_url, state = oauth.create_authorization_url(
        'https://api.schwabapi.com/v1/oauth/authorize',
        state=state)

    return AuthContext(callback_url, authorization_url, state)


def client_from_received_url(
        api_key, app_secret, auth_context, received_url, token_write_func, 
        asyncio=False, enforce_enums=True):
    '''
    Finish a login started by :func:`get_auth_context` and return a client.

    Call this from whatever handles your callback URL, with the request URL it
    was reached at.

    Note this takes a ``token_write_func`` rather than a path: there is no
    ``token_path`` variant, because a server that just authenticated a user
    usually wants the token in the same place as the rest of that user's state
    rather than in a file. See
    :func:`client_from_access_functions` for what the function has to accept.

    :param api_key: Your Schwab application's app key.
    :param app_secret: Application secret provided upon :ref:`app approval
                       <approved_pending>`.
    :param auth_context: The :class:`AuthContext` returned by
                         :func:`get_auth_context` when this login was started.
                         Its ``state`` is checked against the one in
                         ``received_url``, so a redirect belonging to a
                         different login is rejected rather than accepted.
    :param received_url: The *full* URL your callback was requested at, query
                         string included. That query string carries the
                         authorization code, so this value is as sensitive as
                         the token it becomes: do not log it.
    :param token_write_func: Called with the token when it is created and on
                             every refresh thereafter.
    '''
    # XXX: The AuthContext must be serializable, which means the original 
    #      OAuth2Client created in get_auth_context cannot be passed around. 
    #      Instead, we reconstruct it here.
    oauth = OAuth2Client(api_key, redirect_uri=auth_context.callback_url)
    # The token a login exchanges its code for is written just below, so it
    # gets the check a refresh does. Measured: {"message": "Unauthorized"} was
    # written as the token, and every call after it failed without contacting
    # Schwab, restarts included.
    oauth.register_compliance_hook(
            'access_token_response', _refuse_unusable_token_response)

    # A redirect carrying the authorization server's refusal -- access_denied
    # when the user declines -- has no code, and neither does one that is no
    # answer to the login at all. authlib decides on the raw URL: without
    # "code=" it asks for a client_credentials grant, with the app key and
    # secret, and reports that grant's refusal instead; with "#" it takes the
    # fragment as an implicit grant's token and keeps it, with no request.
    # This login is neither, so both are refused here on authlib's own tests,
    # and so is a query that carries no code key. The URL itself is not
    # repeated: it can carry a code.
    query = urllib.parse.parse_qs(urllib.parse.urlparse(received_url).query)
    error = (query.get('error') or [''])[0]
    if error:
        raise LoginExchangeError(
                _venue_text(error),
                _venue_text((query.get('error_description') or [None])[0]))
    if '#' in received_url:
        raise LoginExchangeError(
                'invalid_request',
                'the redirect carries a fragment, which this login does not '
                'read')
    if 'code=' not in received_url or 'code' not in query:
        raise LoginExchangeError(
                'missing_code', 'the redirect carries no authorization code')

    try:
        token = oauth.fetch_token(
            TOKEN_ENDPOINT,
            authorization_response=received_url,
            client_id=api_key, auth=(api_key, app_secret),
            state=auth_context.state)
    except AuthlibBaseError as e:
        # The endpoint's refusal, and what authlib refuses before asking: a
        # state that does not match, an empty code. This library's class, and
        # an OAuthError, so code written against authlib's keeps catching it.
        # authlib's error is not chained: its text is not escaped, and a
        # logged traceback prints a cause in full.
        error, description = _refusal_text(e)
        raise LoginExchangeError(error, description, e.uri) from None

    # Don't emit token details in debug logs
    register_redactions(token)

    # Set up token writing and perform the initial token write
    metadata_manager = TokenMetadata(token, int(time.time()), token_write_func)
    token_write_func = metadata_manager.wrapped_token_write_func()
    token_write_func(token)

    # The synchronous and asynchronous versions of the OAuth2Client are similar
    # enough that can mostly be used interchangeably. The one currently known
    # exception is the token update function: the synchronous version expects a
    # synchronous one, the asynchronous requires an async one. The
    # oauth_client_update_token variable will contain the appropriate one.
    if asyncio:
        # Not excluded from coverage, for the reason the sibling in
        # client_from_access_functions gives: this is the line that writes
        # the token on the async path, and a `no cover` on it hides the one
        # thing worth measuring here.
        async def oauth_client_update_token(t, *args, **kwargs):
            token_write_func(t, *args, **kwargs)
        session_class = AsyncOAuth2Client
        client_class = AsyncClient
    else:
        oauth_client_update_token = token_write_func
        session_class = OAuth2Client
        client_class = Client

    # Return a new session configured to refresh credentials
    return client_class(
        api_key,
        _new_session(session_class, api_key, app_secret, token,
                     oauth_client_update_token),
        token_metadata=metadata_manager, enforce_enums=enforce_enums)


################################################################################
# easy_client


# TODO: Figure out how to properly mock global objects in unittest. This hack 
# ensures that the _get_ipython variable is defined so that we can patch is 
# using module-level patching. This is safe in most contexts, but there are 
# circumstances where it gets weird like starting an ipython notebook after 
# schwaby is loaded.
try:
    _get_ipython = get_ipython
except NameError:
    _get_ipython = None


def __running_in_notebook():
    # Google Colab
    if os.getenv('COLAB_RELEASE_TAG'):
        return True

    # ipython in notebook mode
    if _get_ipython is not None:
        shell = _get_ipython().__class__.__name__
        if shell == 'ZMQInteractiveShell':
            return True

    return False


def easy_client(api_key, app_secret, callback_url, token_path, asyncio=False, 
                enforce_enums=True, max_token_age=60*60*24*6.5,
                callback_timeout=300.0, interactive=True,
                requested_browser=None):
    '''
    Convenient wrapper around :func:`client_from_login_flow` and
    :func:`client_from_token_file`. If ``token_path`` exists, loads the token
    from it. Otherwise open a login flow to fetch a new token. Returns a client
    configured to refresh the token to ``token_path``.

    *Reminder:* You should never create the token file yourself or modify it in
    any way. If ``token_path`` refers to an existing file, this method will
    assume that file is valid token and will attempt to parse it.

    ``max_token_age`` **defaults to 6.5 days**, and a token older than that is
    discarded here and replaced through :func:`client_from_login_flow`. Setting
    ``max_token_age=0`` skips the proactive refresh, but Schwab's refresh token
    expires seven days after authorization regardless, so a long-running program
    still needs some way to log in again.

    In a Jupyter or Colab notebook this routes to
    :func:`client_from_manual_flow` instead, which starts no callback server.

    :param api_key: Your Schwab application's app key.
    :param app_secret: Application secret provided upon :ref:`app approval 
                       <approved_pending>`.
    :param callback_url: Your Schwab application's callback URL. Note this must
                         *exactly* match the value you've entered in your
                         application configuration, otherwise login will fail
                         with a security error. Be sure to check case and 
                         trailing slashes. :ref:`See the above note for
                         important information about setting your callback URL.
                         <callback_url_advisory>`
    :param token_path: Path to which the new token will be written. If the token
                       file already exists, it will be overwritten with a new
                       one. Updated tokens will be written to this path as well.
    :param asyncio: If set to ``True``, this will enable async support allowing
                    the client to be used in an async environment. Defaults to
                    ``False``
    :param enforce_enums: Set it to ``False`` to disable the enum checks on ALL
                          the client methods. Only do it if you know you really
                          need it. For most users, it is advised to use enums
                          to avoid errors.
    :param max_token_age: If the token is loaded from a file but is older than 
                          this age (in seconds), proactively delete it and 
                          create a new one. Assists with 
                          :ref:`token expiration <token_expiration>`. If set to 
                          None, never proactively delete the token.
    :param callback_timeout: See the corresponding parameter to 
                             :func:`client_from_login_flow 
                             <client_from_login_flow>`.
    :param interactive: See the corresponding parameter to 
                        :func:`client_from_login_flow 
                        <client_from_login_flow>`.
    :param requested_browser: See the corresponding parameter to 
                              :func:`client_from_login_flow 
                              <client_from_login_flow>`.
    '''
    api_key = __normalize_credential(api_key, 'api_key')
    app_secret = __normalize_credential(app_secret, 'app_secret')
    if max_token_age is None:
        max_token_age = 0
    if max_token_age < 0:
        raise ValueError('max_token_age must be positive, zero, or None')

    logger = get_logger()

    c = None

    if os.path.isfile(token_path):
        c = client_from_token_file(token_path, api_key, app_secret,
                                   asyncio=asyncio,
                                   enforce_enums=enforce_enums)
        logger.info('Loaded token from file \'%s\'', token_path)

        if max_token_age > 0 and c.token_age() >= max_token_age:
            logger.info('token too old, proactively creating a new one')
            c = None

    # Return early on success
    if c is not None:
        return c

    # Detect whether we're running in a notebook
    if __running_in_notebook():
        c = client_from_manual_flow(api_key, app_secret, callback_url,
                                    token_path, asyncio=asyncio,
                                    enforce_enums=enforce_enums)
        logger.info(
            'Returning client fetched using manual flow, writing' +
            'token to \'%s\'', token_path)
    else:
        c = client_from_login_flow(
            api_key, app_secret, callback_url, token_path, asyncio=asyncio,
            enforce_enums=enforce_enums, callback_timeout=callback_timeout,
            requested_browser=requested_browser, interactive=interactive)

        logger.info(
            'Returning client fetched using web browser, writing' +
            'token to \'%s\'', token_path)

    return c
