.. py:module:: schwab.orders

.. _order_templates:

===============
Order Templates
===============

``schwaby`` strives to be easy to use. This means making it easy to do simple
things, while making it possible to do complicated things. Order construction is
a major challenge to this mission: both simple and complicated orders use the
same format, meaning simple orders require a surprising amount of sophistication
to place.

We get around this by providing templates that make it easy to place common
orders, while allowing advanced users to modify the orders returned from the
templates to create more complex ones. Very advanced users can even create their
own orders from scratch. This page describes the simple templates, while the
:ref:`order_builder` page documents the order builder in all its complexity.


---------------------
Using These Templates
---------------------

These templates serve two purposes. First, they are designed to choose defaults
so you can immediately :ref:`place them <placing_new_orders>`. These defaults
are:

 * All orders execute during the current normal trading session. If placed
   outside of trading hours, they execute during the next normal trading
   session.
 * Time-in-force is set to ``DAY``.
 * All other fields (such as requested destination, etc.) are left unset,
   meaning they receive default treatment from Schwab. Note this treatment
   depends on Schwab's implementation, and may change without warning.

Secondly, they serve as starting points for building more complex order types.
All templates return a pre-populated ``OrderBuilder`` object, meaning complex
functionality can be specified by modifying the returned object. For example,
here is how you would place an order to buy ``GOOG`` for no more than $1250 at
any time in the next six months:

.. code-block:: python

  from schwab.orders.equities import equity_buy_limit
  from schwab.orders.common import Duration, Session

  client = ... # See "Authentication and Client Creation"

  client.place_order(
      account_hash,  # from client.get_account_numbers(), not the account number
      equity_buy_limit('GOOG', 1, '1250.00')
          .set_duration(Duration.GOOD_TILL_CANCEL)
          .set_session(Session.SEAMLESS)
          .build())

You can find a full reference for all supported fields in :ref:`order_builder`.


----------------
Equity Templates
----------------

++++++++++
Buy orders
++++++++++

.. autofunction:: schwab.orders.equities.equity_buy_market
.. autofunction:: schwab.orders.equities.equity_buy_limit
.. autofunction:: schwab.orders.equities.equity_buy_stop
.. autofunction:: schwab.orders.equities.equity_buy_stop_limit
.. autofunction:: schwab.orders.equities.equity_buy_trailing_stop
.. autofunction:: schwab.orders.equities.equity_buy_trailing_stop_limit
.. autofunction:: schwab.orders.equities.equity_buy_market_on_close
.. autofunction:: schwab.orders.equities.equity_buy_limit_on_close

+++++++++++
Sell orders
+++++++++++

.. autofunction:: schwab.orders.equities.equity_sell_market
.. autofunction:: schwab.orders.equities.equity_sell_limit
.. autofunction:: schwab.orders.equities.equity_sell_stop
.. autofunction:: schwab.orders.equities.equity_sell_stop_limit
.. autofunction:: schwab.orders.equities.equity_sell_trailing_stop
.. autofunction:: schwab.orders.equities.equity_sell_trailing_stop_limit
.. autofunction:: schwab.orders.equities.equity_sell_market_on_close
.. autofunction:: schwab.orders.equities.equity_sell_limit_on_close

+++++++++++++++++
Sell short orders
+++++++++++++++++

.. autofunction:: schwab.orders.equities.equity_sell_short_market
.. autofunction:: schwab.orders.equities.equity_sell_short_limit
.. autofunction:: schwab.orders.equities.equity_sell_short_stop
.. autofunction:: schwab.orders.equities.equity_sell_short_stop_limit
.. autofunction:: schwab.orders.equities.equity_sell_short_trailing_stop
.. autofunction:: schwab.orders.equities.equity_sell_short_trailing_stop_limit
.. autofunction:: schwab.orders.equities.equity_sell_short_market_on_close
.. autofunction:: schwab.orders.equities.equity_sell_short_limit_on_close

+++++++++++++++++++
Buy to cover orders
+++++++++++++++++++

.. autofunction:: schwab.orders.equities.equity_buy_to_cover_market
.. autofunction:: schwab.orders.equities.equity_buy_to_cover_limit
.. autofunction:: schwab.orders.equities.equity_buy_to_cover_stop
.. autofunction:: schwab.orders.equities.equity_buy_to_cover_stop_limit
.. autofunction:: schwab.orders.equities.equity_buy_to_cover_trailing_stop
.. autofunction:: schwab.orders.equities.equity_buy_to_cover_trailing_stop_limit
.. autofunction:: schwab.orders.equities.equity_buy_to_cover_market_on_close
.. autofunction:: schwab.orders.equities.equity_buy_to_cover_limit_on_close


-----------------
Options Templates
-----------------

Schwab supports over a dozen options strategies, each of which involve a precise
structure in the order builder. The templates below cover the strategies that have been implemented and
tested; the rest can be built with the :ref:`OrderBuilder <order_builder>`
directly.

Note orders placed using these templates may be rejected, depending on the
user's options trading authorization.



.. _options_symbols:

++++++++++++++++++++++++
Building Options Symbols
++++++++++++++++++++++++

All templates require option symbols, which are somewhat more involved than
equity symbols. They encode the underlying, the expiration date, option type
(put or call) and the strike price. They are especially tricky to extract
because neither the Schwab UI nor the thinkorswim UI reveals the symbol in the
option chain view.

Real trading symbols can be found by requesting the :ref:`option_chain`. They
can also be built using the ``OptionSymbol`` helper, which provides utilities
for creating options symbols. Note it only emits syntactically correct symbols
and does not validate whether the symbol actually represents a traded option:

.. code-block:: python

  import datetime

  from schwab.orders.options import OptionSymbol

  symbol = OptionSymbol(
      'TSLA', datetime.date(year=2020, month=11, day=20), 'P', '1360').build()

.. autoclass:: schwab.orders.options.OptionSymbol
   :special-members:

.. automethod:: schwab.orders.options.OptionSymbol.build

The reverse is also available: hand it a symbol Schwab gave you and get the
parts back.

.. automethod:: schwab.orders.options.OptionSymbol.parse_symbol


++++++++++++++
Single Options
++++++++++++++

Buy and sell single options.

.. autofunction:: schwab.orders.options.option_buy_to_open_market
.. autofunction:: schwab.orders.options.option_buy_to_open_limit
.. autofunction:: schwab.orders.options.option_sell_to_open_market
.. autofunction:: schwab.orders.options.option_sell_to_open_limit
.. autofunction:: schwab.orders.options.option_buy_to_close_market
.. autofunction:: schwab.orders.options.option_buy_to_close_limit
.. autofunction:: schwab.orders.options.option_sell_to_close_market
.. autofunction:: schwab.orders.options.option_sell_to_close_limit


.. _vertical_spreads:

++++++++++++++++
Vertical Spreads
++++++++++++++++

Vertical spreads are a complex option strategy that provides both limited upside
and limited downside. They are constructed by buying an option at one
strike while simultaneously selling another option with the same underlying and
expiration date, except with a different strike, and they can be constructed
using either puts or calls. There is more on the strategy at
`Investopedia
<https://www.investopedia.com/articles/active-trading/032614/which-vertical-option-spread-should-you-use.asp>`__.

``schwaby`` provides utilities for opening and closing vertical spreads in
various ways. It follows the standard ``(bull/bear) (put/call)`` naming
convention, where the name specifies the market attitude and the option type
used in construction.

For consistency's sake, the option with the smaller strike price is always
passed first, followed by the higher strike option. You can find the option
symbols by consulting the return value of the :ref:`option_chain` client call.


~~~~~~~~~~~~~~
Call Verticals
~~~~~~~~~~~~~~

.. autofunction:: schwab.orders.options.bull_call_vertical_open
.. autofunction:: schwab.orders.options.bull_call_vertical_close
.. autofunction:: schwab.orders.options.bear_call_vertical_open
.. autofunction:: schwab.orders.options.bear_call_vertical_close


~~~~~~~~~~~~~
Put Verticals
~~~~~~~~~~~~~

.. autofunction:: schwab.orders.options.bull_put_vertical_open
.. autofunction:: schwab.orders.options.bull_put_vertical_close
.. autofunction:: schwab.orders.options.bear_put_vertical_open
.. autofunction:: schwab.orders.options.bear_put_vertical_close


---------------
Utility Methods
---------------

These methods return orders that represent complex multi-order strategies,
namely "one cancels other" and "first triggers second" strategies. Note they
expect all their parameters to be of type ``OrderBuilder``. You can construct
these orders using the templates above or by
:ref:`creating them from scratch <order_builder>`.

Pass the ``OrderBuilder`` objects themselves, unbuilt and unplaced:

.. code-block:: python

  from schwab.orders.common import first_triggers_second
  from schwab.orders.equities import equity_buy_limit, equity_sell_limit

  c.place_order(
      account_hash,
      first_triggers_second(
          equity_buy_limit('GOOG', 1, '1250.00'),
          equity_sell_limit('GOOG', 1, '1400.00'),
      ).build())

**Do not place the constituent orders first and pass the results.** This is
the mistake worth naming, because the first half of it succeeds:

.. code-block:: python

  # WRONG: both orders are live before the third line runs
  order_one = c.place_order(account_hash, equity_buy_limit(...).build())
  order_two = c.place_order(account_hash, equity_sell_limit(...).build())

  c.place_order(account_hash, first_triggers_second(order_one, order_two))

By the time the last line fails --- it receives HTTP responses where it
expects ``OrderBuilder`` objects --- both orders have already been placed
independently, with none of the trigger relationship you wanted. An
``OrderBuilder`` describes an order without sending it, which is what makes
the composite possible.

.. autofunction:: schwab.orders.common.one_cancels_other
.. autofunction:: schwab.orders.common.first_triggers_second
