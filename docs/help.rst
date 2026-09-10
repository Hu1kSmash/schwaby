.. highlight:: python
.. py:module:: schwaby.debug

.. _help:

============
Getting Help
============

Even the most experienced developer needs help on occasion. This page describes
how you can get help and make progress.

.. note::

   Bug reports and questions go to `the tracker
   <https://github.com/Hu1kSmash/schwaby/issues>`__.



--------------
Before You Ask
--------------

Most problems are solved faster by gathering a little information first.

 1. Check that you are on the latest version, and note which version you are
    using. ``print(schwaby.__version__)`` will tell you.
 2. Note your OS and how you are running your code -- a terminal, a notebook, a
    container, an IDE. Several common failures are specific to one of these.
 3. Capture the full stack trace and error message, not just the last line.
 4. Read the logs. Enabling them is described below, and often the answer is
    already in there.


---------------
Reporting a Bug
---------------

``schwaby`` is not perfect. Features are missing, documentation may be out of
date, and it almost certainly contains bugs. If you think of a way in which it
can be improved, we're glad to hear it.

.. _enable_logging:

~~~~~~~~~~~~~~
Enable Logging
~~~~~~~~~~~~~~

Behind the scenes, ``schwaby`` performs diagnostic logging of its activity
using Python's `logging <https://docs.python.org/3/library/logging.html>`__
module. Two things are needed to see it: somewhere for the messages to go,
and a level low enough to let them through.

.. code-block:: python

  import logging

  logging.getLogger('').addHandler(logging.StreamHandler())
  logging.getLogger('schwaby').setLevel(logging.DEBUG)

**The second line is the one people miss.** A handler on its own changes
nothing, because the root logger's default level is ``WARNING`` and almost
everything this library logs is ``DEBUG``. Adding the handler and stopping
there produces a program that looks like it has logging switched on and emits
none of it.

Setting the level on ``schwaby`` rather than on the root logger keeps the output
to this library. Point it at ``''`` instead if you want everything, including
whatever your other dependencies have to say.

Sometimes this additional logging is enough to debug the problem yourself.
Before you ask for help, read through your logs to see whether there is
anything there that explains it.

Note the streaming client reports handler failures on the ``schwaby.streaming``
logger rather than raising them, so a handler which is quietly failing shows up
there and nowhere else.


~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Gather Logs For Your Bug Report
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

If you still can't work out what is going wrong, ``schwaby`` can gather and
prepare logs for filing issues. It captures the logs, anonymizes them, and dumps
them to the console when the program exits. Enable it by calling this method
**before doing anything else in your application**:

.. code-block:: python

  schwaby.debug.enable_bug_report_logging()

This redacts common secrets such as account IDs, tokens and access keys.
However, **that redaction is not guaranteed to be complete, and checking the
logs before sharing them is your responsibility.** Never share the contents of
your token file.

.. warning::

  **Check by listing what is there, not by searching for what you expect.**

  Two people independently redacted a five-frame account-activity capture
  before it was shared. Both searched it for the account number and the stream
  key, found them replaced, and called it clean. It also contained this:

  .. code-block:: json

    {"TradeTag": "TA_janedoeexamplecom1234567890"}

  --- illustrative here, the real one carried a real address --- which is
  ``jane.doe@example.com`` with the ``@`` and the ``.`` removed ---
  assembled that way by Schwab, not by the sender. ``grep`` for the address
  finds nothing. So does any pattern looking for an ``@``. The same file
  carried a customer ID that outlives the account number, and the holder's
  state of residence.

  A search can only confirm what you already suspect. Parse the payload, walk
  every value at every level --- ``MESSAGE_DATA`` is a JSON string, so its
  contents are one parse deeper than they look --- and read the list. Better
  still, if you are building a fixture rather than filing a report, emit only
  the fields the problem needs and drop the rest by construction. Every finding
  in that capture survived being rebuilt that way, because none of them
  depended on an identifier.

For completeness, here is this method's documentation:

.. automethod:: schwaby.debug.enable_bug_report_logging


~~~~~~~~~~~~~~~~~~
Submit Your Ticket
~~~~~~~~~~~~~~~~~~

A good report includes:

 * **The code that triggers it.** If you would rather not share yours, a short
   script which reproduces the problem is just as good, and often narrows it
   down on its own.
 * **The full stack trace**, not an excerpt.
 * **Logs**, attached as a file rather than pasted into the issue body, and
   checked for anything sensitive first.
 * **Your version, OS and execution environment.**

Then `file an issue <https://github.com/Hu1kSmash/schwaby/issues>`__.
