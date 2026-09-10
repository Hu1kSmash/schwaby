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
    def test_a_mantissa_without_a_scale_is_scale_zero(self):
        # This was a raise, on the reasoning that a scale which is missing
        # cannot be told from one that was lost, and that guessing the common
        # value -- six places -- is wrong by a factor of a million when it is
        # wrong. The reasoning was fine and the premise was not: the shape is
        # ordinary traffic, and the docstring called it unobserved while a
        # capture on disk carried eight of them.
        #
        # One quote, verbatim, which is what settles it. Scale 12 on the
        # prices and no scale at all on the sizes, in the same object:
        quote = {'Ask':     {'lo': '13720000', 'signScale': 12},
                 'AskSize': {'lo': '19200'},
                 'Bid':     {'lo': '13710000', 'signScale': 12},
                 'BidSize': {'lo': '4200'},
                 'Mid':     {'lo': '13715000', 'signScale': 12}}
        self.assertEqual(
                {'Ask': decimal.Decimal('13.72'),
                 'AskSize': decimal.Decimal(19200),
                 'Bid': decimal.Decimal('13.71'),
                 'BidSize': decimal.Decimal(4200),
                 'Mid': decimal.Decimal('13.715')},
                {k: decode_decimal(v) for k, v in quote.items()})
        # $13.72 with 19200 on the ask is a coherent quote; 0.0192 shares is
        # not. The serializer omits what is zero, and the scale is no
        # different from a mantissa member in that respect.

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
        self.assertEqual(decimal.Decimal('6860000'), decode_decimal(6860000))
        self.assertEqual(decimal.Decimal('6.86'),
                         decode_decimal(decimal.Decimal('6.86')))
        self.assertIsNone(decode_decimal(None))
        # A float goes through `str` so its binary expansion does not come
        # along: Decimal(0.1) is 0.1000000000000000055511151231257827.
        self.assertEqual(decimal.Decimal('0.1'), decode_decimal(0.1))

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
        for bad in ('', 'not a number',
                    {'lo': '1', 'signScale': 10 ** 9},
                    {'Lo': '1', 'SignScale': 12},        # not a decimal object
                    {'lo': '1', 'signScale': 12, 'x': 1},
                    {'lo': 'x', 'signScale': 12},        # non-numeric string
                    {'lo': '1', 'signScale': 'x'},       # non-numeric scale
                    {'lo': [1], 'signScale': 12},        # non-scalar: TypeError
                    {'lo': '', 'signScale': 12},         # present but falsy
                    {'lo': False, 'signScale': 12},      # a bool is not one
                    {'lo': float('inf'), 'signScale': 12},  # not an integer
                    {'lo': '-1', 'signScale': 12},       # negative mantissa
                    {'lo': '1', 'signScale': True},      # a bool is not one
                    {'lo': '1', 'signScale': 1.9},       # nor is a float
                    {'lo': 1.9, 'signScale': 12},        # nor is a float
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

    def test_a_bare_value_refuses_what_a_member_refuses(self):
        # The gap that let three rounds of member-validation work sit beside
        # an unvalidated sibling: `decimal.Decimal`'s parser accepts more than
        # `int`'s does, so every token the mantissa path refuses decoded here
        # instead, as a confident wrong number, on a field the docstring says
        # arrives in either shape.
        #
        # Driven from the same list against both paths, so the two cannot
        # drift apart again without a failure.
        for token in ('1_0',        # ten, under PEP 515
                      '12_',        # twelve -- `int` refuses this outright
                      '_12', '1__0', '1_0.5',
                      '\u0663',     # Arabic-Indic three
                      '\uff11\uff12',  # fullwidth twelve
                      '\u06f9',     # Extended Arabic-Indic nine
                      ' 12 ', '\t12\n', '+12',
                      '1e1_0', '1E\u0663',
                      '0x10', '1j', 'inf', 'nan'):
            with self.subTest(token=token):
                with self.assertRaises(UnusableDecimalScale):
                    decode_decimal(token)
                with self.assertRaises(UnusableDecimalScale):
                    decode_decimal({'lo': token, 'signScale': 12})
        # Positive control on both paths, from the same shape of input.
        self.assertEqual(decimal.Decimal('12'), decode_decimal('12'))
        self.assertEqual(decimal.Decimal('0.000012'),
                         decode_decimal({'lo': '12', 'signScale': 12}))

    def test_a_bare_value_that_is_not_a_number_at_all_is_refused(self):
        for value in (True, False, [1], {1, 2}, (1,), object(), b'12',
                      bytearray(b'12')):
            with self.subTest(value=value):
                with self.assertRaises(UnusableDecimalScale):
                    decode_decimal(value)

    def test_a_bare_exponent_is_bounded_like_a_scale(self):
        # The dict path is bounded for a measured reason and this one was not.
        # 1E-999999999 is the same hazard MAX_SIGN_SCALE exists for, arriving
        # through a value that `is_finite()` waves through: it compares
        # neither == 0 nor <= 0, so a corrupt LeavesQuantity reads as still
        # outstanding forever -- while behaving as zero in arithmetic.
        stray = decimal.Decimal('1E-999999999')
        self.assertFalse(stray == 0)
        self.assertFalse(stray <= 0)
        self.assertEqual(decimal.Decimal(0), +stray)

        for label, value in (('1E-999999999', '1E-999999999'),
                             ('1E999999999', '1E999999999'),
                             ('1E-65', '1E-65'), ('1E65', '1E65'),
                             # Not `str()`-able within the digit limit, which
                             # is the point: formatting it is the operation
                             # being guarded against.
                             ('10**10000', 10 ** 10000),
                             ('-10**10000', -10 ** 10000)):
            with self.subTest(value=label):
                with self.assertRaises(UnusableDecimalScale):
                    decode_decimal(value)
        # Positive control: the bound is far outside anything real.
        for value in ('1E-64', '1E64', '6.86', '-6.86', 10 ** 20):
            with self.subTest(value=value):
                self.assertEqual(decimal.Decimal(value), decode_decimal(value))

    def test_a_wide_digit_string_does_not_escape_as_a_bare_value_error(self):
        # `int()` refuses a string past sys.get_int_max_str_digits() -- 4300
        # by default -- with a ValueError that is not a SchwabError, and the
        # range check cannot run until int() has returned. The value is
        # irrelevant: '0' * 4300 + '1' is the integer 1.
        for width in (4300, 4301, 100000):
            with self.subTest(width=width):
                self.assertEqual(
                        decimal.Decimal('0.000001'),
                        decode_decimal({'lo': '0' * width + '1',
                                        'signScale': 12}))
                self.assertEqual(
                        decimal.Decimal('0.000001'),
                        decode_decimal({'lo': '1',
                                        'signScale': '0' * width + '12'}))
        # A member that is genuinely long, rather than merely padded. The
        # cases above all strip to '1', so `lstrip` alone satisfies them and
        # the length check never runs -- exactly the shape that reads as a
        # working guard while proving nothing.
        for width in (11, 4301, 100000):
            with self.subTest(nonzero_width=width):
                with self.assertRaises(UnusableDecimalScale):
                    decode_decimal({'lo': '1' * width, 'signScale': 12})
                with self.assertRaises(UnusableDecimalScale):
                    decode_decimal({'lo': '1', 'signScale': '1' * width})

    def test_an_object_that_is_not_a_decimal_is_refused_not_read_as_zero(self):
        # The mantissa-less shortcut returns zero without inspecting
        # anything, so corruption that changes *keys* rather than values
        # reached it -- and zero is what a $0 commission and a completed fill
        # look like.
        for value in ({'Lo': 6860000, 'SignScale': 12},     # PascalCase
                      {'low': 6860000, 'signScale': 12},    # typo
                      {'symbol': 'AAPL', 'quantity': 100},  # another object
                      {'lo': '6860000', 'signScale': 12, 'flags': 0}):
            with self.subTest(value=value):
                with self.assertRaises(UnusableDecimalScale):
                    decode_decimal(value)
        # Positive control: the two shapes that legitimately decode to zero.
        self.assertEqual(decimal.Decimal(0), decode_decimal({}))
        self.assertEqual(decimal.Decimal(0), decode_decimal({'signScale': 12}))

    def test_a_dict_subclass_whose_get_and_getitem_disagree_is_catchable(self):
        # The comment above the loop says why the members use `.get`; the
        # scale then used `value['signScale']` and escaped as a KeyError.
        class Odd(dict):
            def get(self, key, default=None):
                return 12 if key == 'signScale' else super().get(key, default)

        odd = Odd(lo='6860000')
        with self.assertRaises(KeyError):       # the shape being guarded
            odd['signScale']
        self.assertEqual(decimal.Decimal('6.86'), decode_decimal(odd))
