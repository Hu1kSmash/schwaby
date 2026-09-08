.. _utils:

=========
Utilities
=========

Miscellaneous helpers, all presented under the ``Utils`` class:

.. autoclass:: schwab.utils.Utils

  .. automethod:: __init__
  .. automethod:: set_account_hash


.. _extract_order_id:

---------------------------------------
Extract an order ID from a placed order
---------------------------------------

For successfully placed orders, :meth:`place_order
<schwab.client.Client.place_order>` returns the ID of the newly created order,
encoded in the ``r.headers['Location']`` header. This method reads it out of
the response. The ID is what you then use to monitor or modify the order, as
described in the :ref:`Client documentation <orders-section>`.

.. code-block:: python

  from schwab.utils import Utils

  # Assume client and order already exist and are valid
  account_hash = client.get_account_numbers().json()[0]['hashValue']
  r = client.place_order(account_hash, order)
  order_id = Utils(client, account_hash).extract_order_id(r)

Every outcome other than success raises, so there is no ``None`` to check for.
The one worth handling deliberately is
:class:`~schwab.utils.OrderIdNotFoundError`: Schwab accepted the order and did
not give back an ID, which means **the order may be live** and you have no
handle on it.

.. code-block:: python

  from schwab.utils import (
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

.. automethod:: schwab.utils.Utils.extract_order_id


.. _exceptions:

++++++++++
Exceptions
++++++++++

The exceptions this library raises that a caller might reasonably catch.
:class:`~schwab.streaming.ResponseTimeoutError`,
:class:`~schwab.streaming.UnexpectedResponse`,
:class:`~schwab.streaming.UnexpectedResponseCode`,
:class:`~schwab.streaming.UnparsableMessage` and
:class:`~schwab.streaming.UnusableMessage` are streaming-specific and are
covered in :ref:`the streaming documentation <error_handlers>`.

Every exception class this library *defines* inherits
:class:`~schwab.utils.SchwabError`, so ``except SchwabError`` is one name for
all of them. It is not everything the library can raise: argument validation
still raises builtin ``ValueError`` --- a negative quantity, a float price, a
strike finer than the symbol format carries --- and a builtin describes those
correctly. Two of the order exceptions additionally inherit ``ValueError``
because they always did; :class:`~schwab.utils.OrderIdNotFoundError`
deliberately does not.

.. autoclass:: schwab.utils.SchwabError

.. autoclass:: schwab.utils.UnsuccessfulOrderException
  :members:

.. autoclass:: schwab.utils.OrderIdNotFoundError
  :members:

.. autoclass:: schwab.utils.MissingLocationHeaderError

.. autoclass:: schwab.utils.UnrecognizedLocationError

.. autoclass:: schwab.utils.AccountHashMismatchException

.. autoclass:: schwab.orders.common.InvalidOrderException


``TokenRefreshError`` is documented under :ref:`auth` with the retry guidance it
needs, and is not repeated here.

.. autoclass:: schwab.auth.RedirectTimeoutError

.. autoclass:: schwab.auth.RedirectServerExitedError
