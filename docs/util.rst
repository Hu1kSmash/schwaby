.. _utils:

=========
Utilities
=========

Miscellaneous helpers, all presented under the ``Utils`` class:

.. autoclass:: schwaby.utils.Utils

  .. automethod:: __init__
  .. automethod:: set_account_hash


.. _extract_order_id:

---------------------------------------
Extract an order ID from a placed order
---------------------------------------

For successfully placed orders, :meth:`place_order
<schwaby.client.Client.place_order>` returns the ID of the newly created order,
encoded in the ``r.headers['Location']`` header. This method reads it out of
the response. The ID is what you then use to monitor or modify the order, as
described in the :ref:`Client documentation <orders-section>`.

.. code-block:: python

  from schwaby.utils import Utils

  # Assume client and order already exist and are valid
  account_hash = client.get_account_numbers().json()[0]['hashValue']
  r = client.place_order(account_hash, order)
  order_id = Utils(client, account_hash).extract_order_id(r)

.. note::

  **It returns an** ``int``, and the same order ID reaches you as several
  types depending on where you read it: a JSON number from
  ``get_orders_for_account``, a string inside ``ACCT_ACTIVITY``, and a string
  in the ``Location`` header this parses. Nothing normalises them for you.

  So a dictionary keyed by whatever one call site produced misses lookups from
  another, and it misses them silently --- the symptom arrives much later, as
  an order you placed that you have no record of. Pick one representation and
  convert at every boundary; a consumer running this against funded accounts
  normalises to ``str`` at the single point every placement passes through.

Every outcome other than success raises, so there is no ``None`` to check for.
The one worth handling deliberately is
:class:`~schwaby.utils.OrderIdNotFoundError`: Schwab accepted the order and did
not give back an ID, which means **the order may be live** and you have no
handle on it.

.. warning::

  This raises rather than returning ``None``, which means
  **anything between the call and its return value is skipped** on the
  failing path rather than running with a ``None`` in hand. In one live
  deployment that was the bookkeeping which records an order as the program's
  own, and skipping it made the system report its own orders as manual trades.
  If you have code in that position, move it or catch around it.

.. code-block:: python

  from schwaby.utils import (
      AccountHashMismatchException,
      OrderIdNotFoundError,
      UnsuccessfulOrderException,
  )

  order_id = None
  try:
      order_id = Utils(client, account_hash).extract_order_id(r)
  except UnsuccessfulOrderException as exc:
      # Nothing was placed. Schwab's own reason is in the message.
      log.error('rejected: %s', exc)
  except OrderIdNotFoundError as exc:
      # Something probably was placed. Go and find it.
      log.error('placed but untracked: %s', exc)
      reconcile_recent_orders(account_hash)
  except AccountHashMismatchException as exc:
      # An order IS live, on a different account. This one carries its id.
      log.error('order %s live on account %s', exc.order_id, exc.account_hash)
      raise

  if order_id is None:
      return          # nothing to follow; the branch above said why

Note ``order_id = None`` before the ``try``. Two of those branches handle rather
than re-raise, so without it the name is unbound afterwards and using it raises
``NameError`` --- in the middle of deciding what happened to an order.

Note also what sits *between* the call and whatever you return. This method
raises rather than returning a sentinel, so journalling or bookkeeping written
after it is skipped on exactly the path where an order may be live and
unrecorded. Put that work above the call, or inside the handlers.

.. _reconciling:

~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Finding an order you have no ID for
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``reconcile_recent_orders`` above is a placeholder for the work this section
describes. :class:`~schwaby.utils.OrderIdNotFoundError` means Schwab took the
order and did not hand back a handle, so the only way to find it is to ask what
the account has done lately and recognise it:

.. code-block:: python

  import datetime

  from schwaby.client import Client

  def find_recent(client, account_hash, symbol, quantity, placed_at):
      '''Returns orders that plausibly match one just placed.'''
      window = datetime.timedelta(minutes=10)
      r = client.get_orders_for_account(
              account_hash,
              from_entered_datetime=placed_at - window,
              to_entered_datetime=placed_at + window)
      r.raise_for_status()

      matches = []
      for order in r.json():
          for leg in order.get('orderLegCollection', []):
              if (leg.get('instrument', {}).get('symbol') == symbol
                      and leg.get('quantity') == quantity):
                  matches.append(order)
      return matches

Capture ``placed_at`` **before** calling
:meth:`~schwaby.client.Client.place_order`, not after you have caught the
exception --- by then you are guessing at a time you could have recorded.

.. warning::

  **This identifies orders; it does not identify *the* order.** Nothing in the
  response distinguishes the order you just placed from an identical one placed
  by a strategy running beside you, or by a human at a terminal. If
  ``find_recent`` returns more than one, that is a situation for a person, not
  for code that cancels the first match.

  Give the datetimes a timezone. Naive ones are read as the wall clock of
  whichever machine is running --- see :ref:`the note in the client
  documentation <client>` --- which on a container set to UTC silently shifts
  the window by hours and can return nothing at all.

Every order carries ``orderId``, so once a match is confirmed you have the
handle the failed call could not give you.

.. automethod:: schwaby.utils.Utils.extract_order_id


.. _exceptions:

++++++++++
Exceptions
++++++++++

The exceptions this library raises that a caller might reasonably catch.
:class:`~schwaby.streaming.ResponseTimeoutError`,
:class:`~schwaby.streaming.UnexpectedResponse`,
:class:`~schwaby.streaming.UnexpectedResponseCode`,
:class:`~schwaby.streaming.UnparsableMessage` and
:class:`~schwaby.streaming.UnusableMessage` are streaming-specific and are
covered in :ref:`the streaming documentation <error_handlers>`.

Every exception class this library *defines* inherits
:class:`~schwaby.utils.SchwabError`, so ``except SchwabError`` is one name for
all of them. It is not everything the library can raise: argument validation
still raises builtin ``ValueError`` --- a negative quantity, a float price, a
strike finer than the symbol format carries --- and a builtin describes those
correctly. Two of the order exceptions additionally inherit ``ValueError``
because they always did; :class:`~schwaby.utils.OrderIdNotFoundError`
deliberately does not.

.. py:exception:: schwaby.utils.HTTPStatusError

  What ``raise_for_status()`` raises on a response from any client call, under
  a name this library owns. It is re-exported from ``httpx2`` rather than
  defined here, so it does **not** inherit :class:`~schwaby.utils.SchwabError`.

  .. code-block:: python

    from schwaby.utils import HTTPStatusError

    try:
        response = client.get_quote('AAPL')
        response.raise_for_status()
    except HTTPStatusError as exc:
        status = exc.response.status_code

  **Keep the call inside the** ``try``, not only ``raise_for_status()``. The
  session refreshes an expired access token on the way past, and when the
  token endpoint answers with a server error this class is raised out of the
  call itself --- with ``exc.response`` describing the token request rather
  than the one you made.

  Below 500, whatever the status, the token response is parsed as JSON, and
  what comes out of the call depends on the body:

  - a JSON object carrying an ``error`` key is Schwab rejecting the refresh,
    and raises :class:`~schwaby.utils.TokenRefreshError`;
  - any other JSON that is not a usable bearer token --- an object without an
    access token or a usable expiry, one whose refresh token is present but
    empty, a list, a string, ``null`` --- also raises
    :class:`~schwaby.utils.TokenRefreshError`. Nothing is stored, and the next
    call tries the refresh again;
  - a body that is not JSON at all, such as an empty body or an HTML page,
    raises a ``ValueError`` --- usually ``json.JSONDecodeError``, or
    ``UnicodeDecodeError`` for a body that is not UTF-8 --- and nothing is
    stored either.

  What Schwab's token endpoint sends in the last two cases has not been
  observed.

  **It covers HTTP status, not the network.** A timeout or a dropped
  connection raises one of ``httpx2``'s transport errors ---
  ``httpx2.TimeoutException``, ``httpx2.ConnectError`` and the rest of
  ``httpx2.TransportError`` --- which this name does not catch.

  For status errors, catch it by this name rather than by importing an HTTP
  package yourself. The client's responses come from ``httpx2``, which shares
  no exception hierarchy with ``httpx``, so a handler written against the other
  package never runs --- and nothing reports that it did not.

.. autoclass:: schwaby.utils.SchwabError

.. autoclass:: schwaby.utils.UnsuccessfulOrderException
  :members:

.. autoclass:: schwaby.utils.OrderIdNotFoundError
  :members:

.. autoclass:: schwaby.utils.MissingLocationHeaderError

.. autoclass:: schwaby.utils.UnrecognizedLocationError

.. autoclass:: schwaby.utils.AccountHashMismatchException

.. autoclass:: schwaby.orders.common.InvalidOrderException


``TokenRefreshError`` is documented under :ref:`auth` with the retry guidance it
needs, and is not repeated here.

.. autoclass:: schwaby.auth.RedirectTimeoutError

.. autoclass:: schwaby.auth.RedirectServerExitedError
