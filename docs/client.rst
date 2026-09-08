.. highlight:: python
.. py:module:: schwab.client


.. _client:

===========
HTTP Client
===========

A naive, unopinionated wrapper around the
`Schwab individual trader API
<https://developer.schwab.com/products/trader-api--individual>`_. This
client provides access to all endpoints of the API in as easy and direct a way
as possible.


**Do not attempt to use more than one Client object per token file, as
this will likely cause issues with the underlying OAuth2 session management**

.. code-block:: python

  import httpx2

  from schwab.auth import easy_client

  # Follow the instructions on the screen to authenticate your client.
  c = easy_client(
          api_key='APIKEY',
          app_secret='APP_SECRET',
          callback_url='https://127.0.0.1',
          token_path='/tmp/token.json')

  resp = c.get_price_history_every_day('AAPL')
  assert resp.status_code == httpx2.codes.OK
  history = resp.json()

Note that we create a new client using the ``auth`` package as described in
:ref:`auth`. Creating a client directly is possible, but not recommended.

+++++++++++++++++++
Asyncio Support
+++++++++++++++++++

An asynchronous variant is available through a keyword to the client
constructor. This allows for higher-performance API usage, at the cost
of slightly increased application complexity.

.. code-block:: python

  import httpx2

  from schwab.auth import easy_client

  async def main():
      c = easy_client(
              api_key='APIKEY',
              app_secret='APP_SECRET',
              callback_url='https://127.0.0.1:8182',
              token_path='/tmp/token.json',
              asyncio=True)

      resp = await c.get_price_history_every_day('AAPL')
      assert resp.status_code == httpx2.codes.OK
      history = resp.json()

  if __name__ == '__main__':
      import asyncio
      asyncio.run(main())

Both clients hold an HTTP session with open connections, and both can be told
to let go of it:

.. code-block:: python

  c.close_session()          # synchronous client
  await c.close_async_session()   # asyncio client

Neither is worth calling if you create one client and keep it for the life of
the process, which is what most applications do. They matter when clients are
created repeatedly, where the connections would otherwise be released only
whenever the session happens to be garbage collected. A client cannot be used
after being closed.

+++++++++++++++++++
Calling Conventions
+++++++++++++++++++

Function parameters are categorized as either required or optional.  Required
parameters are passed as positional arguments.  Optional parameters are passed
as keyword arguments.

Parameters which have special values recognized by the API are
represented by `Python enums <https://docs.python.org/3/library/enum.html>`_.
This is because the API rejects requests which pass unrecognized values, and
this enum wrapping is provided as a convenient mechanism to avoid consternation
caused by accidentally passing an unrecognized value.

By default, passing values other than the required enums will raise a
``ValueError``. If you believe the API accepts a value that isn't supported
here, you can use ``set_enforce_enums`` to disable this behavior at your own
risk. If you *do* find a supported value that isn't listed here, please open an
issue describing it or submit a PR adding the new functionality.


++++++++++++++++++++++
Dates and Times
++++++++++++++++++++++

**Give datetimes a timezone.** Every parameter below which takes a
``datetime`` names a moment in time, and a ``datetime`` carrying no ``tzinfo``
does not name one -- it is a wall clock reading with no indication of whose
wall it is on.

Passing one anyway is ambiguous, and the two encodings Schwab uses resolve that
ambiguity differently. The epoch-millisecond parameters, which the price
history endpoints use, read a naive datetime as the local time of whichever
machine is running:

.. code-block:: python

  # The same source line, run in two places.
  c.get_price_history_every_minute(
          'AAPL',
          start_datetime=datetime.datetime(2026, 8, 6, 9, 30),
          end_datetime=datetime.datetime(2026, 8, 6, 16, 0))

  # on a host set to UTC:                startDate=1786008600000
  # on a host set to America/New_York:   startDate=1786023000000

Four hours apart. Nothing in the request records which was meant, so a
container and a laptop quietly disagree about what a given window contains.
The ISO-8601 parameters -- ``from_entered_datetime``, ``to_entered_datetime``
and the transaction dates -- are ambiguous in their own way, sending the wall
clock as written and labelling it UTC.

Attaching a timezone removes the question entirely, and any of these will do:

.. code-block:: python

  import datetime
  import zoneinfo

  # Explicit UTC
  datetime.datetime(2026, 8, 6, 13, 30, tzinfo=datetime.timezone.utc)

  # Market local time, which is usually what you actually mean
  datetime.datetime(2026, 8, 6, 9, 30,
                    tzinfo=zoneinfo.ZoneInfo('America/New_York'))

  # Whatever this machine is set to, but said out loud rather than assumed
  datetime.datetime(2026, 8, 6, 9, 30).astimezone()

An aware datetime produces the same request everywhere, whichever timezone it
carries. Passing a naive one emits a warning naming the parameter. Plain
``date`` objects are unaffected, having no time of day to be ambiguous about.


+++++++++++++
Return Values
+++++++++++++

All methods return a response object generated under the hood by the
`HTTPX2 <https://github.com/pydantic/httpx2/blob/main/docs/quickstart.md>`__ module.
For a full listing of what's possible, read that module's documentation. Most if
not all users can simply use the following pattern:

.. code-block:: python

  r = client.some_endpoint()
  assert r.status_code == httpx2.codes.OK, r.raise_for_status()
  data = r.json()

.. warning::

  **Catch** ``httpx2`` **exceptions, not** ``httpx`` **ones.** This library is
  built on ``httpx2``, and ``httpx`` is not a dependency at all. The two share
  no exception hierarchy, so this catches nothing:

  .. code-block:: python

    import httpx           # the wrong module

    try:
        r = client.get_quote('AAPL')
        r.raise_for_status()
    except httpx.HTTPStatusError:      # never fires
        retry()

  It fails in the worst way available: nothing raises at import, nothing raises
  at the ``try``, and the ``except`` simply never matches --- so the retry never
  runs and the exception propagates to whatever is above. A handler written this
  way around rate limiting presents as an unhandled exception during a burst of
  429s. Import ``httpx2`` and catch ``httpx2.HTTPStatusError`` instead.

The API indicates errors using the response status code, and this pattern will
raise the appropriate exception if the response is not a success. The data can
be fetched by calling the ``.json()`` method.

This data will be pure python data structures which can be directly accessed.
You can also use your favorite data analysis library's dataframe format using
the appropriate library. For instance you can create a `pandas
<https://pandas.pydata.org/>`__ dataframe using `its conversion method
<https://pandas.pydata.org/pandas-docs/stable/reference/api/pandas.DataFrame.from_dict.html>`__.

**Note:** Because this project has no relationship whatsoever with Charles Schwab,
this document makes no effort to describe the structure of the returned JSON
objects. Schwab might change them at any time, at which point this document will
become silently out of date. Instead, each of the methods described below
contains a link to the official documentation. For endpoints that return
meaningful JSON objects, it includes a JSON schema which describes the return
value. Please use that documentation or your own experimentation when figuring
out how to use the data returned by this API.


.. _account_hashes:

++++++++++++++
Account Hashes
++++++++++++++

Many methods of this API are parametrized by account. However, the API does not
accept raw account numbers, but rather account hashes. You can fetch these
hashes using the ``get_account_numbers`` method :ref:`(link)
<account_hashes_method>`.  This method provides a mapping from raw account
number to the account hash that must be passed when referring to that account in
API calls.

Here is an example of how to fetch an account hash and use it to place an order:

.. code-block:: python

  import httpx2

  from schwab.auth import easy_client
  from schwab.orders.equities import equity_buy_market

  c = easy_client(
          api_key='api-key',
          app_secret='app-secret',
          callback_url='https://127.0.0.1:8182',
          token_path='/path/to/token.json')

  resp = c.get_account_numbers()
  assert resp.status_code == httpx2.codes.OK

  # The response has the following structure. If you have multiple linked
  # accounts, you'll need to inspect this object to find the hash you want:
  # [
  #    {
  #        "accountNumber": "123456789",
  #        "hashValue":"123ABCXYZ"
  #    }
  #]
  account_hash = resp.json()[0]['hashValue']

  c.place_order(account_hash, equity_buy_market('AAPL', 1))



++++++++++++++++++
Timeout Management
++++++++++++++++++

Timeouts for HTTP calls are managed under the hood by the ``httpx2`` library.
``schwaby`` defaults to 30 seconds, which experience has shown should be more
than enough to allow even the slowest API calls to complete. A different timeout
specification can be set using this method:

.. automethod:: schwab.client.Client.set_timeout


+++++++++
Token Age
+++++++++

.. automethod:: schwab.client.Client.token_age


++++++++++++
Account Info
++++++++++++

These methods provide access to useful information about accounts. An incomplete
list of the most interesting bits:

* Account balances, including available trading balance
* Positions
* Order history

See the official documentation for each method for a complete response schema.

.. _account_hashes_method:

.. automethod:: schwab.client.Client.get_account_numbers
.. automethod:: schwab.client.Client.get_account
.. automethod:: schwab.client.Client.get_accounts
.. autoclass:: schwab.client.Client.Account
  :members:
  :undoc-members:


+++++++++++++
Price History
+++++++++++++

Schwab provides price history for equities and ETFs. It does not provide price
history for options, futures, or any other instruments.

In the raw API, fetching price history is somewhat complicated: the API offers a
single endpoint :meth:`Client.get_price_history` that accepts a complex variety
of inputs, but fails to document them in any meaningful way.

Thankfully, we've reverse engineered this endpoint and built some helpful
utilities for fetching prices by minute, day, week, etc. Each method can be
called with or without date bounds. When called without date bounds, it returns
all data available. Each method offers a different lookback period, so make sure
to read the documentation below to learn how much data is available.

.. note::

   Give any of these a ``start_datetime`` or an ``end_datetime`` and you get
   that range. No ``period`` is sent alongside it:
   :meth:`Client.get_price_history` documents that the two should not be
   combined, and what Schwab does when they are is not consistent across
   accounts --- it has been reported disregarding the range, and measured
   honouring it.


.. automethod:: schwab.client.Client.get_price_history_every_minute
.. automethod:: schwab.client.Client.get_price_history_every_five_minutes
.. automethod:: schwab.client.Client.get_price_history_every_ten_minutes
.. automethod:: schwab.client.Client.get_price_history_every_fifteen_minutes
.. automethod:: schwab.client.Client.get_price_history_every_thirty_minutes
.. automethod:: schwab.client.Client.get_price_history_every_day
.. automethod:: schwab.client.Client.get_price_history_every_week

For the sake of completeness, here is the documentation for the raw price
history endpoint, in all its complexity.

.. automethod:: schwab.client.Client.get_price_history
.. autoclass:: schwab.client.Client.PriceHistory
  :members:
  :undoc-members:
  :member-order: bysource

++++++++++++++
Current Quotes
++++++++++++++

.. automethod:: schwab.client.Client.get_quote
.. automethod:: schwab.client.Client.get_quotes

.. _option_chain:

+++++++++++++
Option Chains
+++++++++++++

This page does not attempt to explain options themselves. The method below
covers every parameter Schwab's endpoint accepts; for what they mean, read
Schwab's own documentation.

If you know the subject well enough to write something more substantive here,
:ref:`contributing` has the instructions.

.. automethod:: schwab.client.Client.get_option_chain
.. autoclass:: schwab.client.Client.Options
  :members:
  :undoc-members:

If you only need to know *when* contracts expire rather than what they cost,
there is a cheaper call which returns the expiration list on its own:

.. automethod:: schwab.client.Client.get_option_expiration_chain

+++++++++++++++++++++++++++++++++++++
Instrument Searching and Fundamentals
+++++++++++++++++++++++++++++++++++++

.. automethod:: schwab.client.Client.get_instruments
.. automethod:: schwab.client.Client.get_instrument_by_cusip
.. autoclass:: schwab.client.Client.Instrument
  :members:
  :undoc-members:

.. _orders-section:

++++++
Orders
++++++


.. _placing_new_orders:

------------------
Placing New Orders
------------------

Placing new orders can be a complicated task. The :meth:`Client.place_order`
method is used to create all orders, from equities to options. The precise order
type is defined by a complex order spec. Schwab provides some `example order
specs`_ to illustrate the process and provides a schema in the `place order
documentation
<https://developer.schwab.com/products/trader-api--individual/details/specifications/Retail%20Trader%20API%20Production>`__,
but beyond that we're on our own.

``schwaby`` includes some helpers, described in :ref:`order_templates`, which
provide an incomplete utility for creating various order types. While it only
scratches the surface of what's possible, we encourage you to use that module
instead of creating your own order specs.

.. _`example order specs`: https://developer.schwab.com/products/trader-api--individual/details/documentation/Retail%20Trader%20API%20Production

.. automethod:: schwab.client.Client.place_order

**Testing an order without placing it.** Schwab will tell you whether it would
accept an order, and what it would become, without sending it to the market.
That is worth doing the first time you construct an order type programmatically
--- a rejection here costs nothing, while a malformed order discovered at
placement time costs an execution window.

.. automethod:: schwab.client.Client.preview_order

.. _accessing_existing_orders:

-------------------------
Accessing Existing Orders
-------------------------

.. automethod:: schwab.client.Client.get_orders_for_account
.. automethod:: schwab.client.Client.get_orders_for_all_linked_accounts
.. automethod:: schwab.client.Client.get_order
.. autoclass:: schwab.client.Client.Order
  :members:
  :undoc-members:

-----------------------
Editing Existing Orders
-----------------------

Endpoints for canceling and replacing existing orders.

These endpoints require the order ID. Because the API does not return a JSON
response when creating an order, the workflow for extracting this order ID is a
little complicated.  You can fetch the order ID from the response to a
:meth:`place_order <schwab.client.Client.place_order>` request using :ref:`this
helper function <extract_order_id>`. Otherwise, see
:ref:`accessing_existing_orders` for finding historical orders.

.. automethod:: schwab.client.Client.cancel_order
.. automethod:: schwab.client.Client.replace_order


+++++++++++++++
Other Endpoints
+++++++++++++++

Note: if your account is limited to delayed quotes, these quotes are
delayed too.

-------------------
Transaction History
-------------------

.. automethod:: schwab.client.Client.get_transaction
.. automethod:: schwab.client.Client.get_transactions
.. autoclass:: schwab.client.Client.Transactions
  :members:
  :undoc-members:

----------------
User Preferences
----------------

.. automethod:: schwab.client.Client.get_user_preferences

-------------
Market Movers
-------------

.. automethod:: schwab.client.Client.get_movers
.. autoclass:: schwab.client.Client.Movers
  :members:
  :undoc-members:


------------
Market Hours
------------

.. automethod:: schwab.client.Client.get_market_hours
.. autoclass:: schwab.client.Client.MarketHours
  :members:
  :undoc-members:

