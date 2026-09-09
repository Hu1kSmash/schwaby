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


class UnknownDecimalScale(SchwabError, ValueError):
    '''Raised by :func:`decode_decimal` for an object with a mantissa and no
    ``signScale``.

    The scale is what turns the mantissa into a number, so guessing one is a
    silent wrong answer by construction --- and the guess that suggests itself,
    six decimal places, is only the value that happens to be common.

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
    :raises UnknownDecimalScale: if the object carries a mantissa but no
                                 ``signScale``. Never observed; refused rather
                                 than guessed, because a guessed scale is
                                 wrong by a factor of a million and says
                                 nothing.
    '''
    if value is None:
        return None

    if not isinstance(value, dict):
        # The same field has been seen arriving as a bare number, and as a
        # string. Decimal(str(...)) rather than Decimal(float) so a float does
        # not bring its binary expansion along.
        return decimal.Decimal(str(value))

    if value.get('lo') is None:
        # Measured on three fields of one payload: a $0 commission, a market
        # order's absent limit price, and LeavesQuantity on a final fill.
        return decimal.Decimal(0)

    if value.get('signScale') is None:
        raise UnknownDecimalScale(
                'decimal object has a mantissa and no signScale, so its scale '
                'is unknown: {!r}'.format(value))

    mantissa = (int(value['lo'])
                + (int(value.get('mid') or 0) << 32)
                + (int(value.get('hi') or 0) << 64))
    scale = int(value['signScale'])

    # scaleb, not `10 ** (scale // 2)`. The exponent comes from an untrusted
    # field, and the power builds an astronomical integer on a hostile value
    # and hangs the thread -- which a per-item try/except cannot rescue, so one
    # bad field would take a stream down rather than one message. scaleb
    # raises InvalidOperation immediately instead.
    result = decimal.Decimal(mantissa).scaleb(-(scale // 2))
    return -result if scale % 2 else result
