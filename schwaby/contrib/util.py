import decimal
import json
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

    Four things this gets right that a first attempt usually does not, each of
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
    * **A** ``Decimal`` **is returned, never a float.** These are money;
      :meth:`OrderBuilder.set_price
      <schwaby.orders.generic.OrderBuilder.set_price>` has refused floats
      since 2.1.0 for the reason :ref:`price_strings` gives, so a decoded value
      can be fed straight back into a reprice.

    :param value: A decimal object, or a number or string, which is returned
                  as a :class:`~decimal.Decimal` unchanged --- the same field
                  does not always arrive in the same shape.
    :raises UnusableDecimalScale: if the object carries a mantissa and no
                                  ``signScale``, or a ``signScale`` too large
                                  to be a real one. Neither observed; refused
                                  rather than computed, because both produce a
                                  plausible wrong number rather than an error.
    :raises UnusableDecimalScale: if a non-object value is not a number ---
                                  the empty string reaches this function from
                                  the ``SUBSCRIBED`` ack.

    Every failure is a :class:`~schwaby.utils.SchwabError` and a
    :class:`ValueError`, so a caller wrapping each field in one ``except``
    keeps the rest of the message. That is the point of the bound above: a
    hang is not catchable, so it must not be reachable.
    '''
    if value is None:
        return None

    if not isinstance(value, dict):
        # The same field has been seen arriving as a bare number, and as a
        # string. Decimal(str(...)) rather than Decimal(float) so a float does
        # not bring its binary expansion along.
        try:
            result = decimal.Decimal(str(value))
            if not result.is_finite():
                # `json.loads` accepts bare NaN and Infinity, and Decimal
                # accepts both those and the strings. A NaN quantity is not
                # caught downstream either: `leaves <= 0` is False for NaN, so
                # a corrupt field reads as still outstanding forever -- the
                # mirror of the zero-is-a-complete-fill hazard the bound below
                # exists for.
                raise decimal.InvalidOperation()
            return result
        except decimal.InvalidOperation:
            # `decimal.InvalidOperation` is an ArithmeticError, so it is
            # neither a SchwabError nor a ValueError and escapes both of the
            # handlers a caller would reasonably write. The empty string
            # reaches here from the SUBSCRIBED ack.
            raise UnusableDecimalScale(
                    'not a number: {!r}'.format(value)) from None

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

    if value.get('signScale') is None:
        raise UnusableDecimalScale(
                'decimal object has a mantissa and no signScale, so its scale '
                'is unknown: {!r}'.format(value))

    # `.get` on all three, not `value['lo']`. The guard above accepts an
    # object with only `mid` set, so requiring `lo` here would KeyError on
    # exactly the shape that guard was widened to allow.
    #
    # `is None` rather than `or 0`, to match that guard exactly: a
    # present-but-falsy member -- `""`, `{}`, `false` -- passes the guard as
    # "has a mantissa" and `or 0` would then decode it as zero, which this
    # file argues at length is the worst answer available on a fill feed.
    #
    # And `int()` wrapped, because it raises a bare ValueError on a
    # non-numeric string and a TypeError on a non-scalar -- neither a
    # SchwabError, and the TypeError not even a ValueError, so both escape the
    # handlers the docstring tells a caller to write.
    try:
        mantissa = 0
        for name, shift in (('lo', 0), ('mid', 32), ('hi', 64)):
            member = value.get(name)
            if isinstance(member, bool):
                # `int(False)` is 0, so a JSON `false` in a mantissa member
                # would decode as zero rather than as the corruption it is --
                # the same reason `bool` is refused by the order setters.
                raise ValueError(name)
            if member is not None:
                mantissa += int(member) << shift
        scale = int(value['signScale'])
    except (TypeError, ValueError):
        raise UnusableDecimalScale(
                'decimal object has a member that is not an integer: '
                '{!r}'.format(value)) from None
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
