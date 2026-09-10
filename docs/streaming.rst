.. highlight:: python
.. py:module:: schwaby.streaming

.. _stream:


================
Streaming Client
================

A wrapper around the Schwab streaming data API. This is a websockets-based
streaming API that provides up-to-the-second data on market activity,
including Level Two and time-of-sale data for major equities, options and
futures exchanges.

Here's an example of how you can receive book snapshots of ``GOOG`` (note if you
run this outside regular trading hours you may not see anything):

.. code-block:: python

  from schwaby.auth import easy_client
  from schwaby.client import Client
  from schwaby.streaming import StreamClient

  import asyncio
  import json

  # Assumes you've already created a token. See the authentication page for more
  # information.
  client = easy_client(
          api_key='YOUR_API_KEY',
          app_secret='YOUR_APP_SECRET',
          callback_url='https://127.0.0.1',
          token_path='/path/to/token.json')
  stream_client = StreamClient(client)

  async def read_stream():
      await stream_client.login()

      def print_message(message):
        print(json.dumps(message, indent=4))

      # Always add handlers before subscribing because many streams start sending
      # data immediately after success, and messages with no handlers are dropped.
      stream_client.add_nasdaq_book_handler(print_message)
      await stream_client.nasdaq_book_subs(['GOOG'])

      while True:
          await stream_client.handle_message()

  asyncio.run(read_stream())

.. warning::

  **One streaming session per account, and a second one does not fail
  cleanly.** Schwab permits a single stream per set of credentials. Logging in
  a second time does not raise and does not queue --- it takes the connection
  away from the first. If both sides then reconnect, which any sensible
  supervisor will, the two sessions bump each other indefinitely and each sees
  a feed that keeps dying for no local reason.

  This is easy to hit by accident: a research script, a notebook left open, a
  second machine, or a supervised process that restarted while an older one
  was still alive. Nothing in the protocol says which session is the intruder,
  so the symptom appears identically on both.

  If a stream disconnects repeatedly while REST calls on the same token keep
  working, suspect a second subscriber before suspecting the network. Anything
  depending on that feed should treat the condition as a stop rather than a
  warning --- a stream that is being bumped still delivers *some* messages, so
  partial delivery and a healthy quiet market look alike from inside.

  REST is unaffected. ``Client`` calls do not contend with a stream or with
  each other, and can run alongside a streaming session freely.



++++++++++++
Use Overview
++++++++++++

The example above demonstrates the end-to-end workflow for using ``schwaby.stream``.
There's more in there than meets the eye, so let's dive into the details.


----------
Logging In
----------

Before we can perform any stream operations, the client must be logged in to the
stream. Unlike the HTTP client, in which every request is authenticated using a
token, this client sends unauthenticated requests and instead authenticates the
entire stream. As a result, this login process is distinct from the token
generation step that's used in the HTTP client.

Stream login is accomplished simply by calling :meth:`StreamClient.login()`. Once
this happens successfully, all stream operations can be performed. Attempting
to
perform operations that require login before this function is called raises an
exception.

.. automethod:: schwaby.streaming.StreamClient.login

Requests to the streaming server -- logging in, subscribing, logging out -- give
up after ``response_timeout`` seconds if the server accepts the request but
never answers it, raising
:class:`~schwaby.streaming.ResponseTimeoutError`. It defaults to 60 seconds and
can be set per client:

.. code-block:: python

  stream_client = StreamClient(client, response_timeout=30.0)

Pass ``None`` to wait indefinitely. Note that the websockets keepalive does not
cover this case: a connection which is alive but simply not answering keeps
replying to pings, so without a timeout such a request would wait forever.


-----------------------
Logging Out and Closing
-----------------------

For a clean exit, it's recommended to log out of the stream when you're done.
This sends a logout request and then closes the connection.

.. automethod:: schwaby.streaming.StreamClient.logout

If the connection has already failed, or you are tearing a client down without
ceremony, :meth:`close` skips the logout and just closes the socket. It is safe
to call more than once, and safe on a client which was never logged in.

.. automethod:: schwaby.streaming.StreamClient.close

The client is also an async context manager, which closes it on the way out:

.. code-block:: python

  async with StreamClient(client) as stream_client:
      await stream_client.login()
      ...

Closing matters more than it might appear. A client which is never closed leaves
its socket and keepalive task alive until the object is collected, which for a
long-running process means until it exits -- typically leaving the connection to
be finalized during interpreter shutdown, when the event loop may already be
gone.


----------------------
Subscribing to Streams
----------------------

These functions have names that follow the pattern ``SERVICE_NAME_subs``. These
functions send a request to enable streaming data for a particular data stream.
They are *not* thread safe, so they should only be called from one thread.

They can, however, be called while another coroutine is waiting in
``handle_message()``. One request is in flight at a time, and whichever coroutine
is reading the socket hands the response to whoever is waiting for it, so a
subscription made against a quiet stream is sent immediately rather than waiting
for a message to arrive first.

.. _subs_vs_add:

Schwab documents nothing about what a second ``subs`` does to the first. Three
rules account for every frame in a capture made by subscribing repeatedly and
grouping the results by subscription key:

**A** ``subs`` **replaces the subscription for its service.** The keys named in
the previous call stop delivering. There is no acknowledgement of the
replacement and no error --- the old data simply ceases.

**An** ``add`` **appends to it.** Both the earlier keys and the added ones
deliver afterwards.

**Services are independent.** A ``subs`` on ``SCREENER_EQUITY`` does not touch
a live ``SCREENER_OPTION`` subscription, and the two go on pushing at their own
rates side by side.

The first rule has a consequence worth stating on its own: **a** ``subs``
**with a key Schwab silently ignores still replaces a working subscription.**
Schwab acknowledges the new key, delivers nothing for it, and the data you
were receiving is gone. Nothing in the protocol distinguishes that from a
market with nothing to report.

.. note::

  Those three rules are structural and held for every frame in the capture.
  Anything quantitative from the same session --- push rates, how much
  membership moves --- is one nine-minute window and is flagged as such where
  it appears; see :ref:`screener_cadence`.

  A frame count is not a liveness signal, incidentally. The key with by far
  the most frames in that capture had them only because it was subscribed
  longest.


-------------------------
Adding Symbols to Streams
-------------------------

These functions have names that follow the pattern ``SERVICE_NAME_add``, and
they append to the current subscription rather than replacing it --- see
:ref:`the subs and add rules <subs_vs_add>` above. Not every service offers
one; :ref:`equity_charts` and :ref:`futures_charts` do.


-------------------------
Un-Subscribing to Streams
-------------------------

These functions have names that follow the pattern ``SERVICE_NAME_unsubs``.
These functions send a request to stop streaming the named symbols for a
particular service. They are *not* thread safe, so they should only be called
from one thread. Symbols you do not name stay subscribed.


--------------------
Registering Handlers
--------------------

By themselves, the subscription functions outlined above do nothing except cause
messages to be sent to the client. The ``add_SERVICE_NAME_handler`` functions
register functions that will receive these messages when they arrive. When
messages arrive, these handlers will be called serially. There is no limit to
the number of handlers that can be registered to a service.


.. _registering_handlers:

-----------------
Handling Messages
-----------------

Once the stream client is properly logged in, subscribed to streams, and has
handlers registered, we can start handling messages. This is done simply by
awaiting on the ``handle_message()`` function. This function reads a single
message and dispatches it to the appropriate handler or handlers.

If a message is received for which no handler is registered, that message is
ignored.

A handler which raises does not stop the others from seeing the message, and
does not propagate out of ``handle_message()`` into your receive loop, where it
would be indistinguishable from the connection failing. The failure is reported
on the ``schwaby.streaming`` logger instead, with the service name attached. This
applies equally to synchronous and coroutine handlers.

That is worth knowing when debugging: **a handler which is quietly failing shows
up in the logs and nowhere else** -- unless you ask for it. See
:ref:`error_handlers` below. If you are relying on a handler to do something
important, do one or the other. :ref:`enable_logging` shows how to turn
logging on.

Handlers should take a single argument representing the stream message received:

.. code-block:: python

  import json

  def sample_handler(msg):
      print(json.dumps(msg, indent=4))


.. _error_handlers:

------------------------------
Reacting to Absorbed Failures
------------------------------

Skipping a failed handler is the right behaviour, but it leaves you with a log
record rather than something you can act on. If your program needs to *react* --
raise an alert, increment a counter, mark a subscription unhealthy -- register
an error handler:

.. code-block:: python

  def on_stream_error(service, exception, message):
      alert('schwaby stream: %s raised %r' % (service, exception))

  stream_client.add_error_handler(on_stream_error)

It is called for four things: a stream handler which raised, a late rejection of
a request nobody was waiting on, a connection which failed to close after logout,
and a message this client cannot use at all. :meth:`add_error_handler
<schwaby.streaming.StreamClient.add_error_handler>` below enumerates that last
group; it is the canonical one, because this page and two docstrings each
carried their own copy and only one of the three was widened when the list
grew. :class:`UnusableMessage`'s own docstring describes the type rather than
the list, and says so. That group arrives as ``UnusableMessage``, whose
``message``
attribute is the offending value as it arrived --- the sorted list of names,
for an unread channel --- alongside ``cause`` (the
exception which made it unusable, where there was one) and ``count``/``total``
as integers.

Those last ones are **coalesced**: the first three *of each kind* on a
connection, then powers
of ten, with the running count in the message. A systematically malformed
channel produces one per element per tick, so reporting every one would be a
log-volume incident on top of the data outage — and they share a bounded queue
with the late rejections, so a flood would push out the one thing nothing else
will ever report. The count tells you the true scale, and it is kept per kind —
a flood of one sort of malformed message does not silence a different one
arriving beside it.

For the late rejection, the ``UnexpectedResponseCode`` carries the whole
frame — which can hold several responses — so read the rejected one from
``message`` rather than from ``exception.response['response'][0]``. ``service``
and ``message`` are ``None`` where they do not apply --- but do not use that as
a discriminator. ``UnparsableMessage`` carries the raw undecodable text as
``message`` for exactly this reason. Only the close failure leaves both unset *by design*; an
``UnusableMessage`` reports the containing frame as ``message``, which is
non-``None`` in every case but a top-level JSON ``null``. Test
``isinstance(exception, UnusableMessage)`` before anything else if you branch
on the kind. Registering none keeps the
existing behaviour exactly, and the log line is written either way.

**A frame whose JSON will not decode is the one failure this client does not
absorb.** It is reported to your handler as ``UnparsableMessage`` and *also*
raised, so it still ends your receive loop. Everything else on this page is
skipped and reported; this one is skipped, reported and re-raised.

The difference is what is known about what was missed. A structurally unusable
element can be skipped precisely --- the elements beside it are still delivered
and you are told which one went. A frame which will not parse has unknown
contents, so continuing means accepting a gap of unknown size, and it might have
carried a fill. Ending the loop causes a reconnect and a re-subscribe, which is
the one thing that can recover state.

If your feed hits this often, try
:class:`~schwaby.contrib.util.HeuristicJsonDecoder` before concluding the stream
is broken --- it exists because Schwab really does emit JSON the default parser
rejects, which is evidence for a frame-level quirk rather than a dead
connection.

The late rejection reaches you however Schwab frames it. Only one request is
outstanding at a time, so a second response in a frame cannot be an answer to
anything you are waiting on — it is a late answer to an abandoned request,
whether the server sends it alone or batches it behind the answer to a live one.
Both report the same ``UnexpectedResponseCode`` with the same ``service`` and
``message``, because the framing is the server's choice and you cannot predict
which you will get.

The batched one is not delivered from the code that finds it. That code runs
while the read lock and the request lock are both held and the response deadline
is running, so a slow handler there would turn a subscription that *succeeded*
into a ``ResponseTimeoutError``, and one that re-subscribed would block on a lock
its own caller holds. The report is queued instead, and delivered by whichever
coroutine read the frame once it has released its locks — before
``handle_message`` returns, or before the subscribe that read it returns. Five
consequences:

* **Your handler can be called from inside a subscribe.** A slow one delays that
  call returning; it cannot make it fail, because the response has already been
  matched by then. Either way, keep it short.
* A failed request still delivers what it read. The exception you get describes
  the response that answered *your* request and says nothing about the others
  in the frame. Your handler cannot replace that exception: a ``BaseException``
  raised there is logged and swallowed, because the reason Schwab refused your
  request is far more useful than the reason your handler fell over.
* A **cancelled** request reports nothing. Draining while unwinding a cancel
  would make ``close()``, a ``wait_for`` or a ``TaskGroup`` shutdown block for
  as long as your handler takes. Whatever was queued waits for
  ``handle_message``, or is discarded with the session.
* The queue is cleared by ``close()`` and by a fresh ``login()``, along with any
  frames read but not yet handled. Nothing that arrived on a connection you have
  since replaced reaches you afterwards — not a rejection reported against the
  new session, and not a stale quote delivered to a handler as though it were
  live. That holds whether you closed first or simply logged in again.
* The queue is bounded and drops the oldest when full. That needs reports to
  arrive faster than they are drained, which means something is already wrong —
  and the log line is written either way, so nothing unwritten is lost.

The handler may be a coroutine function, like every other handler on this class:

.. code-block:: python

  async def on_stream_error(service, exception, message):
      await alerts.publish(service, exception)

  stream_client.add_error_handler(on_stream_error)

Where a stream handler failed, ``message`` is the message *as that handler saw
it* — relabeled, if the stream relabels. The exception is the one it raised.

If relabeling itself was what failed, the handler never saw anything, and
``message`` is the message as it arrived, with its numeric field ids intact.

This matters most when something else covers for the stream. If a subscription
quietly stops delivering and a REST poll is authoritative anyway, nothing looks
wrong until something the poll does not cover finally breaks. **A silent failure
that a fallback hides is the one most worth having a signal for.**

An error handler is called for effect. If it raises an ``Exception``, that is
logged and swallowed: a callback for absorbed failures must not itself become a
way to fail. A ``BaseException`` — ``CancelledError`` during shutdown, or
``SystemExit`` — is left to propagate, since swallowing those breaks
cancellation and process exit. Where it propagates to depends on which failure
was being reported: into your receive loop for a synchronous handler, into the
failing handler's own task for an asynchronous one.

**The handler runs inline, and a coroutine one is awaited.** The report finishes
before the call that reported the failure returns. Three consequences worth
knowing:

* **Keep it quick.** It runs on the path that found the failure, so a handler
  that blocks holds up ``handle_message`` — exactly as a slow synchronous
  handler always has. If you need to do something slow, hand off to a task of
  your own and return.
* An error handler may itself call ``close()`` or ``logout()``. Tearing the
  stream down is a reasonable reaction to a failure, and it is safe however many
  handlers do it, because nothing is waiting on a set of scheduled reports.
* ``close()`` does not wait for stream handlers you have in flight. If an
  ``async`` stream handler is still running when you close, it has not failed
  yet and so has not reported yet. Await your own handler tasks first if you
  need their reports.

.. automethod:: schwaby.streaming.StreamClient.add_error_handler

.. _data_field_relabeling:

---------------------
Data Field Relabeling
---------------------

Under the hood, this API returns JSON objects with numerical key representing
labels:

.. note::

  The example below relabels every key, because every id in it is one this
  version knows. **An id it does not know stays numeric** and is delivered to
  your handler as it arrived --- see :ref:`unknown field ids
  <unknown_field_ids>`. If you are building a replay fixture or a test double
  for this library, copy the shape a handler *receives* rather than the wire
  format: a double that emits raw numeric keys rehearses against something
  ``schwaby`` never produces.

.. code-block:: python

  {
    "service": "CHART_EQUITY",
    "timestamp": 1715908546054,
    "command": "SUBS",
    "content": [{
          "seq": 0,
          "key": "MSFT",
          "1": 779,
          "2": 421.65,
          "3": 421.79,
          "4": 421.65,
          "5": 421.755,
          "6": 26.0,
          "7": 1715903940000,
          "8": 19859
      }]
  }

These labels are tricky to decode, and require a knowledge of the documentation
to decode properly. ``schwaby`` makes your life easier by doing this decoding
for you, replacing numerical labels with names proposed by the community. The
message above is delivered as:

.. code-block:: python

  {
    "service": "CHART_EQUITY",
    "timestamp": 1715908546054,
    "command": "SUBS",
    "content": [{
          "seq": 0,
          "key": "MSFT",
          "SEQUENCE": 779,
          "OPEN_PRICE": 421.65,
          "HIGH_PRICE": 421.79,
          "LOW_PRICE": 421.65,
          "CLOSE_PRICE": 421.755,
          "VOLUME": 26.0,
          "CHART_TIME_MILLIS": 1715903940000,
          "CHART_DAY": 19859
      }]
  }

This documentation describes the various fields and their numerical values. You
can find them by investigating the various enum classes ending in ``***Fields``.

.. warning::

   **Relabeling is not applied uniformly.** Schwab delivers messages on two
   channels. Content arriving on the ``data`` channel is relabeled as above.
   Content arriving on the ``notify`` channel is passed to your handler
   **unchanged**, with its bare numeric field ids intact.

   Both reach the same handlers, so a handler which assumes relabeling will
   mis-parse a notify frame -- and it will do so by finding nothing rather than
   by raising, since the keys it looks for are simply absent. If you read fields
   by name, check they are present rather than assuming them.

   This is a property of the library, not of Schwab's protocol.

Some streams let you ask for a subset of the fields. The five
:ref:`level_one` subscription functions take a list of the appropriate field
enums as an extra ``fields`` argument; omit it and every supported field is
requested. No other service accepts one.


---------------
Stream Statuses
---------------

Schwab's streaming functionality is closely modelled on that of the former
TDAmeritrade API, and this module was adapted from an implementation written
against it.

As a result, some streams may have been carried over which don't actually work.
Some never worked at all, and were implemented only because now-defunct
documentation referred to them.

Which of them still work is not documented anywhere, so it is worked out by
trying them. If you find one that behaves differently from what is described
here, please report it `on the issue tracker
<https://github.com/Hu1kSmash/schwaby/issues>`__.

The following streams are confirmed working:
 * :ref:`charts`
 * :ref:`level_one`
 * :ref:`level_two`
 * :ref:`screener`
 * :ref:`account_activity`


.. _charts:

++++++++++++
OHLCV Charts
++++++++++++

These streams summarize trading activity on a minute-by-minute basis for
equities and futures, providing OHLCV (Open/High/Low/Close/Volume) data.


.. _equity_charts:

-------------
Equity Charts
-------------

Minute-by-minute OHLCV data for equities.

.. automethod:: schwaby.streaming::StreamClient.chart_equity_subs
.. automethod:: schwaby.streaming::StreamClient.chart_equity_unsubs
.. automethod:: schwaby.streaming::StreamClient.chart_equity_add
.. automethod:: schwaby.streaming::StreamClient.add_chart_equity_handler
.. autoclass:: schwaby.streaming::StreamClient.ChartEquityFields
  :members:
  :undoc-members:


.. _futures_charts:

--------------
Futures Charts
--------------

Minute-by-minute OHLCV data for futures.

.. automethod:: schwaby.streaming::StreamClient.chart_futures_subs
.. automethod:: schwaby.streaming::StreamClient.chart_futures_unsubs
.. automethod:: schwaby.streaming::StreamClient.chart_futures_add
.. automethod:: schwaby.streaming::StreamClient.add_chart_futures_handler
.. autoclass:: schwaby.streaming::StreamClient.ChartFuturesFields
  :members:
  :undoc-members:


.. _level_one:

++++++++++++++++
Level One Quotes
++++++++++++++++

Level one quotes provide an up-to-date view of bid/ask/volume data. In
particular they list the best available bid and ask prices, together with the
requested volume of each. They are updated live as market conditions change.


.. _level_one_quotes_stream:

---------------
Equities Quotes
---------------

Level one quotes for equities traded on NYSE, AMEX, and PACIFIC.

.. automethod:: schwaby.streaming::StreamClient.level_one_equity_subs
.. automethod:: schwaby.streaming::StreamClient.level_one_equity_unsubs
.. automethod:: schwaby.streaming::StreamClient.level_one_equity_add
.. automethod:: schwaby.streaming::StreamClient.add_level_one_equity_handler
.. autoclass:: schwaby.streaming::StreamClient.LevelOneEquityFields
  :members:
  :undoc-members:


.. _level_one_option_stream:

--------------
Options Quotes
--------------

Level one quotes for options. Note you can use
:meth:`Client.get_option_chain() <schwaby.client.Client.get_option_chain>` to fetch
available option symbols.

.. automethod:: schwaby.streaming::StreamClient.level_one_option_subs
.. automethod:: schwaby.streaming::StreamClient.level_one_option_unsubs
.. automethod:: schwaby.streaming::StreamClient.level_one_option_add
.. automethod:: schwaby.streaming::StreamClient.add_level_one_option_handler
.. autoclass:: schwaby.streaming::StreamClient.LevelOneOptionFields
  :members:
  :undoc-members:


.. _level_one_futures_stream:

--------------
Futures Quotes
--------------

Level one quotes for futures.

.. automethod:: schwaby.streaming::StreamClient.level_one_futures_subs
.. automethod:: schwaby.streaming::StreamClient.level_one_futures_unsubs
.. automethod:: schwaby.streaming::StreamClient.level_one_futures_add
.. automethod:: schwaby.streaming::StreamClient.add_level_one_futures_handler
.. autoclass:: schwaby.streaming::StreamClient.LevelOneFuturesFields
  :members:
  :undoc-members:


.. _level_one_futures_options_stream:

----------------------
Futures Options Quotes
----------------------

Level one quotes for futures options.

.. automethod:: schwaby.streaming::StreamClient.level_one_futures_options_subs
.. automethod:: schwaby.streaming::StreamClient.level_one_futures_options_unsubs
.. automethod:: schwaby.streaming::StreamClient.level_one_futures_options_add
.. automethod:: schwaby.streaming::StreamClient.add_level_one_futures_options_handler
.. autoclass:: schwaby.streaming::StreamClient.LevelOneFuturesOptionsFields
  :members:
  :undoc-members:


.. _level_one_forex_stream:

------------
Forex Quotes
------------

Level one quotes for foreign exchange pairs.

.. automethod:: schwaby.streaming::StreamClient.level_one_forex_subs
.. automethod:: schwaby.streaming::StreamClient.level_one_forex_unsubs
.. automethod:: schwaby.streaming::StreamClient.level_one_forex_add
.. automethod:: schwaby.streaming::StreamClient.add_level_one_forex_handler
.. autoclass:: schwaby.streaming::StreamClient.LevelOneForexFields
  :members:
  :undoc-members:


.. _level_two:

++++++++++++++++++++
Level Two Order Book
++++++++++++++++++++

Level two streams provide a view on continuous order books of various securities.
The level two order book describes the current bids and asks on the market, and
these streams provide snapshots of that state.

Due to the lack of official documentation, these streams are largely reverse
engineered. While the labeled data represents a best-effort attempt to
interpret stream fields, it's possible that something is wrong or incorrectly
labeled.

The documentation lists more book types than are implemented here. In
particular, it also lists ``FOREX_BOOK``, ``FUTURES_BOOK``, and
``FUTURES_OPTIONS_BOOK`` as accessible streams. All experimentation has resulted
in these streams refusing to connect, typically returning errors about
unavailable services. Due to this behavior and the lack of official
documentation for book streams generally, ``schwaby`` assumes these streams are not
actually implemented, and so excludes them. If you have any insight into using
them, please `let us know <https://github.com/Hu1kSmash/schwaby/issues>`__.


-------------------------------------
Equities Order Books: NYSE and NASDAQ
-------------------------------------

``schwaby`` supports level two data for NYSE and NASDAQ, which are the two major
exchanges dealing in equities, ETFs, etc. Stocks are typically listed on one or
the other, and it is useful to learn about the differences between them:

 * `"The NYSE and NASDAQ: How They Work" on Investopedia
   <https://www.investopedia.com/articles/basics/03/103103.asp>`__
 * `"Here's the difference between the NASDAQ and NYSE" on Business Insider
   <https://www.businessinsider.com/heres-the-difference-between-the-nasdaq-and-nyse-2017-7>`__
 * `"Can Stocks Be Traded on More Than One Exchange?" on Investopedia
   <https://www.investopedia.com/ask/answers/05/stockmultipleexchanges.asp>`__

You can identify on which exchange a symbol is listed by using
:meth:`Client.get_instruments() <schwaby.client.Client.get_instruments>`:

.. code-block:: python

  import httpx2

  from schwaby.client import Client

  r = client.get_instruments(
          ['GOOG'], projection=Client.Instrument.Projection.FUNDAMENTAL)
  assert r.status_code == httpx2.codes.OK, r.raise_for_status()
  print(r.json())

However, many symbols have order books available on these streams even though
this API call returns neither NYSE nor NASDAQ. The only sure-fire way to find out
whether the order book is available is to attempt to subscribe and see what
happens.

Note that, to match what little documentation exists, the NYSE book is called
"listed". Testing indicates this stream corresponds to the NYSE book; if you
see behaviour suggesting otherwise, please
`let us know <https://github.com/Hu1kSmash/schwaby/issues>`__.

.. automethod:: schwaby.streaming::StreamClient.nyse_book_subs
.. automethod:: schwaby.streaming::StreamClient.nyse_book_unsubs
.. automethod:: schwaby.streaming::StreamClient.nyse_book_add
.. automethod:: schwaby.streaming::StreamClient.add_nyse_book_handler

.. automethod:: schwaby.streaming::StreamClient.nasdaq_book_subs
.. automethod:: schwaby.streaming::StreamClient.nasdaq_book_unsubs
.. automethod:: schwaby.streaming::StreamClient.nasdaq_book_add
.. automethod:: schwaby.streaming::StreamClient.add_nasdaq_book_handler


------------------
Options Order Book
------------------

This stream provides the order book for options. It's not entirely clear what
exchange it aggregates from, but it's been tested to work and deliver data. The
leading hypothesis is that it is the order book for the
`Chicago Board of Exchange <https://www.cboe.com/us/options>`__ options
exchanges, though that is a guess and not an informed one.

.. automethod:: schwaby.streaming::StreamClient.options_book_subs
.. automethod:: schwaby.streaming::StreamClient.options_book_unsubs
.. automethod:: schwaby.streaming::StreamClient.options_book_add
.. automethod:: schwaby.streaming::StreamClient.add_options_book_handler


.. _book_fields:

---------------------
Book Message Contents
---------------------

All three book streams deliver the same shape, and it is nested where the other
services are flat. A message has a symbol, a timestamp, and two lists of price
levels; each level carries its total size and a further list breaking that size
down by exchange:

.. code-block:: python

  {
      'SYMBOL': 'GOOG',
      'BOOK_TIME': 1757088000000,
      'BIDS': [
          {
              'BID_PRICE': 207.42,
              'TOTAL_VOLUME': 900,
              'NUM_BIDS': 3,
              'BIDS': [
                  {'EXCHANGE': 'NSDQ', 'BID_VOLUME': 500, 'SEQUENCE': 12},
                  {'EXCHANGE': 'ARCA', 'BID_VOLUME': 400, 'SEQUENCE': 15},
              ],
          },
          ...
      ],
      'ASKS': [...],   # the same, with ASK_PRICE, NUM_ASKS and ASK_VOLUME
  }

Note that ``BIDS`` names two different things one level apart: the list of price
levels, and the per-exchange breakdown inside each level. That is Schwab's
numbering, kept as-is rather than renamed, so the labels match the field
numbers if you ever have to read a raw frame.

As with everything on this page, these labels are reverse engineered --- see
the caveat at the top of :ref:`level_two`. Field *numbers* come from Schwab;
the names attached to them are a best-effort reading.

.. autoclass:: schwaby.streaming::StreamClient.BookFields
  :members:
  :undoc-members:

.. autoclass:: schwaby.streaming::StreamClient.BidFields
  :members:
  :undoc-members:

.. autoclass:: schwaby.streaming::StreamClient.AskFields
  :members:
  :undoc-members:

.. autoclass:: schwaby.streaming::StreamClient.PerExchangeBidFields
  :members:
  :undoc-members:

.. autoclass:: schwaby.streaming::StreamClient.PerExchangeAskFields
  :members:
  :undoc-members:


.. _screener:

++++++++
Screener
++++++++

Top 10 advances and decliners by volume, trades, percent change and average percent
volume.

.. note::

  **A screener push is the whole list, not a change to it.** Schwab classifies
  ``SCREENER_EQUITY`` and ``SCREENER_OPTION`` as *Whole* services, unlike the
  ``LEVELONE_*`` services which send only what changed. Every message carries
  the complete ranked list as it stands, so a symbol missing from one push
  means "not in this snapshot" rather than "removed" --- diffing consecutive
  pushes into additions and removals invents events that Schwab never sent.

  Each push is also self-describing: ``SORT_FIELD`` and ``FREQUENCY`` are
  present in every message, so the active criteria never have to be inferred
  from what was subscribed.

**The key is not a stock symbol.** It is ``(PREFIX)_(SORTFIELD)_(FREQUENCY)``
--- passing a ticker subscribes to nothing and reports no error.

(PREFIX)_(SORTFIELD)_(FREQUENCY) where PREFIX is:
 * Indices: $COMPX, $DJI, $SPX, INDEX_ALL
 * Exchanges: NYSE, NASDAQ, OTCBB, EQUITY_ALL
 * Option: OPTION_PUT, OPTION_CALL, OPTION_ALL

and sortField is:
 * VOLUME, TRADES, PERCENT_CHANGE_UP, PERCENT_CHANGE_DOWN, AVERAGE_PERCENT_VOLUME

and frequency is:
 * 0, 1, 5, 10, 30, 60 minutes (0 is for all day)

.. danger::

  **Schwab does not reject a malformed key. It acknowledges success and then
  sends nothing, indefinitely.** Measured against a live account:
  ``NOT_A_PREFIX_VOLUME_5``, ``NASDAQ_NOT_A_SORT_5``, ``NASDAQ_VOLUME_7`` and
  the bare ticker ``AAPL`` each returned ``code: 0, "SUBS command
  succeeded"`` and delivered zero frames.

  So there is no error to catch and no exception to see. A typo in a key is
  indistinguishable, from inside your program, from a quiet market --- and a
  screener that legitimately has nothing to report looks exactly the same.
  Validate keys before subscribing rather than after.

  This is why the prefix above matters. ``$SPX.X`` is not a valid prefix:
  over REST it is an HTTP 400, and on the stream it is accepted and silent
  forever.

.. note::

  ``AVERAGE_PERCENT_VOLUME`` is documented by Schwab and accepted by the
  stream, and delivers nothing --- ``SUBS command succeeded`` followed by
  zero frames, the same signature as an invalid key. The other four sort
  fields all return distinct populations. Treat it as unavailable rather
  than as something you are using incorrectly.

.. _screener_cadence:

-------------------------------
What was measured, and how much
-------------------------------

Schwab documents none of the following. It comes from a single capture ---
one nine-minute window, midday on a normal trading session, one account ---
so treat the numbers as an order of magnitude rather than a specification,
and re-measure if you are going to depend on them.

**The frequency selects a time bucket, not a push rate.** Subscribing at
``_0``, ``_1``, ``_5`` and ``_60`` gave four different rankings and the same
push interval. The bucket changes *what* is ranked; it does not change how
often you hear about it.

**Push interval is a property of the service.** ``SCREENER_EQUITY`` pushed
about every 10 seconds and ``SCREENER_OPTION`` about every 5, at every
frequency tried. If you subscribe to both, expect the two rates interleaved
--- a stall detector that assumes one rate will misjudge the other.

**A subscription answers immediately, then falls onto the server's clock.**
The first frame arrived within 0.03 seconds of the ``SUBS`` acknowledgement
in eleven of fourteen subscriptions. The interval that follows it is a
partial cycle, so discard it before averaging anything.

**Frequency ``0`` is what the REST endpoint returns.** A stream subscription
at ``NASDAQ_VOLUME_0`` and a
:meth:`~schwaby.client.Client.get_movers` call 70 seconds apart produced the
same ten symbols in the same order. The other buckets shared only four to six
of those ten. So :meth:`~schwaby.client.Client.get_movers` is not a different
view of the data --- it is this service's whole-session bucket, and it is the
only one of the six that REST exposes.

Both the equity and option screener streams use a common set of fields:

.. autoclass:: schwaby.streaming::StreamClient.ScreenerFields
  :members:
  :undoc-members:


---------------
Screener Equity
---------------

.. automethod:: schwaby.streaming::StreamClient.screener_equity_subs
.. automethod:: schwaby.streaming::StreamClient.screener_equity_unsubs
.. automethod:: schwaby.streaming::StreamClient.screener_equity_add
.. automethod:: schwaby.streaming::StreamClient.add_screener_equity_handler


---------------
Screener Option
---------------

.. automethod:: schwaby.streaming::StreamClient.screener_option_subs
.. automethod:: schwaby.streaming::StreamClient.screener_option_unsubs
.. automethod:: schwaby.streaming::StreamClient.screener_option_add
.. automethod:: schwaby.streaming::StreamClient.add_screener_option_handler


.. _account_activity:

++++++++++++++++
Account Activity
++++++++++++++++

.. automethod:: schwaby.streaming::StreamClient.account_activity_sub
.. automethod:: schwaby.streaming::StreamClient.account_activity_unsubs
.. automethod:: schwaby.streaming::StreamClient.add_account_activity_handler
.. autoclass:: schwaby.streaming::StreamClient.AccountActivityFields
  :members:
  :undoc-members:

----------------------------------------
What the payload looks like, as observed
----------------------------------------

The three fields above are relabeled for you. What is *inside* ``MESSAGE_DATA``
is not documented by Schwab, so every consumer ends up reverse-engineering it
and keeping the results privately.

What follows was collected by watching a live ``ACCT_ACTIVITY`` feed over
roughly a year. **It is an observation log, not a contract.** Schwab publishes
none of this, nothing here is validated by this library, and a shape that has
not been seen is not thereby impossible. Treat it as a map drawn by someone who
has been there, not as a specification.

.. _confidence_tags:

Some of it we are not sure about, and those are marked. Two tags appear below
and on the other pages:

**Unconfirmed** --- reasoned from a specification or from how the data must be
laid out, and never actually seen on a feed. We handle it because the failure
would be silent, not because we have met it.

**Seen once, not reproduced** --- observed and written down at the time, but not
reliably reproducible since, so we cannot tell you when it happens.

**If you can confirm or refute either kind, please**
`open an issue <https://github.com/Hu1kSmash/schwaby/issues>`__. A single
message saying "I see this" or "my traffic contradicts it" settles a question
that no amount of reasoning here will, and account and asset mix differ enough
that someone else's feed is genuinely different evidence. Being told we are
wrong is the point of publishing these rather than keeping them.

**The order identifier appears under at least seven spellings**, and which one
you get depends on the message:

.. code-block:: python

  ('SchwabOrderID', 'schwabOrderID', 'OrderID', 'orderId',
   'OrderKey', 'orderKey', 'order_id')

**The symbol appears under four**, in descending order of preference:

.. code-block:: python

  ('Symbol', 'symbol', 'PrimaryMarketSymbol', 'UnderlyingSymbol')

``Symbol`` is the tradeable ticker, and is an OCC option string for an option
leg. The other two are fallbacks for shapes that carry only those.

**Order statuses that end an order** are exported as
:attr:`Client.Order.TERMINAL_STATUSES
<schwaby.client.Client.Order.TERMINAL_STATUSES>`:

.. code-block:: python

  ('FILLED', 'REJECTED', 'CANCELED', 'EXPIRED', 'REPLACED')

Note ``CANCELED`` with one L. These are values of the REST ``status`` field ---
what :meth:`get_order <schwaby.client.Client.get_order>` returns --- and the
``MESSAGE_TYPE`` tokens below are a different vocabulary that does not map onto
them one for one. ``FILLED``, ``CANCELED`` and ``REJECTED`` have been observed
ending an order. ``EXPIRED`` and ``REPLACED`` are
:ref:`Unconfirmed <confidence_tags>`: included by reading, and never captured.

**``MESSAGE_TYPE`` tokens observed.** These are the tokens **as they appear on
the wire**, and most of them are CamelCase. ``SUBSCRIBED`` is a genuine
exception. A consumer comparing against an upper-case ``'ORDERCREATED'``
matches nothing.

**Match case-insensitively, and do not assume case is the only difference.**
``ORDERUROUT`` is not a case variant of ``OrderUROutCompleted`` but a truncation
of it, and no amount of case folding will make the two meet. A list containing
it matches nothing at all.

.. code-block:: python

  # Measured on a live feed by driving the states deliberately.
  ('SUBSCRIBED', 'OrderCreated', 'OrderAccepted',
   'CancelAccepted', 'ExecutionCreated', 'OrderUROutCompleted',

   # Seen on the same feed over roughly a year, but recorded before the
   # casing above was measured, so the spelling of these is the observer's
   # rather than the venue's. Match case-insensitively.
   'EXECUTIONREQUESTED', 'EXECUTIONREQUESTCREATED',
   'EXECUTIONREQUESTCOMPLETED', 'ORDERFILLCOMPLETED',
   'ORDERPARTIALFILL', 'ORDERPARTIALLYFILLED', 'ORDERREJECTED',
   'ORDERCANCELED', 'ORDERCANCELLED', 'ORDEREXPIRED', 'ORDERREPLACED',

   # A resting order's lifecycle. Attested by a note rather than a frame; see
   # below.
   'ORDERMONITORCREATED', 'ORDERMONITORCOMPLETED',
   'CHANGECREATED', 'CHANGEACCEPTED')

``ORDERCANCELED`` and ``ORDERCANCELLED`` are both in that second block, and
**that is not evidence Schwab spells it two ways.** The comment above the block
says why: those spellings are the observer's, recorded before the casing was
measured. A list normalised to upper case cannot settle a question about
spelling, and reading it as though it could is the same error as treating an
enum member as proof that a key is delivered.

What was actually measured cuts the other way. The one cancel driven
deliberately, below, produced ``CancelAccepted`` and ``OrderUROutCompleted``
--- neither of which is any case variant of either upper-case token. Schwab
documents the ``MESSAGE_TYPE`` vocabulary nowhere at all; a search of the whole
developer portal returns nothing describing these values.

So: **match case-insensitively, and prefer a substring over an enumerated
set.** A consumer holding an exact list --- including this one --- can miss a
real cancel and see only silence, which on an order feed is the failure that
costs something.

``OrderUROutCompleted`` says the order **came off the book**. It does not say
why, and it is worth resisting the obvious gloss. Calling it "an unsolicited
out" asserts that nobody asked for the cancellation --- and the same token ends
a cancel you issued yourself. Any name or operator-facing phrase built on it
should describe what the venue did, not what caused it. The cause is carried by
the token beside it, below.

**A cancel you issue yourself looks like this**, measured by placing a
resting order and cancelling it::

  place   -> OrderCreated, OrderAccepted
  cancel  -> CancelAccepted, ExecutionCreated, OrderUROutCompleted

and ``get_order`` then reads ``CANCELED``. Identical for a limit and a stop.

**A buy rejected for buying power looks almost the same**, and this is worth
knowing because the HTTP response does not tell you::

  place   -> HTTP 201 with a real order id
             OrderCreated, OrderAccepted, CancelAccepted, OrderUROutCompleted

about a second later, with ``get_order`` reading ``REJECTED`` and a
``statusDescription`` naming the buying power. **A caller which checks only the
HTTP status believes the order was accepted.** Re-read the order.

Note the two sequences differ only by ``ExecutionCreated``, so do not use the
presence of ``OrderUROutCompleted`` alone to tell a cancel from a rejection.

.. danger::

  **A cancel emits** ``ExecutionCreated`` **carrying a non-zero quantity, on an
  order that filled nothing.** Measured by placing an unfillable limit against
  a funded account and cancelling it:

  .. code-block:: python

    # Inside the parsed MESSAGE_DATA, not beside it -- see below, where
    # MESSAGE_DATA turns out to be a JSON string that has to be parsed a
    # second time. `EventType` is that inner document's own type field and
    # is not the same key as the content item's MESSAGE_TYPE.
    {"EventType": "ExecutionCreated",
     "ExecutionInfo": {
        "ExecutionQuantity": {"lo": "1000000", "signScale": 12},   # decodes to 1
        "ExecutionTransType": "UROut",
        "CancelType": "ClientCancel"}}

  A genuine fill carries the *same* ``ExecutionQuantity`` field with
  ``ExecutionTransType: "Fill"``. **The quantity cannot tell a fill from a
  cancel. Only the trans-type can.**

  This is the expensive one because of where it lands: an execution algorithm
  cancels a clip to reprice, many times per parent order, by design. A consumer
  reading quantities by shape books a phantom fill on every reprice, concludes
  the clip is done, never places the remainder, and believes it holds a
  position it does not have. Nothing raises.

  Exclude on any ``UROUT`` / ``CANCEL`` / ``BUST`` / ``REJECT`` signal rather
  than trying to enumerate every fill label. The error is asymmetric ---
  under-counting is recoverable from a REST poll, over-counting is not.

**Two enumerated fields arrive as either the label or its ordinal.**
``ResponseType`` and ``RouteStatus`` were captured as both a string and an
integer on the *same* order, from two ``ExecutionRequestCompleted`` frames two
milliseconds apart::

  seq=10   ResponseType='Accepted'   RouteStatus='RouteVenueAccepted'
  seq=11   ResponseType=8            RouteStatus=8

Not a version difference and not an account difference --- one order, one
cancel, one capture. Any model declaring these ``str``, or ``int``, breaks on
the sibling frame.

**Numbers are** ``{"lo": "<mantissa>", "signScale": N}``, **and an odd**
``signScale`` **means the value is negative.**

**This library ships one**, because every consumer was writing it and the
first attempt is reliably wrong in at least one of the ways below:

.. code-block:: python

  from schwaby.contrib.util import decode_decimal

  decode_decimal({"lo": "6860000", "signScale": 12})   # Decimal('6.860000')
  decode_decimal({"lo": "6860000", "signScale": 13})   # Decimal('-6.860000')
  decode_decimal({"signScale": 12})                    # Decimal('0')
  decode_decimal({"lo": "19200"})                      # Decimal('19200')

**Wrap each field, not the message.** ``decode_decimal`` raises rather than
guess --- at a corrupt member, and at any key it does not recognise, which is
how a change at Schwab's end reaches you. Decoded one field at a time, a value
it refuses costs that field; decoded in one ``try``, it costs the message:

.. code-block:: python

  import logging

  from schwaby.contrib.util import decode_decimal, UnusableDecimalScale

  def decode_fields(message, names):
      out = {}
      for name in names:
          if name not in message:
              continue
          try:
              out[name] = decode_decimal(message[name])
          except UnusableDecimalScale as exc:
              # Keep the exception. It carries the specific complaint and a
              # bounded repr of the value -- a corrupt member and an
              # unrecognised key are both refusals, and only one of them
              # also writes a line of its own.
              logging.warning('could not decode %s: %s', name, exc)
      return out

``UnusableDecimalScale`` is both a :class:`~schwaby.utils.SchwabError` and a
``ValueError``, so either name catches it.

.. autofunction:: schwaby.contrib.util.decode_decimal
.. autoclass:: schwaby.contrib.util.UnusableDecimalScale

.. danger::

  **Read** ``lo`` **alone and a large enough number decodes to a smaller one,
  silently.** The encoding is a serialized .NET ``System.Decimal``: a 96-bit
  mantissa split across ``lo``, ``mid`` and ``hi``, where the true value is
  ``lo + (mid << 32) + (hi << 64)``. At ``signScale`` 12 --- six decimal
  places --- ``lo`` alone tops out at **4294.967295**. A principal amount, a
  total, or a share price above roughly $4,295 needs ``mid``, and a decoder
  ignoring it returns a plausible wrong number with no exception:

  .. code-block:: python

    {"lo": "705032704", "mid": 1, "signScale": 12}
    # lo alone       ->    705.032704
    # lo + mid<<32   ->   5000.000000      <- the actual value

  :ref:`Unconfirmed <confidence_tags>`. No captured payload has carried a
  non-zero ``mid`` or ``hi``, on either feed anyone here has watched --- this
  is reasoned from the .NET layout rather than observed. It is documented
  anyway because a value that does not fit in 32 bits cannot be sent in ``lo``
  alone, so the alternative to reading all three is waiting for a large enough
  number to find out. **If you have a payload with** ``mid`` **set, please**
  `open an issue <https://github.com/Hu1kSmash/schwaby/issues>`__ **with the
  field** --- it would turn the most consequential guess on this page into a
  fact.

``Decimal`` rather than a float, deliberately: these are money, and
:meth:`set_price <schwaby.orders.generic.OrderBuilder.set_price>` has refused a
float since 2.1.0 for the reason :ref:`price_strings` gives. A value decoded
into a binary float cannot be fed back into a reprice without going through the
conversion this library exists to avoid, and accumulating one over a day's
principal reintroduces exactly the error class that made limit prices a cent
low. The value is built from a string, sign included, because **every
operation** on a ``Decimal`` applies the caller's context and only construction
does not: a consumer who sets ``decimal.getcontext().prec = 6`` elsewhere in
their process would otherwise get ``1234.57`` for a price of ``1234.5678``, and
negating a positive result is such an operation, so the sign has to go into the
string too.

The scale is also bounded rather than computed with. ``10 ** (signScale // 2)``
on a garbage or hostile ``signScale`` builds an astronomical integer and hangs
the thread, and a hang is not catchable by a per-item ``try``/``except``, so one
bad field would take the stream down rather than one message. Leaving the
arithmetic to refuse is not enough either --- a scale of a few million
underflows to a zero that compares equal to zero, which on a fill feed is the
worst answer available. No such payload has been observed; the bound is there
because the failure would be silent.

Confirmed against known truth on a 1-share order at a $6.86 limit:
``LimitPrice`` ``{"lo": "6860000", "signScale": 12}`` is ``6.86``, and
``EstimatedPrincipalAmount`` ``{"lo": "6860000", "signScale": 13}`` is
``-6.86`` --- principal on a *buy*, so cash out. **A decoder that handles only
the even case flips the sign on every cash-direction field, silently.**

The rule itself is not guesswork. **Schwab's Trader API support confirmed the
conversion in writing**, while stating that it remains undocumented publicly.
That is the strongest attestation any part of this section has, and it exists
nowhere official.

.. danger::

  **A decimal object carrying no mantissa at all is zero, not unknown.**
  Measured on three fields of one payload:
  ``ActualChargedCommissionAmount {}`` was a genuine $0 commission,
  ``RoutedPrice {}`` was a market order with no limit price, and
  ``LeavesQuantity {"signScale": 12}`` arrived on the **final fill event** of a
  completed order.

  Reading that third one as "unknown" makes a *complete* fill report as
  "remaining outstanding, quantity unknown". Silent, wrong, and on the fill
  path --- which is the worst combination available on this feed.

  **"No mantissa" means none of** ``lo``, ``mid`` **or** ``hi``, not an absent
  ``lo``. The serializer omits zero members --- including the scale, see
  below --- so a value whose low 32 bits
  happen to be zero arrives as ``{"mid": 1, "signScale": 12}`` --- which is
  ``4294.967296``, and which a guard keyed on ``lo`` alone reads as zero. That
  is the same slice-reading defect as reading ``lo`` for the mantissa, in the
  guard that runs immediately before it.

.. danger::

  **An absent** ``signScale`` **is scale 0, not a missing scale.** The
  serializer omits whatever is zero, and that applies to the scale exactly as
  it applies to a mantissa member. One quote, verbatim, is what settles it:

  .. code-block:: python

    {"Ask":     {"lo": "13720000", "signScale": 12},   # 13.72
     "AskSize": {"lo": "19200"},                       # 19200 shares
     "Bid":     {"lo": "13710000", "signScale": 12},   # 13.71
     "BidSize": {"lo": "4200"},                        # 4200 shares
     "Mid":     {"lo": "13715000", "signScale": 12}}   # 13.715

  Prices scaled by six places, sizes with no scale at all, in the same object.
  $13.72 with 19200 on the ask is a coherent quote; 0.0192 shares is not.

  This library *refused* the shape at first, on the reasoning that a scale
  which is missing cannot be told from one that was lost --- which sounds
  careful and cost both sizes on every quote carrying them. If your decoder
  treats an absent ``signScale`` as an error, or guesses 12 because that is
  the common value, ``AskSize`` and ``BidSize`` are what it gets wrong: the
  second guess is off by a factor of a million with nothing raised.

.. danger::

  **Any key you do not recognise inside a decimal object means the object
  cannot be trusted, and** ``decode_decimal`` **refuses it.**

  The temptation is to ignore the key and decode the rest --- the encoding is
  positional, so a fifth key does not move the other four. That is true and
  it is not the problem. The problem is that an unrecognised key may be a
  *renamed* one, and from inside a payload the two are indistinguishable: the
  serializer omits whatever is zero, so a component missing because it was
  renamed looks exactly like one missing because it is zero.

  .. code-block:: python

    {"lo": "6860000", "SignScale": 12}    # a rename, or an addition?

  Read as an addition, there is no ``signScale``, so the absent-scale rule
  applies and a **$6.86 limit price decodes as $6,860,000**. Three attempts at
  telling the two cases apart each missed a spelling of "missing" --- the key
  absent, the key present and ``null``, the key present and ``0`` --- and the
  last of those cannot be told apart at all, because ``signScale: 0``
  genuinely means scale 0.

  **So the cost is paid deliberately.** A key Schwab adds arrives on every
  decimal object at once, so every money and quantity field raises until
  ``schwaby`` knows about it. That is loud, it is catchable per field, and the
  key is named in the log on the first message --- against a silent wrong
  price on a funded account, it is not a close call. If you see it, please
  `open an issue <https://github.com/Hu1kSmash/schwaby/issues>`__ with the
  field.

.. _unknown_field_ids:

.. note::

  **A field id this library has no name for is delivered to your handler
  under its numeric key**, while every field it does know is relabeled as
  usual. A field Schwab adds reaches you rather than breaking you.

  It is also logged, once per field per table, on the ``schwaby.streaming``
  logger --- because being delivered and being *noticed* are different
  things, and the field tables are only as current as the last time someone
  looked. If you see that line, please `open an issue
  <https://github.com/Hu1kSmash/schwaby/issues>`__ with the id and the value.

  This one is not reported through :func:`add_error_handler
  <schwaby.streaming.StreamClient.add_error_handler>`: nothing was absorbed
  and nothing failed. It is a change in the venue, which is an operator's
  concern rather than a caller's.

.. warning::

  **A whole service, or a whole channel, that this version does not know is
  a different matter: the messages are dropped.** There is no handler to
  route an unknown service to and no field table to relabel it with, and a
  channel this version does not read is a compartment of the frame nobody
  looks in. Your handlers never fire for either, so without a report they
  are invisible from inside a consumer.

  Both *are* reported --- a ``WARNING``, and an :class:`UnusableMessage`
  through :func:`add_error_handler
  <schwaby.streaming.StreamClient.add_error_handler>`. **Every distinct
  service or channel name is named at least once, in both** --- up to the
  first 64 of them on a client, after which the guarantee stops and a line
  says so --- and everything after that first sighting is coalesced so a systematic change cannot become
  a log flood. The coalescing counts per *kind*, which is why the first
  sighting is guaranteed separately: without it a fourth new service arriving
  beside three others would never be reported at all.

  What appeared is on the exception's ``message``: the service name for a
  service, the sorted list of channel names for a channel.

  A service you simply registered no handler for is **not** reported. That is
  your own choice, and a line per message on a feed you deliberately ignored
  is noise rather than news. Nor is a ``notify`` frame that names no service,
  which is not required to name one.

  The frame is not dropped over an unread channel --- the compartments that
  *are* understood still hold real data, and refusing the whole frame would
  turn an addition into an outage.

**The sign is not the side.** Fill quantities and prices arrive positive, with
an even ``signScale``; buy versus sell comes from ``BuySellCode``. The odd
branch exists so a genuinely negative field does not decode positive, and the
one it has been observed on is ``EstimatedPrincipalAmount``, negative on a buy
because the cash goes out. A consumer inferring direction from the sign is
wrong in a way that looks entirely plausible.

**Timestamp and container fields arrive as** ``{}``, not ``null`` and not
absent, where the populated form is ``{"DateTimeString": "..."}``. Seen on
``ExecutionTime``, ``RouteAcknowledgmentTimeStamp``, ``AsOfTimeStamp``,
``PreferredRoute``, ``EquityOrderLeg`` and others in a single five-frame
capture. A truthiness check handles it; ``d['ExecutionTime']['DateTimeString']``
does not.

**``MESSAGE_DATA`` is a string, not an object** --- it has to be parsed a
second time. It is the empty string on the ``SUBSCRIBED`` ack, which is not a
malformed message and should not be reported as one.

.. warning::

  **And it is not always JSON.** Schwab sends notices through the same field
  as plain prose --- ``"Feature not supported"`` has been observed --- so a
  consumer calling ``json.loads`` unconditionally raises on a message that is
  merely informational, on the account feed, at whatever moment Schwab decides
  to tell you something. Parse it defensively and treat a failure as "this one
  is a notice", not as a broken frame.

.. warning::

  **A re-subscribe can replay recent activity in a stripped shape.**
  :ref:`Seen once, not reproduced <confidence_tags>`.

  What was recorded at the time: after a reconnect, the stream re-sent recent
  activity with **no account, no symbol and no execution id** --- a bare
  quantity and price with none of the identifying envelope. A consumer without
  a dedup guard re-books an old fill as a fresh one.

  The shape is the useful part, because it is recognisable: those three absent
  together is not what a live event looks like. A guard can key on that as well
  as on execution id, which is what the consumer who reported it does.

  **We cannot tell you when this happens.** A later capture is a
  counter-example --- a subscribe on an account with four filled orders that
  same day produced a bare ``SUBSCRIBED`` ack and then silence, replaying
  nothing. So "replays on every re-subscribe" is wrong, and what triggers it is
  unknown. What is true either way, and worth building for: **a consumer must
  be idempotent across reconnects.**

  `An issue <https://github.com/Hu1kSmash/schwaby/issues>`__ from anyone who
  can reproduce this on demand would be worth a great deal.

**Content items within one message are not necessarily in lifecycle order.** A
``CancelAccepted`` naming no symbol was seen arriving ahead of the
``ExecutionRequested`` for the same order that did name the instrument, so a
consumer ruling item-by-item makes its verdict depend on Schwab's ordering
within the batch. Scan the whole batch before classifying any of it.

All of the above was measured against a funded account, by placing an
unfillable limit order and cancelling it --- except the batch ordering, which
was recorded in production without the frame being retained and so is attested
by a note rather than by bytes anyone can still produce. Schwab documents none
of it.

**The four tokens in that last group belong to resting orders** ---
``ORDERMONITORCREATED``, ``ORDERMONITORCOMPLETED``, ``CHANGECREATED`` and
``CHANGEACCEPTED``. They are the lifecycle of a limit or
stop order sitting on the book, and a program that places only market orders
will never see them --- it will meet them the first time a human places an order
by hand in the same account from Schwab's own interface. That is exactly how
they were observed. If you match ``MESSAGE_TYPE`` against an allow-list, an
ordinary hand trade will otherwise raise an unknown-shape alert.

Their provenance is thinner than the rest of this list, and that is worth
saying. They were recorded as a vocabulary at the time they were seen, but the
frames themselves were not retained --- capture on that feed began the following
day, added in response to the very event that produced them. So these five are
attested by a contemporaneous note rather than by a frame anyone can still
produce. They are here because a token you have not heard of costs a consumer an
alert whether or not the frame survives, but weight them accordingly.

They carry no fill to act on; the authoritative fill remains
``ORDERFILLCOMPLETED``. They are also chatty: one hand-placed order change was
observed emitting nineteen messages, sixteen of them from this group, so
consider logging them below the level you use for fills.

**``CHANGECREATED``, ``CHANGEACCEPTED`` and ``ORDERREPLACED`` mean a working
order was amended or cancelled.** That is worth separating from the rest. For an
order your own program placed it is a safety event --- something modified a live
order mid-flight --- while for an instrument you do not manage it is just
somebody editing their own order. It is the one distinction in this group that
changes what an operator should do about it.

**Distinguishing a relabeled item from a raw one.** After relabeling, a
``data``-channel content item carries ``seq``, ``key``, ``ACCOUNT``,
``MESSAGE_TYPE`` and ``MESSAGE_DATA``. A content item carrying none of those is
a ``notify``-channel item, which this library forwards unchanged --- see the
warning under :ref:`Data Field Relabeling <data_field_relabeling>` above.
Observed values there include an activity token of ``orderfill`` and a benign
notice reading ``feature not supported``.

If you learn something this list gets wrong, a pull request correcting it is
more useful than a private patch.


.. _json_decoding:

++++++++++++++++++++++++++++
When the JSON Will Not Parse
++++++++++++++++++++++++++++

Schwab sometimes sends the streaming server's messages in a form Python's
``json`` module refuses. When that happens the frame raises
:class:`~schwaby.streaming.UnparsableMessage`, which is reported to your error
handler and then ends your receive loop --- see
:ref:`Reacting to Absorbed Failures <error_handlers>` for why that one is not
absorbed like the others.

If you see it more than once, try the heuristic decoder before concluding the
stream is broken. It attempts the plain decoding first and falls back to
repairing the escaping Schwab is known to get wrong:

.. code-block:: python

  from schwaby.contrib.util import HeuristicJsonDecoder

  stream_client.set_json_decoder(HeuristicJsonDecoder())

That this exists at all is the evidence that unparsable frames are a quirk of
the venue rather than a sign of a dead connection.

You can supply your own decoder by subclassing
:class:`~schwaby.contrib.util.StreamJsonDecoder`. It must return the decoded
JSON; this library reads it structurally rather than requiring ``dict`` and
``list`` exactly, so a decoder returning a mapping type of your own works ---
but a JSON array must be **indexable**, because routing reads element zero and
handlers are given the frame afterwards. A generator will not serve.

.. automethod:: schwaby.streaming::StreamClient.set_json_decoder

.. autoclass:: schwaby.contrib.util.StreamJsonDecoder
  :members:

.. autoclass:: schwaby.contrib.util.HeuristicJsonDecoder
  :members:
