import decimal
import httpx2
import unittest

from schwaby.orders.generic import *
from schwaby.orders.common import *
from ..utils import has_diff, no_duplicates


class OrderBuilderTest(unittest.TestCase):

    def setUp(self):
        self.maxDiff = None
        self.order_builder = OrderBuilder()

    ##########################################################################
    # Session

    @no_duplicates
    def test_session_success(self):
        self.order_builder.set_session(Session.NORMAL)
        self.assertFalse(has_diff({
            'session': 'NORMAL'
        }, self.order_builder.build()))

        self.order_builder.clear_session()
        self.assertFalse(has_diff({}, self.order_builder.build()))

    @no_duplicates
    def test_session_wrong_type(self):
        with self.assertRaisesRegex(
                ValueError, 'schwaby.orders.common.Session.NORMAL'):
            self.order_builder.set_session('NORMAL')

    @no_duplicates
    def test_session_wrong_type_no_check(self):
        self.order_builder = OrderBuilder(enforce_enums=False)
        self.order_builder.set_session('NORMAL')
        self.assertFalse(has_diff({
            'session': 'NORMAL'
        }, self.order_builder.build()))

    ##########################################################################
    # Duration

    @no_duplicates
    def test_duration_success(self):
        self.order_builder.set_duration(Duration.DAY)
        self.assertFalse(has_diff({
            'duration': 'DAY'
        }, self.order_builder.build()))

        self.order_builder.clear_duration()
        self.assertFalse(has_diff({}, self.order_builder.build()))

    @no_duplicates
    def test_duration_wrong_type(self):
        with self.assertRaisesRegex(
                ValueError, 'schwaby.orders.common.Duration.DAY'):
            self.order_builder.set_duration('DAY')

    @no_duplicates
    def test_duration_wrong_type_no_check(self):
        self.order_builder = OrderBuilder(enforce_enums=False)
        self.order_builder.set_duration('DAY')
        self.assertFalse(has_diff({
            'duration': 'DAY'
        }, self.order_builder.build()))

    ##########################################################################
    # OrderType

    @no_duplicates
    def test_order_type_success(self):
        self.order_builder.set_order_type(OrderType.MARKET)
        self.assertFalse(has_diff({
            'orderType': 'MARKET'
        }, self.order_builder.build()))

        self.order_builder.clear_order_type()
        self.assertFalse(has_diff({}, self.order_builder.build()))

    @no_duplicates
    def test_order_type_wrong_type(self):
        with self.assertRaisesRegex(
                ValueError, 'schwaby.orders.common.OrderType.MARKET'):
            self.order_builder.set_order_type('MARKET')

    @no_duplicates
    def test_order_type_wrong_type_no_check(self):
        self.order_builder = OrderBuilder(enforce_enums=False)
        self.order_builder.set_order_type('MARKET')
        self.assertFalse(has_diff({
            'orderType': 'MARKET'
        }, self.order_builder.build()))

    ##########################################################################
    # ComplexOrderStrategyType

    @no_duplicates
    def test_complex_order_strategy_type_success(self):
        self.order_builder.set_complex_order_strategy_type(
            ComplexOrderStrategyType.IRON_CONDOR)
        self.assertFalse(has_diff({
            'complexOrderStrategyType': 'IRON_CONDOR'
        }, self.order_builder.build()))

        self.order_builder.clear_complex_order_strategy_type()
        self.assertFalse(has_diff({}, self.order_builder.build()))

    @no_duplicates
    def test__wrong_type(self):
        with self.assertRaisesRegex(
                ValueError, 
                'schwaby.orders.common.ComplexOrderStrategyType.IRON_CONDOR'):
            self.order_builder.set_complex_order_strategy_type('IRON_CONDOR')

    @no_duplicates
    def test__wrong_type_no_check(self):
        self.order_builder = OrderBuilder(enforce_enums=False)
        self.order_builder.set_complex_order_strategy_type('IRON_CONDOR')
        self.assertFalse(has_diff({
            'complexOrderStrategyType': 'IRON_CONDOR'
        }, self.order_builder.build()))

    ##########################################################################
    # Quantity

    @no_duplicates
    def test_quantity_success(self):
        self.order_builder.set_quantity(12)
        self.assertFalse(has_diff({
            'quantity': 12
        }, self.order_builder.build()))

        self.order_builder.clear_quantity()
        self.assertFalse(has_diff({}, self.order_builder.build()))

    @no_duplicates
    def test_quantity_negative(self):
        with self.assertRaises(ValueError):
            self.order_builder.set_quantity(-12)

    @no_duplicates
    def test_quantity_zero(self):
        with self.assertRaises(ValueError):
            self.order_builder.set_quantity(0)

    ##########################################################################
    # RequestedDestination

    @no_duplicates
    def test_requested_destination_success(self):
        self.order_builder.set_requested_destination(Destination.INET)
        self.assertFalse(has_diff({
            'requestedDestination': 'INET'
        }, self.order_builder.build()))

        self.order_builder.clear_requested_destination()
        self.assertFalse(has_diff({}, self.order_builder.build()))

    @no_duplicates
    def test_requested_destination_wrong_type(self):
        with self.assertRaisesRegex(
                ValueError, 'schwaby.orders.common.Destination.INET'):
            self.order_builder.set_requested_destination('INET')

    @no_duplicates
    def test_requested_destination_wrong_type_no_check(self):
        self.order_builder = OrderBuilder(enforce_enums=False)
        self.order_builder.set_requested_destination('INET')
        self.assertFalse(has_diff({
            'requestedDestination': 'INET'
        }, self.order_builder.build()))

    @no_duplicates
    def test_the_venue_enum_lands_on_the_field_schwab_enumerates(self):
        # Until 3.0.0 the Destination enum was applied to destinationLinkName,
        # which Schwab's schema types as a free string; requestedDestination is
        # the field whose enumeration is exactly these twelve values. An order
        # asking for NYSE therefore carried the venue in a field that does not
        # select one, and nothing rejected it -- the request was well-formed,
        # so the only symptom was routing you did not choose.
        built = (OrderBuilder()
                 .set_requested_destination(Destination.NYSE)
                 .build())
        self.assertEqual({'requestedDestination': 'NYSE'}, built)
        self.assertNotIn('destinationLinkName', built)

    ##########################################################################
    # DestinationLinkName

    @no_duplicates
    def test_destination_link_name_takes_a_free_string(self):
        # Schwab's schema says `destinationLinkName: string`, with no
        # enumeration. Validating it against Destination refused every legal
        # value and accepted only twelve that belong to another field.
        self.order_builder.set_destination_link_name('anything at all')
        self.assertFalse(has_diff({
            'destinationLinkName': 'anything at all'
        }, self.order_builder.build()))

        self.order_builder.clear_destination_link_name()
        self.assertFalse(has_diff({}, self.order_builder.build()))

    ##########################################################################
    # TaxLotMethod

    @no_duplicates
    def test_tax_lot_method_success(self):
        # The enum was exported from schwaby.orders.common from the beginning
        # and had no setter, so the field could not be sent at all. It matters
        # on a closing order: FIFO and LIFO realise different gains.
        self.order_builder.set_tax_lot_method(TaxLotMethod.LIFO)
        self.assertFalse(has_diff({
            'taxLotMethod': 'LIFO'
        }, self.order_builder.build()))

        self.order_builder.clear_tax_lot_method()
        self.assertFalse(has_diff({}, self.order_builder.build()))

    @no_duplicates
    def test_tax_lot_method_wrong_type(self):
        with self.assertRaisesRegex(
                ValueError, 'schwaby.orders.common.TaxLotMethod.FIFO'):
            self.order_builder.set_tax_lot_method('FIFO')

    @no_duplicates
    def test_every_tax_lot_method_schwab_lists_is_available(self):
        # Schwab's order schema enumerates these seven. An enum that advertises
        # values the venue rejects, or omits ones it accepts, is the defect
        # this asserts against -- it has happened here before.
        self.assertEqual(
                ['AVERAGE_COST', 'FIFO', 'HIGH_COST', 'LIFO', 'LOSS_HARVESTER',
                 'LOW_COST', 'SPECIFIC_LOT'],
                sorted(m.value for m in TaxLotMethod))

    ##########################################################################
    # StopPrice

    @no_duplicates
    def test_stop_price_success(self):
        self.order_builder.set_stop_price('42.90')
        self.assertFalse(has_diff({
            'stopPrice': '42.90'
        }, self.order_builder.build()))

        self.order_builder.clear_stop_price()
        self.assertFalse(has_diff({}, self.order_builder.build()))

    @no_duplicates
    def test_stop_price_as_string(self):
        self.order_builder.set_stop_price('invalid')
        self.assertFalse(has_diff({
            'stopPrice': 'invalid'
        }, self.order_builder.build()))

    @no_duplicates
    def test_stop_price_negative(self):
        self.order_builder.set_stop_price('-1.31')
        self.assertFalse(has_diff({
            'stopPrice': '-1.31'
        }, self.order_builder.build()))

    @no_duplicates
    def test_stop_price_zero(self):
        self.order_builder.set_stop_price('0.00')
        self.assertFalse(has_diff({
            'stopPrice': '0.00'
        }, self.order_builder.build()))

    @no_duplicates
    def test_stop_price_precision_is_not_second_guessed(self):
        # These used to be truncated to two places. Now whatever precision the
        # caller wrote is what Schwab is asked for.
        for value in ('1.99999', '2.00001'):
            builder = OrderBuilder()
            builder.set_stop_price(value)
            self.assertFalse(has_diff({'stopPrice': value}, builder.build()))

    @no_duplicates
    def test_copy_stop_price(self):
        self.order_builder.copy_stop_price(2.00001)
        self.assertFalse(has_diff({
            'stopPrice': 2.00001
        }, self.order_builder.build()))

    @no_duplicates
    def test_copy_stop_price_hopelessly_invalid(self):
        self.order_builder.copy_stop_price(['hopelessly invalid'])
        self.assertFalse(has_diff({
            'stopPrice': ['hopelessly invalid']
        }, self.order_builder.build()))

    ##########################################################################
    # StopPriceLinkBasis

    @no_duplicates
    def test_stop_price_link_basis_success(self):
        self.order_builder.set_stop_price_link_basis(StopPriceLinkBasis.ASK)
        self.assertFalse(has_diff({
            'stopPriceLinkBasis': 'ASK'
        }, self.order_builder.build()))

        self.order_builder.clear_stop_price_link_basis()
        self.assertFalse(has_diff({}, self.order_builder.build()))

    @no_duplicates
    def test_stop_price_link_basis_wrong_type(self):
        with self.assertRaisesRegex(
                ValueError, 'schwaby.orders.common.StopPriceLinkBasis.ASK'):
            self.order_builder.set_stop_price_link_basis('ASK')

    @no_duplicates
    def test_stop_price_link_basis_wrong_type_no_check(self):
        self.order_builder = OrderBuilder(enforce_enums=False)
        self.order_builder.set_stop_price_link_basis('ASK')
        self.assertFalse(has_diff({
            'stopPriceLinkBasis': 'ASK'
        }, self.order_builder.build()))

    ##########################################################################
    # StopPriceLinkType

    @no_duplicates
    def test_stop_price_link_type_success(self):
        self.order_builder.set_stop_price_link_type(StopPriceLinkType.VALUE)
        self.assertFalse(has_diff({
            'stopPriceLinkType': 'VALUE'
        }, self.order_builder.build()))

        self.order_builder.clear_stop_price_link_type()
        self.assertFalse(has_diff({}, self.order_builder.build()))

    @no_duplicates
    def test_stop_price_link_type_wrong_type(self):
        with self.assertRaisesRegex(
                ValueError, 'schwaby.orders.common.StopPriceLinkType.VALUE'):
            self.order_builder.set_stop_price_link_type('VALUE')

    @no_duplicates
    def test_stop_price_link_type_wrong_type_no_check(self):
        self.order_builder = OrderBuilder(enforce_enums=False)
        self.order_builder.set_stop_price_link_type('VALUE')
        self.assertFalse(has_diff({
            'stopPriceLinkType': 'VALUE'
        }, self.order_builder.build()))

    ##########################################################################
    # StopPriceOffset

    @no_duplicates
    def test_stop_price_offset_success(self):
        self.order_builder.set_stop_price_offset(12.98)
        self.assertFalse(has_diff({
            'stopPriceOffset': 12.98
        }, self.order_builder.build()))

        self.order_builder.clear_stop_price_offset()
        self.assertFalse(has_diff({}, self.order_builder.build()))

    ##########################################################################
    # StopType

    @no_duplicates
    def test_stop_type_success(self):
        self.order_builder.set_stop_type(StopType.MARK)
        self.assertFalse(has_diff({
            'stopType': 'MARK'
        }, self.order_builder.build()))

        self.order_builder.clear_stop_type()
        self.assertFalse(has_diff({}, self.order_builder.build()))

    @no_duplicates
    def test_stop_type_wrong_type(self):
        with self.assertRaisesRegex(
                ValueError, 'schwaby.orders.common.StopType.MARK'):
            self.order_builder.set_stop_type('MARK')

    @no_duplicates
    def test_stop_type_wrong_type_no_check(self):
        self.order_builder = OrderBuilder(enforce_enums=False)
        self.order_builder.set_stop_type('MARK')
        self.assertFalse(has_diff({
            'stopType': 'MARK'
        }, self.order_builder.build()))

    ##########################################################################
    # PriceLinkBasis

    @no_duplicates
    def test_price_link_basis_success(self):
        self.order_builder.set_price_link_basis(PriceLinkBasis.AVERAGE)
        self.assertFalse(has_diff({
            'priceLinkBasis': 'AVERAGE'
        }, self.order_builder.build()))

        self.order_builder.clear_price_link_basis()
        self.assertFalse(has_diff({}, self.order_builder.build()))

    @no_duplicates
    def test_price_link_basis_wrong_type(self):
        with self.assertRaisesRegex(
                ValueError, 'schwaby.orders.common.PriceLinkBasis.AVERAGE'):
            self.order_builder.set_price_link_basis('AVERAGE')

    @no_duplicates
    def test_price_link_basis_wrong_type_no_check(self):
        self.order_builder = OrderBuilder(enforce_enums=False)
        self.order_builder.set_price_link_basis('AVERAGE')
        self.assertFalse(has_diff({
            'priceLinkBasis': 'AVERAGE'
        }, self.order_builder.build()))

    ##########################################################################
    # PriceLinkType

    @no_duplicates
    def test_price_link_type_success(self):
        self.order_builder.set_price_link_type(PriceLinkType.PERCENT)
        self.assertFalse(has_diff({
            'priceLinkType': 'PERCENT'
        }, self.order_builder.build()))

        self.order_builder.clear_price_link_type()
        self.assertFalse(has_diff({}, self.order_builder.build()))

    @no_duplicates
    def test_price_link_type_wrong_type(self):
        with self.assertRaisesRegex(
                ValueError, 'schwaby.orders.common.PriceLinkType.PERCENT'):
            self.order_builder.set_price_link_type('PERCENT')

    @no_duplicates
    def test_price_link_type_wrong_type_no_check(self):
        self.order_builder = OrderBuilder(enforce_enums=False)
        self.order_builder.set_price_link_type('PERCENT')
        self.assertFalse(has_diff({
            'priceLinkType': 'PERCENT'
        }, self.order_builder.build()))

    ##########################################################################
    # PriceOffset

    @no_duplicates
    def test_price_offset_success(self):
        self.order_builder.set_price_offset(12.98)
        self.assertFalse(has_diff({
            'priceOffset': 12.98
        }, self.order_builder.build()))

        self.order_builder.clear_price_offset()
        self.assertFalse(has_diff({}, self.order_builder.build()))

    @no_duplicates
    def test_price_offset_completes_the_price_link_trio(self):
        # The stop-price side already had basis, type and offset. The
        # price-linked side had a basis and a type but no offset, so a
        # price-linked order could not actually be expressed.
        self.order_builder.set_price_link_basis(PriceLinkBasis.BASE)
        self.order_builder.set_price_link_type(PriceLinkType.VALUE)
        self.order_builder.set_price_offset(1.5)
        self.assertFalse(has_diff({
            'priceLinkBasis': 'BASE',
            'priceLinkType': 'VALUE',
            'priceOffset': 1.5,
        }, self.order_builder.build()))

    ##########################################################################
    # Price

    @no_duplicates
    def test_price_success(self):
        self.order_builder.set_price('23.49')
        self.assertFalse(has_diff({
            'price': '23.49'
        }, self.order_builder.build()))

        self.order_builder.clear_price()
        self.assertFalse(has_diff({}, self.order_builder.build()))

    @no_duplicates
    def test_price_success_as_string(self):
        self.order_builder.set_price('invalid')
        self.assertFalse(has_diff({
            'price': 'invalid'
        }, self.order_builder.build()))

    @no_duplicates
    def test_price_negative(self):
        self.order_builder.set_price('-1.23')
        self.assertFalse(has_diff({
            'price': '-1.23'
        }, self.order_builder.build()))

    @no_duplicates
    def test_price_zero(self):
        self.order_builder.set_price('0.00')
        self.assertFalse(has_diff({
            'price': '0.00'
        }, self.order_builder.build()))

    @no_duplicates
    def test_price_precision_is_not_second_guessed(self):
        # See test_stop_price_precision_is_not_second_guessed.
        for value in ('19.9999999', '20.00000001'):
            builder = OrderBuilder()
            builder.set_price(value)
            self.assertFalse(has_diff({'price': value}, builder.build()))

    @no_duplicates
    def test_copy_price(self):
        self.order_builder.copy_price(19.9999999)
        self.assertFalse(has_diff({
            'price': 19.9999999
        }, self.order_builder.build()))

    @no_duplicates
    def test_copy_price_hopelessly_invalid(self):
        self.order_builder.copy_price(['hopelessly invalid'])
        self.assertFalse(has_diff({
            'price': ['hopelessly invalid']
        }, self.order_builder.build()))

    ##########################################################################
    # ActivationPrice

    @no_duplicates
    def test_activation_price_success(self):
        self.order_builder.set_activation_price(54.03)
        self.assertFalse(has_diff({
            'activationPrice': 54.03
        }, self.order_builder.build()))

        self.order_builder.clear_activation_price()
        self.assertFalse(has_diff({}, self.order_builder.build()))

    @no_duplicates
    def test_activation_price_negative(self):
        with self.assertRaises(ValueError):
            self.order_builder.set_activation_price(-3.94)

    @no_duplicates
    def test_activation_price_zero(self):
        with self.assertRaises(ValueError):
            self.order_builder.set_activation_price(0.0)

    ##########################################################################
    # SpecialInstruction

    @no_duplicates
    def test_special_instruction_success(self):
        self.order_builder.set_special_instruction(
            SpecialInstruction.DO_NOT_REDUCE)
        self.assertFalse(has_diff({
            'specialInstruction': 'DO_NOT_REDUCE'
        }, self.order_builder.build()))

        self.order_builder.clear_special_instruction()
        self.assertFalse(has_diff({}, self.order_builder.build()))

    @no_duplicates
    def test_special_instruction_wrong_type(self):
        with self.assertRaisesRegex(
                ValueError,
                'schwaby.orders.common.SpecialInstruction.DO_NOT_REDUCE'):
            self.order_builder.set_special_instruction('DO_NOT_REDUCE')

    @no_duplicates
    def test_special_instruction_wrong_type_no_check(self):
        self.order_builder = OrderBuilder(enforce_enums=False)
        self.order_builder.set_special_instruction('DO_NOT_REDUCE')
        self.assertFalse(has_diff({
            'specialInstruction': 'DO_NOT_REDUCE'
        }, self.order_builder.build()))

    ##########################################################################
    # OrderStrategyType

    @no_duplicates
    def test_order_strategy_type_success(self):
        self.order_builder.set_order_strategy_type(OrderStrategyType.OCO)
        self.assertFalse(has_diff({
            'orderStrategyType': 'OCO'
        }, self.order_builder.build()))

        self.order_builder.clear_order_strategy_type()
        self.assertFalse(has_diff({}, self.order_builder.build()))

    @no_duplicates
    def test_order_strategy_type_wrong_type(self):
        with self.assertRaisesRegex(
                ValueError, 'schwaby.orders.common.OrderStrategyType.OCO'):
            self.order_builder.set_order_strategy_type('OCO')

    @no_duplicates
    def test_order_strategy_type_wrong_type_no_check(self):
        self.order_builder = OrderBuilder(enforce_enums=False)
        self.order_builder.set_order_strategy_type('OCO')
        self.assertFalse(has_diff({
            'orderStrategyType': 'OCO'
        }, self.order_builder.build()))

    ##########################################################################
    # ChildOrderStrategies

    @no_duplicates
    def test_add_child_order_strategy_success(self):
        self.order_builder.add_child_order_strategy(
            OrderBuilder().set_session(Session.NORMAL))
        self.assertFalse(has_diff({
            'childOrderStrategies': [{'session': 'NORMAL'}]
        }, self.order_builder.build()))

        self.order_builder.clear_child_order_strategies()
        self.assertFalse(has_diff({}, self.order_builder.build()))

    @no_duplicates
    def test_add_child_order_strategy_dict(self):
        self.order_builder.add_child_order_strategy(
            {'session': 'NORMAL'})
        self.assertFalse(has_diff({
            'childOrderStrategies': [{'session': 'NORMAL'}]
        }, self.order_builder.build()))

    @no_duplicates
    def test_add_child_order_strategy_invalid_type(self):
        with self.assertRaises(ValueError):
            self.order_builder.add_child_order_strategy(10)

    @no_duplicates
    def test_add_child_order_strategy_httpx2_response(self):
        with self.assertRaisesRegex(
                ValueError, 'Child order cannot be a response'):
            self.order_builder.add_child_order_strategy(httpx2.Response(200))

    ##########################################################################
    # OrderLegCollection

    @no_duplicates
    def test_add_equity_leg_success(self):
        self.order_builder.add_equity_leg(EquityInstruction.BUY, 'GOOG', 10)
        self.order_builder.add_equity_leg(
            EquityInstruction.SELL_SHORT, 'MSFT', 1)
        self.assertFalse(has_diff({
            'orderLegCollection': [{
                'instruction': 'BUY',
                'instrument': {
                    'symbol': 'GOOG',
                    'assetType': 'EQUITY'
                },
                'quantity': 10,
            }, {
                'instruction': 'SELL_SHORT',
                'instrument': {
                    'symbol': 'MSFT',
                    'assetType': 'EQUITY'
                },
                'quantity': 1,
            }]
        }, self.order_builder.build()))

        self.order_builder.clear_order_legs()
        self.assertFalse(has_diff({}, self.order_builder.build()))

    @no_duplicates
    def test_add_equity_leg_wrong_type(self):
        with self.assertRaisesRegex(
                ValueError, 'schwaby.orders.common.EquityInstruction.BUY'):
            self.order_builder.add_equity_leg('BUY', 'GOOG', 10)

    @no_duplicates
    def test_add_equity_leg_wrong_type_no_check(self):
        self.order_builder = OrderBuilder(enforce_enums=False)

        self.order_builder.add_equity_leg('BUY', 'GOOG', 10)
        self.order_builder.add_equity_leg('SELL_TO_CLOSE', 'MSFT', 1)

        self.assertFalse(has_diff({
            'orderLegCollection': [{
                'instruction': 'BUY',
                'instrument': {
                    'symbol': 'GOOG',
                    'assetType': 'EQUITY'
                },
                'quantity': 10,
            }, {
                'instruction': 'SELL_TO_CLOSE',
                'instrument': {
                    'symbol': 'MSFT',
                    'assetType': 'EQUITY'
                },
                'quantity': 1,
            }]
        }, self.order_builder.build()))

    @no_duplicates
    def test_add_equity_leg_negative_quantity(self):
        with self.assertRaises(ValueError):
            self.order_builder.add_equity_leg(
                EquityInstruction.BUY, 'GOOG', -1)

    @no_duplicates
    def test_add_equity_leg_zero_quantity(self):
        with self.assertRaises(ValueError):
            self.order_builder.add_equity_leg(
                EquityInstruction.BUY, 'GOOG', 0)

    @no_duplicates
    def test_add_option_leg_success(self):
        self.order_builder.add_option_leg(
            OptionInstruction.BUY_TO_OPEN, 'GOOG31433C1342', 10)
        self.order_builder.add_option_leg(
            OptionInstruction.BUY_TO_CLOSE, 'MSFT439132P35', 1)
        self.assertFalse(has_diff({
            'orderLegCollection': [{
                'instruction': 'BUY_TO_OPEN',
                'instrument': {
                    'symbol': 'GOOG31433C1342',
                    'assetType': 'OPTION'
                },
                'quantity': 10,
            }, {
                'instruction': 'BUY_TO_CLOSE',
                'instrument': {
                    'symbol': 'MSFT439132P35',
                    'assetType': 'OPTION'
                },
                'quantity': 1,
            }]
        }, self.order_builder.build()))

        self.order_builder.clear_order_legs()
        self.assertFalse(has_diff({}, self.order_builder.build()))

    @no_duplicates
    def test_add_option_leg_wrong_type(self):
        with self.assertRaisesRegex(
                ValueError, 'schwaby.orders.common.OptionInstruction.BUY_TO_OPEN'):
            self.order_builder.add_option_leg(
                'BUY_TO_OPEN', 'GOOG31433C1342', 10)

    @no_duplicates
    def test_add_option_leg_wrong_type_no_check(self):
        self.order_builder = OrderBuilder(enforce_enums=False)

        self.order_builder.add_option_leg('BUY_TO_OPEN', 'GOOG31433C1342', 10)
        self.order_builder.add_option_leg('BUY_TO_CLOSE', 'MSFT439132P35', 1)
        self.assertFalse(has_diff({
            'orderLegCollection': [{
                'instruction': 'BUY_TO_OPEN',
                'instrument': {
                    'symbol': 'GOOG31433C1342',
                    'assetType': 'OPTION'
                },
                'quantity': 10,
            }, {
                'instruction': 'BUY_TO_CLOSE',
                'instrument': {
                    'symbol': 'MSFT439132P35',
                    'assetType': 'OPTION'
                },
                'quantity': 1,
            }]
        }, self.order_builder.build()))

        self.order_builder.clear_order_legs()
        self.assertFalse(has_diff({}, self.order_builder.build()))

    @no_duplicates
    def test_add_option_leg_negative_quantity(self):
        with self.assertRaises(ValueError):
            self.order_builder.add_option_leg(
                OptionInstruction.BUY_TO_OPEN, 'GOOG31433C1342', -1)

    @no_duplicates
    def test_add_option_leg_zero_quantity(self):
        with self.assertRaises(ValueError):
            self.order_builder.add_option_leg(
                OptionInstruction.BUY_TO_OPEN, 'GOOG31433C1342', 0)


class OrderBuilderExamplesTest(unittest.TestCase):

    def setUp(self):
        self.maxDiff = None

    ##########################################################################
    # Functional tests from here:
    # Adapted from the order samples in the retired TDAmeritrade docs.
    @no_duplicates
    def test_quantity_negative(self):
        with self.assertRaises(ValueError):
            self.order_builder.set_quantity(-12)

    @no_duplicates
    def test_quantity_wrong_type_no_check(self):
        self.order_builder = OrderBuilder(enforce_enums=False)
        self.order_builder.set_quantity('')
        self.assertFalse(has_diff({
            'quantity': ''
        }, self.order_builder.build()))


class OrderBuilderExamplesTest(unittest.TestCase):

    @no_duplicates
    def setUp(self):
        self.maxDiff = None

    ##########################################################################
    # Functional tests from here:
    # Adapted from the order samples in the retired TDAmeritrade docs.

    @no_duplicates
    def test_buy_market_stock(self):
        builder = (
            OrderBuilder()
            .set_order_type(OrderType.MARKET)
            .set_session(Session.NORMAL)
            .set_duration(Duration.DAY)
            .set_order_strategy_type(OrderStrategyType.SINGLE)
            .add_equity_leg(EquityInstruction.BUY, 'XYZ', 15))

        expected = {
            'orderType': 'MARKET',
            'session': 'NORMAL',
            'duration': 'DAY',
            'orderStrategyType': 'SINGLE',
            'orderLegCollection': [
                {
                    'instruction': 'BUY',  # The original says 'Buy'
                    'quantity': 15,
                    'instrument': {
                        'symbol': 'XYZ',
                        'assetType': 'EQUITY'
                    }
                }
            ]
        }

        self.assertFalse(has_diff(expected, builder.build()))

    @no_duplicates
    def test_buy_limit_single_option(self):
        builder = (
            OrderBuilder()
            .set_complex_order_strategy_type(ComplexOrderStrategyType.NONE)
            .set_order_type(OrderType.LIMIT)
            .set_session(Session.NORMAL)
            .set_price('6.45')
            .set_duration(Duration.DAY)
            .set_order_strategy_type(OrderStrategyType.SINGLE)
            .add_option_leg(OptionInstruction.BUY_TO_OPEN, 'XYZ_032015C49', 10))

        expected = {
            'complexOrderStrategyType': 'NONE',
            'orderType': 'LIMIT',
            'session': 'NORMAL',
            'price': '6.45',
            'duration': 'DAY',
            'orderStrategyType': 'SINGLE',
            'orderLegCollection': [
                {
                    'instruction': 'BUY_TO_OPEN',
                    'quantity': 10,
                    'instrument': {
                        'symbol': 'XYZ_032015C49',
                        'assetType': 'OPTION'
                    }
                }
            ]
        }

        self.assertFalse(has_diff(expected, builder.build()))

    @no_duplicates
    def test_buy_limit_vertical_call_spread(self):
        builder = (
            OrderBuilder()
            .set_order_type(OrderType.NET_DEBIT)
            .set_session(Session.NORMAL)
            .set_price('1.20')
            .set_duration(Duration.DAY)
            .set_order_strategy_type(OrderStrategyType.SINGLE)
            .add_option_leg(OptionInstruction.BUY_TO_OPEN, 'XYZ_011516C40', 10)
            .add_option_leg(
                OptionInstruction.SELL_TO_OPEN, 'XYZ_011516C42.5', 10))

        expected = {
            'orderType': 'NET_DEBIT',
            'session': 'NORMAL',
            'price': '1.20',
            'duration': 'DAY',
            'orderStrategyType': 'SINGLE',
            'orderLegCollection': [
                {
                    'instruction': 'BUY_TO_OPEN',
                    'quantity': 10,
                    'instrument': {
                        'symbol': 'XYZ_011516C40',
                        'assetType': 'OPTION'
                    }
                },
                {
                    'instruction': 'SELL_TO_OPEN',
                    'quantity': 10,
                    'instrument': {
                        'symbol': 'XYZ_011516C42.5',
                        'assetType': 'OPTION'
                    }
                }
            ]
        }

        self.assertFalse(has_diff(expected, builder.build()))

    @no_duplicates
    def test_custom_option_spread(self):
        builder = (
            OrderBuilder()
            .set_order_strategy_type(OrderStrategyType.SINGLE)
            .set_order_type(OrderType.MARKET)
            .add_option_leg(OptionInstruction.SELL_TO_OPEN, 'XYZ_011819P45', 1)
            .add_option_leg(OptionInstruction.BUY_TO_OPEN, 'XYZ_011720P43', 2)
            .set_complex_order_strategy_type(ComplexOrderStrategyType.CUSTOM)
            .set_duration(Duration.DAY)
            .set_session(Session.NORMAL))

        expected = {
            'orderStrategyType': 'SINGLE',
            'orderType': 'MARKET',
            'orderLegCollection': [
                {
                    'instrument': {
                        'assetType': 'OPTION',
                        'symbol': 'XYZ_011819P45'
                    },
                    'instruction': 'SELL_TO_OPEN',
                    'quantity': 1
                },
                {
                    'instrument': {
                        'assetType': 'OPTION',
                        'symbol': 'XYZ_011720P43'
                    },
                    'instruction': 'BUY_TO_OPEN',
                    'quantity': 2
                }
            ],
            'complexOrderStrategyType': 'CUSTOM',
            'duration': 'DAY',
            'session': 'NORMAL'
        }

        self.assertFalse(has_diff(expected, builder.build()))

    @no_duplicates
    def test_conditional_order_one_triggers_another(self):
        builder = (
            OrderBuilder()
            .set_order_type(OrderType.LIMIT)
            .set_session(Session.NORMAL)
            .set_price('34.97')
            .set_duration(Duration.DAY)
            .set_order_strategy_type(OrderStrategyType.TRIGGER)
            .add_equity_leg(EquityInstruction.BUY, 'XYZ', 10)
            .add_child_order_strategy(
                OrderBuilder()
                .set_order_type(OrderType.LIMIT)
                .set_session(Session.NORMAL)
                .set_price('42.03')
                .set_duration(Duration.DAY)
                .set_order_strategy_type(OrderStrategyType.SINGLE)
                .add_equity_leg(EquityInstruction.SELL, 'XYZ', 10)))

        expected = {
            'orderType': 'LIMIT',
            'session': 'NORMAL',
            'price': '34.97',
            'duration': 'DAY',
            'orderStrategyType': 'TRIGGER',
            'orderLegCollection': [
                {
                    'instruction': 'BUY',
                    'quantity': 10,
                    'instrument': {
                        'symbol': 'XYZ',
                        'assetType': 'EQUITY'
                    }
                }
            ],
            'childOrderStrategies': [
                {
                    'orderType': 'LIMIT',
                    'session': 'NORMAL',
                    'price': '42.03',
                    'duration': 'DAY',
                    'orderStrategyType': 'SINGLE',
                    'orderLegCollection': [
                        {
                            'instruction': 'SELL',
                            'quantity': 10,
                            'instrument': {
                                'symbol': 'XYZ',
                                'assetType': 'EQUITY'
                            }
                        }
                    ]
                }
            ]
        }

        self.assertFalse(has_diff(expected, builder.build()))

    @no_duplicates
    def test_conditional_order_one_cancels_another(self):
        builder = (
            OrderBuilder()
            .set_order_strategy_type(OrderStrategyType.OCO)
            .add_child_order_strategy(
                OrderBuilder()
                .set_order_type(OrderType.LIMIT)
                .set_session(Session.NORMAL)
                .set_price('45.97')
                .set_duration(Duration.DAY)
                .set_order_strategy_type(OrderStrategyType.SINGLE)
                .add_equity_leg(EquityInstruction.SELL, 'XYZ', 2)
            )
            .add_child_order_strategy(
                OrderBuilder()
                .set_order_type(OrderType.STOP_LIMIT)
                .set_session(Session.NORMAL)
                .set_price('37.00')
                .set_stop_price('37.03')
                .set_duration(Duration.DAY)
                .set_order_strategy_type(OrderStrategyType.SINGLE)
                .add_equity_leg(EquityInstruction.SELL, 'XYZ', 2)))

        expected = {
            'orderStrategyType': 'OCO',
            'childOrderStrategies': [
                {
                    'orderType': 'LIMIT',
                    'session': 'NORMAL',
                    'price': '45.97',
                    'duration': 'DAY',
                    'orderStrategyType': 'SINGLE',
                    'orderLegCollection': [
                        {
                            'instruction': 'SELL',
                            'quantity': 2,
                            'instrument': {
                                'symbol': 'XYZ',
                                'assetType': 'EQUITY'
                            }
                        }
                    ]
                },
                {
                    'orderType': 'STOP_LIMIT',
                    'session': 'NORMAL',
                    'price': '37.00',
                    'stopPrice': '37.03',
                    'duration': 'DAY',
                    'orderStrategyType': 'SINGLE',
                    'orderLegCollection': [
                        {
                            'instruction': 'SELL',
                            'quantity': 2,
                            'instrument': {
                                'symbol': 'XYZ',
                                'assetType': 'EQUITY'
                            }
                        }
                    ]
                }
            ]
        }

        self.assertFalse(has_diff(expected, builder.build()))

    @no_duplicates
    def test_conditional_order_one_triggers_a_one_cancels_other(self):
        builder = (
            OrderBuilder()
            .set_order_strategy_type(OrderStrategyType.TRIGGER)
            .set_session(Session.NORMAL)
            .set_duration(Duration.DAY)
            .set_order_type(OrderType.LIMIT)
            .set_price('14.97')
            .add_equity_leg(EquityInstruction.BUY, 'XYZ', 5)
            .add_child_order_strategy(
                OrderBuilder()
                .set_order_strategy_type(OrderStrategyType.OCO)
                .add_child_order_strategy(
                    OrderBuilder()
                    .set_order_strategy_type(OrderStrategyType.SINGLE)
                    .set_session(Session.NORMAL)
                    .set_duration(Duration.GOOD_TILL_CANCEL)
                    .set_order_type(OrderType.LIMIT)
                    .set_price('15.27')
                    .add_equity_leg(EquityInstruction.SELL, 'XYZ', 5))
                .add_child_order_strategy(
                    OrderBuilder()
                    .set_order_strategy_type(OrderStrategyType.SINGLE)
                    .set_session(Session.NORMAL)
                    .set_duration(Duration.GOOD_TILL_CANCEL)
                    .set_order_type(OrderType.STOP)
                    .set_stop_price('11.27')
                    .add_equity_leg(EquityInstruction.SELL, 'XYZ', 5))))

        expected = {
            'orderStrategyType': 'TRIGGER',
            'session': 'NORMAL',
            'duration': 'DAY',
            'orderType': 'LIMIT',
            'price': '14.97',
            'orderLegCollection': [
                {
                    'instruction': 'BUY',
                    'quantity': 5,
                    'instrument': {
                        'assetType': 'EQUITY',
                        'symbol': 'XYZ'
                    }
                }
            ],
            'childOrderStrategies': [
                {
                    'orderStrategyType': 'OCO',
                    'childOrderStrategies': [
                        {
                            'orderStrategyType': 'SINGLE',
                            'session': 'NORMAL',
                            'duration': 'GOOD_TILL_CANCEL',
                            'orderType': 'LIMIT',
                            'price': '15.27',
                            'orderLegCollection': [
                                {
                                    'instruction': 'SELL',
                                    'quantity': 5,
                                    'instrument': {
                                        'assetType': 'EQUITY',
                                        'symbol': 'XYZ'
                                    }
                                }
                            ]
                        },
                        {
                            'orderStrategyType': 'SINGLE',
                            'session': 'NORMAL',
                            'duration': 'GOOD_TILL_CANCEL',
                            'orderType': 'STOP',
                            'stopPrice': '11.27',
                            'orderLegCollection': [
                                {
                                    'instruction': 'SELL',
                                    'quantity': 5,
                                    'instrument': {
                                        'assetType': 'EQUITY',
                                        'symbol': 'XYZ'
                                    }
                                }
                            ]
                        }
                    ]
                }
            ]
        }

        self.assertFalse(has_diff(expected, builder.build()))

    @no_duplicates
    def test_sell_trailing_stop_stock(self):
        builder = (
            OrderBuilder()
            .set_complex_order_strategy_type(ComplexOrderStrategyType.NONE)
            .set_order_type(OrderType.TRAILING_STOP)
            .set_session(Session.NORMAL)
            .set_stop_price_link_basis(StopPriceLinkBasis.BID)
            .set_stop_price_link_type(StopPriceLinkType.VALUE)
            .set_stop_price_offset(10)
            .set_duration(Duration.DAY)
            .set_order_strategy_type(OrderStrategyType.SINGLE)
            .add_equity_leg(EquityInstruction.SELL, 'XYZ', 10))

        expected = {
            'complexOrderStrategyType': 'NONE',
            'orderType': 'TRAILING_STOP',
            'session': 'NORMAL',
            'stopPriceLinkBasis': 'BID',
            'stopPriceLinkType': 'VALUE',
            'stopPriceOffset': 10,
            'duration': 'DAY',
            'orderStrategyType': 'SINGLE',
            'orderLegCollection': [
                {
                    'instruction': 'SELL',
                    'quantity': 10,
                    'instrument': {
                        'symbol': 'XYZ',
                        'assetType': 'EQUITY'
                    }
                }
            ]
        }

        self.assertFalse(has_diff(expected, builder.build()))


class PricesAreStringsTest(unittest.TestCase):
    '''Prices used to be accepted as floats and converted here, truncating to
    the venue's tick size. That conversion is gone.

    It was removed rather than fixed because even done correctly -- and it was
    not, for a while: truncating the binary value sent roughly one in twenty
    cent-granular prices a tick low, silently -- the library was choosing a
    rounding on the caller's behalf for a value denominated in money. The
    caller knows whether their price should round up, down or to the nearest
    tick, and this cannot.'''

    def setUp(self):
        self.order_builder = OrderBuilder()

    @no_duplicates
    def test_a_float_price_is_refused(self):
        for setter in ('set_price', 'set_stop_price'):
            for value in (12.34, 0, 199, 8.2):
                builder = OrderBuilder()
                with self.assertRaises(ValueError, msg='{}({!r})'.format(
                        setter, value)) as cm:
                    getattr(builder, setter)(value)
                self.assertIn('must be a string', str(cm.exception))

    @no_duplicates
    def test_the_error_says_what_to_do_instead(self):
        # A caller hitting this is mid-migration and wants the fix, not a
        # restatement of the type.
        with self.assertRaises(ValueError) as cm:
            self.order_builder.set_price(8.2)
        message = str(cm.exception)
        self.assertIn('format', message)
        self.assertIn('copy_price', message)

    @no_duplicates
    def test_a_string_price_is_stored_exactly_as_given(self):
        # The whole point: what the caller wrote is what Schwab receives. 8.2
        # is the value the old binary truncation turned into '8.19'.
        for value in ('8.20', '8.2', '0.1869', '199.99', '0.00'):
            builder = OrderBuilder()
            builder.set_price(value)
            self.assertEqual(value, builder.build()['price'])

    @no_duplicates
    def test_a_decimal_price_is_accepted_exactly(self):
        # Decimal is the type that avoids the float problem rather than hiding
        # it, so it is taken as given -- rendering it decides nothing.
        for value, expected in (('19.99', '19.99'), ('0.1869', '0.1869'),
                                ('-5.00', '-5.00'), ('0.00', '0.00')):
            builder = OrderBuilder()
            builder.set_price(decimal.Decimal(value))
            self.assertEqual(expected, builder.build()['price'])

    @no_duplicates
    def test_a_decimal_never_renders_in_scientific_notation(self):
        # str(Decimal('1E+2')) is '1E+2', which is not a price. format(d, 'f')
        # is what keeps that out of an order.
        builder = OrderBuilder()
        builder.set_price(decimal.Decimal('1E+2'))
        self.assertEqual('100', builder.build()['price'])

    @no_duplicates
    def test_a_decimal_with_real_price_precision_is_kept(self):
        # The guard must not catch prices anyone actually sends. Schwab's
        # sub-penny prices and option strikes stop at four places.
        for value in ('19.99', '0.1869', '1234.5678', '0.00012345', '100'):
            builder = OrderBuilder()
            builder.set_price(decimal.Decimal(value))
            self.assertEqual(value, builder.build()['price'])

    @no_duplicates
    def test_a_non_finite_decimal_is_refused_including_the_signalling_nan(self):
        # NaN and the infinities have a non-integer Decimal exponent, so they
        # skip the decimal-places guard. format() renders them as text and
        # _assert_finite reads a signalling NaN as "not a number at all" and
        # lets it past, so Decimal('sNaN') reached an order as the string
        # 'sNaN' until this was checked explicitly.
        for spelling in ('NaN', 'sNaN', 'Infinity', '-Infinity'):
            for setter in ('set_price', 'set_stop_price'):
                builder = OrderBuilder()
                with self.assertRaises(ValueError, msg=spelling) as cm:
                    getattr(builder, setter)(decimal.Decimal(spelling))
                self.assertIn('finite', str(cm.exception))

    @no_duplicates
    def test_copy_price_accepts_a_decimal(self):
        # copy_* skip validation, so a Decimal reaches _build_object raw. It
        # has no __dict__, so without a branch there it fell through to vars()
        # and raised `vars() argument must have __dict__ attribute` at build()
        # time -- far from the call, naming neither field nor type.
        for setter, field in (('copy_price', 'price'),
                              ('copy_stop_price', 'stopPrice')):
            builder = OrderBuilder()
            getattr(builder, setter)(decimal.Decimal('19.99'))
            self.assertEqual('19.99', builder.build()[field])

    @no_duplicates
    def test_copy_price_refuses_a_non_finite_decimal(self):
        # copy_* validate nothing by contract, but rendering a non-finite
        # Decimal produces the well-formed string "NaN", which serializes and
        # transmits. The float escape hatch is not equivalent: a bare NaN is
        # invalid JSON that json.dumps refuses, so it cannot quietly become an
        # order. This one could.
        for spelling in ('NaN', 'sNaN', 'Infinity', '-Infinity'):
            for setter in ('copy_price', 'copy_stop_price'):
                builder = OrderBuilder()
                with self.assertRaises(ValueError, msg=spelling):
                    getattr(builder, setter)(decimal.Decimal(spelling))

    @no_duplicates
    def test_a_decimal_in_a_numeric_field_is_refused_at_the_setter(self):
        # Refused where the field is still known. Rejecting it at build()
        # named the value but not which of several numeric fields held it.
        for setter in ('set_quantity', 'set_stop_price_offset',
                       'set_activation_price', 'set_price_offset'):
            builder = OrderBuilder()
            with self.assertRaises(ValueError, msg=setter) as cm:
                getattr(builder, setter)(decimal.Decimal('10'))
            self.assertIn('number in Schwab', str(cm.exception))

    @no_duplicates
    def test_the_builder_refuses_a_decimal_it_cannot_serialize(self):
        # The setters catch this first, so this exercises the backstop
        # directly. It exists because a Decimal has no __dict__: without a
        # branch for it, the object builder falls to its reflection path and
        # raises `vars() argument must have __dict__ attribute`, naming
        # neither the field nor the type. Rendering every Decimal as a string
        # instead was worse -- Schwab types quantity, stopPriceOffset and
        # activationPrice as numbers, so they went out as "10" and "0.50".
        builder = OrderBuilder()
        builder._quantity = decimal.Decimal('10')
        with self.assertRaises(ValueError) as cm:
            builder.build()
        self.assertIn('Only prices', str(cm.exception))

    @no_duplicates
    def test_no_setter_lets_a_non_finite_decimal_through(self):
        # _assert_finite read float(Decimal('sNaN')) raising ValueError as
        # "not a number at all" and returned, so every numeric setter passed a
        # signalling NaN to a `<= 0` comparison that raised a bare
        # decimal.InvalidOperation -- the failure it runs first to prevent.
        for setter in ('set_price', 'set_stop_price', 'set_quantity',
                       'set_activation_price', 'set_stop_price_offset',
                       'set_price_offset'):
            for spelling in ('sNaN', 'NaN', 'Infinity', '-Infinity'):
                builder = OrderBuilder()
                with self.assertRaises(
                        ValueError, msg='{}({})'.format(setter, spelling)):
                    getattr(builder, setter)(decimal.Decimal(spelling))

    @no_duplicates
    def test_decimal_depth_is_not_policed(self):
        # A guard on decimal places was tried and removed. Schwab types both
        # price fields number($double), so a long spelling parses to the same
        # double a short one does -- unreadable, not wrong -- and no digit
        # count separates a float's expansion from honest arithmetic: measured
        # over realistic inputs, float-derived values span 0 to 53 places and
        # string arithmetic spans 1 to 9. Policing depth therefore refused
        # valid computed limits to prevent a merely long payload.
        cases = (
            # an ordinary computed limit: a 0.025% offset on a four-place quote
            decimal.Decimal('123.4567') * decimal.Decimal('1.00025'),
            # two four-place factors, which leaves trailing zeros
            decimal.Decimal('100.00') * decimal.Decimal('1.0500')
            * decimal.Decimal('1.0500'),
            # the float expansion the guard existed to catch
            decimal.Decimal(0.1),
        )

        for value in cases:
            builder = OrderBuilder()
            builder.set_price(value)
            sent = builder.build()['price']
            self.assertEqual(
                    float(value), float(sent),
                    '{} was altered on the way through'.format(value))

    @no_duplicates
    def test_copy_stop_price_names_its_own_field(self):
        # Both copy_ setters share a renderer. It hardcoded 'price', so an
        # order setting both told the caller the wrong one had been rejected.
        with self.assertRaises(ValueError) as cm:
            OrderBuilder().copy_stop_price(decimal.Decimal('NaN'))
        self.assertIn('stop price', str(cm.exception))

    @no_duplicates
    def test_the_error_does_not_recommend_a_rounding_conversion(self):
        # '{:.2f}'.format rounds where the removed conversion truncated, so
        # recommending it as a drop-in would move prices by a tick. The message
        # has to say so rather than suggest it bare.
        with self.assertRaises(ValueError) as cm:
            OrderBuilder().set_price(8.2)
        self.assertIn('rounds', str(cm.exception))
        self.assertIn('truncate', str(cm.exception))

    @no_duplicates
    def test_copy_price_still_takes_anything(self):
        # The documented escape hatch, for rebuilding a historical order
        # exactly as the venue reported it.
        self.order_builder.copy_price(12.34)
        self.assertEqual(12.34, self.order_builder.build()['price'])


class NonFinitePriceTest(unittest.TestCase):
    '''A price names a finite number of currency units. NaN and the infinities
    do not, and they arrive by computation rather than by typing -- dividing by
    a size that turned out to be zero, or deriving a limit from a quote that
    was missing. The builder already refuses a non-positive quantity, so
    refusing these is the same check applied to the other half of the order.'''

    def setUp(self):
        self.order_builder = OrderBuilder()

    @no_duplicates
    def test_non_finite_price_is_refused_whichever_check_catches_it(self):
        # As a float it is refused for being a float; as a string it is refused
        # for not naming a finite number of dollars. Neither reaches an order.
        for setter in ('set_price', 'set_stop_price'):
            for value in (float('nan'), float('inf'), float('-inf'),
                          'nan', 'inf', '-inf'):
                builder = OrderBuilder()
                with self.assertRaises(ValueError, msg='{}({!r})'.format(
                        setter, value)):
                    getattr(builder, setter)(value)

    @no_duplicates
    def test_non_finite_price_as_string_rejected(self):
        # str(float('nan')) is 'nan', so passing a computed price through str()
        # -- the documented way to pass prices -- reaches here just as easily.
        for value in ('nan', 'NaN', 'inf', '-inf', 'Infinity'):
            with self.assertRaises(ValueError) as cm:
                self.order_builder.set_price(value)
            self.assertIn('finite', str(cm.exception))

    @no_duplicates
    def test_ordinary_string_prices_still_pass_through_untouched(self):
        for value in ('0.07', '421.35', '1.0050', '-5.00', '0'):
            self.order_builder.set_price(value)
            self.assertEqual(value, self.order_builder.build()['price'])

    @no_duplicates
    def test_non_numeric_strings_are_still_the_callers_business(self):
        # This library does not validate that a price is well formed; Schwab
        # rejects what it does not like. Only values which are definitely not
        # a number of dollars are refused here.
        self.order_builder.set_price('abc')
        self.assertEqual('abc', self.order_builder.build()['price'])

    @no_duplicates
    def test_every_numeric_setter_refuses_non_finite(self):
        # A price is not the only number in an order which can arrive by
        # computation. A trailing stop offset derived from a quote that came
        # back empty reaches set_stop_price_offset the same way.
        # set_price and set_stop_price take strings, so a non-finite float
        # trips the type check first; they are swept as strings instead.
        numeric_setters = ('set_activation_price', 'set_stop_price_offset',
                           'set_price_offset', 'set_quantity')
        string_setters = ('set_price', 'set_stop_price')

        for name in numeric_setters:
            for value in (float('nan'), float('inf'), float('-inf')):
                builder = OrderBuilder()
                with self.assertRaises(ValueError, msg='{}({!r})'.format(
                        name, value)) as cm:
                    getattr(builder, name)(value)
                self.assertIn('finite', str(cm.exception))

        for name in string_setters:
            for value in ('nan', 'inf', '-inf'):
                builder = OrderBuilder()
                with self.assertRaises(ValueError, msg='{}({!r})'.format(
                        name, value)) as cm:
                    getattr(builder, name)(value)
                self.assertIn('finite', str(cm.exception))

    @no_duplicates
    def test_nan_defeats_the_positivity_guards(self):
        # NaN compares False against everything, so `quantity <= 0` and
        # `activation_price <= 0.0` do not fire for it. The finite check has to
        # run first or those guards are simply bypassed.
        self.assertFalse(float('nan') <= 0)

        for name in ('set_quantity', 'set_activation_price'):
            builder = OrderBuilder()
            with self.assertRaises(ValueError):
                getattr(builder, name)(float('nan'))

    @no_duplicates
    def test_a_built_order_is_always_serializable(self):
        # json.dumps emits a bare NaN, which is not valid JSON, so an order
        # carrying one is not merely wrong -- it cannot be transmitted as JSON
        # at all without a parser willing to accept a non-standard token.
        import json

        builder = OrderBuilder()
        for name in ('set_quantity', 'set_activation_price',
                     'set_stop_price_offset'):
            try:
                getattr(builder, name)(float('nan'))
            except ValueError:
                pass

        encoded = json.dumps(builder.build(), allow_nan=False)
        self.assertNotIn('NaN', encoded)

    @no_duplicates
    def test_ordinary_values_still_pass_through(self):
        builder = OrderBuilder()
        builder.set_quantity(10)
        builder.set_activation_price(42.35)
        builder.set_stop_price_offset(1.5)
        built = builder.build()
        self.assertEqual(10, built['quantity'])
        self.assertEqual(42.35, built['activationPrice'])
        self.assertEqual(1.5, built['stopPriceOffset'])

    @no_duplicates
    def test_copy_price_still_bypasses_validation(self):
        # copy_price documents itself as skipping the validation, and callers
        # reconstructing a historical order rely on that.
        self.order_builder.copy_price(float('nan'))
        self.assertNotEqual(None, self.order_builder.build()['price'])


class NonNumericOrderFieldTest(unittest.TestCase):
    '''The numeric order fields, given something that is not a number.

    `_assert_finite` used to answer "can float() parse it" and return quietly
    when the answer was no. That is the wrong question in both directions, and
    the two directions failed differently:

    Things that are not numbers answer no. `set_quantity(None)` then reached
    `quantity <= 0` and raised a bare TypeError naming neither the field nor
    the value. The two offset setters have no comparison after the call at all,
    so they *accepted* it -- and `_build_object` drops a None, so
    `set_stop_price_offset(None)` built an order with no offset on it and said
    nothing. A trailing stop with no offset is a different order from the one
    that was asked for, placed without an error.

    And things that are not numbers answer yes. `float(b'1')` is 1.0, so a
    bytes passed and was stored as bytes; `json.dumps` refuses those, so the
    order was not wrong so much as unsendable, discovered at the point of
    sending it.
    '''

    SETTERS = ('set_quantity', 'set_stop_price_offset', 'set_price_offset',
               'set_activation_price')

    # `object()` is in here on purpose: it is the case that answers "no" to
    # both questions, so a fix which only catches None and lists still passes
    # for it, and one which only catches the un-floatable still misses bytes.
    NOT_NUMBERS = (None, [], {}, b'1', object())

    # Strings are refused too, but for a different stated reason -- Schwab
    # types these fields as numbers, so a string is the wrong type rather than
    # a missing value -- and by a different branch. Kept separate so a fix to
    # one branch cannot make the other's assertion pass by accident.
    NOT_NUMBERS_BUT_STRINGS = ('1.5', 'abc')

    @no_duplicates
    def test_a_non_number_is_refused_and_the_message_names_the_field(self):
        for setter in self.SETTERS:
            for value in self.NOT_NUMBERS:
                with self.subTest(setter=setter, value=value):
                    builder = OrderBuilder()
                    with self.assertRaises(ValueError) as ctx:
                        getattr(builder, setter)(value)
                    # The field name is the point. A bare TypeError from the
                    # comparison two lines later says only that NoneType and
                    # int do not compare.
                    field = setter[len('set_'):].replace('_', ' ')
                    self.assertIn(field, str(ctx.exception))

    @no_duplicates
    def test_a_string_is_refused_on_a_field_schwab_types_as_a_number(self):
        # These reached `_assert_finite`'s string branch, which exists for the
        # price fields and returns quietly for anything it cannot parse. The
        # offsets have no comparison after the call, so `set_price_offset('abc')`
        # built `{"priceOffset": "abc"}` and sent it.
        for setter in self.SETTERS:
            for value in self.NOT_NUMBERS_BUT_STRINGS:
                with self.subTest(setter=setter, value=value):
                    builder = OrderBuilder()
                    with self.assertRaises(ValueError) as ctx:
                        getattr(builder, setter)(value)
                    self.assertIn('not a price string', str(ctx.exception))
                    self.assertEqual({}, builder.build())

    @no_duplicates
    def test_the_price_fields_still_take_a_string(self):
        # The other half of the same change, and the reason `_assert_finite`
        # takes a flag rather than deciding from the value: a price field is a
        # string in Schwab's schema and a numeric field is not.
        builder = OrderBuilder()
        builder.set_price('12.34')
        builder.set_stop_price(decimal.Decimal('5.6'))
        self.assertEqual({'price': '12.34', 'stopPrice': '5.6'},
                         builder.build())

    @no_duplicates
    def test_the_offsets_do_not_silently_accept_one(self):
        # Separate from the assertion above because these two are the ones
        # that raised nothing at all: the failure was an order that built
        # cleanly with the field missing, not an exception of the wrong type.
        for setter in ('set_stop_price_offset', 'set_price_offset'):
            with self.subTest(setter=setter):
                builder = OrderBuilder()
                with self.assertRaises(ValueError):
                    getattr(builder, setter)(None)
                self.assertEqual({}, builder.build())

    @no_duplicates
    def test_numbers_are_still_accepted(self):
        # The positive control. Every assertion above passes just as well
        # against a setter that refuses everything.
        builder = OrderBuilder()
        builder.set_quantity(10)
        builder.set_stop_price_offset(1.5)
        builder.set_price_offset(2)
        builder.set_activation_price(42.35)
        self.assertEqual(
                {'quantity': 10, 'stopPriceOffset': 1.5, 'priceOffset': 2,
                 'activationPrice': 42.35},
                builder.build())

    @no_duplicates
    def test_a_built_order_still_serialises(self):
        # What the bytes case actually broke. float(b'1') is 1.0, so the guard
        # passed and the bytes went into the order; the failure was json.
        import json
        builder = OrderBuilder()
        builder.set_quantity(10)
        builder.set_stop_price_offset(1.5)
        json.dumps(builder.build(), allow_nan=False)
