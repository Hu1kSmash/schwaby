import decimal
import json
import logging
import re
from schwaby.streaming import StreamJsonDecoder
from schwaby.utils import SchwabError


def get_logger():
    return logging.getLogger(__name__)


class HeuristicJsonDecoder(StreamJsonDecoder):
    def decode_json_string(self, raw):
        '''
        Attempts the following, in order:
        
        1. Return the JSON decoding of the raw string. 
        2. Replace all instances of ``\\\\\\\\`` with ``\\\\`` and return the 
           decoding.

        Note alternative (and potentially expensive) transformations are only 
        performed when ``JSONDecodeError`` exceptions are raised by earlier 
        stages.
        '''

        # Note "no cover" pragmas are added pending addition of real-world test 
        # cases which trigger this issue.

        try:
            return json.loads(raw)
        except json.decoder.JSONDecodeError:  # pragma: no cover
            raw = raw.replace('\\\\', '\\')

        return json.loads(raw)  # pragma: no cover


#: The largest ``signScale`` :func:`decode_decimal` will compute with. A .NET
#: ``Decimal`` scale is 0 to 28, which is a ``signScale`` of at most 57.
MAX_SIGN_SCALE = 64

#: The largest exponent a bare value may carry, in either direction. The
#: dict path is bounded by :data:`MAX_SIGN_SCALE` for a measured reason; a
#: bare value needs the same bound for the same reason. ``1E-999999999``
#: parses, and then compares neither ``== 0`` nor ``<= 0`` while behaving as
#: zero in arithmetic -- so a corrupt ``LeavesQuantity`` reads as still
#: outstanding forever. ``1E999999999`` parses and makes the *caller\'s* next
#: arithmetic raise `decimal.Overflow`, which is an `ArithmeticError` and so
#: escapes the one ``except`` this module tells callers to write. No real
#: field on this feed is within thirty orders of magnitude of either.
MAX_EXPONENT = 64

#: What a bare numeric value may look like. `decimal.Decimal` accepts rather
#: more than this -- PEP 515 underscores anywhere (``1_0`` is ten, ``12_`` is
#: twelve where `int` refuses it outright), surrounding whitespace, a leading
#: ``+``, and every Unicode decimal digit, so ``'\u0663'`` is three and
#: ``'\uff11\uff12'`` is twelve. Each of those is a corrupt field decoding as
#: a confident wrong number, which is the same defect the mantissa members
#: are validated against; the two paths refuse the same tokens now.
_BARE_NUMBER = re.compile(
        r'-?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][-+]?[0-9]+)?\Z')

#: Every key a serialized ``System.Decimal`` is known to carry. An object with
#: none of them is not one, and would otherwise take the mantissa-less
#: shortcut and decode as zero -- the worst wrong answer available on this
#: feed -- without anything having looked at it.
#:
#: A key *alongside* these is a different thing entirely, and is ignored. See
#: :func:`_report_unknown_keys` for why that direction rather than refusing.
_DECIMAL_KEYS = frozenset(('lo', 'mid', 'hi', 'signScale'))

#: The mantissa side of that. Named because an absent mantissa and an absent
#: scale are different questions once an unrecognised key is in play.
_MANTISSA_KEYS = frozenset(('lo', 'mid', 'hi'))

#: How many distinct unknown keys to name in the log before giving up. A cap
#: rather than an unbounded set, because the thing being counted is attacker-
#: or corruption-controlled and this set never shrinks.
_MAX_REPORTED_KEYS = 32

_reported_keys = set()


def _report_unknown_keys(unknown):
    """Say once, per distinct key, that Schwab sent something new.

    The alternative was refusing the object, which this function did for one
    release. That is the loud direction and it is the wrong one *here*: a key
    Schwab adds appears on every decimal object at once, so refusing turns a
    schema addition into every money and quantity field on the feed raising
    together -- a dead feed, from a change that costs nothing to ignore. The
    encoding is positional in `lo`/`mid`/`hi`/`signScale`; a fifth key does
    not move the other four.

    What is *not* ignored is an object carrying none of the four -- not a
    decimal object at all -- or one whose *missing* component the unknown key
    could be, since guessing there is a wrong number in both directions.

    One gap remains and it is inherent rather than chosen. `lo`, `mid` and
    `hi` are omitted individually when zero, so the absence of one carries no
    information, and a renamed *member* beside a surviving member cannot be
    told from an ordinary omission:

        {"lo": "705032704", "Mid": 1, "signScale": 12}   ->  705.032704

    which is the truncation this decoder exists to prevent, seven times low.
    Nothing here can detect that; the unknown key is logged on the first
    message carrying it, and that is the whole of the warning available.

    Reported once per distinct key rather than once per message: this fires
    on a live feed, where a per-message line is a flood and a flood is its own
    way of hiding the message.

    Once per *process*, not per connection: the set is module-level and is not
    cleared by `login` or `close`, which do clear the absorbed counters. That
    is deliberate --- a key Schwab added is a fact about the venue, not about
    one socket --- and it means two clients in one process share the
    suppression.
    """
    room = _MAX_REPORTED_KEYS - len(_reported_keys)
    if room <= 0:
        return
    # Normalised to `str` on the way in so a non-string key cannot slip the
    # dedup by being unequal to its own spelling, and truncated to the room
    # left rather than after the fact: the cap is justified by input nobody
    # here controls, and `update` before the check let one object carrying
    # 500 unknown keys put all 500 in a set bounded at 32.
    fresh = sorted({_safe_key(k) for k in unknown} - _reported_keys)[:room]
    if not fresh:
        return
    _reported_keys.update(fresh)
    if len(_reported_keys) >= _MAX_REPORTED_KEYS:
        get_logger().warning(
                'That is %d distinct unknown keys, which is as many as '
                'schwaby will name. Any further ones decode the same way and '
                'are not reported.', _MAX_REPORTED_KEYS)
    get_logger().warning(
            'Schwab sent %s inside a decimal object, which this version of '
            'schwaby does not know about. The value still decodes -- the '
            'encoding is positional and an added key does not move the '
            'others -- and this is reported once per key, not per message. '
            'If you see this, please open an issue at '
            'https://github.com/Hu1kSmash/schwaby/issues so the field can be '
            'documented: it is undocumented publicly and a capture is the '
            'only way anyone learns what it means.',
            ', '.join(repr(k) for k in fresh))


class UnusableDecimalScale(SchwabError, ValueError):
    '''Raised by :func:`decode_decimal` when the ``signScale`` cannot be used:
    absent on an object that has a mantissa, or too large to be real.

    The scale is what turns the mantissa into a number, so guessing one is a
    silent wrong answer by construction --- and the guess that suggests itself,
    six decimal places, is only the value that happens to be common.

    A real .NET ``Decimal`` scale is 0 to 28, so ``signScale`` does not exceed
    about 57. Anything larger is refused rather than computed: past roughly two
    million the value underflows to a zero that compares equal to zero, and a
    corrupt ``LeavesQuantity`` reading as zero is a complete fill.

    A :class:`~schwaby.utils.SchwabError` so ``except SchwabError`` covers it
    like everything else this library defines, and a :class:`ValueError` so it
    also reads as what it is.
    '''


def _safe_key(key):
    """A key's name, bounded and guaranteed not to raise.

    `str()` of a wide integer raises past `sys.get_int_max_str_digits()`, and
    a key is whatever the decoder produced. Bounded in length as well as in
    count: `_reported_keys` never shrinks, so a 2 MB key would be retained
    forever and named in full in a 2 MB log line.
    """
    try:
        text = str(key)
    except Exception:
        return '<{} that cannot be named>'.format(type(key).__name__)
    return text if len(text) <= 64 else text[:61] + '...'


def _safe_repr(value):
    """`repr` that cannot raise on the value it is describing.

    An error message is not the place to hit the integer digit limit:
    `repr` of a dict containing a 5,000-digit int raises `ValueError`, which
    would replace a `SchwabError` describing corruption with a bare exception
    describing nothing. Measured -- `decimal.Decimal(10 ** 10000)` is fine and
    `str()` of that integer is not, so the hazard is the formatting rather
    than the arithmetic.
    """
    try:
        text = repr(value)
    except ValueError:
        return '<{} that cannot be formatted>'.format(type(value).__name__)
    return text if len(text) <= 200 else text[:197] + '...'


def _decode_bare(value):
    """The other half of the surface, validated the same way as a member.

    This path is defensive: no captured payload has carried a money or
    quantity field as a bare number or string, and the docstring above says
    so rather than implying otherwise. It carries the same untrusted text as
    the mantissa members either way, and for three review rounds it was the
    permissive one.
    `decimal.Decimal`'s parser accepts more than `int`'s does, so every token
    `_unsigned_32` refuses decoded here instead: ``'1_0'`` as ten,
    ``'\u0663'`` as three, ``' 12 '`` and ``'+12'`` and ``'\uff11\uff12'``
    as twelve. All of them are `json.loads`-reachable and none of them raised.
    """
    if isinstance(value, bool) or value is None:
        # `None` is handled by the caller; a bool is not a number here for the
        # same reason it is not one in a mantissa.
        raise UnusableDecimalScale(
                'not a number: {}'.format(_safe_repr(value)))

    if isinstance(value, decimal.Decimal):
        result = value
    elif isinstance(value, int):
        # Bounded before conversion, cheaply. `decimal.Decimal(int)` is
        # fine at any width -- measured, and the comment here said otherwise
        # for one commit; it is `str()` and `repr()` that raise a bare
        # `ValueError` past `sys.get_int_max_str_digits()`. `_safe_repr`
        # handles that where it matters, so this is a cheap pre-check rather
        # than the thing standing between a caller and an escape. 256 bits is
        # 78 digits, well outside the bound below, so it refuses only what
        # that would refuse anyway.
        if value.bit_length() > 256:
            # Reported by width rather than by value: formatting the value
            # is the operation being guarded against.
            raise UnusableDecimalScale(
                    'a {}-bit integer is outside +/-1E{}, so it is not a '
                    'real value'.format(value.bit_length(), MAX_EXPONENT))
        result = decimal.Decimal(value)
    elif isinstance(value, float):
        # `str` of a float is short, ASCII and never surprising; going
        # through it stops the binary expansion coming along.
        result = decimal.Decimal(str(value))
    elif isinstance(value, str):
        if not (value.isascii() and _BARE_NUMBER.match(value)):
            # The empty string reaches here from the SUBSCRIBED ack.
            raise UnusableDecimalScale(
                'not a number: {}'.format(_safe_repr(value)))
        result = decimal.Decimal(value)
    else:
        raise UnusableDecimalScale(
                'not a number: {}'.format(_safe_repr(value)))

    if not result.is_finite():
        # `json.loads` accepts bare NaN and Infinity, and Decimal accepts
        # those and their strings. A NaN quantity is not caught downstream
        # either: `leaves <= 0` is False for NaN, so a corrupt field reads as
        # still outstanding forever.
        raise UnusableDecimalScale(
                'not a number: {}'.format(_safe_repr(value)))

    exponent = result.as_tuple().exponent
    if not (-MAX_EXPONENT <= exponent
            and result.adjusted() <= MAX_EXPONENT):
        raise UnusableDecimalScale(
                'magnitude is outside 1E+/-{}, so this is not a real '
                'value: {}'.format(MAX_EXPONENT, _safe_repr(value)))

    return result


def _unsigned_32(member, name, value):
    """One place that decides what a decimal member is.

    An `int` that is not a `bool`, or a string of ASCII digits, and in either
    case inside an unsigned 32-bit range. `bool` is excluded because `int(True)`
    is 1 and `int(False)` is 0, so a JSON `true` in a mantissa would decode as a
    number rather than as the corruption it is -- the same reason the order
    setters refuse one.
    """
    if isinstance(member, bool) or not isinstance(member, (int, str)):
        ok = False
    elif isinstance(member, str):
        # `isdigit` alone is not ASCII: '\u00b2'.isdigit() is True and
        # `int` refuses it, while '\u0663'.isdigit() is True and `int`
        # decodes it as 3. Both are corruption, and only one raises.
        ok = member.isascii() and member.isdigit()
        if ok:
            # Length before value, because `int()` refuses a string past
            # `sys.get_int_max_str_digits()` -- 4300 by default -- with a
            # bare `ValueError` that is not a SchwabError, and the range
            # check below cannot run until `int()` has returned. Leading
            # zeros are stripped rather than counted so a padded member is
            # judged on its value: the widest member is 4294967295, ten
            # digits.
            digits = member.lstrip('0') or '0'   # an all-zero member
            ok = len(digits) <= 10
            member = int(digits) if ok else 0
        else:
            member = 0
    else:
        ok = True
    if not ok or not 0 <= member < 2 ** 32:
        raise UnusableDecimalScale(
                '{} is not an unsigned 32-bit integer: {}'.format(
                    name, _safe_repr(value)))
    return member


def decode_decimal(value):
    '''Decodes the scaled-integer decimal objects Schwab streams on
    ``ACCT_ACTIVITY``.

    Those fields do not arrive as numbers. They arrive as a serialized .NET
    ``System.Decimal``::

        {"lo": "6860000", "signScale": 12}       ->  Decimal('6.860000')

    a 96-bit integer mantissa split across ``lo``, ``mid`` and ``hi``, and a
    ``signScale`` packing the scale in its magnitude and the sign in its
    parity. The conversion is undocumented publicly; Schwab's Trader API
    support confirmed it in writing, and it reproduces every payload anyone
    here has captured, including Schwab's own worked example
    ``{"lo": "40000000", "signScale": 13}`` -> ``Decimal('-40.000000')``.

    Five things this gets right that a first attempt usually does not, each of
    which is a wrong number rather than an error:

    * **The mantissa spans three fields.** Reading ``lo`` alone truncates
      anything over ``4294.967295`` at ``signScale`` 12 --- a principal, a
      total, or a share price reaches that easily.
    * **An odd** ``signScale`` **means negative.** The sign is not the side:
      direction is ``BuySellCode``, and this branch exists so a genuinely
      negative field, such as principal on a buy, does not decode positive.
    * **A mantissa-less object is zero, not unknown.** ``LeavesQuantity``
      arrives that way on the final fill of a completed order, and reading it
      as unknown reports a complete fill as still outstanding.
    * **An absent** ``signScale`` **is scale 0, not a missing scale.** The
      serializer omits whatever is zero, and that applies to the scale as
      much as to a mantissa member: ``AskSize`` and ``BidSize`` arrive as
      ``{"lo": "19200"}`` beside an ``Ask`` of
      ``{"lo": "13720000", "signScale": 12}`` on the same quote. Refusing
      the shape --- which this function did until it was checked against a
      capture --- costs both sizes on every quote it appears in.
    * **A** ``Decimal`` **is returned, never a float.** These are money;
      :meth:`OrderBuilder.set_price
      <schwaby.orders.generic.OrderBuilder.set_price>` has refused floats
      since 2.1.0 for the reason :ref:`price_strings` gives, so a decoded value
      can be fed straight back into a reprice.

    :param value: A decimal object, or a number or string. A non-object is
                  validated and returned as a :class:`~decimal.Decimal`. That
                  path is defensive rather than observed: every money and
                  quantity field in the one capture on hand is an object, on
                  every message. What *is* measured is the empty string,
                  which arrives as the ``SUBSCRIBED`` ack's ``MESSAGE_DATA``
                  and reaches here from any consumer decoding fields
                  generically.
    :raises UnusableDecimalScale: if any member is not an unsigned 32-bit
                                  integer, if the ``signScale`` is outside
                                  ``0..64``, or if the object carries *none*
                                  of ``lo``, ``mid``, ``hi`` and
                                  ``signScale``, which means it is not one of
                                  these at all. Refused rather than computed
                                  with, because each of those produces a
                                  plausible wrong number rather than an
                                  error.

                                  A key **alongside** those four is a schema
                                  addition rather than corruption, and on an
                                  object carrying both a mantissa and a
                                  ``signScale`` it is ignored and logged
                                  once: a key Schwab adds arrives on every
                                  decimal object at once, and refusing it
                                  would turn a harmless addition into every
                                  money and quantity field failing together.

                                  It is **refused** where a component is
                                  missing, because the unknown key may be
                                  that component renamed and guessing is a
                                  wrong number either way. So a payload that
                                  legitimately omits one --- an ``AskSize``
                                  with no scale, a $0 commission with no
                                  mantissa --- raises while an added key is
                                  still unknown.
    :raises UnusableDecimalScale: if a non-object value is not a number, or
                                  carries an exponent past 64 in either
                                  direction. The empty string reaches this
                                  function from the ``SUBSCRIBED`` ack.

    Every failure is a :class:`~schwaby.utils.SchwabError` and a
    :class:`ValueError`, so a caller wrapping each field in one ``except``
    keeps the rest of the message. That is the point of the bound above: a
    hang is not catchable, so it must not be reachable.
    '''
    if value is None:
        return None

    if not isinstance(value, dict):
        return _decode_bare(value)

    # `set(value)`, not `value.keys() - ...`: a mapping whose `keys()` does
    # not return a set view makes the difference operator raise a bare
    # TypeError, and this module promises every failure is a SchwabError.
    keys = set(value)
    unknown = keys - _DECIMAL_KEYS
    if unknown:
        # *Present* means carrying a value, which is the same rule the
        # mantissa loop and the scale below both apply -- they treat an
        # explicit JSON null as an absence, deliberately and with a test.
        # Asking a different question here was the two-walks-disagree shape
        # inside one function: keyed on mere presence,
        # `{"lo": "6860000", "signScale": null, "SignScale": 12}` slipped the
        # guard, then met the absent-scale rule, and decoded $6.86 as
        # $6,860,000 again -- the exact defect this refusal exists to close.
        present = {k for k in keys & _DECIMAL_KEYS
                   if value.get(k) is not None}
        if keys & _DECIMAL_KEYS and (
                not present & _MANTISSA_KEYS or 'signScale' not in present):
            # An unknown key beside a *missing* component is ambiguous: the
            # unknown key may be that component, renamed. Both directions are
            # a silent wrong number if guessed at, and they are not
            # symmetric in cost:
            #
            #   {"lo": "6860000", "SignScale": 12}
            #
            # has no `signScale`, so the absent-scale rule would read it at
            # scale 0 and turn a $6.86 limit price into $6,860,000. The
            # mirror, a renamed mantissa beside a real scale, reads as zero.
            # Refusing is the only answer that is not a confident wrong
            # number, and it catches a rename on the first message.
            #
            # This costs something and the cost is the right way round: if
            # Schwab *adds* a key, objects that legitimately omit a component
            # -- an AskSize with no scale, a $0 commission with no mantissa --
            # raise until the key is known, while every complete object still
            # decodes and names the new key in the log immediately.
            raise UnusableDecimalScale(
                    'decimal object is missing its {} and carries {}, which '
                    'may be that component under a new name: {}'.format(
                        'mantissa' if not present & _MANTISSA_KEYS
                        else 'signScale',
                        ', '.join(sorted(map(_safe_key, unknown))),
                        _safe_repr(value)))
        if not keys & _DECIMAL_KEYS:
            # None of the four. Not a decimal object at all -- a PascalCase
            # `{"Lo": ..., "SignScale": ...}`, or an unrelated object
            # entirely -- and it would otherwise fall straight through the
            # mantissa-less shortcut below and decode as a genuine zero,
            # which is what a $0 commission and a completed fill look like.
            raise UnusableDecimalScale(
                    'not a decimal object -- no lo, mid, hi or signScale, '
                    'only {}: {}'.format(
                        ', '.join(sorted(map(_safe_key, unknown))),
                        _safe_repr(value)))
        # A key *alongside* the four is a schema addition, not corruption.
        # Ignored, and said out loud once, for the reasons on
        # `_report_unknown_keys`. This direction was chosen deliberately:
        # refusing made a field Schwab adds take the whole feed down.
        _report_unknown_keys(unknown)

    # The scale is read, validated and bounded *before* the mantissa-less
    # shortcut, not after. That shortcut returns without looking at anything
    # else, so `{"signScale": "garbage"}` -- or 1.9, or -1, or 65 -- decoded
    # as a clean zero while the identical corruption one key over raised. No
    # wrong number came of it, since a mantissa-less object is zero at any
    # scale, but it discarded the only signal that the message was corrupt.
    #
    # An absent `signScale` is scale 0, by the same rule as an absent
    # mantissa member: the serializer omits what is zero. This was a raise
    # until it was checked against a capture, on the reasoning that a missing
    # scale could not be told from a lost one -- but `AskSize` and `BidSize`
    # arrive as `{"lo": "19200"}` with no scale at all, beside an `Ask` of
    # `{"lo": "13720000", "signScale": 12}` on the same quote. $13.72 and
    # 19200 shares, which is coherent; 0.0192 shares is not. So the shape is
    # ordinary traffic, and refusing it cost both sizes on every quote.
    #
    # `.get`, not `value['signScale']`, for the same reason the members use
    # it -- and because a dict subclass whose `get` and `__getitem__`
    # disagree would otherwise escape as a `KeyError`. An explicit JSON
    # `null` is an absence here, exactly as it is in the member loop: the two
    # follow one rule and briefly did not.
    scale_member = value.get('signScale')
    scale = _unsigned_32(0 if scale_member is None else scale_member,
                         'signScale', value)

    # Bounded explicitly rather than left to the arithmetic to refuse.
    # `10 ** (scale // 2)` on a hostile exponent builds an astronomical integer
    # and hangs the thread, which a per-item try/except cannot rescue -- but
    # `scaleb` is not the answer on its own either: its operand limit only
    # trips far out, and every scale between roughly two million and four
    # million underflows to a zero that compares equal to zero instead of
    # raising. Zero is the worst wrong answer available on this feed.
    if not 0 <= scale <= MAX_SIGN_SCALE:
        raise UnusableDecimalScale(
                'signScale {} is outside 0..{}, so it is not a real one: '
                '{}'.format(scale, MAX_SIGN_SCALE, _safe_repr(value)))

    if all(value.get(k) is None for k in ('lo', 'mid', 'hi')):
        # Measured on three fields of one payload: a $0 commission, a market
        # order's absent limit price, and LeavesQuantity on a final fill.
        #
        # All three parts, not `lo` alone: the serializer omits zero members,
        # so a value whose low 32 bits happen to be zero arrives as
        # `{"mid": 1, ...}` and keying on `lo` would decode it as zero -- the
        # same slice-reading defect this function exists to fix, in the guard
        # that precedes it.
        #
        # Returning `Decimal(0)` rather than falling through is normalisation
        # and only that, now the scale above is validated either way: falling
        # through gives `Decimal('0.000000')` at scale 12, and
        # `Decimal('-0.000000')` at scale 13. Both compare equal to zero, and
        # the second one *prints* as a negative quantity.
        return decimal.Decimal(0)

    # Validated by shape rather than coerced and caught. Three rounds of
    # review found the same class here: `int()` raises a bare `ValueError` on
    # a non-numeric string, a `TypeError` on a non-scalar and an
    # `OverflowError` on an infinite float -- and where it does *not* raise it
    # is worse, because `int(1.9)` is 1 and `int('1_0')` is 10, so corruption
    # becomes a confident wrong number. Each fix enumerated one more exception
    # type and the next round found another shape.
    #
    # So this asks what a member *is* instead. A .NET Decimal carries three
    # unsigned 32-bit mantissa members and a scale; anything that is not one
    # of those is refused, once, here.
    mantissa = 0
    for name, shift in (('lo', 0), ('mid', 32), ('hi', 64)):
        member = value.get(name)
        if member is None:
            continue
        mantissa += _unsigned_32(member, name, value) << shift

    # Built from a string, sign included, because every *operation* on a
    # Decimal applies the caller's context and only construction does not.
    # A consumer who sets `decimal.getcontext().prec = 6` somewhere else in
    # their process -- an ordinary thing to do when formatting money -- would
    # otherwise get 1234.57 for a price of 1234.5678, silently, from a
    # function whose whole purpose is that its result can be fed back into
    # `set_price`.
    #
    # The sign is part of that. `-result` is `Decimal.__neg__`, which is a
    # context-aware operation and rounds -- so an earlier version of this was
    # exact for positive values and rounded negative ones, which is the branch
    # `EstimatedPrincipalAmount` arrives on.
    return decimal.Decimal('{}{}E-{}'.format(
            '-' if scale % 2 else '', mantissa, scale // 2))
