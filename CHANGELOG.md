# Changelog

Release notes for `schwaby`, newest first, written for someone deciding whether
to upgrade.

`schwaby` began from [`alexgolec/schwab-py`](https://github.com/alexgolec/schwab-py)
and has been a separate project since 2.2.0; the
[README](README.md#where-this-came-from) has that story, and Alex Golec's
copyright and MIT licence are retained unchanged. The only trace of it in this
file is the numbering: **1.6.0 is the first release of this project, and
everything at or below 1.5.0 is `schwab-py`'s.** Those notes live in its
repository. 1.5.0 is still `schwab-py`'s latest release.

Entries are left as they were written on the day they shipped. The older ones
describe a project that still tracked upstream and still called itself a fork,
because at the time it did; none of that has been rewritten to agree with how
things work now, because a changelog edited to match the present is not a
record of anything.

Getting a fact wrong is the exception. Where an entry says something that was
untrue when it was written, it gets corrected and the correction says so.

---

## Unreleased

### A field Schwab adds no longer takes the feed down

4.2.0 made `decode_decimal` refuse a decimal object carrying any key it did
not recognise. That was the wrong direction and this reverses it.

The reasoning behind the refusal was that an unknown key could be a renamed
mantissa, and decoding around it would report a real value as zero — which is
what a $0 commission and a completed fill look like. True, and it ignored how
the two failures differ in blast radius. **A key Schwab adds appears on every
decimal object at once**, so refusing it turns a harmless schema addition into
every money and quantity field on the feed failing together, on a morning
nobody chose. The encoding is positional — `lo`, `mid` and `hi` are fixed
slices of one 96-bit mantissa — so a fifth key does not move the other four.

So an unrecognised key alongside `lo`, `mid`, `hi` or `signScale` is now
**ignored and logged once per key** on the `schwaby.contrib.util` logger,
which `enable_bug_report_logging` now collects. An object carrying **none** of
those four is still refused: that is not a decimal object, and the
mantissa-less rule would decode it as a genuine zero.

One shape is knowingly given up: a mantissa under a name this library does not
know, beside a valid `signScale` — `{"low": 1, "signScale": 12}` — still reads
as zero. A serializer does not typo, so that means a rename rather than
corruption, and a rename is visible in the same log line on the first message
that carries it.

If you see that warning, please [open an
issue](https://github.com/Hu1kSmash/schwaby/issues) with the field. The
encoding is undocumented publicly and a capture is the only way anyone learns
what a new key means.

### A field Schwab adds to a stream is now visible, not just survivable

Nothing else in the library *rejects* an unexpected field — measured rather
than assumed. An unknown numeric field id on a stream you subscribe to is
delivered to your handler under its numeric key while every known field still
relabels, so a new field reaches you rather than breaking you. That half
already worked and has not changed.

What did not work is finding out. It was silent, so a field could appear and
sit in your messages indefinitely with nothing to notice it. It is now logged
once per field per table on the `schwaby.streaming` logger.

Not reported through `add_error_handler`: nothing was absorbed and nothing
failed. It is a change in the venue, which is an operator's concern rather
than a caller's.

The discriminator is that Schwab's field ids are numeric strings. `key`,
`seq`, `delayed` and `assetMainType` all arrive alongside the numbered fields
and none of them is a field, so reporting anything merely missing from the
table would log four lines per message — a flood, which hides the one line
that matters.

## 4.2.0

*2026-09-09*

### `schwaby.contrib.util.decode_decimal`

New, and the first thing in this release that is code rather than prose.

Schwab streams money and quantities on `ACCT_ACTIVITY` as a serialized .NET
`System.Decimal` — `{"lo": "6860000", "signScale": 12}` — not as numbers. The
encoding is undocumented publicly; Schwab's Trader API support confirmed the
conversion in writing. Every consumer ends up writing this, and the first
attempt is reliably wrong in at least one of five ways, each of which is a
**wrong number rather than an error**:

- the mantissa spans `lo`, `mid` and `hi`, so reading `lo` alone truncates
  anything over `4294.967295` at `signScale` 12 — `$5,000.00` decodes as
  `$705.03`;
- an odd `signScale` means negative, and the sign is not the side;
- an object with a `signScale` and no mantissa is **zero**, not unknown — it
  arrives that way as `LeavesQuantity` on the final fill of a completed order,
  where reading it as unknown reports a complete fill as outstanding;
- an object with a mantissa and **no `signScale` at all** is scale 0, not an
  error and not the usual 12. `AskSize` and `BidSize` arrive as
  `{"lo": "19200"}` beside an `Ask` of `{"lo": "13720000", "signScale": 12}`
  in the same quote — $13.72 with 19200 on the ask. Guessing 12 there is
  wrong by a factor of a million; refusing it loses both sizes on every
  quote;
- `10 ** (signScale // 2)` on a hostile scale builds an astronomical integer
  and hangs the thread, which a per-item `try`/`except` cannot rescue.

It returns a `decimal.Decimal`, never a float, so a decoded price can be fed
straight back into `set_price`.

Every member is validated by shape — an unsigned 32-bit integer, or a string
of ASCII digits inside that range — rather than coerced with `int()` and the
resulting exceptions enumerated. That distinction is the whole of this
function's review history and it is worth stating, because coercion fails in
both directions: `int()` raises a bare `ValueError` on some corruption and
silently *accepts* the rest. `int(1.9)` is 1, `int(True)` is 1, `int('1_0')`
is 10 under PEP 515, and `int('٣')` is 3. Bare values go through the same
validation, because `decimal.Decimal`'s parser is more permissive still — it
takes `'1_0'`, `' 12 '`, `'+12'`, `'12_'` where `int` refuses it outright, and
every Unicode decimal digit. Anything that is not a number this encoding could
have carried raises `UnusableDecimalScale`, which is both a `SchwabError` and
a `ValueError`, so one `except` around each field keeps the rest of the
message.

#### What five review rounds on it found

Worth recording, because the function is 80 lines and every round found
something, and because the shape of what they found is the argument for
shipping a tested one rather than a documentation sample:

- **The caller's `decimal` context changes the answer.** Construction of a
  `Decimal` is context-free; every *operation* is not, including `scaleb` and
  unary minus. A consumer who sets `getcontext().prec = 6` elsewhere in their
  process — an ordinary thing to do when formatting money — got `1234.57` for
  a price of `1234.5678`. Fixed by building the value from a string with the
  sign in it; a first fix that negated a string-built positive was exact for
  positive values and rounding for negative ones, which is the branch
  `EstimatedPrincipalAmount` arrives on.
- **A scale is bounded, not computed with.** Leaving it to the arithmetic is
  not enough: every scale between roughly two and four million underflows to a
  zero that compares equal to zero, and zero is what a completed fill looks
  like.
- **The library refused a shape Schwab actually sends.** The `{"lo": "19200"}`
  case above. It raised, and the docstring said the shape had never been
  observed, while a capture carried eight of them. Found by decoding real
  traffic rather than by reading the code — the only finding of the five
  rounds that reading could not have produced.
- **A guard's exception list covers the shapes you thought of.** Three
  consecutive rounds each added an exception type to the coercion path and the
  next round found a shape raising a different one, or none at all. Replaced
  with shape validation, which is what the bullet above describes.
- **The fix landed on one branch and not its sibling.** Members were validated
  while bare values, four lines away, still went to `Decimal`'s permissive
  parser. Both go through one list now, driven by one test, so they cannot
  drift apart again.

None of this changes a decoded value for any payload anyone has captured: all
37 decimal objects in the reference capture decode to the same numbers before
and after, except the eight that used to raise.

This library still models nothing else inside `MESSAGE_DATA`, and this does not
change that — it decodes one primitive encoding, in `contrib`, alongside the
JSON decoders the streaming docs already recommend. The documentation carried
this as a code sample until now, which meant every consumer copied it; the
sample was itself wrong about `mid` for a few hours today, which is the
argument for shipping one tested version instead.

### Documentation

**The `Duration` enum lists four values Schwab rejects for equity orders**, and
now says so where someone setting a duration will see it. `IMMEDIATE_OR_CANCEL`,
`END_OF_WEEK`, `END_OF_MONTH` and `NEXT_END_OF_MONTH` come back `HTTP 400`. The
enum mirrors Schwab's schema rather than what the equity endpoint takes, and the
rejection happens at *placement* rather than at build — so `OrderBuilder` builds
the order without complaint and it fails on the live path. Two consumers have
built a time-in-force table from this enum and been wrong; it was documented on
the enum's own docstring, which is not where either of them looked.

**The `{lo, signScale}` decoder published in 4.1.0 was wrong for large
numbers.** It read only `lo`. The encoding is a serialized .NET
`System.Decimal` whose 96-bit mantissa spans `lo`, `mid` and `hi`, so at
`signScale` 12 anything above **4294.967295** overflows into `mid` and a
`lo`-only decoder returns a plausible smaller number with no exception —
`{"lo": "705032704", "mid": 1, "signScale": 12}` is $5,000.00 and decoded as
$705.03. A principal, a total or a share price reaches that easily. Reasoned
from the .NET layout rather than observed: no captured payload has carried a
non-zero `mid`, but a value that does not fit in 32 bits cannot be sent in `lo`
alone.

Three more facts about the same encoding, from the consumer who took it to
Schwab: the conversion was **confirmed in writing by Schwab Trader API
support** on 2026-06-24, who stated it while confirming it remains publicly
undocumented; a decimal object with a `signScale` and no `lo` is **zero, not
unknown**, measured on a `LeavesQuantity` arriving on the final fill event of a
completed order, where reading it as unknown makes a complete fill report as
outstanding; and the sign is not the side — direction comes from `BuySellCode`,
and the odd-`signScale` branch exists so a genuinely negative field does not
decode positive.

**A normalizer in front of the setters defeats the `bool` rejection.** A
consumer adapting to 4.1.0 wrote a helper to turn a `numpy.int64` quantity into
a plain `int`; `float(True)` is `1.0` and `(1.0).is_integer()` is `True`, so it
turned `True` into `1` and rebuilt the silent one-share order 4.1.0 was released
to close — in code written to accommodate that very fix. Documented next to the
accepted-types table.

**Three hazards were re-graded from `warning` to `danger`**, by consequence
rather than by feel: the asymmetric argument order on `cancel_order` and
`get_order`, which sends a well-formed request naming the wrong account or the
wrong order; `replace_order` returning a new ID, where anything tracking the
old one concludes the position closed while the replacement is still working;
and `'{:.2f}'.format(value)` rounding a buy limit up, which is a fill one tick
worse than intended. All three can cost money, which is the line `danger` is
for. The docs carry more of these than they did; the levels are what changed.

**A removed date left a sentence broken.** That same docstring read "for every
asset type. As of / Schwab accepts only" — release step 4 strips dates from
shipped documentation and greps for dates, not for the wreckage removing one
leaves. It shipped that way through 4.1.0. Fixed, and there is now a check that
no line ends with `As of`, since nothing but a date follows it.

**Removed the `httpx` warning from the HTTP client page.** It showed the wrong
code — `import httpx`, `except httpx.HTTPStatusError` — inside a `warning`
block, which is the most visually prominent thing on the page. Someone skimming
for a retry pattern could reasonably have copied it, which is the opposite of
what it was for.

It was written for readers arriving from `schwab-py`, and this has not been a
fork since 2.2.0. The section already says responses come from HTTPX2 and links
it, and every example on every page imports `httpx2`, so the fact is carried by
the code rather than by a caution about a package this library does not depend
on.

## 4.1.0

### An order field that is not a number is refused, and says which field

`OrderBuilder`'s numeric setters --- `set_quantity`, `set_stop_price_offset`,
`set_price_offset` and `set_activation_price` --- validated by asking whether
`float()` could parse the value. That is the wrong question in both directions,
and the two directions failed differently.

**Things that are not numbers answered no, and were let through.**
`set_quantity(None)` reached `quantity <= 0` and raised
`TypeError: '<=' not supported between instances of 'NoneType' and 'int'`,
naming neither the field nor the value. The two offset setters have no
comparison after the check at all, so they *accepted* it --- and since a `None`
is dropped when the order is built, `set_stop_price_offset(None)` produced an
order with no offset on it and no complaint. A trailing stop with no offset is
a different order from the one that was asked for, placed without an error.

**And things that are not numbers answered yes.** `float(b'1')` is `1.0`, so a
`bytes` passed the check and was stored as `bytes`; `json.dumps` refuses those,
so the order was not wrong so much as unsendable, discovered at the point of
sending it.

**A string on a numeric field was taken as well.**
`set_price_offset('abc')` built `{"priceOffset": "abc"}` and sent it. Schwab
types these fields as numbers; the string branch exists for the *price* fields,
which are strings in the schema, and the validator could not tell the two kinds
of field apart. It is now told which it is guarding, the same way it already
distinguished them for `decimal.Decimal`.

All four now raise a `ValueError` naming the field, and take an `int` or a
`float` and nothing else. The predicate is `_build_object`'s own, deliberately:
a `fractions.Fraction` and the numpy scalar types are numbers mathematically
and still cannot be serialized into an order, so they raise here rather than
at `build()` with `vars() argument must have __dict__ attribute`.

The price fields are unchanged: they still take a string or a
`decimal.Decimal`, and an unparseable price string is still left between the
caller and Schwab.

### What actually changes, measured against 4.0.0

Generated by running both versions rather than written from memory --- two
earlier attempts at this table were wrong, in opposite directions, about what
4.0.0 did.

| 4.0.0 | now | which calls |
| --- | --- | --- |
| setter raised `TypeError` | `ValueError` naming the field | `set_quantity` / `set_activation_price` given a `str`, `None` or `bytes`; the prebuilt templates likewise, since their leg goes through the same check |
| setter raised `ValueError` | `ValueError`, different message | `set_quantity(False)`, `set_activation_price(False)` --- `False <= 0` already caught these |
| **setter returned, `build()` then raised `TypeError`** | **setter raises** | a `fractions.Fraction` anywhere; `bytes` on the two offsets |
| **built an order** | **setter raises** | the two offsets given a `str`, `None`, `True` or `False`; `set_quantity(True)`; `set_activation_price(True)` |

Only the last two rows are breaking, and the fourth is the one that matters: a
call that produced a placeable order now raises. The third moves a failure from
`build()` to the setter, which is the same failure arriving somewhere useful.

Note what is *not* claimed: that Schwab refuses a string there. Schwab's schema
types `price` and `stopPrice` as `number($double)` too, and this library sends
those as strings because that is what the venue takes --- so the schema does
not separate the two groups, and nothing here has been measured against a live
account. The reason is that the four setters disagreed with each other:
`set_quantity` and `set_activation_price` already refused a string through
their range check, and the two offsets accepted one only because nothing
downstream compares them to zero. That is an accident of which setters have a
range check, not a decision anyone made.

### The four numeric setters now agree with each other

Worth stating on its own, because it changes what working integrator code has
to pass. Through 4.0.0 the builder enforced three different rules and nothing
said so:

| setter | 4.0.0 accepted | 4.1.0 accepts |
| --- | --- | --- |
| `set_price`, `set_stop_price` | `str`, `Decimal` | unchanged |
| `set_quantity`, `set_activation_price` | `str` → `TypeError`, `bool` → **accepted** | `int`, `float` |
| `set_price_offset`, `set_stop_price_offset` | `str`, `int`, `float`, `bool`, `None` | `int`, `float` |

`set_quantity(True)` used to be accepted, silently: `True` is an `int`
subclass, so it passed every check. Measured, it serializes as
`{"quantity": true}` — a JSON boolean, not `1`. What Schwab does with that has
not been observed, so the claim here is the checkable one: the order was built
without complaint carrying a value that is not a number in the schema, which is
unsendable rather than wrong. It is refused now, on all four setters.

The two price setters are unchanged and are deliberately the opposite rule:
they take a `str` or a `decimal.Decimal` and refuse an `int` or a `float`,
because a float cannot carry a price exactly. So the builder has two rules, not
one. `docs/order-builder.rst` now states the accepted types **per setter**, in
a table the test suite checks against the validators — in both directions, so
it fails if the table lies and if a validator drifts from it.

### Documentation, from a consumer running this against funded accounts

Findings that could not have come from reading the library, each measured:

**The account-activity payload.** A cancel emits `ExecutionCreated` carrying a
non-zero `ExecutionQuantity` on an order that filled nothing — only
`ExecutionTransType` distinguishes it from a fill, so a consumer reading
quantities by shape books a phantom fill on every reprice. `ResponseType` and
`RouteStatus` arrive as *either* the label or its ordinal, two milliseconds
apart on the same order. Numbers are `{lo, signScale}` and an **odd**
`signScale` means negative, so a decoder handling only the even case flips the
sign on every cash-direction field. Timestamps arrive as `{}` rather than null,
`MESSAGE_DATA` is a JSON string, and content items within one message are not
in lifecycle order.

**The client surface.** `place_order`, `replace_order` and `preview_order` take
the account hash first; `cancel_order` and `get_order` take the order ID first.
Both are opaque strings, so swapping them raises nothing locally.
`Utils.extract_order_id` returns an `int` where order IDs are strings
everywhere else. And `easy_client` opens a browser when the token ages out,
which on a daemon is a window nobody sees — the install page now points
unattended processes at `client_from_token_file`.

### Tests for eighteen statements that had none

Reading every uncovered line in the package rather than the percentage. Most
were input validation that simply had no test --- `OptionSymbol`'s rejection of
a malformed expiration date, an unknown contract type and a wrong expiration
*type*, plus its accepted-but-unexercised `datetime.datetime` argument;
`convert_enum_iterable`'s enforcement arm; `Utils.set_account_hash`, which is
public and documented and was called by nothing; `_describe_error` on a body
that is not a JSON object; the token-refresh classifier on a non-string
description; `set_json_decoder`'s type check; and receiving before `login()`.

One was worth more than the rest. **`client_from_access_functions(asyncio=True)`
was never called by any test.** Every `client_from_*` entry point funnels into
that function, and its asyncio branch --- which selects the async session and
client, and wraps the token writer in an `async def` --- was unreached. The
line that actually writes the token there carried a `# pragma: no cover`, so it
was excluded from measurement as well as untested: a write that quietly did
nothing on the async path would have shown up as neither a failure nor a gap.
It is tested and measured now, and the pragma is gone.

Coverage went from 98.40% to 99.31%, which is not the point; what changed is
that fourteen of those statements were reachable from a public method with a
plausible argument.

## 4.0.0

**The package you import is now `schwaby`, not `schwab`.**

```python
import schwaby                      # was: import schwab
from schwaby.auth import easy_client
```

Every method, argument, enum and return value is what 3.0.3 shipped, and
nothing was added or removed from the API.

**Three things beyond the import line carry the name, and a find-and-replace
over imports alone will miss all of them.**

*Loggers are now `schwaby.*`.* They were `schwab.auth`, `schwab.client.base`,
`schwab.streaming` and `schwab.debug`, and they are now the same names under
`schwaby`. Anything configuring or filtering on them needs updating --- which
includes the incantation this project's own help page recommends:

```python
logging.getLogger('schwaby').setLevel(logging.DEBUG)   # was: 'schwab'
```

A log filter that silently stops matching is the failure worth calling out
here: it does not raise, and the symptom is an absence.

*Fully-qualified class names in a few error messages* now read `schwaby.…`.
Anything matching on those strings needs the same treatment. They are
diagnostics rather than an interface, but a test asserting on one will fail.

*The login flow's internal status path is now `/schwaby-internal/status`.* This
is the one actual behaviour change, and it is here because of what the release
makes possible. `client_from_login_flow` decides whether the thing listening on
your callback port is its own server by fetching that path and requiring a 200.
It was `/schwab-py-internal/status` --- the path `schwab-py` serves, for the
same purpose --- so a `schwab-py` login flow already holding the port answered
it, the check read that as success, and the browser delivered the authorization
code into the other project's queue while this one timed out. Unreachable while
the two could not be installed together; merely unlikely now. Nothing calls
this path but the library itself.

### Why

The distribution has been `schwaby` since 2.6.0 while the package stayed
`schwab` — the same directory `schwab-py` ships. That is what made the two
impossible to install together: `pip` does not know they are the same project,
so installing one over the other left both registered and both claiming the
same files, and `pip uninstall schwab-py` afterwards deleted the shared files
and destroyed the install.

Every workaround for that was a workaround for the shared name:

- an import-time check that scanned `sys.path` to detect the collision, which
  grew to 224 of the 234 lines in `__init__.py`. Ten remain.
- 17 tests and 15 red-proof cases holding it in place
- a `WARNING` block in the README and an install-order caveat in
  getting-started, both of them telling you to uninstall `schwab-py` first
- 3.0.2 spent entirely on reworking the check after it false-positived on this
  repository's own source tree

**Keeping `import schwab` working and removing the collision were mutually
exclusive.** Any compatibility shim would still have to ship a directory called
`schwab`, which is the collision. There was no version of this that gave both.

All of it is gone. `__init__.py` is ten lines.

### What this buys

`schwaby` and `schwab-py` now install side by side and do not interfere. They
ship different packages, so both can be present and you import whichever you
mean — which also makes it possible to compare the two against one account,
something that was not possible before.

### Migrating

Change the imports. There is no compatibility shim and there deliberately is
not one: a package named `schwab` is the thing that collides, so shipping one
to ease the transition would reintroduce exactly what this release removes.

If you pin `schwaby<4` you keep 3.0.3, which continues to work and continues
to collide.

**If you currently have both `schwaby` and `schwab-py` installed, reinstall
`schwab-py` after upgrading.** This is the one upgrade that breaks something,
and it breaks quietly.

`pip install -U schwaby` uninstalls 3.0.3 first, and uninstalling a
distribution deletes every path it recorded at install time. 3.0.3 recorded
`schwab/*` --- the same paths `schwab-py` had overwritten --- so pip removes
`schwab-py`'s files while still listing it as installed. Measured: 21 of the 25
files under `schwab/` go, `pip check` reports no broken requirements, and
`import schwab` still *succeeds*, because the four files 3.0.3 never shipped
survive and keep the directory a package. The first real use is what fails:

```
>>> import schwab.auth
ModuleNotFoundError: No module named 'schwab.auth'
```

Either uninstall `schwab-py` before upgrading, or run
`pip install --force-reinstall schwab-py` afterwards. Nothing needs doing if
you only ever had `schwaby`.

---

## 3.0.3

Documentation only. No behaviour changed, and the library's code is
byte-identical to 3.0.2 with docstrings stripped.

Upgrade if you read the documentation. Nothing about a running program changes.

### Three worked examples did not run

The first code on the HTTP client page imported `client_from_manual_flow` and
then called `easy_client`, which it never imported. Its asyncio counterpart did
the same and added two more faults: `redirect_uri`, which is not a parameter of
`easy_client`, and a missing `app_secret`, which is required. Copying either
gave a `NameError` or a `TypeError` before reaching the part you came for. Two
more examples used `httpx2` without importing it.

The test that exists to catch this could not see it. `DocExampleTest` resolves a
call only when the block imported that name from `schwab`, so the wrong import
concealed the wrong argument. With the imports corrected it catches the argument
error, verified by putting it back.

### Two links sent readers to the wrong place

`.. _orders-section:` labelled *Current Quotes*, and `util.rst` links to it when
explaining how to get an order ID. `:ref:`enable_logging <help>`` had its label
and target swapped, so it went to the top of the help page rather than to the
section showing the incantation.

### The logging instructions did not enable logging

`help.rst` told you to add a handler to the root logger and stop there. The root
logger defaults to `WARNING` and this library emits twelve debug calls and four
info calls against three warnings, so that snippet dropped almost everything it
existed to show. Anyone following the page to gather evidence for a bug report
got a program that looked instrumented and emitted nothing.

### The venue selector was documented nowhere

`set_requested_destination` appeared in no page. The section titled "Requested
Destination" documented `set_destination_link_name`, which is not how a venue is
chosen — Schwab types `destinationLinkName` as a free string and `Destination`'s
values belong to `requestedDestination`. Both are documented now, with the
distinction stated.

### What the movers and screener endpoints actually do

Measured against a live account rather than read from Schwab's documentation,
which is wrong or silent on all of it.

`get_movers` **ignores `sort_order` and `frequency`**. Both reach the wire and
the response is identical whichever values are passed: always the top ten by
share volume, always whole-session. Two different sort values returned identical
lists in all 495 index-cycles of a full session. `PERCENT_CHANGE_DOWN` does not
invert the ranking.

`totalVolume` **is the index total, not the instrument's** — identical on every
row, in all 990 responses of that session. `marketShare` is `volume /
totalVolume * 100` exactly, so it carries nothing the other two do not, and its
value depends on which index was queried.

**Membership barely moves.** Median zero changes between consecutive five-minute
polls, maximum one, twelve or thirteen distinct symbols across four hours. That
follows from ranking on cumulative session volume, which only grows.

**`INDEX_ALL` does not return indices.** It returns equities, it is not
`EQUITY_ALL`, and none of its members appeared in the `NYSE` list at any point
while eight or nine of ten appeared in `NASDAQ`. What it selects is undocumented
and is not established here, and the docstring says so.

### The streaming screener

**The subscription key is not a stock symbol.** It is
`(PREFIX)_(SORTFIELD)_(FREQUENCY)`. `docs/streaming.rst` always had this right;
the docstrings, which are what a reader sees at the call site, said "Equity
symbols to subscribe to".

**Schwab does not reject a malformed key.** `NOT_A_PREFIX_VOLUME_5`,
`NASDAQ_NOT_A_SORT_5`, `NASDAQ_VOLUME_7` and a bare `AAPL` each returned
`code: 0, "SUBS command succeeded"` and delivered nothing. There is no error to
catch, so a typo is indistinguishable from a quiet market. `AVERAGE_PERCENT_VOLUME`
behaves the same way despite being documented by Schwab.

**A `subs` replaces, an `add` appends, and the two screener services are
independent.** This page previously said the results "vary", were undocumented,
"seem to" clear the old subscription, and that the interpretation "may be
incorrect". The rules now come from grouping a capture's frames by subscription
key, and they account for every frame. The consequence worth knowing: a `subs`
carrying a key Schwab silently ignores still replaces a working subscription.

**A screener push is the whole list, not a change to it.** Both screener
services are *Whole* in Schwab's classification, so a symbol missing from a push
means "not in this snapshot" rather than "removed".

**One streaming session per account, and a second does not fail cleanly.** It
takes the connection from the first, and if both reconnect they bump each other
indefinitely. Nothing in the protocol names the intruder. A bumped stream still
delivers some messages, so it has to be treated as a stop rather than a warning.

Push cadence, the immediate frame on subscribe, and the equivalence between
stream frequency 0 and the REST endpoint are recorded with their sample size —
one nine-minute window — because that is what they rest on.

`$SPX.X` is corrected to `$SPX`. Over REST it is an HTTP 400; on the stream it
is accepted and silent forever.

### Everywhere

Dates and version history are out of the shipped documentation. A finding
measured against a live account still says so, because Schwab's documentation
has been wrong about several of these, but the date it was measured only asks a
reader a question they cannot answer. What changed belongs in this file.

Five hyperlink targets were split across a line. Sphinx joins them and the pages
render correctly; everything reading the source, `LinkTest` included, was seeing
`https://www.sec.gov/` and `https://www.investopedia.com/articles/active-trading/032614/`.
Now guarded by a test.

All ten documentation pages were read line by line. Roughly 250 lines of
trailing whitespace, a section that hyperlinked to itself, prose contradicting
its own JSON, an exception list naming four of five, and a composite-order
section that showed only the mistake and never the correct call.

---

## 3.0.2

One fix, to the collision warning 3.0.1 added, and the defects found while
making it.

### The warning fired on this project's own working tree

3.0.1 warned whenever it found a `schwab_py-*` distribution registered. That is
not enough to conclude anything: `schwab_py` is the name **this** project
published before 2.6.0, so an editable install made from a checkout of that era
registers it, and the warning reported a collision between the working tree and
itself. It fired on every `pytest` run in this repository.

The check now requires both names — `schwaby` **and** `schwab-py` — before it
says anything, which is what "both are installed" was always supposed to mean.

### What else changed in the same code

Everything below is a defect the check had independently of the false positive,
found by reviewing the fix for it.

- **One known false positive, left in deliberately.** A virtualenv that
  carried a pre-2.6.0 *editable* install and then received `pip install -e .`
  holds both names for a single source tree, and this warns about it. Telling
  that apart needs `direct_url.json` from both registrations; that was
  written, reviewed and then removed. It was a third of the check's code and
  produced both of the only two serious defects five review rounds found —
  one arrangement of registrations went entirely unwarned because of it.

  What it bought was suppressing a wrong warning for someone who can clear it
  with `pip uninstall schwab-py` (safe here: an editable install's `RECORD`
  claims no files under `schwab/`). That is not worth the code, and it is not
  worth the risk that the code silences a real collision.

- **Both `.dist-info` and `.egg-info` count, with a discriminator.** On a
  Debian or Ubuntu system interpreter the distro-packaged modules register as
  `.egg-info` and almost nothing else does — 73 against 35 `.dist-info` on the
  machine this was measured on — so skipping the layout would hide a
  legacy-installed `schwab-py`, which is exactly the old install this check
  exists to find. What separates a real install from the build artefact a
  source tree accumulates is the directory: an install directory never contains
  `setup.py` or `pyproject.toml`, and a checkout always does.

- **The distribution name is split at the version, not at a hyphen.** A name
  can contain one (`schwab-py-2.5.1`) and can end in one (`schwab-py`,
  unversioned), so neither the first nor the last hyphen is the boundary. A
  version component always begins with a digit and a name never does. The
  legacy `setup.py install` spelling, `<name>-<version>-py<X.Y>.egg-info`, is
  handled too.

- **Warnings-as-errors is told, and the import still survives.** Under
  `PYTHONWARNINGS=error`, `-W error`, or a consumer's pytest
  `filterwarnings = error`, `warnings.warn` raises. Failing `import schwab`
  over a condition where the files on disk still work is the opposite of what
  this check is for; swallowing the raise would leave the configuration that
  asked to be told loudest the only one told nothing. The raise is caught and
  the same text printed to stderr — as is a `warnings.showwarning` replacement
  that raises something else, and a stderr closed by the time the fallback
  runs. `sys.stderr` being `None` — pythonw, and hosts that detach it — is
  handled too, because `print(file=None)` writes to *stdout*, and a
  multi-line diagnostic in a program's data stream is its own bug. No
  diagnostic fails this import, and none of them lands anywhere it should
  not.

- **A checkout's own metadata is not an install, whichever spelling it is
  in.** `setup.py dist_info` writes a `.dist-info` into the source root, so
  restricting the source-tree discriminator to `.egg-info` left the false
  positive reachable by a different artefact.

- **The install order is documented, because it decides whether you are
  warned at all.** Both projects ship a `schwab/__init__.py`, so whichever is
  installed *second* overwrites the other's — install `schwab-py` over
  `schwaby` and the file carrying this check is the one that goes. The README
  and the getting-started guide said "`import schwab` warns" without that
  condition, which would let a user read silence as confirmation the install
  was fine, and then run the `pip uninstall schwab-py` that destroys it.

If you are on 3.0.1 and have never installed `schwab-py`, you will not have
seen any of this. Upgrading is worthwhile only if you did see a warning you
believe was wrong.

---

## 3.0.1

Two fixes and one addition, all found in the hours after 3.0.0 shipped. Nothing
in 3.0.0's published artifact is wrong; upgrading is worthwhile but not urgent.

### Fixed

**`setup.py` read `README.md` and `version.py` without an encoding**, so Python
decoded them with the locale's preferred one. On Linux that is UTF-8 and the
result is correct; on Windows it is a codepage, and every em dash in the README
became a replacement character in the `long_description` the build produced. A
wheel built on Linux is fine and one built on Windows is quietly corrupt.

The published 3.0.0 is unaffected — it was built on `ubuntu-latest`, and its
description on PyPI has its em dashes and no replacement characters.

There was already a test for this. It compares a UTF-8 read against what
`setup.py` produced, so it cannot fail where the locale is UTF-8 — which is
everywhere the suite normally runs. It passed on every Linux run and failed on
every Windows one. The new test reads `setup.py`'s source and requires an
encoding on every `open()`, which is locale-independent.

**Four tests failed on Windows** because `os.path.relpath` raises when the two
paths are on different drives, and the control fixtures live in a temp
directory — `D:` on a runner where the checkout is `C:`. This only ever showed
on tag pushes, since pushes to `main` run Linux alone and the full matrix runs
on tags.

### Added

**`import schwab` now warns when `schwab-py` is installed beside it.** This is
the only place it can be said. `pip` does not implement `Conflicts-Dist` — its
resolver never reads the field — and a wheel runs no code when it is installed,
by design, so nothing can refuse or flag the install itself. Import is the first
moment.

A warning rather than an exception: the library places orders, the files usually
work, and the damage comes from the *next* `pip uninstall`. Failing the import
would break a running system to complain about a state that has not broken it
yet. The message says not to run `pip uninstall schwab-py`, and what to run
instead.

It is a directory listing rather than an `importlib.metadata` lookup, which
opens and parses a metadata file for every installed package. Both answer the
question identically. Measured with `time.perf_counter` around a single call in
a fresh interpreter, five runs, 92 distributions installed, on the maintainer's
aarch64 Linux box: the listing 0.21–0.23 ms, `importlib.metadata` 104–105 ms,
`import schwab` itself 143–166 ms. Those absolutes move a lot with the machine,
the filesystem cache and the number of packages — a reviewer on other hardware
measured 23–27 ms and 70–77 ms for the same two — so the ratio is the durable
part, and it says the metadata walk costs a large fraction of the import while
telling us nothing extra.

### Documentation

**The upgrade order is now in the README, the getting-started guide and the
3.0.0 upgrade table**, because installing `schwaby` over `schwab-py` leaves both
registered claiming the same files, and `pip uninstall schwab-py` afterwards
deletes the shared files and destroys the install — `pip` goes on reporting
`schwaby` as present while `import schwab` raises `ModuleNotFoundError`.

## 3.0.0

**This release removes public API.** `schwab.contrib.orders` and the
`schwab-order-codegen.py` script are gone. If you import either, this will not
be a drop-in upgrade; everything else is unchanged and needs no edits.

The major number is the point. 2.1.0 was also breaking and shipped as a minor,
on the reasoning that this project was installed from pinned git tags and so
nothing could upgrade into it by accident. That stopped being true at 2.6.0,
the first PyPI release: a requirement of `schwaby` or `schwaby>=2.6` now
resolves to this on a routine `pip install -U`, and a changelog banner does not
help someone who never reads one.

**A plain `pip install schwaby` is now the whole library.** The extras are gone
and the order-code generator is gone with them.

**Upgrading from 2.x.** For most programs this is a version bump and nothing
else. These are the things that need an edit, and only if you use them:

| If you | Then |
|---|---|
| import `schwab.contrib.orders` | It is gone. Nothing replaces it; the builder it reconstructed is still there. |
| run `schwab-order-codegen.py` or `schwab-generate-token.py` | Both are gone. `docs/auth.rst` has the four lines that replace the token script. |
| pin `schwaby[login]` or `schwaby[codegen]` | Drop the bracket. The pin still installs, with a warning. |
| call `set_destination_link_name` to pick a venue | Use `set_requested_destination`. The old one was writing to a field that does not select one. |
| pass `account_id=` to `StreamClient` | Remove it. It was accepted and never used. |
| let `AccountHashMismatchException` propagate | It now carries `.order_id`, so a live order on the wrong account is recoverable for the first time — but **anything between the call and your return is skipped on that path too**, journalling included. Catch it, record from `exc.order_id`, then re-raise. |
| construct `AccountHashMismatchException` yourself | It now requires `(response, order_id, account_hash, message)` rather than a bare message. Catching it is unchanged. |
| have `schwab-py` installed | **Uninstall it first**: `pip uninstall -y schwab-py && pip install schwaby`. Installing over it leaves both registered claiming the same files, and the obvious cleanup afterwards destroys the install. |
| pin by git URL rather than from PyPI | A PyPI install writes no `direct_url.json`, so anything verifying the pin by reading `requested_revision` out of it stops answering. Check how you assert your pin before you switch. |
| check `extract_order_id` for `None` | It raises now. Catch `OrderIdNotFoundError` — and read the entry below, because that case means an order may be live. **Anything you do between that call and your return is now skipped on that path**, so check what sits there: journalling and bookkeeping most often. |

Nothing else in the public surface moved.


### Added

**`set_requested_destination`, for routing an order to a specific venue.** The
`Destination` enum has been exported since the beginning and there was no
setter that put it on the right field --- see Fixed, below.

**`SchwabError`, a base class for every exception this library defines.**
`except SchwabError` is now one name for all of them. Before this the nearest
thing was `except ValueError`, which also catches every `int()` and `float()` in
the same block.

It is not everything the library can raise, and the documentation says so:
argument validation still raises builtin `ValueError` in about thirty places, and
a builtin describes "you passed a negative quantity" correctly.

All fifteen exception classes inherit it, and a test walks the package with
`pkgutil` and fails if one does not — a base covering most of them would be worse than none, since
it invites `except SchwabError` as a complete guard while quietly not being one.
`UnsuccessfulOrderException` and `AccountHashMismatchException` keep `ValueError`
as well, because code catching them that way predates this release and they do
describe a caller mistake.

**`OrderIdNotFoundError` deliberately does not inherit `ValueError`.** It was
going to, for consistency, until a consumer pointed out that this hands back the
exact failure the raise was introduced to remove: `except ValueError` is the
idiom people reach for around `int()` and `float()`, order specs coerce exactly
those a few lines from the call, and the one exception here that means *an order
may be live and untracked* would be swallowed by a block aimed at parsing. It is
also the wrong word — `ValueError` says the caller passed a bad argument, and
they did not. This is remote state.

**`set_tax_lot_method`.** `TaxLotMethod` was likewise exported from
`schwab.orders.common` with no way to use it, so `taxLotMethod` could not be
sent at all. It matters on a closing order: FIFO and LIFO realise different
gains against the same position.


### Fixed

**The venue enum was being written to a field that does not select a venue.**
`set_destination_link_name` validated its argument against `Destination`, whose
twelve values are the ones Schwab lists for `requestedDestination`.
`destinationLinkName` is a free string in Schwab's schema, so an order asking
for NYSE was well-formed and nothing rejected it --- the only symptom would
have been routing you did not choose. Use
`set_requested_destination`. `set_destination_link_name` still exists and now
takes the string its field is typed as, which also means it no longer refuses
legal values.

*Not verified against a live account.* This is read from Schwab's published
order schema, which enumerates those values under `requestedDestination` and
types `destinationLinkName` as `string`. If you have been relying on the old
behaviour, check your fills before assuming either version routed as you
intended.

**`extract_order_id` returned `None` for two different situations, one of them
dangerous.** No `Location` header, and a `Location` that did not parse. Both
mean Schwab *accepted* the order and did not give back an ID — so the order is
very likely live and you have no handle on it — and both shared a return value
with every harmless thing that returns `None`. The natural handling,
`if order_id:`, therefore skipped tracking a live order.

Each now raises its own exception: `MissingLocationHeaderError` and
`UnrecognizedLocationError`, both under `OrderIdNotFoundError`, so catch the
base unless you need to tell them apart. The response is on the exception, and
`UnrecognizedLocationError.location` carries the header verbatim — please
include it in a bug report, because it means Schwab changed the URL format.

The messages say what matters rather than what happened: *the order may be
live: check get_orders_for_account*.

**Five exceptions were broken across a process boundary.** Any that carry the
thing they are about as a leading positional argument — a response, a raw frame
— passed only the message up to `BaseException`, so the default reconstruction
called `__init__` with the message alone. Measured on 2.6.0:
`UnparsableMessage` and `ResponseTimeoutError` raised
`TypeError: ... missing required positional arguments` under `copy`, `deepcopy`
and `pickle`; `UnexpectedResponse`, `UnexpectedResponseCode` and
`UnusableMessage` reconstructed with the message silently gone. The other six
were fine.

The new exceptions in this release have the same shape, so the fix covers all
fifteen rather than the five.

This library does not itself move an exception across a boundary — the login
flow's child process sends back a URL string, and `RedirectServerExitedError` is
constructed in the parent. It matters for callers who do: a `ProcessPoolExecutor`
over placements, say, where the exception saying *an order is live on the wrong
account* is the last one that should arrive as a `TypeError` about argument
counts. Fixed once on `SchwabError` rather than fourteen times, and a test
round-trips every exception class the package defines.

**`AccountHashMismatchException` parsed the order ID and threw it away.** It is raised only after the response came back successful *and* a valid order ID
was parsed out of it, on the line above — so Schwab placed something, on an
account the caller was not expecting to trade, and the ID was discarded on the
way out. The message was `order request account hash != Utils.account_hash`,
which reads as a configuration complaint.

So on 2.x that order was not merely untracked, it was **unrecoverable** short of
going back to `get_orders_for_account` and guessing which one it was. This is the
first release in which it can be owned.

In a fleet the plausible trigger is a `Utils` cross-wired between accounts, which
is exactly the fault you would most want to find with the order ID in hand.

It now carries `.order_id`, `.account_hash`, `.response` and
`.expected_account_hash` — the last keyword-only, so it cannot take the slot a
message is passed in — and says *the order is live on the account Schwab
named*.

**If you construct this exception yourself, the signature changed.** On 2.x it
had no `__init__` and took a bare message, so `AccountHashMismatchException('…')`
worked. It now requires `(response, order_id, account_hash, message)`. Catching
it is unchanged; only construction breaks, which in practice means test doubles
and wrappers. It keeps `ValueError`, because
that predates this release and it does describe a caller mistake, but the
docstring warns that a broad `except ValueError` will swallow it.

**A rejected order threw away Schwab's explanation of why.**
`UnsuccessfulOrderException` carried the HTTP status code and nothing else, so
`order not successful: status 400` was the whole of what you got --- a status
code does not distinguish a malformed order from one the account cannot afford.
Schwab types an error body as `{"message": ..., "errors": [...]}`, and that text
now appears in the exception message, bounded so it cannot put an entire order
echo into a log line. The rejected response is on the exception as `.response`
for the rest of it.

**The wheel was shipping a top-level `tests` package into your site-packages.**
`find_packages()` matched it alongside `schwab`, so `pip install schwaby`
installed 21 test files as an importable top-level `tests` module --- which
collides file-for-file with any other distribution that ships one, and answers
`import tests` from any directory that is not your project root. Present in
every release up to and including 2.6.0. Uninstalling and reinstalling clears
it; `pip uninstall schwaby` on its own may take files another package put
there, so check `site-packages/tests` afterwards if you had one.

**`cryptography` is a declared dependency now.** The callback server runs with
`ssl_context='adhoc'`, and `werkzeug` builds that certificate with
`from cryptography import x509` --- which neither Flask nor Werkzeug declares.
It was present only because `authlib` happens to require it. Had that changed,
the child process would have died inside `app.run` and the parent would have
reported `RedirectServerExitedError`, blaming your callback port for a missing
package. The parent checks for it explicitly, alongside `flask`.


### Changed

**The install is bigger, not smaller.** 2.3.0 split the login packages out and
took a fresh install from 27 packages to 15. Putting them back reverses that:
measured, 2.5.1 pulls in 14 packages and 3.0.0 pulls in 24, `cryptography`
among them. If you remember the slimming, it did not survive — the reasoning
below is why, and a trading box now has those back to patch and audit.

**`flask`, `multiprocess` and `psutil` are hard dependencies again.** They were
split out into a `login` extra in 2.3.0. The split saved twelve packages for
nobody — every consumer that authenticates at all installed the extra — and cost
three ways to be wrong, all of which fail late rather than at install time:

- `pip freeze` drops extras. A `schwaby[login]` line freezes to
  `schwaby==X.Y.Z`, and installing from that leaves the login packages out with
  no warning from either command. This is not specific to this package —
  measured on `requests[socks]`, which freezes the same way.
- `easy_client` needs the extra even when a token file already exists, because
  its `max_token_age` defaults to 6.5 days and it re-authenticates through the
  login flow past that. A program installed without the extra ran for 6.5 days
  and then failed on a routine refresh.
- An extra is a second thing to get right in a pin, and nothing checks it.

**The `login` and `codegen` extras are gone entirely.** A `schwaby[login]` pin
still installs correctly --- `pip` treats an unknown extra as a warning, not an
error --- it just prints `does not provide the extra 'login'` while doing it.
Keeping empty extras to suppress that would have defended about four hours of
PyPI history, since `schwaby[login]` was only ever installable from 2.6.0. Drop
the bracket from your requirements line and there is nothing to notice.

**Copyright is now asserted jointly.** `LICENSE` carries
`Copyright (c) 2023 Alex Golec` unchanged, as the MIT licence requires, with
`Copyright (c) 2026 Tom Hirt` added beneath it for the work since. The package
metadata reads `Author: Tom Hirt and Alex Golec`, and the documentation footer
says `2023 Alex Golec, 2026 Tom Hirt` rather than describing this as a fork
someone maintains.

Measured, for the record: of the 8,412 lines under `schwab/`, 4,832 are Alex
Golec's, 3,113 are Tom Hirt's, and 463 come from other contributors to the
original project. The licence is unchanged and remains MIT.

**The PyPI classifier said `Development Status :: 1 - Planning`**, inherited and
never updated, and it is what the project page would have shown. It now says
`5 - Production/Stable`.


### Removed

**The order-code generator.** `schwab.contrib.orders`, the
`schwab-order-codegen.py` script, the `codegen` extra's `autopep8` dependency,
and the `construct_repeat_order` / `code_for_builder` / `UnrepeatableOrderError`
API. It turned an order from your history back into the `OrderBuilder` code that
would place it again — a one-off aid for writing a program, not something a
running one calls, and it reconstructed a builder by pattern-matching against a
response format that has moved on. Nothing else in the library used it.

`schwab.contrib.util` is unaffected — `StreamJsonDecoder` and
`HeuristicJsonDecoder`, which the streaming documentation recommends for frames
that will not parse, are still there and still imported the same way.

**`StreamClient` no longer takes `account_id`.** It was accepted and never
used --- the parameter appeared exactly once in `streaming.py`, in the
signature. It is from the TD Ameritrade streamer, which needed one. The main
streaming example in the documentation passed it, so readers were being taught
to supply a value that went nowhere.

**`schwab/_optional.py`.** With nothing optional left to import, the
lazy-import-with-a-helpful-ImportError machinery has nothing to guard.
`auth.py` imports `flask`, `multiprocess` and `psutil` inside the functions
that use them, which keeps the parent-process import check that distinguishes a
broken install from a callback server that exited.

**The `schwab-generate-token.py` script is gone.** It fetched a token and wrote
it to a file without your having to write any Python, and it was the only thing
this package installed onto your `PATH`. Nothing in the library used it, and it
was packaged the legacy way --- `scripts=` copies the file verbatim, so the
installed command carried a `.py` extension and a project prefix from before the
rename.

The case it existed for is creating a token on a machine with a browser and
copying the file to one without. That recipe is still in `docs/auth.rst`, now
with the four lines of `client_from_login_flow` that replace the script. A
`pip install schwaby` no longer puts anything in `bin/`.

**`tox.ini` is gone, and CI runs the suite directly.** tox was not doing what
it looked like it was doing. It built an sdist, installed it into an isolated
environment, and then ran `coverage` as an external command --- which executed
under a different interpreter and imported `schwab` from the working tree, so
the environment tox had just built was never the one under test. Measured, not
inferred. The Python version came from `setup-python` either way, which is the
part that mattered, so the matrix still runs 3.10 through 3.14 on the same
platforms. `passenv` and `setenv` went with it: the `CI` variable the macOS skip
reads was only awkward because tox filtered the environment.

**`MAINTAINING.md` is gone, replaced by `RELEASING.md`.** A quarter of it
described a fork: the branch model, the `upstream-main` mirror, the pull-request
discipline, and why that arrangement ended. None of it applies to a standalone
project, and `upstream-main` no longer exists. What survives is the release
checklist and the rules this project has learned by getting them wrong, under a
name that says what the file is for.

**Every referral to the original project's tracker is gone**, from the README,
`docs/help.rst`, `CONTRIBUTING.rst`, `SECURITY.md`, the issue and pull-request
templates, and the issue-template contact links. They asked reporters to file
the same thing on somebody else's repository, which is a promise this project
cannot keep and a piece of coupling it has to maintain. The credit to Alex Golec
stays exactly as it is, in the README and the licence, because that is
authorship rather than process.

The warnings that `schwaby` and `schwab-py` cannot be installed together stay
too. Those are about a real collision that silently overwrites files, not about
the relationship between the projects.

### Documentation

**The documentation is published at
[schwaby.readthedocs.io](https://schwaby.readthedocs.io/), and every link now
points there.** Previously they pointed at the `.rst` sources on GitHub, which
renders them as plain text — including the link PyPI shows in its sidebar, and
the three URLs `client_from_login_flow` prints during an interactive login.

User-facing links use `/en/stable/`, which tracks the latest tag, so someone on
3.0.0 reads 3.0.0's documentation rather than whatever is on `main`.

**A caution about real money, where a reader will actually meet it.** The README
carried an inherited one-paragraph disclaimer at the very bottom, under the
licence, which is where nobody reads. There is now a `[!CAUTION]` block near the
top of the README, a `.. danger::` on the documentation front page, and a warning
at the head of the getting-started guide.

It makes the claim rather than hedging it: this places real orders with real
money and it *has* bugs, not "may have". Every defect ever found here was in code
with 100% test coverage and the ones that mattered were silent; the changelog
lists them precisely because the next one is in there unfound. You are
responsible for every order your code places, and the author, maintainer and
contributors are not. Start small, reconcile against the broker rather than
trusting what the library says happened, read the code on any path that places an
order, and assume the bug you have not found is on the path you did not read.

The bottom-of-file disclaimers were also rewritten. They were inherited, one
carried a "This authors" typo, and neither mentioned financial loss.

**A documented example called a method that does not exist.** The level two
page told readers to use `Client.search_instruments()` and gave a worked
example; the method is `get_instruments`, so anyone copying it got an
`AttributeError`. `sphinx -W` does not resolve cross-reference targets, so the
build had always passed. Every name the documentation points at is now resolved
against the code by a test.

**A second documentation example could not run.** `client.rst` showed placing
an order with `easy_client(..., webdriver_func=make_webdriver)` and a
`selenium` import. There is no `webdriver_func` parameter, and selenium is not
a dependency and appears nowhere in the library --- it is left over from the TD
Ameritrade era, when the login flow drove a real browser. A test now checks
every keyword argument in every documentation example against the signature of
the thing being called.

**`client_from_login_flow` pointed a `ValueError` at `schwab-py.readthedocs.io`
--- the original project's documentation site.** A user who got the callback URL
hostname wrong was sent to read a different project's docs. The callback-URL
advisory now has its own section in `docs/auth.rst`, which is where the error
links, and a test fails on any documentation host this project does not
control.

`easy_client`'s 6.5-day proactive re-authentication is now documented in
`auth.rst`, where it belongs. It was previously described only as a reason to
install an extra, so it disappeared with the extras despite being a property of
`easy_client` rather than of the packaging.

**Three dead links, one of them the first click in the onboarding flow.**
`docs/getting-started.rst` sent new users to `beta-developer.schwab.com` to
create their developer account and register their app --- a hostname that no
longer resolves, so step one of the guide failed with nothing else on the page
to try. It points at `developer.schwab.com` now. A docstring in
`orders/generic.py` linked to `developer.schwabmeritrade.com`, from the TD
Ameritrade era, and `auth.py` sent a rejected callback URL to the *original*
project's documentation site.

**`get_instruments`' docstring documented a `symbol` parameter.** The argument
is `symbols`, so the rendered signature and the rendered parameter list
disagreed.

**`place_order`'s first argument is an account *hash*, and the order-template
docs called it `account_id`.** One example passed the literal `1000`. Those are
different things: the hash comes from `get_account_numbers()`, and an account
number will not work.

**The rendered documentation was titled `schwab-py`.** `docs/conf.py` kept the
original project's name through the rename, so every page title and browser tab
named a different library while the text on the page said to install `schwaby`.
Alex Golec's `author` and `copyright` entries are unchanged and deliberately
so.

**The docs no longer call this project by three different names.**
`schwab-api`, from two renames ago, appeared four times across the client and
streaming pages. Prose written in the voice of the original project's community
("we in the community aren't currently clear...") has been rewritten to say
what is actually known, and a "July 21, 2024" update notice has become a
statement that does not need a date attached to stay true.

Documentation examples use `example.com`, which RFC 2606 reserves for the
purpose, rather than `callback.com` --- a real registrable domain that someone
else owns.

**The "Critical Schwab Bug" section is gone from the getting-started guide.** It
described a July 2024 outage on a developer console that no longer exists, and
it was the first thing a new reader saw.

**The README is Markdown now, not reStructuredText.** GitHub renders `.rst`
without admonition styling or much typography, so the repository landing page
was the plainest version of the text; PyPI takes `text/markdown` natively. The
documentation under `docs/` is unchanged and still reStructuredText, so the
Sphinx and Read the Docs build is untouched --- verified by building it in a
clean environment from `docs/requirements.txt` alone.

One thing this costs, recorded because it is not obvious: `twine check
--strict` was a real gate on the `.rst` README --- reStructuredText rejects
malformed markup, which is what caught a title underline one character short
that would have published a release with a blank description. Markdown rejects
almost nothing. Measured against `readme_renderer`, it refuses an empty
document and a whitespace-only one and accepts unclosed HTML tags and broken
link syntax. And without `readme_renderer[md]` installed, `twine check
--strict` reports PASSED having rendered nothing at all. So the release
workflow installs that extra, and the suite asserts the property directly: the
description renders, it is substantial, it still has its headings and its
table, and it names what a reader needs.

**The README leads with what the library does.** It opens with the claim, then
four short examples --- authenticate, fetch, place an order, stream --- that
show the whole surface before asking anyone to read prose. The origin story and
the credit to Alex Golec are the last section rather than the first thing a new
reader meets. Same for `docs/index.rst`.

**The web-application login flow is documented.** `get_auth_context`,
`client_from_received_url` and `AuthContext` have been public for a long time
with no docstrings and no mention in the documentation. They are how you log in
when the callback arrives as an ordinary request to a server you already run,
in a different process from the one that started the login --- the case
`client_from_login_flow`, which runs a local callback server and blocks, cannot
serve. `docs/auth.rst` has a worked example.

**The book message structure is documented.** Every other stream had a field
reference; the three order-book streams had none, so a subscriber received a
nested structure with nothing describing it. `BookFields`, `BidFields`,
`AskFields`, `PerExchangeBidFields` and `PerExchangeAskFields` are on the
streaming page now, with a worked example of the shape.

**Two more examples, and a warning that had no code.** `examples/` now holds
three files, chosen because each teaches an assembly the reference
documentation cannot: the streaming consumer with its bounded queue,
`auth/token_lifecycle.py`, and `orders/order_lifecycle.py`.

The token one exists because Schwab's refresh token expires seven days after
the original authorization and refreshing does not extend it, so every
unattended program stops within seven days and the only question is whether it
stops at a moment you chose. The documentation explains that across four
separate passages and never shows the loop.

The order one exists because `extract_order_id` returns `None` for two
different reasons --- a rejection, and a response carrying no `Location` header
--- and the snippet in `util.rst` ends in `assert order_id is not None`, which
papers over exactly that.

Separately, `client.rst` now warns that `httpx2` exceptions are not `httpx`
exceptions. Catching `httpx.HTTPStatusError` around a call into this library
fails silently: nothing raises at import, nothing raises at the `try`, and the
`except` simply never matches. That is what broke a consumer's rate-limit retry
on the 2.0.0 upgrade, and until now it appeared nowhere as code.

**The one file under `examples/` was four years stale and now has a test.** It
subscribed to `TWTR`, `FB` and `FIT` --- delisted in 2022, renamed in 2022, and
acquired in 2021 --- carried two TODOs about API that either works now or never
existed, and passed the `account_id` above. It has been rewritten around what
the example is actually for: a bounded queue between the socket and your
processing, and an error handler, which is the shape a long-running consumer
wants and the one thing a README snippet cannot show.

Nothing referenced `examples/` and nothing checked it, which is why it drifted.
The keyword-argument check that covers documentation code blocks now covers
these files too, and fails if the directory disappears from its walk.

## 2.6.0

**First release published to PyPI, as `schwaby`.**

```shell
pip install schwaby
```

```python
import schwab   # unchanged
```

### Changed

**The distribution is now `schwaby`; the importable package is still `schwab`.**
Those differ deliberately. Keeping the import means an existing consumer changes
one line of `requirements.txt` and nothing else — the Pillow and PyCryptodome
model for a project that began as a fork of a dormant one.

**The cost is that `schwaby` and `schwab-py` cannot be installed together.** Both
provide the `schwab` package, so whichever lands second silently overwrites the
other's files. `pip` gives no warning and nothing fails at install time; the
first sign is behaviour from a version you did not choose. Uninstall `schwab-py`
before installing this.

**The repository is `Hu1kSmash/schwaby` and is no longer a fork.** Old URLs
redirect, so existing clones and git-pinned installs keep working.

**The extras command no longer names a version.** A missing `login` or `codegen`
extra now says `pip install "schwaby[login]"`, which stays correct for every
release, rather than interpolating a git URL and a tag.

### Documentation

**The project is described as itself rather than as a fork of something else.**
The README opens with what `schwaby` is and puts the history in a note below it:
it began from `alexgolec/schwab-py`, which gave it its shape and most of its
code, and became separate because running systematic strategies against funded
accounts imposes requirements a general-purpose wrapper has no reason to
prioritise. Alex Golec's copyright and licence are unchanged.

**Two client endpoints were documented for the first time**, both inherited in
2024 and never covered: `preview_order`, which asks Schwab whether it would
accept an order without placing it, and `get_option_expiration_chain`, which is
cheaper than a full chain when you only need expiry dates. All 31 public client
methods are now in the documentation.

`get_option_expiration_chain`'s docstring was also wrong — it described
`get_user_preferences`.

**`set_json_decoder` is documented**, which mattered after 2.5.0 told readers to
try `HeuristicJsonDecoder` when frames will not parse without saying how. Four
exceptions a caller might catch, and `OptionSymbol.parse_symbol`, also gained
entries.

## 2.5.1

### Fixed

**`UnparsableMessage` reached `add_error_handler` from only one of the two
readers, so 2.5.0's headline claim was false on the other.** Two coroutines can
be holding the read lock when a frame arrives — `handle_message`, and a request
waiting on its response — and the report was added only to the first. Whenever a
subscribe won the read lock, an unparsable frame raised out of that subscribe
with the callback never firing. Which coroutine reads any given frame is a lock
race the caller cannot see, so the callback fired or did not fire for reasons
nothing in the API explains.

Both readers now report, through one guarded helper rather than two copies —
and exactly once. `handle_message` hands the same exception to the waiting
request through `_fail_pending_request` before reporting it, so both readers see
one object for one bad frame; reporting from each would have replaced "fires or
does not fire" with "fires once or twice", still decided by a lock race the
caller cannot see, and with both reports carrying an identical triple a consumer
cannot distinguish from two genuinely different bad frames.

**The comment claiming the raw text makes the pair "never both empty" was
wrong.** `raw_msg` is the empty string for an empty text frame, so a report is
distinguishable from the logout-close failure under `is None` but not under a
falsy test. The limitation is documented and pinned by a test rather than
overclaimed; the documented discriminator remains the exception type. The note
now sits on `UnparsableMessage`, which is the class that has a `raw_msg`, rather
than on `UnusableMessage`, which does not.

## 2.5.0

### Added

**`UnparsableMessage` now reaches `add_error_handler`.** It was the one failure
class that ended the caller's receive loop *and* gave a consumer who replaced
log-scraping with the callback no programmatic signal at all — the state 2.2.0
was added to eliminate, one layer below where 2.4.0 eliminated it. It is
reported and then re-raised, so the control flow is unchanged.

Reported from outside the `async with` that holds the read lock, so no user code
runs under it; a handler raising `BaseException` there is logged rather than
allowed to replace the parse failure the caller needs to see.

**That it still ends the loop is now a decision rather than an omission**, and
the docstring gives the reasoning and the counter-argument. A structurally
unusable element can be skipped precisely; a frame that will not parse has
unknown contents, so continuing accepts a gap of unknown size — and a reconnect
re-subscribes, which is the only thing that recovers state.
`HeuristicJsonDecoder` exists because Schwab really does emit unparsable JSON,
which is evidence the other way, so the docs now point at it.

Found by a consumer tracing the failure classes rather than grepping for them.

### Documentation

**`OrderUROutCompleted` was described as "an unsolicited out", in the sentence
that corrected its spelling.** That asserts nobody asked for the cancellation,
and the same token ends a cancel you issued yourself — so the description
contradicted the sequences printed a few lines below it. It now says the order
came off the book, and says explicitly that the token does not carry the cause.

Found by a consumer applying the two-sequence table to their own operator-facing
wording: a phrase asserting a cause the token cannot carry is a small lie on the
screen every time an order is rejected. The same wording was in these docs.

## 2.4.1

### Documentation

**The `ACCT_ACTIVITY` `MESSAGE_TYPE` tokens were documented in the wrong case,
and one of them in the wrong spelling.** Measured on 2026-09-05 by driving the
states deliberately against a live account: the tokens are CamelCase on the
wire — `OrderCreated`, `OrderAccepted`, `CancelAccepted`, `ExecutionCreated`,
`OrderUROutCompleted` — with `SUBSCRIBED` a genuine exception.

A consumer comparing against `'ORDERCREATED'` matched nothing. Worse,
`ORDERUROUT` is not a case variant of `OrderUROutCompleted`, so upper-casing
does not rescue it — and at least one consumer had copied that token from this
list, where it would have alerted on an ordinary cancel.

The two sequences a caller most needs to tell apart are now written down: a
cancel you issue yourself, and a buy rejected for buying power. They differ only
by `ExecutionCreated`, and the rejection returns **HTTP 201 with a real order
id** and only becomes `REJECTED` about a second later — so a caller checking
only the HTTP status believes it worked.

**Five more `ACCT_ACTIVITY` `MESSAGE_TYPE` tokens.** `ORDERMONITORCREATED`,
`ORDERMONITORCOMPLETED`, `CHANGECREATED`, `CHANGEACCEPTED` and
`EXECUTIONCREATED` — the lifecycle of a resting limit or stop order, observed on
a live feed on 2026-07-27. A program placing only market orders never sees them
and will meet them the first time a human places an order by hand in the same
account, which is a plain way to get an unknown-shape alert from an allow-list.
The note also flags that `CHANGECREATED`/`CHANGEACCEPTED`/`ORDERREPLACED` mean a
*working order was amended* — a safety event for an order you placed yourself.

### Fixed

**CI was red on macOS on every tag since 2.0.0, and the README badge said
otherwise.** Seven `ClientFromLoginFlowTest` cases really start the callback
server in a child process and talk to it over loopback; on the macOS runners
the child starts and never answers on its port, so each fails after the full
30-second wait, on every Python version. Windows passes, and Windows also spawns
rather than forks, so this is not a start-method problem.

They are skipped on macOS CI runners only — not on a developer's Mac — with a
comment saying what would settle whether this is a runner restriction or a real
macOS defect. The other 923 tests pass on macOS and that coverage is kept.

## 2.4.0

### Added

**`add_error_handler` now reports a fourth kind of absorbed failure: a message
this client cannot use at all.** A frame which is not an object, an element of
`data` or `notify` which is not an object, a `service` which is not a name.
These arrive as the new `UnusableMessage`, whose `message` is the offending
value as it arrived, with `cause` (the exception that made it unusable, where
there was one) and `count`/`total` as integers — reports are suppressed after
the first few, so the counts are the only way to see the true scale, and a
consumer alerting on drop volume should not have to parse them out of prose.

That callback exists so an absorbed failure is not visible only in a log, and
these are absorbed failures. A consumer who registered a handler to stop
scraping logs would otherwise watch a subscription go silent after a framing
change with no programmatic signal at all — the state 2.2.0 added the callback
to eliminate.

`UnusableMessage` rather than the existing `UnparsableMessage`, which means the
JSON did not decode and carries the parse exception. Here the JSON is fine and
the structure is not; one type meaning two shapes is a trap this library has
been bitten by before.

### Changed

**A systematically malformed channel no longer logs one line per element.** The
first three on a connection are logged, then powers of ten, with the running
count. A framing change on a `LEVELONE_EQUITIES` subscription across a few
hundred symbols would otherwise produce a warning per element per tick
indefinitely — a log-volume incident on top of the data outage.

Reports are coalesced onto the same schedule for a second reason: they share a
bounded queue with the late rejections, so reporting every occurrence would let
a flood of unusable elements evict a rejection of an abandoned request, which is
the one thing nothing else will ever report.

### Fixed

**A malformed message ended the caller's receive loop.** Four related cases,
all of which raised out of `handle_message` where the caller could only read
them as the stream having failed:

* an element of `data` or `notify` which is not an object — `d.get('service')`
  is evaluated at the dispatch call site, outside the `try` that protects
  handlers;
* a `service` which is not a name, since the handler lookup is evaluated in the
  same place;
* a frame which is not an object at all — a top-level JSON array or string;
* a top-level JSON `null`, which was indistinguishable from the internal
  sentinel meaning "this response was routed to its waiter", so it was dropped
  without even a log line.

Each is now logged and skipped, and well-formed elements beside a bad one are
still dispatched.

**A failure to relabel a message was reported as a handler failure.** Relabeling
is this library's work, not the caller's, but it happens inside the per-handler
`try` — so a `content` the field tables could not read was reported as "your
handler raised", once per registered handler, uncounted and uncoalesced. It is
absorbed once per message now, and it carries the `service` it belongs to and
the exception that caused it: `_BookHandler` indexes four levels deep, so the
`KeyError` is the only thing that says which field moved.

**A response frame with an unreadable request id was logged and nothing else.**
It was the one unusable-message path that bypassed the counting and the
callback — so it repeated per re-read inside a single subscribe, and a venue
emitting a non-numeric `requestid` gave a consumer no programmatic signal at
all.

**An `UnusableMessage` could not be told from a failure to close after logout.**
Both arrived as `service=None, message=None`, which the documentation designates
as the close-failure signature. The report now carries the containing frame as
`message`, which is non-`None` for every case but a top-level JSON `null`, and
the documentation says to test `isinstance(exception, UnusableMessage)` rather
than branching on both being unset.

**A custom JSON decoder returning anything but `dict` and `list` broke the
stream.** `set_json_decoder` is a public hook which promises only "the decoded
JSON", but the type checks above were written against the concrete types, so a
decoder returning a `Mapping` which is not a `dict` — or tuples for arrays — had
every frame dropped, presenting as a permanently dead feed with no exception.
The checks are against the operations used, not against `collections.abc` — an
ABC matches only real subclasses and registered types, which would have narrowed
what a decoder may return rather than tolerating it. A JSON array must be
**indexable**, which tuples are: routing reads `frame['response'][0]`,
validation reads it again, and handlers get the frame afterwards, so a
single-pass iterable cannot serve however tolerant the iteration is. One is now
refused and reported rather than accepted and found empty by whichever function
read it second.

Separately, and cosmetically: the debug log formatted frames with `json.dumps`,
which refuses such an object. Logging swallows a formatting failure, so the
stream survived either way, but the content of every debug line was replaced by
a traceback — for exactly the people who customised the decoder and are most
likely to be reading it.

**A mismatched request id was reported as a malformed frame.** All five fields
were read before any was compared, so a frame carrying an id this client never
issued *and* missing another field said `malformed response frame: KeyError:
'service'`. The id mismatch is the more diagnostic fact — it means the server
and this client disagree about what was asked — and it was hidden behind
whichever field happened to be absent. The id is compared before the rest is
read.

**`set_json_decoder` looked its base class up through `schwab.contrib.util`,**
which raises `AttributeError` unless the caller happened to have imported that
module. Someone subclassing `StreamJsonDecoder` where it is actually defined,
in `schwab.streaming`, has no reason to have done so. It is the same class
either way; the local name is used now.

**A malformed answer took down the receive loop as well as its own request.**
`_validate_response` read four fields straight after the request id, unguarded.
A `KeyError` there reached `_fail_pending_request`, which sets the exception on
the waiting future *and* re-raises — so one unreadable field failed the request
with a bare `KeyError` and ended the caller's `handle_message` loop with it. A
frame too malformed to check is now returned as an `UnexpectedResponse` saying
so, which fails that request and leaves the stream usable.

Found by sweeping for the shape of the 2.3.0 fix rather than waiting for a
report: 2.3.0 guarded the request-id read, and this sat four lines below it.

A rejection carrying a code but no `msg` is also no longer treated as an
unreadable frame. The code is the part a caller can act on, and losing it to a
missing message field was the worse outcome.

### Documentation

**`pip freeze` drops the extra from a pinned line, and says nothing.** A
requirements file carrying `schwab-py[login] @ git+...@v2.3.0` installs
correctly, but freezing that environment writes `schwab-py @ git+...@<sha>`
back out, and installing from *that* silently omits `flask`, `multiprocess` and
`psutil`. The failure surfaces later, when something first calls the login flow.
Documented in `docs/getting-started.rst`. This is a `pip` limitation with direct
URL requirements, not something this package can fix.

## 2.3.0

### Changed

**The login flow and the code generator are now optional extras.** A plain
install requires three packages -- `authlib`, `httpx2`, `websockets` -- and
nothing else. `flask`, `multiprocess` and `psutil` move to `schwab-py[login]`;
`autopep8` moves to `schwab-py[codegen]`.

**This is breaking if you use `client_from_login_flow` or `easy_client`.** Add
`[login]` to your install. Calling either without it raises an `ImportError`
that names the extra and gives the command, rather than a bare "No module named
'flask'".

Note the second one carefully: **`easy_client` needs `[login]` even when you
already have a token file.** Its `max_token_age` defaults to 6.5 days, and a
token older than that is discarded and replaced through the login flow. So a
plain install works for 6.5 days and then fails on a routine re-authentication,
which on an unattended machine is the worst shape this break can take. Pass
`max_token_age=0` to disable the proactive refresh if you really want to run
`easy_client` without the extra — but Schwab's refresh token expires seven days
from authorization regardless, so anything long-running needs a way to log in
again either way.

In a Jupyter or Colab notebook `easy_client` routes to `client_from_manual_flow`
instead, which starts no callback server, so notebook users need no extra.

Not affected: `client_from_token_file`, `client_from_access_functions`,
`client_from_received_url`, `client_from_manual_flow`, and the streaming client.
None of them touch the callback server.

Measured on a clean 3.14 install: 27 packages before, 15 after. The twelve that
go are `flask` and its tree (`blinker`, `click`, `itsdangerous`, `jinja2`,
`markupsafe`, `werkzeug`), `multiprocess` and `dill`, `psutil`, and `autopep8`
with `pycodestyle`.

None of them were ever used outside the interactive login flow and the code
generator, and two -- `multiprocess` and `psutil` -- were imported at module
scope, so every `import schwab.auth` paid for them. A process that loads a token
from a file and streams quotes has no use for a web framework, and on a machine
that places trades each package is something to patch, audit and break on
upgrade.

### Fixed

**A malformed response element ended the caller's receive loop.** A `"content"`
present but JSON `null` made `content.get('code')` raise `AttributeError` out of
`handle_message`. Both framings of the same event now share one parser, so a bad
element is logged and skipped and the good elements beside it still report —
they had diverged, with one framing hardened and the other not, which is exactly
what the framing-independence contract above says cannot happen.

**`login()` did not close the connection it replaced.** Calling it on a healthy
client — a re-authentication, a preferences refresh — dropped a live websocket
and its reader with nothing closing it. After a `ConnectionClosed` the old
socket is already gone and this is a no-op, which is why it went unnoticed. A
failure to close is logged rather than raised, since the login is the operation
the caller asked for.

**A rejection riding along in a matched response frame was reported by
nothing.** `_validate_response` reads `response[0]`, which is the answer to the
outstanding request; a frame carrying a second response handed it to the waiter
unexamined. If that one was a rejection, nothing mentioned it -- the same gap
the late-rejection reporting closed in 2.2.0, one element along. Additional
responses are now checked. A rejection logs a warning and is reported to
`add_error_handler`; a success alongside the answer logs at INFO and is not
reported, exactly as on the orphan path.

The rule is that every response no waiter claimed is checked, which is usually
everything past element 0. In the window where a waiter has already timed out
but the pending slot is not yet cleared, nobody claims element 0 either, and it
is checked too.

Reporting it matters more than it might look. `_request_lock` keeps one request
outstanding, so a second response in a frame cannot answer anything the client
is waiting on — it is a late answer to an abandoned request, the same class the
orphan path has reported since 2.2.0. Whether Schwab sends it in its own frame
or batches it behind the answer to a live request is the server's choice. Had
only the orphan framing reported, a consumer who replaced log-scraping with
`add_error_handler` would see a rejection one day and silently miss the
identical one the next, for a reason invisible to them.

**But not from where it is found.** That code runs holding the read lock, on the
request path the request lock too, and inside the response deadline — a user
handler called there let a slow one turn a subscription that *succeeded* into a
`ResponseTimeoutError`, and one that re-subscribed blocked on a lock its own
caller held. Both were real, and both have tests. The report is queued instead,
and delivered by whichever coroutine read the frame once it has released its
locks: before `handle_message` returns, or before the request that read it
returns. Draining only in `handle_message` was not enough — when a subscribe
wins the read lock, `handle_message` can be parked in `recv()` with its own
drain already behind it, and the report would then wait for the next inbound
message, which on a quiet stream is unbounded.

The consequence to know: **your error handler can be called from inside a
subscribe.** A slow one delays that call returning; it cannot make it fail. A
request delivers what it read even when it fails, since the exception the caller
gets describes only the response that answered their own request — though a
handler's `BaseException` is logged rather than allowed to replace it, and a
*cancelled* request skips the report so a shutdown never waits on user code. The queue is
bounded, and is cleared by `close()` and by a fresh `login()`, so a torn-down
session's rejection is never reported against a new one whichever way the caller
reconnects. Frames read but not yet handled are cleared with it — that deque
predates this change and had the same leak, which would have left the standalone
framing crossing sessions while the batched one did not, and could hand a
handler a quote from a dead connection. The log line written when each
rejection is found is the complete record; the callback is the convenience. All
of this is stated in `add_error_handler`'s docstring and in `docs/streaming.rst`.

## 2.2.0

### Added

**`StreamClient.add_error_handler`.** A stream handler which raises is logged
and skipped, and a connection which fails to close after logout is logged. Both
are the right behaviour, and both leave the caller with a log record rather than
something to react to. The only way to react was to attach a `logging.Handler`
and match on message text, which every consumer who cared had to write and which
coupled them to strings this library is free to reword.

```python
def on_stream_error(service, exception, message):
    alert('schwab stream: %s raised %r' % (service, exception))

stream_client.add_error_handler(on_stream_error)
```

Registering none keeps the existing behaviour exactly, and the log line is
written either way.

It reports three kinds of failure: a stream handler which raised, a late
rejection of a request nobody was waiting on, and a connection which failed to
close after logout. The middle one is the least obvious and the most
actionable -- the request was abandoned, so nothing else will ever say Schwab
refused it.

Handler failures are wired at two places in the code, not one. A synchronous
handler's exception passes through an `except` block; an asynchronous handler's
does not -- it surfaces in the task's done callback, at a different logging
level, in a different function, with no `except` around it. Wiring only the
`except` clauses would look complete and cover synchronous handlers only, which
for this purpose is worse than none: it turns "no signal" into "a signal, and it
is quiet". There is a test that fails if the async site is left unwired and
passes if only the synchronous one is checked, and the async case asserts on the
service name as well as the exception -- reporting it as `None` would satisfy a
weaker assertion while making "mark this subscription unhealthy" impossible.

The handler may be a coroutine function, like every other handler on this class,
and it is **awaited** rather than scheduled — the report finishes before the call
that found the failure returns. That keeps the report reliable with no machinery
to keep it alive: nothing to drain at shutdown, so nothing that can deadlock,
time out or be cancelled half-delivered while draining. The cost is that the
handler runs on the path that found the failure, so a slow one holds up
`handle_message`, exactly as a slow synchronous handler always has. Hand off to
your own task if you need to do something slow.

A handler of the wrong shape is refused at registration rather than discovered
at report time. Every other `add_*_handler` here takes a one-argument callback,
so `add_error_handler(lambda exc: ...)` is the natural mistake -- and it would
raise `TypeError` on every report, inside the `except` clause that exists to
stop an error handler failing the stream, so it would never run and never say
so.

### Documented

**Relabeling is not applied uniformly, and the docs said nothing about it.**
Content on the `data` channel is relabeled from numeric field ids to names.
Content on the `notify` channel is forwarded unchanged, ids intact. Both reach
the same handlers, so a handler assuming relabeling mis-parses a notify frame --
by finding nothing rather than by raising, since the keys it looks for are
simply absent. The Data Field Relabeling section explained the mechanism and
never mentioned the exception.

**What `ACCT_ACTIVITY` payloads actually look like.** Schwab documents the
three top-level fields and nothing inside `MESSAGE_DATA`, so every consumer
reverse-engineers it privately. The order identifier appears under seven
spellings, the symbol under four, and both `CANCELED` and `CANCELLED` occur in
Schwab's own status tokens. That is now written down, collected by watching a
live feed over roughly a year, and labelled as an observation log rather than a
contract -- nothing in it is validated by this library, and a shape that has not
been seen is not thereby impossible.

### Changed

**This fork no longer tracks upstream.** Through 2.1.0 every change was branched from a mirror of
`alexgolec/schwab-py` and offered as a pull request before being merged here, on the reasoning that
anything upstream took would shrink the divergence. Upstream merged none of them, and its
maintainer confirmed in September 2026 that he does not intend to update the project.

So the arrangement had become a ritual with a real cost: the last several changes were cut from
`main` because they could not sensibly be cut from anywhere else, and each needed a paragraph
explaining why it had not been sent. Topic branches now come from `main`, there is no PR queue, and
changes are made because they are right for this library rather than because upstream might take
them. `MAINTAINING.md` records what this changed and what it did not.

Nothing about the code's authorship changes. This is still overwhelmingly Alex Golec's work, the
licence and attribution are untouched, and if upstream ever resumes, `upstream-main` is still
mirrored here and reconciling with it beats defending the fork.

## 2.1.0

> **This is a breaking release despite the minor version.** It removes three
> deprecated APIs and changes how `decimal.Decimal` prices are rendered. The
> number is minor because this fork is installed from pinned tags rather than
> version ranges -- nothing upgrades into it by accident -- but if you are
> moving from 2.0.x by hand, read this section rather than skimming it.

### Removed

Three APIs which had been deprecated with warnings. All three are breaking for
code still using them, and all three fail loudly at the point of use rather
than changing what an order or a subscription means.

**Prices must be strings.** `set_price` and `set_stop_price` used to accept
any number and truncate it -- two decimal places, or four below one. Passing a
number now raises `ValueError` naming the fix.

Note this includes **integers**: `set_price(1250)` raises, not just
`set_price(1250.0)`. Integer prices were the form this project's own
documentation used, so auditing your call sites for float literals alone will
miss some. The removed helper was named `truncate_float`, which is worth
searching for too -- it was a public module-level name, and anything importing
it now gets an `ImportError`.

The conversion was removed rather than kept because the library was choosing a
rounding, for a value denominated in money, on behalf of a caller who knows
what the order is for. Whether a limit should round up, down or to the nearest
tick is a trading decision.

**`'{:.2f}'.format(value)` is not an equivalent.** It rounds; the old code
truncated toward zero, and used four decimal places below one. `19.9999999`
became `19.99` rather than `20.00`, and `0.186992` became `0.1869` rather than
`0.19`. On a buy limit that difference is a price one tick higher than the one
asked for. The docs carry the exact `decimal.ROUND_DOWN` equivalent for anyone
who wants to migrate without moving any prices.

**`decimal.Decimal` now passes through exactly, where it used to be truncated.
This one is a silent change and the only one in this release.** A `Decimal` was
not a `str`, so it fell through to the same conversion floats did:
`set_price(Decimal('12.129'))` sent `'12.12'` up to 2.0.1, and sends `'12.129'`
now. If you compute prices as `Decimal` and relied on the library to round
them, it no longer will, and a sub-penny equity limit is rejected by Schwab
rather than caught here.

That is the intended behaviour rather than an oversight -- a `Decimal` carries
the precision its author chose, and silently discarding it was the bug -- but
it is a change to what a previously correct call puts on the wire, so check
your `Decimal` call sites. It is rendered with `format(d, 'f')` rather than
`str(d)`, because `str(Decimal('1E+2'))` is `'1E+2'`, which is not a price.

**Build a `Decimal` from a string, not a float.** `Decimal(0.1)` is that
float's binary expansion to 55 decimal places, and rendering it exactly -- the
point of accepting `Decimal` at all -- puts all 57 characters on the wire. Up
to 2.0.1 the truncation hid this.

It is not refused. A guard on decimal places was written for this release and
then removed, because measuring it showed the guard could not do what it
claimed: over realistic inputs, float-derived values span 0 to 53 decimal
places while string arithmetic spans 1 to 9, so no threshold separates
contamination from an honest computed limit. What a threshold does reliably is
refuse a valid price at order-placement time. And the payload it prevents is
merely long, not wrong -- Schwab types both `price` and `stopPrice` as
`number($double)`, so a 57-character spelling parses to the same double a short
one does.

So `Decimal(str(value))` is a habit this library recommends and does not
enforce, and rounding a computed price stays your decision:
`value.quantize(Decimal('0.01'))`.

`copy_price` and `copy_stop_price` still skip the type check, which is what
`contrib.orders` uses to reconstruct a historical order. They do refuse a
non-finite `Decimal`, since rendering one produces the transmittable string
`"NaN"`; `float('nan')` is still accepted there because it does not survive
serialization -- `httpx2` builds request bodies with `allow_nan=False`, so the
request raises before it is sent. Note that a bare `json.dumps` of the built
order does *not* raise: it emits the invalid-JSON token `NaN`. If you serialize
a spec yourself for logging or a diff, that is what you will see.

**`websocket_connect_args['extra_headers']` is no longer translated.**
websockets 14.0 renamed it to `additional_headers`; this library had been
rewriting it silently and warning. Passing it now raises `ValueError` naming
the replacement, as `create_protocol` and `read_limit` already did.

The argument is documented as a passthrough to `websockets.connect()`. Quietly
substituting one name for another on the way through is not a passthrough, and
it meant the name a caller wrote was not the name the library called.

**`LevelOneOptionFields.STRIKE_TYPE` is gone.** It was an alias of
`STRIKE_PRICE`, kept when field 20 was renamed to what Schwab actually
documents it as. Code naming it now raises `AttributeError` instead of reading
a field whose messages are labelled something else.

### Fixed

**`Decimal` is for the price fields only, and is refused elsewhere at the
setter.** `quantity`, `activationPrice`, `stopPriceOffset` and `priceOffset`
are `number($double)` in Schwab's schema, so passing a `Decimal` to one raises
where the field is still known.

This is narrower than it may read: a plain `str` in those fields is still
accepted and still produces a string where Schwab's schema says number.
`Decimal` is refused because this library would be the one rendering it, which
is a choice it should not make silently; a string is what the caller wrote.
Whether to refuse that too is a separate question, not settled here.

**`copy_price` and `copy_stop_price` could not take a `Decimal`.** They skip
validation by design, so a `Decimal` reached the object builder raw -- and
having no `__dict__`, it fell to the reflection path and raised `vars()
argument must have __dict__ attribute` from `build()`, far from the call that
caused it and naming neither the field nor the type.

**A non-finite `Decimal` now raises a clear error from every numeric setter.**
`_assert_finite` reads `float(Decimal('sNaN'))` raising `ValueError` as "not a
number at all" and returned, so a signalling NaN reached the `<= 0` comparisons
in the setters and raised a bare `decimal.InvalidOperation` whose message is a
repr of its own class -- the exact failure that check runs first to prevent.
2.0.x refused these too, just unreadably; this is a diagnosis fix, not a safety
one. (`Decimal('sNaN')` never reached an order in a released version. It could
briefly during this release's development, which is why the test exists.)

**`OptionSymbol` accepted a non-finite strike.** `float('nan')` parses and
`nan <= 0` is `False`, so `'nan'` and `'inf'` passed the constructor's
positivity check and failed later inside `build()` as `cannot convert NaN to
integer`, naming neither the strike nor the symbol. They are refused at
construction now, next to the positivity check.

**`OptionSymbol` accepted strikes it could not encode.** (The checks that
enforce this are exact regardless of the process-wide `decimal` context. They
read the parsed strike's own digits rather than scaling it first, because
multiplying consults the context and no fixed precision covers every input.) The symbol's strike
field is eight digits of thousandths, so it cannot carry more than three
decimal places and stops just below `$100,000`. Neither limit was checked: a
strike of `2.0019` was truncated to `00002001`, naming a `$2.001` contract, and
`0.0005` became a strike of zero, while `700000` widened the field and produced
a 22-character symbol. Both are refused at construction now, where the strike
is still in hand, rather than producing a symbol for a different contract or
none.

**Option symbols encoded some strike prices a tenth of a cent low.**
`OptionSymbol.build()` scaled the strike by 1000 as a binary float and
truncated with `int()`, which truncates the representation error along with the
value: `2.01 * 1000` is `2009.9999999999998`, so the symbol came out
`...C00002009` rather than `...C00002010` -- a different contract, or one that
does not exist. 590 of the 100,000 cent-granular strikes between `$0.01` and
`$1000.00` were affected, on the order-placement path.

This is the same defect that made limit prices a cent low, in a different
function, and it survived the fix to that one. It now scales in `decimal`,
which is exact: the strike is already validated as a string on the way in.

### Note

The three removals convert a silent accommodation into an error, so code which
already passed strings, used `additional_headers` and named `STRIKE_PRICE` is
unaffected by them.

The `Decimal` change above is the exception, and the only thing here that
alters a working call without raising. If you pass `Decimal` prices, read that
paragraph.

## 2.0.1

### Documented

**2.0.0 moved where TLS trust comes from, and did not say so.** `httpx` depended on
`certifi` and built its default SSL context from the CA bundle shipped inside that
wheel. `httpx2` does not depend on `certifi` at all -- it uses `truststore`, which
reads the **operating system** trust store:

```
httpx/_config.py    create_ssl_context -> import certifi
                    ssl.create_default_context(cafile=certifi.where())

httpx2/_config.py   create_ssl_context -> import truststore
                    ctx = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
```

Two things follow, neither of which is a defect, but both of which are worth
knowing before you move this onto a new host:

- **TLS now depends on the host having a populated CA store.** A slim or distroless
  container without `ca-certificates` worked on 1.11.4, because the bundle travelled
  inside the `certifi` wheel. On 2.0.0 and later, certificate verification fails and
  surfaces as `httpx2.ConnectError`. It will not hide: nothing in this library
  catches that outside the login flow's readiness probe, so it reaches your caller
  on every request rather than being absorbed and retried. A sudden run of connect
  errors immediately after a host or image change is this, not the network.
- **The trust set is whatever the OS trusts.** An interception or corporate root
  installed in the host store is now trusted for Schwab traffic, where previously
  only certifi's bundle was. That is a real change in posture for a process holding
  tokens with full account access.

`SSL_CERT_FILE` and `SSL_CERT_DIR` still override, if you would rather pin the trust
set explicitly than inherit the host's.

### Removed

**Four dependency declarations that nothing imports.** `certifi` was there to keep a
current CA bundle in the `httpx -> httpcore -> certifi` chain, which 2.0.0 removed;
leaving it declared implies a bundle that is no longer in the path. `urllib3`,
`python-dateutil` and `nose` are referenced nowhere in the library, the tests or the
tooling.

**A warning suppression which could never fire.** The login flow's readiness probe
wrapped its `verify=False` request in a filter for
`urllib3.exceptions.InsecureRequestWarning`. `urllib3` is not in `httpx2`'s stack and
was not in `httpx`'s either; measured against a self-signed local server, `httpx2`
emits no warning at all on `verify=False`. The filter, the `urllib3` import and two
`-W ignore::urllib3.exceptions.*` entries in `tox.ini` are all gone.

### Fixed

**`make release` uploaded to PyPI with the tests switched off.** It ran
`twine upload dist/*` against `schwab-py`, which on PyPI is the *upstream* project --
so it would have replaced someone else's package with this library under their name.
The test step had been commented out with a `# TODO: Reinstate tests before
releasing` left in place. Inherited from upstream, where publishing to PyPI is
correct. There is now no release target: it prints why and exits non-zero, and
points at `MAINTAINING.md`. `make coverage` ran `nose`, which does not work on any
Python this project supports, and `make dist` used the deprecated
`setup.py sdist bdist_wheel`.

**`requests` was used by the test suite and declared nowhere.** It arrived only
because `twine` pulls in `requests-toolbelt`. It is now a dev dependency in its own
right.

## 2.0.0

### Changed

**This library now uses `httpx2` rather than `httpx`, and requires Authlib
1.8 or later.** Both are breaking changes for anything that catches an
exception from a client call or type-checks a response.

Authlib 1.8 stopped importing `httpx` directly. Its `httpx_client` integration
imports `httpx2` when that package is installed and falls back to `httpx` when
it is not, emitting a deprecation warning it forces past the default filters.
The fallback is documented as temporary. Since `OAuth2Client` inherits from
whichever module Authlib resolved, the client's response and exception types
were being decided by whatever else happened to be installed.

`httpx` and `httpx2` share no hierarchy: `httpx2.Response` is not
`httpx.Response`, and `httpx2.HTTPStatusError` is not a subclass of
`httpx.HTTPStatusError`. Disagreeing with Authlib therefore fails silently
rather than loudly -- an `isinstance` check stops matching, an `except` clause
stops catching, and only in production, where the response comes from Authlib's
session rather than from a test which constructed its own.

**If you catch `httpx` exceptions around calls into this library, or check
responses against `httpx` types, those now need to name `httpx2`.** Catching a
tuple of both is a way to cross the change without a flag day.

Declaring `httpx2` alone would not have been enough. The previous floor of
`authlib>=1.6.0` left Authlib 1.7.2 with `httpx2` installed a legal
resolution -- Authlib on `httpx`, this library's guards on `httpx2`, every
guard silently dead. The floor moved to `authlib>=1.8` for that reason.

### Added

**A test that a half-finished version of this change cannot pass.**
`tests/http_module_test.py` compares the module Authlib built `OAuth2Client`
on against the module each guarding file imported, and fails when they differ.

It was needed because nothing else could see the problem. Every other test in
the suite constructs the type it then asserts on, so the whole of it passed
against Authlib 1.8 with `httpx2` installed while the library still guarded on
`httpx` -- 824 of 824 green, on 3.12 and 3.14, in exactly the configuration
where `add_child_order_strategy` no longer recognises a response and the login
flow's readiness wait no longer absorbs a refused connection.

## 1.11.4

### Fixed

**A test added in 1.11.3 raced a server it never started.** The new coverage
for the connect-timeout case mocked the readiness check to report success, then
let the flow open a browser whose stub makes a real request to the callback
port -- where nothing was listening, because the readiness check was a mock.
Local timing hid it; one CI runner did not:

```
ConnectionRefusedError: [Errno 111] Connection refused
FAILED test (ubuntu-latest, 3.12, py312)
```

No library change. The test now delivers no callback and asserts on the timeout
plus the attempt count, which is what it was actually for.

## 1.11.3

Cross-platform. This fork's CI runs Linux on every push and all three platforms
on tags, and the tag builds had been red on macOS and Windows for as long as
that matrix has existed -- while the badge, which tracks the default branch,
stayed green on Linux. Nobody looked.

### Fixed

**A connect timeout ended the login flow instead of being waited through.**
`client_from_login_flow` starts a local server and polls it until it answers,
catching `httpx.ConnectError` for "not listening yet". Whether an unbound port
refuses a connection or silently drops it is a property of the host: a firewall
drops, and so do the macOS runners. A dropped attempt raises
`httpx.ConnectTimeout`, which is a *sibling* of `ConnectError` rather than a
subclass --

    ConnectError    -> NetworkError     -> TransportError
    ConnectTimeout  -> TimeoutException -> TransportError

-- so it escaped and took the login flow down while the server was still
starting. Every login-flow test fails on macOS for this reason. It is not a
test-only problem: on a machine that drops rather than refuses, the login flow
could not complete at all. Offered upstream as #264.

### Documentation

**The token file is not protected on Windows, and the docs said it was.**
Windows has no POSIX file mode -- `os.chmod` toggles a read-only bit and
ignores the rest -- so the token is left readable by any account on the
machine. The docs stated flatly that it is written mode `0600`. For a file that
grants full access to a funded account, claiming a protection that does not
exist is worse than claiming nothing. Now a warning saying what a Windows user
should do instead, and noting that the atomic-write and durability properties
*do* hold there; only the permission narrowing does not.

The test asserting mode `0600` is now skipped off POSIX rather than relaxed to
something both platforms satisfy, which would have stopped it meaning anything
where it matters.

### Notes

Every "tests passing" figure reported during the 1.9.0-1.11.2 work was Linux
only, and said so nowhere. The macOS and Windows columns existed the whole
time.

## 1.11.2

A second review, over the whole divergence from 1.5.0 rather than the last
release. One serious defect and four small ones.

### Fixed

**A late stream response wedged every request that followed it.**
`_read_and_route` checked each response frame against whichever request was
pending and, on a mismatch, failed that request with it. So the late answer to
an abandoned request killed an unrelated one -- and was consumed doing it,
leaving the next stale answer for the next request:

```
request 1: ResponseTimeoutError (timed out)
request 2: UnexpectedResponse: unexpected requestid: 0
request 3: UnexpectedResponse: unexpected requestid: 1
request 4: UnexpectedResponse: unexpected requestid: 2
```

One client-side timeout and the stream never completed another request. 1.9.0
taught `handle_message` to tolerate an orphaned response; this is the same
tolerance one level down, where the routing happens. Since this fork added
`DEFAULT_RESPONSE_TIMEOUT`, it supplied the trigger as well as the fault.

The rule is bounded on both sides: an id issued *earlier* is lateness and the
frame is handed back as an orphan, while an id never issued is genuine
protocol confusion and still fails, as it always has.

**The library warned about a datetime it invented itself.**
`get_price_history_every_day('AAPL')`, with no dates at all, warned about
`start_datetime` and blamed the caller's line -- the loudest possible false
alarm from 1.9.0's warning, on the most common way these endpoints are called.
The substituted default was naive, so the check could not tell it from
something a caller wrote. It also made the `startDate` for an unparameterized
call depend on the host's offset. Now explicitly UTC, and the suite passes
identically under four timezones, which it did not before.

**`priceOffset` was dropped when repeating an order.** `contrib.orders` knew
`priceLinkBasis` and `priceLinkType` but not the offset those apply to, so
`construct_repeat_order` returned a price-linked order with no offset -- a
different order at a different price, silently. `set_price_offset` is this
fork's own addition, which is why the table never grew an entry for it.

**A mangled link.** The fork's URL rewrite broke a string concatenation in
`add_child_order_strategy`, leaving `.../docs/index.rstorder-templates.html`,
a 404.

**A docstring promised a guard one configuration does not have.** Passing a
collection of order statuses raises `ValueError` with the enum check on, and
does not with `enforce_enums=False` -- which `client_from_login_flow` defaults
to. Now qualified.

### Notes

The `priceOffset` gap survived the audit's own round-trip probe, which
reported 48 of 48 templates identical. None of those templates uses a
price-linked order, so the number said nothing about the field. A probe that
passes because it never exercised the case is the same failure as a test that
passes either way.

1.11.1 shipped a comment claiming the path separator in the frame walk could
not be tested without a real sibling package on disk, and concluded it was
untestable. The first half was right; the conclusion was not. Building that
layout on disk is about ten lines, and there is now a test which fails when
the separator is removed.

## 1.11.1

A final audit of the audit. Everything here is a defect in something 1.9.0
through 1.11.0 introduced -- three of them in guards which did not guard as
much as they claimed.

### Fixed

**The finite-number check covered only prices.** It missed the other numbers
in an order, and it missed the worse case: the existing positivity guards do
not catch NaN, because NaN compares False against everything. `quantity <= 0`
and `activation_price <= 0.0` are both False for it.

```python
OrderBuilder().set_activation_price(nan).set_stop_price_offset(nan)
              .set_quantity(nan).build()
# {'quantity': nan, 'stopPriceOffset': nan, 'activationPrice': nan}
# json.dumps → {"quantity": NaN, ...}   which is not valid JSON
```

Those are the fields a trailing stop and a conditional order use.
`set_quantity`, the order legs, `set_activation_price`, `set_stop_price_offset`
and `set_price_offset` now check before the positivity comparison. Offered
upstream as #262.

**A locally-unusable token was reported as retryable.** `MissingTokenError`
and `InvalidTokenError` are `OAuthError` subclasses which authlib raises
*before any HTTP call*, when the stored token has no refresh token or cannot
be used. They arrived with `refresh_token_invalid=False` and a message saying
the failure "may be transient", so an application following the documented
pattern retried forever on a permanent, purely local condition -- the exact
failure the attribute exists to prevent, reached through the attribute.
Offered upstream as #266.

**The naive-datetime warning blamed the library, not the caller.** A fixed
`stacklevel` cannot be right when the endpoints sit at different depths --
`get_price_history_every_day` wraps `get_price_history`, and
`get_orders_for_account` goes through `_make_order_query`. Two of the four
entry points pointed at `base.py`, which tells a caller nothing they can act
on. The frames are now counted. Offered upstream as #259.

**The bug-report log guard missed a broken pipe.** It caught `ValueError` but
not `BrokenPipeError`, which is an `OSError`, so `python bot.py 2>&1 | head`
still produced the "Exception ignored in atexit callback" traceback the guard
was added to remove. Offered upstream as #257.

**A rejected subscription became invisible.** 1.9.0 stopped an orphaned stream
response from ending the receive loop, which was right, but logged every one
at INFO -- including failures. The request was abandoned, so nothing else ever
reports that Schwab refused it. Non-zero codes now log at WARNING. Offered
upstream as #261.

### Changed

**The token temp-file sweep waits five minutes rather than one.** The
threshold is not a tidiness knob, it is the entire mechanism keeping the sweep
from deleting a file a *different* process is still writing -- which was
demonstrated, and costs that process its token update. Widened, with the
reasoning moved into the constant, and the assumption that mtime and the local
clock agree written down for anyone on a network mount.

### Notes

Two of these were found because a test passed when it should not have. The
warning-attribution fix shipped with an off-by-one that a filename-only
assertion could not see, and the sweep's concurrency test used a file created
that instant, which survives any threshold. Both tests now fail against the
code they are meant to catch.

One thing is deliberately untested and says so in the source: the path
separator in the frame walk's prefix comparison. Reaching it needs a real
sibling package on disk, and every test written for it passed with the
separator removed.

## 1.11.0

### Added

**`TokenRefreshError.refresh_token_invalid`, saying whether a retry can work
at all.** 1.10.0 reported the token's age and left the caller to infer the
rest. This makes the inference directly.

A refresh token Schwab has rejected as invalid cannot be retried back to life:
a new one comes only from the authorization_code flow, which needs a human at
a browser. An unattended application which treats that like a dropped
connection keeps asking, and the endpoint it hammers is the one its own
recovery depends on.

```python
try:
    r = c.get_quote('AAPL')
except TokenRefreshError as e:
    if e.refresh_token_invalid:
        alert('refresh token is dead, log in again')
    else:
        retry_later()
```

RFC 6749 §5.2 defines `invalid_grant` for exactly this case, so the signal is
a standard one. Schwab does not put it where the standard says. It answers
with an outer code of `unsupported_token_type` -- which RFC 7009 defines for
the *revocation* endpoint and which describes nothing that happened -- and
nests the real response as a JSON string inside the description:

```
unsupported_token_type: 400 Bad Request: {"error_description":"Refresh token
is invalid, expired or revoked","error":"invalid_grant"}
```

Observed on a live account on 2026-08-02, by letting a refresh token reach its
seven day expiry deliberately. Schwab documents neither the response nor the
nesting, so that is one account on one day rather than a specification. Both
placements are accepted, in case the outer code is ever corrected.

Anything unrecognized returns `False`. An application stopped by a failure it
could have retried through is worse off than one which retried a little too
long, so the unrecognized case falls on the transient side.

Offered upstream as #266.

### Notes

1.10.0's documentation said this library would not tell you whether a retry
could succeed, because Schwab documents the seven day term but not the
response. That was true of the documentation and remains true; it was not true
that nobody had seen the response. This corrects it.

## 1.10.0

The rest of the production audit -- the findings which were real but not
urgent, plus the one piece of design it turned up.

One behaviour change worth reading before upgrading: a rejected token refresh
now raises a schwab-py exception rather than an authlib one.

### Added

**`TokenRefreshError`, so a failed refresh has a type of ours.** The session
refreshes the token on the way past an ordinary request, so a refusal by
Schwab surfaced from `get_quote` as
`authlib.integrations.base_client.errors.OAuthError` -- a type this library
never mentioned, from a package the user did not choose to depend on. Measured
across the failure spectrum, callers were catching three different third-party
types and none of them ours.

`schwab.utils.TokenRefreshError` now wraps it, keeping the original as
`__cause__` and carrying the token's age:

```python
from schwab.utils import TokenRefreshError

try:
    r = c.get_quote('AAPL')
except TokenRefreshError as e:
    if e.token_age is not None and e.token_age > 7 * 24 * 60 * 60:
        alert('token past its seven day window, log in again')
    else:
        retry_later()
```

It deliberately does **not** classify the failure as terminal or transient.
Schwab documents the seven day refresh token term but not what the token
endpoint returns when that term expires, so a classifier keyed on error codes
would be built on a payload shape nobody has seen. The age is documented; the
error code is not. Offered upstream as #263.

**`Client.close_session()`.** `AsyncClient` could always be closed and the
synchronous client could not, so an application creating clients repeatedly
held their connections until each session happened to be garbage collected.
Both are now documented, which neither was. Offered upstream as #265.

### Fixed

**Temporary token files left behind by a hard kill are now swept.** The
cleanup on the exception path never runs for a `SIGKILL`, and what a killed
write leaves behind is not an empty file -- it is a complete, readable copy of
the token. Measured: 25 processes killed inside the write window left 12 of
them, and nothing ever removed them. A refresh token stays valid for the rest
of its seven days, so a process which crash-loops accumulates live credentials
next to its token file. Each subsequent write now removes any it finds, but
only files matching this library's own temporary name, and only once they are
a minute old -- another process may be partway through its own write, and
deleting its temporary file would break it. Added to #232, which introduced
the temporary file.

**The wait for the login callback server is bounded, and checks what
answered.** A server which started but never answered left
`client_from_login_flow` spinning with nothing on screen to say why; it was
still going when killed at 12 seconds. Worse, the status check's response was
assigned and never read, so *any* listener on that port counted as the
callback server -- and continuing hands it the login redirect, which carries an
authorization code good enough to take over the account. A non-200 now refuses
the flow. Interactive path only. Offered upstream as #264.

### Documentation

`client_from_access_functions` said "Please see this example for details"
about the exact signatures a custom token-storage function must have, and
linked to a file which returns 404 in this fork and upstream. The one place
the signatures were explained was the one place that was not there. They are
now written out inline, where they cannot rot the same way.

## 1.9.0

From a production-readiness audit which mostly ran the library rather than
reading it. Four defects, three of them silent.

Two change behaviour in ways worth reading before upgrading: aware datetimes
now produce different (correct) requests on three endpoints, and a NaN price is
now refused rather than sent.

### Fixed

**A timezone-aware datetime was formatted, not converted, before being labelled
UTC.** Schwab documents `fromEnteredTime`, `toEnteredTime` and the transaction
dates as `yyyy-MM-dd'T'HH:mm:ss.SSSZ`, where the trailing `Z` asserts UTC.
`_format_date_as_iso` applied `strftime` to whatever it was given, so a
datetime carrying any other timezone had its local wall clock stamped as UTC:

```python
eastern.localize(datetime.datetime(2024, 6, 5, 0, 3, 2))   # == 04:03:02 UTC
# sent as fromEnteredTime=2024-06-05T00:03:02Z             -- four hours early
```

Passing a correctly-zoned datetime is what triggered it. Naive datetimes were
unaffected, as were the price history endpoints, which use a different encoding
that was already correct. Affects `get_orders_for_account`,
`get_orders_for_all_linked_accounts` and `get_transactions`. Offered upstream
as #258.

**A response nobody was waiting for ended the caller's receive loop.**
`handle_message` raised `UnexpectedResponse` on any response frame. One reaches
it whenever a request was abandoned -- timed out, or cancelled -- and the
server answered afterwards, and that answer is frequently a *successful* one.
So a subscribe which timed out client-side and in fact worked killed the
session the moment Schwab acknowledged it, taking every message queued behind
it. That is most likely exactly when the venue is slow, which is when a stream
is least worth dropping. Now logged and skipped. `UnexpectedResponse` still
covers a response whose service, command or request id does not match the
request being waited on. Offered upstream as #261.

**Prices which are not a finite number built sendable orders.**

```python
equity_buy_limit('AAPL', 10, float('nan')).build()
# {'orderType': 'LIMIT', 'price': 'NaN', ...}
```

NaN is not typed, it is computed -- a limit derived from a quote that was
missing. The builder already refused a non-positive quantity, so this is the
same check on the other half of the order. The string spellings are refused
too, since `str()` of a computed price is the documented way to pass one.
Strings which are not numbers at all still pass through, and `copy_price` still
bypasses everything as documented. Offered upstream as #262.

**Bug report logs did not redact Schwab's account identifiers.** The default
patterns matched an earlier API's field names -- `accountId`, `displayName`.
Schwab returns `accountNumber` and `hashValue` and neither matched anything, so
the two identifiers a user is least likely to want in a public issue were the
two that survived. Named in full rather than adding `account` as a substring:
redaction is a whole-log string replacement, and `account` also matches
`accountValue` and `accountColor`, which would take every balance and the word
"Green" with it. Offered upstream as #260.

### Added

**A warning when a date parameter is given a datetime with no timezone.** These
parameters name an instant; a naive datetime does not. The epoch-millisecond
encoding reads one as the host's local time, so the same source line sends
different requests on different machines:

```
host set to UTC:                startDate=1786008600000
host set to America/New_York:   startDate=1786023000000
```

Four hours apart, with nothing in the request recording which was meant.
Warning rather than reinterpreting: silently treating them as UTC would change
behaviour for anyone relying on local time without telling them. Aware
datetimes and plain `date` objects are unaffected. Offered upstream as #259.

### Documentation

A **Dates and Times** section in the client documentation, which previously
said nothing about timezones anywhere. Covers both encodings, the measured
numbers above, and the three spellings that remove the ambiguity.

### Notes

Nothing here changes how a request is built for a caller already passing aware
datetimes to the price history endpoints, or already passing prices as strings.

The audit also confirmed, by measurement rather than reading: 80 `SIGKILL`s
inside the token write window corrupted nothing; 400 streaming cancellation
storms left no orphaned lock and every client usable, where v1.7.1 fails 40 out
of 40; price truncation agrees exactly with an independent oracle across 40,009
values; and all 48 order templates round-trip byte-identically through
`contrib.orders`.

## 1.8.1

### Fixed

**`enable_bug_report_logging()` wrote its report to whatever `sys.stderr` was at
import time.** The stream was captured as a default argument, which Python
evaluates once when the module is imported -- not when the logs are written,
which happens at program exit. An application that redirects `sys.stderr` after
importing the library, as a daemon writing to a log file does, had its bug
report delivered to the stream it redirected away from. Measured with a
`StringIO` standing in for the log file, the redirected stream received 0 bytes
before the fix and the whole report after it.

If that original stream had since been closed, the program's last output was a
traceback out of an `atexit` handler instead of the report. This library's own
test suite printed one on every run.

`sys.stderr` is now looked up when the report is written, and a closed stream is
treated as nowhere to write rather than something to raise about. Passing
`output=` explicitly is unchanged. Offered upstream as #257.

### Documentation

The README's "Why should I use `schwab-py`?" section listed two reasons and left
out the two largest parts of the library: the streaming client and order
construction. Rewritten to cover both, with the "minimal wrapping" claim narrowed
to the HTTP layer where it holds -- the parameter enums, `OrderBuilder` and the
streaming relabeler are all deliberately opinionated. Offered upstream as #255.

Nothing in this release changes how a request is built or an order is
constructed.

## 1.8.0

Most of this comes from reading Schwab's published documentation against the
library, rather than from finding something at runtime.

### Fixed

**Field 20 of `LEVELONE_OPTIONS` is the strike price, not a "strike type".**
Schwab's streamer documentation gives it as `| 20 | Strike Price | double |
Contract strike price |`. It was named `STRIKE_TYPE`, so every relabeled option
quote carried a key describing neither the field nor its contents -- and the
enum had no strike price at all, across fifty-six fields on an options quote
feed. Reported upstream as #197.

`STRIKE_TYPE` is kept as an alias, so code naming it keeps working. Messages are
now labeled `STRIKE_PRICE`, which is a **breaking change** for anything reading
that key.

This needed a supporting fix. `key_mapping` built its number-to-name table from
`__members__`, which yields aliases as well as canonical members, so whichever
alias was defined last would silently have decided the label. It now iterates
the enum itself. No other field enum has an alias, so nothing else changes.

**A historical order with an `UNKNOWN` field could not be reconstructed, and
said so badly.** Schwab documents `UNKNOWN` as a value both `duration` and
`orderType` can come back as, while stating it is not accepted as an input.
Omitting it from the request enums is therefore right, but
`construct_repeat_order` fed historical values into those same enums and failed
with a bare `KeyError: 'UNKNOWN'`, naming neither the field nor the problem.

Such an order genuinely cannot be repeated, so the fix is to say so rather than
to accept the value: `UnrepeatableOrderError` names the field, carries the
value, and explains that Schwab reports it but will not accept it.

**`get_price_history`'s docstring contradicted Schwab's documentation.** It said
`period` "should not be provided" alongside a date range. What Schwab documents,
under `startDate`, is "if not specified startDate will be (endDate - period)" --
so `period` derives a start date when one is absent rather than overriding one
that is present. Supplying both is harmless. The valid `period` values per
`period_type` are now documented too; they were not stated anywhere.

That wording is what led 1.7.0's release notes to claim Schwab honours `period`
over an explicit range. It does not, and those notes have been corrected.

### Added

`UnrepeatableOrderError`, described above.

### Changed

Documented what this fork already did but never explained: that the token file is
a credential written `0600` and replaced atomically, that surrounding whitespace
is stripped from keys and secrets and why the warning is worth acting on, and
that a subscription can be made while another coroutine waits in
`handle_message`.

CI now runs `actions/checkout` and `actions/setup-python` at v7 rather than v2,
which were five years old and being force-run on a Node runtime they were not
built for. Dependabot watches them monthly so they cannot drift that far again.

### Verified against the documentation, and correct

Worth recording, since the point of the exercise was to find gaps: all thirteen
order enums match the published schemas value for value, apart from the `UNKNOWN`
handling above. All eleven market-data parameter enums match. Every market-data
endpoint and parameter is implemented. Across eight streaming services and 230
fields, every field number is correct and field 20 was the only name that meant
something different from the field.

One thing that looked wrong and is not: the `CHART_EQUITY` field table in
Schwab's streamer documentation disagrees with this library, and the library is
right. Against a real message, only the library's mapping yields self-consistent
OHLC; the documented one gives an open of 779 on a $421 stock and a high below
the low.

### Breaking changes

- Option quotes are relabeled with `STRIKE_PRICE` where they previously used
  `STRIKE_TYPE`. The enum member `STRIKE_TYPE` still resolves, so only code
  reading the key out of a relabeled message is affected.

---

## 1.7.2

### Fixed

**Cancelling a stream request left the client unusable.** A request waiting for
its response races two things: the response arriving via whoever is currently
reading the socket, and the socket becoming free so it can read for itself. The
second is an `asyncio.Lock.acquire()`, and it can succeed at the same moment the
waiter stops waiting.

That race was handled when the wait timed out, but not when the waiting
coroutine was cancelled. A cancellation delivered while the acquire had already
completed skipped the branch which releases, so the read lock was left held by
nobody:

    after cancel: read_lock held=True, request_lock held=False
    client still usable after a cancelled request: no

Nothing could read after that, for the rest of the process. Every exit from the
wait now releases what it took.

This was introduced in 1.7.0 by the change which stopped requests blocking
behind a waiting consumer, so it does not affect 1.6.0 or earlier. Anyone on
1.7.0 or 1.7.1 who cancels stream operations -- during shutdown, or by wrapping
a subscription in a timeout -- should take this.

---

## 1.7.1

No library changes: `schwab/` is identical to 1.7.0 apart from the version string.
This release exists because the 1.7.0 tag shipped install instructions which
installed the wrong project.

### Fixed

**The install instructions installed upstream.** The README's getting-started
section and `docs/getting-started.rst` both said `pip install schwab-py`, which
fetches the original project from PyPI. A reader who got as far as installing
got upstream, whatever the fork notice at the top of the page said -- and it
would appear to work, since the importable package has the same name either way.
The fork notice itself still pinned 1.6.0.

**The documentation build could not run from a clean checkout.**
`docs/requirements.txt` pinned `websockets==12.0`, and this library imports
`websockets.asyncio`, which does not exist before 14.0. It went unnoticed because
a development environment already has a current websockets, so it only failed
somewhere clean. `authlib` and `httpx` were pinned below the floors `setup.py`
requires. All are floors now, so they cannot drift from `setup.py` silently.

### Added

A security policy, an issue template chooser, a pull request template, and
`.gitignore` rules for the token file -- which is a live credential, and which
nothing previously stopped git from offering to commit.

---

## 1.7.0

### Fixed

**Response redaction never ran.** Both client modules imported
`register_redactions_from_response` and then defined a no-op of the same name
over the top of it, so every call after every request went to the stub. The API
key and the token are registered elsewhere and were genuinely redacted, but
nothing the redactor was meant to pick out of responses — account numbers,
account hashes, the other id-ish values it looks for — ever was. Since
`enable_bug_report_logging()` tells users their logs are scrubbed, someone
following the documented process for filing a bug could publish those.

Collection is now gated on `enable_bug_report_logging()`, which is the only
thing that promises redaction and already documents that it carries a
performance penalty. Clients which do not call it are unaffected, which is what
they were getting anyway.

**A request could not be made while a consumer was waiting for a message.** One
lock covered every stream operation and `handle_message` held it across the
read, so on a quiet stream a subscription, unsubscription or logout waited for
a message to arrive first — indefinitely, if none did. Measured beforehand: a
subscribe issued against a parked consumer sat for a full second without a
single byte reaching the socket.

Reading and requesting are now separated. A read lock keeps a single reader,
since `websockets` does not allow two coroutines to call `recv()` concurrently,
and whoever holds it routes what it reads: a response to a request in flight is
delivered to the coroutine waiting for it. A request sends immediately and then
waits on whichever comes first — its response arriving via the reader, or the
socket becoming free so it can read itself. Response validation and every error
it can raise are unchanged.

**The price history helpers ignored the date range they were given.**
`get_price_history` documents that `period` "should not be provided if
`start_datetime` and `end_datetime`", and all seven `get_price_history_every_*`
helpers provided both. `period` is now sent only when no range was asked for,
since the helpers synthesize one spanning decades when the caller gives none.

What Schwab does when it receives both is **not** consistent across accounts. The
upstream report describes the range being disregarded; a funded margin account we
had measurements from returned byte-identical responses with and without `period`
across four frequency and range combinations, so there the range was honoured
either way. Both can be true — entitlements, symbol class, or the API changing
since the report would all explain it. The fix does not depend on which: sending a
`period` the caller never asked for is wrong regardless, and it is what the
library's own docstring says not to do.

**The asyncio example called a function which does not exist.**
`asyncio.run_until_complete()` is not a thing; `run_until_complete` is a method
on an event loop. The example now uses `asyncio.run()`.

**Two format strings did nothing.** A DELETE was logged with
`'Req %s: DELETE to %s'.format(...)`, which does not substitute `%s`, so every
DELETE logged its placeholders verbatim. The price history path called
`.format(symbol)` on a string with no placeholders.

### Added

**`OrderBuilder.set_price_offset`.** The stop-price side already had a basis, a
type and an offset; the price-linked side had a basis and a type but no offset,
so a price-linked order could not be expressed at all and had to be assembled as
a raw order.

### Changed

Removed the original project's Discord invitations, funding links and badges,
its `tda-api` transition guide, and 44 links to `developer.tdameritrade.com` and
`tickertape.tdameritrade.com`, whose portals were retired along with the API
they documented. Several documentation and issue links pointed at repositories
which do not exist. Documentation now describes this fork's behaviour, and
issues are directed at this fork's tracker with a note that anything not caused
by these changes is better reported upstream.

`tox.ini` now covers py313 and py314, which CI already ran.

### Breaking changes

- A request which is never answered raises `ResponseTimeoutError` rather than
  blocking message handling. This was already true in 1.6.0; what changed here
  is that it no longer blocks anything except subsequent requests.
- Messages read while a request is in flight are handed to `handle_message` in
  arrival order. Previously they were held back until the request completed,
  which could not interleave because the lock prevented it.

### Verification

Full suite passes on CPython 3.12 and 3.14.

---

## 1.6.0

First release of this project. Branched from `alexgolec/schwab-py`'s `main`
a few commits past their v1.5.0 release — far enough to pick up an
in-development version string reading `1.5.1`, which was never tagged and never
released. **v1.5.0 is that project's last release**, so 1.5.0 is the highest
version number in this file that belongs to anyone else.

### Fixed

**Prices passed as floats could be a cent too low.** `set_price` and `set_stop_price` truncate
rather than round, which is intentional and documented, but the truncation was computed by scaling a
binary float and taking `int()`. That truncates the representation error along with the value:
`8.2 * 100` is `819.9999999999999`, so a price already at two decimal places came back as `8.19`.
Across every cent from $1.00 to $1000.00, 4,583 of 99,901 prices — 4.6% — were affected, always one
tick low and always silently. Truncation is now done in decimal. Sub-dollar prices at four decimal
places were wrong 5.7% of the time and are likewise fixed.

**The token file could be destroyed by a crash during a refresh.** The token was written with a
plain truncating `open(path, 'w')`, so a process that died between the truncation and the end of the
write left a file that could not be parsed — unrecoverable without a fresh interactive login. Tokens
are now written to a temporary file and renamed into place, which is atomic, so an interrupted write
leaves the existing token intact. The rename is flushed, and symlinked token paths are resolved so a
linked token file is written through rather than replaced.

**The token file was created world-readable.** It was written with whatever the umask permitted,
commonly `0644`, despite holding a credential granting full account access. It is now created `0600`,
with the mode set before the file becomes visible at its final name. Tokens written by earlier
versions are corrected on their next refresh.

**A stream handler that raised could take down the receive loop, or fail silently.** Handlers are
registered through one API but were dispatched with opposite error semantics: a synchronous handler
that raised propagated out of `handle_message`, aborting dispatch for every other handler on that
message and surfacing in the caller's loop as though the connection had failed; an asynchronous one
was scheduled and never awaited, so its exception was discarded entirely. Both kinds are now
isolated and logged. Message relabeling is inside the guard, so a message with an unexpected shape
is reported rather than escaping.

**A request the streaming server never answered wedged the entire client.** `_await_response` waited
with no upper bound while holding the lock that serializes stream operations, so an unanswered
request blocked every other operation — including message handling — for the life of the process.
Requests now time out. The websockets keepalive does not cover this case: a connection that is alive
but not answering keeps replying to pings.

**`notify` messages without a `service` key raised.** The `notify` dispatch path lacked the
membership check its `data` counterpart has. With a `defaultdict` this silently accreted an empty
entry per unrecognised service, and a frame without a `service` key raised `KeyError` out of
`handle_message`.

**The default price-history end date was off by the host's UTC offset**, and used the deprecated
`datetime.utcnow()`. `utcnow()` returns a naive datetime holding a UTC wall time, which
`_format_date_as_millis` then converted as though it were local.

**`get_orders_for_account` documented a `statuses` parameter that does not exist.** Passing it
raised `TypeError`. Multiple statuses are deliberately rejected, so the line was vestigial. Both
order-query methods now state that only a single status is accepted.

### Added

**`StreamClient.close()`**, plus `async with` support. There was previously no way to close a
stream: `logout()` sent the logout frame but left the socket, its keepalive task and its buffers
alive until the object was collected, leaving callers to finalize the connection during interpreter
shutdown. `logout()` now closes the socket as well, and a failure to close no longer masks the error
that caused it.

**Twenty-four equity order templates**, covering stop, stop-limit, trailing stop, trailing stop
limit, market-on-close and limit-on-close across all four equity instructions. The existing eight
templates covered `MARKET` and `LIMIT` only, so every other order type had to be assembled by hand.
Signatures of the existing templates are unchanged.

On the trailing templates, `stop_price_link_type` is a required argument rather than a defaulted
one. An offset of `2.5` means a 2.5% trail under `PERCENT` and a $2.50 trail under `VALUE`, the
venue accepts both, and there is no error to observe if the wrong one is chosen — so the caller is
asked to say which they mean.

**`response_timeout`** on `StreamClient`, defaulting to 60 seconds. `None` restores the previous
wait-forever behaviour.

### Changed

**`StreamClient` now uses `websockets.asyncio` instead of `websockets.legacy`.** The legacy
implementation has been deprecated since websockets 14.0 (2024-11-09), and because the import is at
module scope its eventual removal would break `import schwab.streaming` outright rather than
degrading a single code path. The dependency floor is now `websockets>=14.0`.

`websocket_connect_args` is a documented passthrough to `connect()`, and websockets 14.0 renamed
`extra_headers` to `additional_headers` and removed `create_protocol` and `read_limit`.
`extra_headers` is translated automatically with a `DeprecationWarning`; the removed arguments raise
an error naming the problem rather than an opaque `TypeError` from inside the library.

The connection object is now `ClientConnection` rather than `WebSocketClientProtocol`. Nothing in
the library names or annotates it, but code that annotates it itself will need updating.

**`Duration` now documents which values Schwab accepts for equity orders.** `IMMEDIATE_OR_CANCEL`,
`END_OF_WEEK`, `END_OF_MONTH` and `NEXT_END_OF_MONTH` are rejected at placement for equities with
`HTTP 400`. The values are retained, since they may be valid for other asset types, and because
removing an enum member would break anyone who has one wired up. Established by placing one equity
order per value against a live account.

### Breaking changes

- A synchronous stream handler that raises no longer propagates to the caller. Failures are logged
  with the service name. Callers relying on an exception to detect handler bugs should watch the
  `schwab.streaming` logger instead.
- Stream operations now raise `ResponseTimeoutError` after 60 seconds rather than waiting forever.
  Pass `response_timeout=None` to restore the old behaviour.
- `websockets>=14.0` is required.

### Verification

The full test suite passes on CPython 3.12 and 3.14. The websockets migration was additionally
verified against a live Schwab stream: login, level-one and account-activity subscriptions, a custom
`ssl_context`, and the `extra_headers` compatibility path.

The behaviour of `Duration` and the required fields for each equity order type were established by
placing real orders against a live account rather than from documentation.
