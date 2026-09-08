===========================
Contributing to ``schwaby``
===========================

Fixing a bug? Adding a feature? Cleaning something up for the sake of cleaning
it up? All welcome, and no improvement is too small.

Branch from ``main``, one concern per branch. `RELEASING.md
<https://github.com/Hu1kSmash/schwaby/blob/main/RELEASING.md>`__ collects the
rules this project has learned by getting them wrong, and is worth ten minutes
before a first patch.

------------------------------
Setting up the dev environment
------------------------------

Everything, including the development tooling, is declared in ``setup.py``.
There is no separate requirements file.

.. code-block:: shell

  python -m venv venv
  source venv/bin/activate       # Windows: venv\Scripts\activate

  pip install -e ".[dev]"

The ``-e`` matters: without it you edit the source tree and test the copy in
``site-packages``, and the two drift the moment you change anything.

Then check the environment before you change anything, so a later failure is
yours:

.. code-block:: shell

  pytest tests/ -q

Around 950 tests, about six seconds. If your virtualenv is not active in a new
terminal, run the ``activate`` line again; ``deactivate`` turns it off.

-------------------------
Running what CI will run
-------------------------

Four commands. Between them they are everything the ``tests`` workflow checks,
so a green run here is a green run there:

.. code-block:: shell

  pytest tests/ -q                          # the suite
  python -m sphinx -W docs/ /tmp/docs-build # docs, warnings are errors
  python -m build                           # the artifacts
  python -m twine check --strict dist/*     # what PyPI will accept

Two of those are easy to get wrong locally:

- **``-W`` is not optional.** CI builds the documentation with warnings as
  errors. Without it a broken cross-reference builds cleanly on your machine and
  fails on the pull request.
- **``python -m build``, not ``setup.py``.** ``setup.py`` is not imported by the
  suite, so an edit that leaves it unparseable is invisible to ``pytest``. The
  build is what proves the package still assembles.

CI runs the suite on CPython 3.10 through 3.14. Pushes to a branch run Linux
only; pull requests and tags run Windows and macOS as well.

--------------------------
A note about the testing
--------------------------

The suite mocks the network. It proves this library builds the request it
intended to build. It says nothing about whether Schwab accepts that request, or
whether the answer is right.

That distinction has mattered every time. **Every defect found in this library so
far has been in code with 100% line coverage** — prices silently a cent low, a
token file a crash could destroy, an enum advertising values the venue rejects,
redaction that never ran, an order routed to a venue nobody asked for. Coverage
measures which lines executed, not whether what they produced was correct.

So when you add a test: **revert your fix and watch it fail.** A test that passes
either way is worse than no test, because it looks like protection. Two things
that have made that check lie here, both worth knowing:

- Clear ``__pycache__`` first. CPython invalidates bytecode on modification time
  and size, so a same-second edit can leave a stale ``.pyc`` in place and you
  measure the old code.
- Confirm the edit actually applied. A mutation that silently did not change the
  file looks exactly like a test that did not notice.

If a behaviour can only be established against the live API, say so in the pull
request — say what you observed and when. That is a good answer, and a more
honest one than a mocked test implying more than it proves.

-------------------
Documenting changes
-------------------

No feature is complete without a description of how to use it. If your change
alters an external-facing interface or its semantics, the docstrings must say so;
a substantial new module may justify a section of its own.

.. code-block:: shell

  python -m sphinx -W docs/ /tmp/docs-build

Documentation is checked by the suite as well as by Sphinx, which surprises
people, so it is worth knowing what is enforced:

- every ``:func:``/``:meth:``/``:class:`` target and every ``autodoc`` directive
  must resolve to something that exists — ``sphinx -W`` does *not* check this,
  and a page once told readers to call a method that had never existed
- every keyword argument in a documentation code block, and in ``examples/``,
  must be one the callee actually accepts
- every URL host must be on an allowlist, and links into this repository must
  point at a file that exists — including the ``#anchor``, which must match a
  real section heading
- no link target may wrap across a line. Sphinx joins the halves and renders
  it correctly, so the page looks right while everything reading the source,
  the link checker included, sees a truncated URL

If one of those fails, the test says which file and which line.

--------
Examples
--------

``examples/`` is deliberately small. An example earns its place when it teaches
an *assembly* the reference documentation cannot — a bounded queue in front of a
stream, a token-expiry loop, an order followed to a terminal state. A sample that
just repeats a call is already covered by the docs, and an uncovered file is one
that rots: the streaming example spent four years subscribing to tickers that had
been delisted.
