import decimal
import unittest

from schwaby.contrib.util import (
    MAX_SIGN_SCALE, decode_decimal, HeuristicJsonDecoder,
    UnusableDecimalScale)
from schwaby.utils import SchwabError
from ..utils import no_duplicates


class HeuristicJsonDecoderTest(unittest.TestCase):
    def test_raw_string_decodes(self):
        self.assertEqual(HeuristicJsonDecoder().decode_json_string(
            r'{"\\\\\\\\": "test"}'),
            {r'\\\\': 'test'})


    def test_replace_backslashes(self):
        # TODO: Actually collect some failing use cases...
        pass


class DecodeDecimalTest(unittest.TestCase):
    """The ACCT_ACTIVITY scaled-integer decimal.

    Every case here is a value someone captured off a live account, except the
    two marked otherwise. The four things this gets right are each a wrong
    number rather than an error, which is why they are worth pinning.
    """

    @no_duplicates
    def test_the_measured_payloads_decode_exactly(self):
        for field, expected, label in (
                ({'lo': '6860000', 'signScale': 12}, '6.86', 'LimitPrice'),
                ({'lo': '84279900', 'signScale': 12}, '84.2799', 'AveragePrice'),
                ({'lo': '152000000', 'signScale': 12}, '152', 'CumulativeQuantity'),
                ({'lo': '13710000', 'signScale': 12}, '13.71', 'Bid')):
            with self.subTest(label):
                self.assertEqual(decimal.Decimal(expected),
                                 decode_decimal(field))

    @no_duplicates
    def test_an_odd_signscale_is_negative(self):
        # Schwab's own worked example, and the field it was observed on:
        # principal is negative on a buy because the cash goes out.
        self.assertEqual(decimal.Decimal('-40'),
                         decode_decimal({'lo': '40000000', 'signScale': 13}))
        self.assertEqual(decimal.Decimal('-6.86'),
                         decode_decimal({'lo': '6860000', 'signScale': 13}))

    @no_duplicates
    def test_a_mantissa_without_a_scale_is_refused_rather_than_guessed(self):
        # Never observed. Refused because the guess that suggests itself --
        # six places, the common value -- is wrong by a factor of a million
        # when it is wrong, and says nothing when it is right.
        with self.assertRaises(UnusableDecimalScale):
            decode_decimal({'lo': '6860000'})

    @no_duplicates
    def test_a_mantissa_less_object_is_zero_not_unknown(self):
        # Measured on three fields of one payload, the third of which is the
        # hazard: LeavesQuantity on the *final* fill event of a completed
        # order. Read as unknown, a complete fill reports as outstanding.
        self.assertEqual(decimal.Decimal(0), decode_decimal({'signScale': 12}))
        self.assertEqual(decimal.Decimal(0), decode_decimal({}))

    @no_duplicates
    def test_the_mantissa_spans_three_fields(self):
        # Reasoned from the .NET layout, not observed -- no captured payload
        # has carried a non-zero mid. At signScale 12 `lo` alone tops out at
        # 4294.967295, so a lo-only decoder returns 705.032704 for $5,000.
        self.assertEqual(
                decimal.Decimal('5000'),
                decode_decimal({'lo': '705032704', 'mid': 1, 'signScale': 12}))
        self.assertEqual(
                decimal.Decimal('18446744073709.551616'),
                decode_decimal({'lo': '0', 'mid': 0, 'hi': 1,
                                'signScale': 12}))

    @no_duplicates
    def test_it_returns_decimal_so_the_value_can_be_repriced(self):
        # The point of not returning a float: set_price refuses one, so a
        # decoded value has to survive being fed straight back.
        from schwaby.orders.generic import OrderBuilder
        price = decode_decimal({'lo': '6860000', 'signScale': 12})
        self.assertIsInstance(price, decimal.Decimal)
        self.assertEqual({'price': '6.860000'},
                         OrderBuilder().set_price(price).build())

    @no_duplicates
    def test_a_hostile_scale_does_not_hang(self):
        # `10 ** (signScale // 2)` on this builds an astronomical integer and
        # hangs the thread, which a per-item try/except cannot rescue -- one
        # bad field would take a stream down rather than one message.
        #
        # `contrib/decimal-scale-is-bounded` red-proofs this. An earlier
        # version relied on `scaleb`'s own operand limit rather than an
        # explicit bound, and that could not be red-proofed at all: mutating
        # it did not fail the test, it hung the run until killed.
        with self.assertRaises(UnusableDecimalScale):
            decode_decimal({'lo': '1', 'signScale': 10 ** 9})

    @no_duplicates
    def test_a_bare_number_passes_through(self):
        # The same field does not always arrive in the same shape.
        self.assertEqual(decimal.Decimal('6.86'), decode_decimal('6.86'))
        self.assertEqual(decimal.Decimal('6.86'), decode_decimal(6.86))
        self.assertIsNone(decode_decimal(None))

    @no_duplicates
    def test_the_callers_decimal_context_cannot_change_the_answer(self):
        # `scaleb` applies the *caller's* context. A consumer setting
        # `getcontext().prec = 6` somewhere else in their process -- an
        # ordinary thing to do when formatting money -- got 1234.57 for a
        # price of 1234.5678, silently, from a function whose whole purpose is
        # that its result feeds back into set_price.
        # Both branches. An earlier fix built the value from a string and
        # then negated it -- and `Decimal.__neg__` is itself a context-aware
        # operation, so it was exact for positive values and rounded negative
        # ones. Every test then in the file used an even signScale, so nothing
        # could see it: the fix stopped at the first branch and so did the
        # fixture.
        cases = ((12, '1234.5678'), (13, '-1234.5678'))
        original = decimal.getcontext().prec
        try:
            for scale, expected in cases:
                for prec in (1, 3, 6, 28, 60):
                    with self.subTest(signScale=scale, prec=prec):
                        decimal.getcontext().prec = prec
                        self.assertEqual(
                                decimal.Decimal(expected),
                                decode_decimal({'lo': '1234567800',
                                                'signScale': scale}))
        finally:
            decimal.getcontext().prec = original

    @no_duplicates
    def test_a_full_width_mantissa_does_not_round(self):
        # 96 bits is the widest a .NET Decimal carries, and it rounds under
        # the default precision of 28 if the value goes through the context.
        mantissa = 2 ** 96 - 1
        for scale, sign in ((12, ''), (13, '-')):
            with self.subTest(signScale=scale):
                field = {'lo': str(mantissa & 0xFFFFFFFF),
                         'mid': (mantissa >> 32) & 0xFFFFFFFF,
                         'hi': mantissa >> 64,
                         'signScale': scale}
                self.assertEqual(
                        decimal.Decimal('{}{}E-6'.format(sign, mantissa)),
                        decode_decimal(field))

    @no_duplicates
    def test_an_implausible_scale_raises_rather_than_underflowing_to_zero(self):
        # Relying on the arithmetic to refuse is not enough: `scaleb`'s operand
        # limit only trips far out, so every scale from roughly two million to
        # four million underflowed to a zero that compares equal to zero. On
        # this feed that is the worst wrong answer available -- a corrupt
        # LeavesQuantity reading as zero is a complete fill.
        for scale in (MAX_SIGN_SCALE + 1, 2000100, 4000000, 10 ** 9, -1):
            with self.subTest(signScale=scale):
                with self.assertRaises(UnusableDecimalScale):
                    decode_decimal({'lo': '1', 'signScale': scale})

        # The bound itself is usable, so the assertions above are about
        # implausible scales rather than about the check refusing everything.
        self.assertEqual(decimal.Decimal(1),
                         decode_decimal({'lo': '1', 'signScale': 0}))
        self.assertIsNotNone(
                decode_decimal({'lo': '1', 'signScale': MAX_SIGN_SCALE}))

    @no_duplicates
    def test_a_mantissa_carried_only_in_mid_is_not_read_as_zero(self):
        # The serializer omits zero members, so a value whose low 32 bits are
        # zero arrives without `lo`. Keying the mantissa-less guard on `lo`
        # alone decoded that as zero -- the slice-reading defect this function
        # exists to fix, in the guard immediately before it.
        self.assertEqual(decimal.Decimal('4294.967296'),
                         decode_decimal({'mid': 1, 'signScale': 12}))
        self.assertEqual(decimal.Decimal('4294.967296'),
                         decode_decimal({'lo': '0', 'mid': 1, 'signScale': 12}))

    @no_duplicates
    def test_every_failure_is_catchable_by_one_except(self):
        # `decimal.InvalidOperation` is an ArithmeticError, so it is neither a
        # SchwabError nor a ValueError and escapes both handlers a caller
        # would reasonably write -- while the prose tells them to wrap each
        # field so one odd value does not cost the message. The empty string
        # reaches here from the SUBSCRIBED ack.
        # Enumerated from the ways a *member* can be wrong rather than from
        # the inputs the code already converts -- a list of the latter agrees
        # with the implementation by construction and cannot find a hole.
        for bad in ('', 'not a number', {'lo': '1'},
                    {'lo': '1', 'signScale': 10 ** 9},
                    {'lo': 'x', 'signScale': 12},        # non-numeric string
                    {'lo': '1', 'signScale': 'x'},       # non-numeric scale
                    {'lo': [1], 'signScale': 12},        # non-scalar: TypeError
                    {'lo': '', 'signScale': 12},         # present but falsy
                    {'lo': False, 'signScale': 12},      # int(False) is 0
                    {'lo': float('inf'), 'signScale': 12},  # OverflowError
                    {'lo': '-1', 'signScale': 12},       # negative mantissa
                    {'lo': '-1', 'signScale': 13},       # negative, odd scale
                    {'lo': '1', 'signScale': True},      # int(True) is 1
                    {'lo': '1', 'signScale': 1.9},       # int() truncates
                    {'lo': 1.9, 'signScale': 12},        # int() truncates
                    {'lo': '1_0', 'signScale': 12},      # PEP 515 underscore
                    {'lo': '\u00b2', 'signScale': 12},   # isdigit, int refuses
                    {'lo': '\u0663', 'signScale': 12},   # isdigit, int accepts
                    {'lo': str(2 ** 32), 'signScale': 12},  # wider than a member
                    'NaN', 'Infinity', float('nan'), float('inf')):
            with self.subTest(value=bad):
                with self.assertRaises(SchwabError):
                    decode_decimal(bad)
                with self.assertRaises(ValueError):
                    decode_decimal(bad)

    def test_a_corrupt_member_is_refused_rather_than_coerced(self):
        # The failure mode this guards is not an escaping exception -- it is
        # the corruption that decodes. `int(1.9)` is 1, `int('1_0')` is 10 and
        # `int('\u0663')` is 3, so each of these produced a plausible wrong
        # number on a money field, silently, in an earlier version.
        #
        # Every case carries its own positive control: the same object with a
        # sound member in the same slot must decode to a known value. Without
        # it an assertRaises passes just as well when the fixture never
        # reached the member at all.
        for name in ('lo', 'mid', 'hi'):
            for corrupt in (1.9, float('inf'), True, False, '-1', '1_0',
                            ' 1', '1 ', '', '0x10', '\u00b2', '\u0663',
                            [1], {'a': 1}, 2 ** 32, -1):
                with self.subTest(member=name, value=corrupt):
                    with self.assertRaises(UnusableDecimalScale):
                        decode_decimal({name: corrupt, 'signScale': 12})
            self.assertEqual(
                    {'lo': decimal.Decimal('0.000001'),
                     'mid': decimal.Decimal('4294.967296'),
                     'hi': decimal.Decimal('18446744073709.551616')}[name],
                    decode_decimal({name: '1', 'signScale': 12}))

    def test_a_corrupt_scale_is_refused_rather_than_coerced(self):
        for corrupt in (1.9, float('inf'), True, False, '-1', '1_0', ' 1',
                        '', '0x10', '\u00b2', '\u0663', [1], 2 ** 32, -1):
            with self.subTest(value=corrupt):
                with self.assertRaises(UnusableDecimalScale):
                    decode_decimal({'lo': '1', 'signScale': corrupt})
        # Positive control in the same slot.
        self.assertEqual(decimal.Decimal('0.000001'),
                         decode_decimal({'lo': '1', 'signScale': 12}))
        self.assertEqual(decimal.Decimal('-0.000001'),
                         decode_decimal({'lo': '1', 'signScale': 13}))
