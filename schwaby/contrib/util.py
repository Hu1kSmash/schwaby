import decimal
import json
import re
from schwaby.streaming import StreamJsonDecoder
from schwaby.utils import SchwabError


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

#: Every key a serialized ``System.Decimal`` can carry. An object with none
#: of them is not one, and would otherwise take the mantissa-less shortcut
#: and decode as zero -- the worst wrong answer available on this feed --
#: without anything having looked at it.
_DECIMAL_KEYS = frozenset(('lo', 'mid', 'hi', 'signScale'))


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


def _decode_bare(value):
    """The other half of the surface, validated the same way as a member.

    The same field does not always arrive in the same shape -- a value that
    is an object on one message is a bare number or a bare string on the
    next -- so this path carries exactly the same untrusted text as the
    mantissa members, and for three review rounds it was the permissive one.
    `decimal.Decimal`'s parser accepts more than `int`'s does, so every token
    `_unsigned_32` refuses decoded here instead: ``'1_0'`` as ten,
    ``'\u0663'`` as three, ``' 12 '`` and ``'+12'`` and ``'\uff11\uff12'``
    as twelve. All of them are `json.loads`-reachable and none of them raised.
    """
    if isinstance(value, bool) or value is None:
        # `None` is handled by the caller; a bool is not a number here for the
        # same reason it is not one in a mantissa.
        raise UnusableDecimalScale('not a number: {!r}'.format(value))

    if isinstance(value, decimal.Decimal):
        result = value
    elif isinstance(value, int):
        # Bounded before conversion. `str()` on a wide integer raises a bare
        # `ValueError` past `sys.get_int_max_str_digits()`, and so does
        # `decimal.Decimal(int)` itself -- measured, it is not only the
        # `str()` that was here before. Neither is a SchwabError. 256 bits is
        # 78 digits, comfortably past the bound below, so this refuses only
        # what that would refuse anyway and never reaches the conversion.
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
            raise UnusableDecimalScale('not a number: {!r}'.format(value))
        result = decimal.Decimal(value)
    else:
        raise UnusableDecimalScale('not a number: {!r}'.format(value))

    if not result.is_finite():
        # `json.loads` accepts bare NaN and Infinity, and Decimal accepts
        # those and their strings. A NaN quantity is not caught downstream
        # either: `leaves <= 0` is False for NaN, so a corrupt field reads as
        # still outstanding forever.
        raise UnusableDecimalScale('not a number: {!r}'.format(value))

    exponent = result.as_tuple().exponent
    if not (-MAX_EXPONENT <= exponent
            and result.adjusted() <= MAX_EXPONENT):
        raise UnusableDecimalScale(
                'exponent is outside +/-{}, so this is not a real value: '
                '{!r}'.format(MAX_EXPONENT, value))

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
                '{} is not an unsigned 32-bit integer: {!r}'.format(
                    name, value))
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

    :param value: A decimal object, or a number or string, which is returned
                  as a :class:`~decimal.Decimal` unchanged --- the same field
                  does not always arrive in the same shape.
    :raises UnusableDecimalScale: if any member is not an unsigned 32-bit
                                  integer, if the ``signScale`` is outside
                                  ``0..64``, or if the object carries a key
                                  other than ``lo``, ``mid``, ``hi`` and
                                  ``signScale``. Refused rather than computed
                                  with, because each of those produces a
                                  plausible wrong number rather than an
                                  error.
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

    if not _DECIMAL_KEYS.issuperset(value):
        # An object carrying none of the four keys would otherwise fall
        # straight through the mantissa-less shortcut below and decode as
        # zero without anything having looked at it -- so a PascalCase
        # `{"Lo": ..., "SignScale": ...}`, a `{"low": ...}` typo, or an
        # entirely unrelated object all read as a genuine zero. Zero is the
        # worst wrong answer available on this feed: it is what a $0
        # commission and a completed fill look like.
        #
        # Refusing an *unknown* key rather than requiring a known one is the
        # loud direction. If Schwab ever changes this serialization, every
        # field raises at once and says so, instead of a feed quietly
        # reporting zeros.
        raise UnusableDecimalScale(
                'not a decimal object -- unexpected {}: {!r}'.format(
                    ', '.join(sorted(set(value) - _DECIMAL_KEYS)), value))

    if all(value.get(k) is None for k in ('lo', 'mid', 'hi')):
        # Measured on three fields of one payload: a $0 commission, a market
        # order's absent limit price, and LeavesQuantity on a final fill.
        #
        # All three parts, not `lo` alone: the serializer omits zero members,
        # so a value whose low 32 bits happen to be zero arrives as
        # `{"mid": 1, ...}` and keying on `lo` would decode it as zero -- the
        # same slice-reading defect this function exists to fix, in the guard
        # that precedes it.
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
    # disagree would otherwise escape as a `KeyError`.
    scale = _unsigned_32(value.get('signScale', 0), 'signScale', value)

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
                '{!r}'.format(scale, MAX_SIGN_SCALE, value))

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
