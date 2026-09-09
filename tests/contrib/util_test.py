import decimal
import unittest

from schwaby.contrib.util import (
    decode_decimal, HeuristicJsonDecoder, UnknownDecimalScale)
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
        with self.assertRaises(UnknownDecimalScale):
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
        # Demonstrated rather than argued, and it is the one guarantee here
        # with no `redproof.py` case: swapping `scaleb` for the power does not
        # make this test *fail*, it makes the run stop responding until it is
        # killed. The harness cannot express that, and it is a more convincing
        # result than a red would have been.
        with self.assertRaises(decimal.InvalidOperation):
            decode_decimal({'lo': '1', 'signScale': 10 ** 9})

    @no_duplicates
    def test_a_bare_number_passes_through(self):
        # The same field does not always arrive in the same shape.
        self.assertEqual(decimal.Decimal('6.86'), decode_decimal('6.86'))
        self.assertEqual(decimal.Decimal('6.86'), decode_decimal(6.86))
        self.assertIsNone(decode_decimal(None))
