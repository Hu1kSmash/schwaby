'''The red-proof entries tools/redproof.py judges: one per guard, each naming
the mutation that turns the guard off, the test that must then fail, and a
substring that must appear in the failure.

Add an entry when you fix something. The comment above an entry is the
record of why the guard exists, and of what made an earlier version of the
entry lie; keep it when the entry changes.
'''

class Case:
    def __init__(self, name, path, old, new, test, expect, superseded=None,
                 removed=None, also=None):
        self.name = name
        self.path = path        # relative to the repository root
        self.old = old          # exact text to replace
        self.new = new          # what to replace it with
        self.test = test        # pytest node id which must fail; a list
                                # runs several in that order, for a defect
                                # that needs one test to run before another
        self.expect = expect    # substring which must appear in the failure

        # Set when a mutation stops breaking anything *because a later change
        # subsumed the thing it was testing*. GREEN then means the knob is no
        # longer load-bearing, which is a real answer and not a sleeping check
        # -- so the case is kept, with the reason, rather than deleted. Deleting
        # it would lose the fact that it used to matter.
        self.superseded = superseded

        # Set when the code the case mutates was DELETED. Different from
        # superseded: that one still runs and comes back green, this one has
        # nothing left to run against. Without it the case crashes on open(),
        # which reads as the harness being broken rather than the case being
        # obsolete -- three cases sat like that after 3.0.0 removed the extras
        # and the code generator. Kept with the reason for the same reason
        # superseded ones are.
        self.removed = removed

        # A second (old, new) pair applied together with the first. Some
        # guards cannot be turned off at one point: removing a `try` needs its
        # `except` to go too, and a one-sided edit is a SyntaxError, which
        # fails every test in the file and would be read as a red. Both halves
        # must appear exactly once, like the first pair.
        self.also = also


CASES = [
    Case('exceptions/parent-side-flask',
         'schwaby/auth.py',
         "    importlib.import_module('flask')\n",
         '',
         'tests/auth_test.py::ParentSideImportTest'
         '::test_a_missing_flask_is_not_a_redirect_server_failure',
         'is not an instance of'),


    Case('extras/exact-module-name',
         'schwaby/_optional.py',
         "or getattr(exc, 'name', None) != module_name)",
         "or (getattr(exc, 'name', None) or '').split('.')[0] "
         "!= module_name.split('.')[0])",
         'tests/auth_test.py::ImportOptionalTest'
         '::test_a_missing_submodule_is_not_reported_as_a_missing_package',
         'extra',
         removed=('schwaby/_optional.py was deleted in 3.0.0 with the extras machinery.')),

    Case('streaming/element-zero-unclaimed',
         'schwaby/streaming.py',
         '            first_unclaimed = 0',
         '            first_unclaimed = 1',
         'tests/streaming_test.py::StreamClientTest'
         '::test_a_rejection_at_element_zero_is_reported_once_nobody_waits',
         '2 != 1'),

    Case('streaming/skip-malformed-element',
         'schwaby/streaming.py',
         """            if not _is_mapping(response):
                self._absorb('an unparseable response element', response,
                             frame=frame)
                continue
""",
         '',
         'tests/streaming_test.py::StreamClientTest'
         '::test_both_framings_survive_a_malformed_element',
         'ACCT_ACTIVITY'),

    Case('streaming/validate-response-guard',
         'schwaby/streaming.py',
         """        except (AttributeError, IndexError, KeyError, OverflowError,
                TypeError, ValueError) as exc:""",
         """        except IndexError as exc:""",
         'tests/streaming_test.py::StreamClientTest'
         '::test_a_malformed_answer_fails_only_its_own_request',
         'KeyError'),

    Case('streaming/login-clears-overflow',
         'schwaby/streaming.py',
         # Anchored on the comment above it: the same two lines appear in
         # close() too, and a mutation hitting both would prove neither.
         """        # the guarantee has to hold whichever teardown they used.
        self._pending_reports.clear()
        self._overflow_items.clear()
""",
         """        # the guarantee has to hold whichever teardown they used.
        self._pending_reports.clear()
""",
         'tests/streaming_test.py::StreamClientTest'
         '::test_login_discards_unhandled_frames_from_the_dead_session',
         '0 != 1'),

    Case('streaming/close-clears-overflow',
         'schwaby/streaming.py',
         """        self._overflow_items.clear()
        self._absorbed = 0
        self._absorbed_kinds.clear()

        if socket is not None:""",
         """        self._absorbed = 0
        self._absorbed_kinds.clear()

        if socket is not None:""",
         'tests/streaming_test.py::StreamClientTest'
         '::test_close_discards_unhandled_frames_from_the_dead_session',
         '0 != 2'),

    Case('streaming/data-channel-guard',
         'schwaby/streaming.py',
         """        for d in self._iter_channel(msg, 'data'):""",
         """        for d in msg.get('data', []):""",
         'tests/streaming_test.py::StreamClientTest'
         '::test_a_malformed_data_element_does_not_end_the_receive_loop',
         "'str' object has no attribute 'get'"),

    Case('streaming/notify-channel-guard',
         'schwaby/streaming.py',
         """        for d in self._iter_channel(msg, 'notify'):""",
         """        for d in msg.get('notify', []):""",
         'tests/streaming_test.py::StreamClientTest'
         '::test_a_channel_of_the_wrong_shape_does_not_end_the_loop',
         'not iterable'),

    Case('streaming/extra-on-routing-path-cannot-raise',
         'schwaby/streaming.py',
         """            content = response.get('content')
            if not _is_mapping(content):
                content = {}
""",
         """            content = response.get('content') or {}
""",
         'tests/streaming_test.py::StreamClientTest'
         '::test_a_malformed_extra_cannot_fail_a_successful_subscribe',
         "'str' object has no attribute 'get'"),

    Case('streaming/non-dict-frame',
         'schwaby/streaming.py',
         """        if not _is_mapping(msg):
            self._absorb('a message which is not an object', msg)
            return
""",
         '',
         'tests/streaming_test.py::StreamClientTest'
         '::test_a_frame_which_is_not_an_object_is_ignored',
         "'list' object has no attribute 'get'"),

    Case('streaming/non-dict-frame-in-routing',
         'schwaby/streaming.py',
         """        if not _is_mapping(frame):
            return frame

""",
         '',
         'tests/streaming_test.py::StreamClientTest'
         '::test_a_frame_which_is_not_an_object_is_ignored',
         'not iterable'),

    Case('streaming/unhashable-service',
         'schwaby/streaming.py',
         """        try:
            handlers = self._handlers.get(service, ())
        except TypeError:""",
         """        handlers = self._handlers.get(service, ())
        if False:""",
         'tests/streaming_test.py::StreamClientTest'
         '::test_a_service_which_is_not_a_name_is_ignored',
         'unhashable'),

    Case('streaming/routed-sentinel',
         'schwaby/streaming.py',
         "            if msg is not ROUTED:",
         "            if msg is not None:",
         'tests/streaming_test.py::StreamClientTest'
         '::test_a_null_frame_is_logged_like_any_other',
         'StopIteration'),

    Case('streaming/absorbed-reaches-handler',
         'schwaby/streaming.py',
         """        self._pending_reports.append((
                UnusableMessage(""",
         """        if False:
            self._pending_reports.append((
                UnusableMessage(""",
         'tests/streaming_test.py::StreamClientTest'
         '::test_an_absorbed_message_reaches_the_error_handler',
         '4 != 0'),

    Case('streaming/absorbed-log-is-rate-limited',
         'schwaby/streaming.py',
         "        if not force and n > 3 and not _is_power_of_ten(n):\n"
         "            return\n",
         "        if False:\n            return\n",
         'tests/streaming_test.py::StreamClientTest'
         '::test_absorbed_warnings_do_not_flood',
         '6 != 5000'),

    Case('streaming/decoder-may-return-a-mapping',
         'schwaby/streaming.py',
         "def _is_sequence(obj):",
         "def _is_sequence(obj):\n    return isinstance(obj, list)\n\ndef _unused(obj):",
         'tests/streaming_test.py::StreamClientTest'
         '::test_a_custom_decoder_returning_a_mapping_still_works',
         '1 != 0'),

    Case('streaming/requestid-compared-first',
         'schwaby/streaming.py',
         """            mismatched_id = (resp_request_id
                             if resp_request_id != request_id else None)
""",
         "            mismatched_id = None\n",
         'tests/streaming_test.py::StreamClientTest'
         '::test_a_mismatched_request_id_is_named_as_such',
         'malformed response frame'),

    Case('streaming/absorbed-reports-coalesced',
         'schwaby/streaming.py',
         "        if not force and n > 3 and not _is_power_of_ten(n):\n"
         "            return\n",
         '',
         'tests/streaming_test.py::StreamClientTest'
         '::test_a_flood_of_absorbed_messages_cannot_evict_a_rejection',
         'not found in'),

    Case('streaming/absorbed-count-reset-on-login',
         'schwaby/streaming.py',
         """        self._overflow_items.clear()
        self._absorbed = 0
        self._absorbed_kinds.clear()


""",
         """        self._overflow_items.clear()


""",
         'tests/streaming_test.py::StreamClientTest'
         '::test_logging_in_again_resets_the_absorbed_count',
         '0 != 5'),

    Case('streaming/null-channel-absorbed',
         'schwaby/streaming.py',
         """        if channel not in msg:
            return
""",
         """        if msg.get(channel) is None:
            return
""",
         'tests/streaming_test.py::StreamClientTest'
         '::test_a_null_channel_is_absorbed_not_dropped',
         '2 != 0'),

    Case('streaming/iter-responses-duck-typed',
         'schwaby/streaming.py',
         "        if not _is_sequence(responses):",
         "        if not isinstance(responses, list):",
         'tests/streaming_test.py::StreamClientTest'
         '::test_a_response_field_of_the_wrong_shape_is_ignored',
         '1 != 0'),

    Case('streaming/mapping-is-structural',
         'schwaby/streaming.py',
         "    return hasattr(obj, 'get') and hasattr(obj, '__contains__')",
         "    import collections.abc\n    return isinstance(obj, dict)",
         'tests/streaming_test.py::StreamClientTest'
         '::test_a_custom_decoder_returning_a_mapping_still_works',
         '1 != 0'),

    Case('streaming/decoder-base-class-lookup',
         'schwaby/streaming.py',
         "        if not _is_instance(json_decoder, StreamJsonDecoder):",
         "        if not _is_instance(json_decoder, "
         "schwaby.contrib.util.StreamJsonDecoder):",
         'tests/streaming_test.py::StreamClientTest'
         '::test_set_json_decoder_does_not_need_contrib_imported',
         'schwaby'),

    Case('streaming/pretty-falls-back-to-repr',
         'schwaby/streaming.py',
         """        try:
            return json.dumps(obj, indent=4)
        except Exception:""",
         "        if True:\n            return json.dumps(obj, indent=4)\n"
         "        if False:",
         'tests/streaming_test.py::StreamClientTest'
         '::test_a_debug_line_survives_an_unserialisable_frame',
         'not JSON serializable'),

    Case('streaming/mapping-is-not-a-sequence',
         'schwaby/streaming.py',
         "            and not _is_mapping(obj))",
         "            and True)",
         'tests/streaming_test.py::StreamClientTest'
         '::test_a_mapping_channel_is_absorbed_once_not_once_per_key',
         '1 != 3'),

    Case('streaming/responses-must-be-indexable',
         'schwaby/streaming.py',
         """    return (hasattr(obj, '__getitem__')
            and hasattr(obj, '__len__')
            and not isinstance(obj, (str, bytes, bytearray))""",
         """    return (hasattr(obj, '__iter__')
            and not isinstance(obj, (str, bytes, bytearray))""",
         'tests/streaming_test.py::StreamClientTest'
         '::test_a_single_pass_iterable_is_refused_rather_than_half_read',
         'not subscriptable'),

    Case('streaming/absorb-counts-per-kind',
         'schwaby/streaming.py',
         "        n = self._absorbed_kinds[what]",
         "        n = self._absorbed",
         'tests/streaming_test.py::StreamClientTest'
         '::test_a_flood_of_one_kind_does_not_silence_another',
         '7 != 6'),

    Case('streaming/unusable-carries-the-frame',
         'schwaby/streaming.py',
         "                frame if frame is not None else offender))",
         "                offender))",
         'tests/streaming_test.py::StreamClientTest'
         '::test_an_unusable_message_is_not_mistaken_for_a_close_failure',
         'True is not false'),

    Case('streaming/relabel-failure-is-absorbed',
         'schwaby/streaming.py',
         """                if not relabel_failed:
                    relabel_failed = True""",
         """                if False:
                    relabel_failed = True""",
         'tests/streaming_test.py::StreamClientTest'
         '::test_a_relabel_failure_is_absorbed_not_blamed_on_handlers',
         '1 != 0'),

    Case('streaming/id-mismatch-built-outside-the-try',
         'schwaby/streaming.py',
         """        if mismatched_id is not None:
            return UnexpectedResponse(
                resp, 'unexpected requestid: {}'.format(
                    _safe_value(mismatched_id)))
""",
         '',
         'tests/streaming_test.py::StreamClientTest'
         '::test_a_mismatched_request_id_is_named_as_such',
         'UnexpectedResponse'),

    Case('streaming/request-id-absorbed',
         'schwaby/streaming.py',
         """            self._absorb('a response frame with no usable request id',
                         frame.get('response'), frame=frame)""",
         """            self.logger.warning('no usable request id: %r',
                                frame.get('response'))""",
         'tests/streaming_test.py::StreamClientTest'
         '::test_an_unusable_request_id_is_absorbed_not_only_logged',
         '1 != 0'),

    # The guard has a test and had no case, which is how a branch stays
    # load-bearing on paper. It matters now because a real doubly-answered
    # SUBS is the only observed producer of a stale-id frame arriving while
    # another request is in flight -- without the guard the innocent request
    # dies on the old answer, and its own answer then kills the next one.
    # The other half of the same guard, and the one value that decides it:
    # response_id == self._request_id, an id one past the highest issued.
    # Measured GREEN before the test below existed -- `<` could be `<=` and
    # all 1229 tests passed, so the strictness was proven by nothing. Deleting
    # the whole guard (the case after this one) cannot prove it either: in that
    # fixture the stale id is genuinely lower, so this comparison never
    # decides anything there. A case per branch, not a case per line.
    Case('streaming/the-stale-boundary-is-strict',
         'schwaby/streaming.py',
         "        if response_id != request_id and "
         "response_id < self._request_id:\n",
         "        if response_id != request_id and "
         "response_id <= self._request_id:\n",
         'tests/streaming_test.py::StreamClientTest'
         '::test_an_id_one_past_the_highest_issued_fails_the_request',
         'timed out after'),

    Case('streaming/stale-id-is-not-this-requests-answer',
         'schwaby/streaming.py',
         "        if response_id != request_id and "
         "response_id < self._request_id:\n"
         "            return frame\n",
         '',
         'tests/streaming_test.py::StreamClientTest'
         '::test_a_stale_response_does_not_poison_the_next_request',
         'unexpected requestid: 1'),

    Case('streaming/relabel-names-service-and-cause',
         'schwaby/streaming.py',
         "                            msg, frame=msg, service=service, cause=exc)",
         "                            msg, frame=msg)",
         'tests/streaming_test.py::StreamClientTest'
         '::test_a_relabel_failure_names_its_service_and_its_cause',
         "'NASDAQ_BOOK' != None"),

    Case('streaming/unusable-carries-counts',
         'schwaby/streaming.py',
         "                    cause=cause, count=n, total=self._absorbed),",
         "                    cause=cause),",
         'tests/streaming_test.py::StreamClientTest'
         '::test_an_unusable_message_carries_its_counts_as_numbers',
         'None'),

    Case('streaming/unparsable-is-reported',
         'schwaby/streaming.py',
         """            except UnparsableMessage as exc:""",
         """            except _NeverRaised as exc:""",
         'tests/streaming_test.py::StreamClientTest'
         '::test_an_unparsable_frame_is_reported_and_still_raised',
         'JSONDecodeError'),

    Case('streaming/unparsable-report-holds-no-lock',
         'schwaby/streaming.py',
         """                        self._fail_pending_request(exc)
                        raise
            except UnparsableMessage as exc:""",
         """                        self._fail_pending_request(exc)
                        await self._report_error(exc)
                        raise
            except UnparsableMessage as exc:""",
         'tests/streaming_test.py::StreamClientTest'
         '::test_reporting_an_unparsable_frame_holds_no_lock',
         'True'),

    Case('streaming/unparsable-not-a-close-signature',
         'schwaby/streaming.py',
         "            await self._report_error(exc, message=exc.raw_msg)",
         "            await self._report_error(exc)",
         'tests/streaming_test.py::StreamClientTest'
         '::test_an_unparsable_frame_is_not_reported_as_a_close_failure',
         'True is not false'),

    Case('streaming/unparsable-reported-from-request-path',
         'schwaby/streaming.py',
         """        except UnparsableMessage as exc:
            # The other reader.""",
         """        except _NeverRaised as exc:
            # The other reader.""",
         'tests/streaming_test.py::StreamClientTest'
         '::test_an_unparsable_frame_reports_from_the_request_path_too',
         'JSONDecodeError'),

    Case('streaming/unparsable-reported-once',
         'schwaby/streaming.py',
         """        if getattr(exc, '_schwab_reported', False):
            return""",
         """        if False:
            return""",
         'tests/streaming_test.py::StreamClientTest'
         '::test_an_unparsable_frame_is_reported_exactly_once',
         '1 != 2'),

    # --- 3.0.0 / 3.0.1: the exception surface -------------------------------
    #
    # All of this was red-proofed by hand across four review rounds, and the
    # hand version had a blind spot the harness does not: a grep for `FAILED`
    # cannot see a subTest, which pytest reports as SUBFAILED. One mutation
    # came back "nothing noticed" and had in fact been caught. These cases go
    # through the return code instead.

    Case('exceptions/parent-side-cryptography',
         'schwaby/auth.py',
         "    importlib.import_module('cryptography')\n",
         '',
         'tests/auth_test.py::ParentSideImportTest'
         '::test_a_missing_cryptography_is_not_a_redirect_server_failure',
         'is not an instance of'),

    Case('exceptions/missing-location-raises',
         'schwaby/utils.py',
         '            raise MissingLocationHeaderError(',
         '            return None\n            raise MissingLocationHeaderError(',
         'tests/utils_test.py::UtilsTest'
         '::test_no_location_header_raises_rather_than_returning_none',
         'MissingLocationHeaderError'),

    Case('exceptions/unparsable-location-raises',
         'schwaby/utils.py',
         '            raise UnrecognizedLocationError(',
         '            return None\n            raise UnrecognizedLocationError(',
         'tests/utils_test.py::UtilsTest'
         '::test_unparsable_location_raises_and_carries_the_header',
         'UnrecognizedLocationError'),

    # The one the hand red-proof reported GREEN. Aliasing the two classes
    # together passes every other assertion here, because each raise site
    # names its own class and both assertRaises calls still match. Nothing
    # asserted they were distinct until this was noticed.
    Case('exceptions/causes-are-distinct',
         'schwaby/utils.py',
         'class UnrecognizedLocationError(OrderIdNotFoundError):',
         'UnrecognizedLocationError = MissingLocationHeaderError\n'
         'class _UnusedUnrecognizedLocationError(OrderIdNotFoundError):',
         'tests/utils_test.py::UtilsTest'
         '::test_the_two_causes_are_distinguishable',
         'unexpectedly identical'),

    # ValueError is the idiom around int() and float(), and order specs coerce
    # exactly those a few lines from the call -- so inheriting it would let the
    # one exception meaning "an order may be live" be swallowed by a block
    # aimed at parsing.
    Case('exceptions/order-id-not-a-valueerror',
         'schwaby/utils.py',
         'class OrderIdNotFoundError(SchwabError):',
         'class OrderIdNotFoundError(SchwabError, ValueError):',
         'tests/utils_test.py::UtilsTest'
         '::test_a_broad_except_valueerror_does_not_swallow_it',
         'True is not false'),

    # Raised only after a successful response AND a parsed id, so an order is
    # live -- on an account the caller did not expect. 2.x parsed the id one
    # line above and threw it away.
    Case('exceptions/hash-mismatch-carries-the-order',
         'schwaby/utils.py',
         '        self.order_id = order_id',
         '        self.order_id = None',
         'tests/utils_test.py::UtilsTest'
         '::test_get_order_nonmatching_account_hash',
         '123'),

    # A base covering most exceptions is worse than none: it invites
    # `except SchwabError` as a complete guard while quietly not being one.
    Case('exceptions/schwab-error-covers-everything',
         'schwaby/streaming.py',
         'class ResponseTimeoutError(SchwabError):',
         'class ResponseTimeoutError(Exception):',
         'tests/utils_test.py::UtilsTest'
         '::test_schwab_error_covers_every_exception_the_library_defines',
         'ResponseTimeoutError'),

    # These carry the thing they are about as a leading positional and pass
    # only the message to BaseException, so the default reconstruction called
    # __init__ with the message alone.
    Case('exceptions/reconstruction-across-a-boundary',
         'schwaby/utils.py',
         '    def __reduce__(self):\n        # Exceptions here take the thing',
         '    def _disabled_reduce(self):\n        # Exceptions here take the thing',
         'tests/utils_test.py::UtilsTest'
         '::test_every_exception_survives_a_process_boundary',
         'TypeError'),

    # --- 3.0.1: packaging and the install collision -------------------------

    # Without encoding= Python decodes with the locale's preferred one. A wheel
    # built on Linux is correct and one built on Windows is quietly corrupt.
    Case('packaging/setup-py-names-an-encoding',
         'setup.py',
         "with open('README.md', 'r', encoding='utf-8') as f:",
         "with open('README.md', 'r') as f:",
         'tests/packaging_test.py::LongDescriptionTest'
         '::test_setup_py_names_an_encoding_when_it_reads_a_file',
         'without an encoding'),

    # `_assert_finite` used to ask "can float() parse it" and return quietly
    # when the answer was no. Wrong in both directions: None answered no and
    # got through (the offset setters have no comparison after the call, so
    # they built an order with the field silently missing), and b'1' answered
    # yes and got through as bytes, which json.dumps refuses.
    Case('orders/non-number-order-field',
         'schwaby/orders/generic.py',
         "        if not isinstance(price, numbers.Real):",
         "        if False:",
         'tests/orders/generic_test.py::NonNumericOrderFieldTest'
         '::test_a_non_number_is_refused_and_the_message_names_the_field',
         'must be a number',
         removed='The predicate this mutates, `numbers.Real`, existed for one '
                 'commit. A review pointed out it is wider than what '
                 '`_build_object` can serialize -- Fraction and the numpy '
                 'scalars are Real and all reach vars() -- so it became '
                 '`(int, float)`, the serializer\'s own test. '
                 '`orders/number-predicate-matches-the-serializer` replaces '
                 'this case and proves the narrower one; kept because the '
                 'wider predicate is the obvious thing to reach for again.'),

    # The predicate has to match the serializer, not mathematics. Fraction and
    # the numpy scalars that do not subclass int/float are all numbers.Real,
    # and every one of them reaches vars() in _build_object.
    Case('orders/number-predicate-matches-the-serializer',
         'schwaby/orders/generic.py',
         "        if _is_instance(price, bool) or not _is_instance(price, (int, float)):",
         "        if not _is_instance(price, numbers.Real):",
         'tests/orders/generic_test.py::NonNumericOrderFieldTest'
         '::test_a_non_number_is_refused_and_the_message_names_the_field',
         # Fraction is a numbers.Real, so under the wider predicate it is
         # accepted and no ValueError arrives at all.
         'ValueError not raised',
         also=('import math\n', 'import math\nimport numbers\n')),

    # __add_order_leg calls the same validator, so a string quantity reaches
    # every prebuilt template -- the most-used entry point in the package.
    Case('orders/templates-refuse-a-string-quantity',
         'schwaby/orders/generic.py',
         "        if not price_field:",
         "        if False:",
         'tests/orders/generic_test.py::NonNumericOrderFieldTest'
         '::test_the_prebuilt_templates_refuse_a_string_quantity_too',
         # The pre-fix symptom on the leg path: the string reached
         # `quantity <= 0` and raised this instead.
         "not supported between instances of 'str'"),

    # The old version of this built an order from two good values and asserted
    # json.dumps worked, which it did before the fix too.
    Case('orders/guard-keeps-the-order-serialisable',
         'schwaby/orders/generic.py',
         "        if _is_instance(price, bool) or not _is_instance(price, (int, float)):",
         "        if _is_instance(price, type(None)):",
         'tests/orders/generic_test.py::NonNumericOrderFieldTest'
         '::test_the_guard_is_what_keeps_a_built_order_serialisable',
         'ValueError not raised'),

    # bool is an int subclass, so the serializer's own predicate lets it
    # through -- and `_build_object` passes it on, so `set_quantity(True)`
    # built `{"quantity": true}`. A JSON true is not a number in Schwab's
    # schema: the bytes ending, reached by the one type `(int, float)` admits.
    Case('orders/bool-is-not-a-quantity',
         'schwaby/orders/generic.py',
         "        if _is_instance(price, bool) or not _is_instance(price, (int, float)):",
         "        if not _is_instance(price, (int, float)):",
         'tests/orders/generic_test.py::NonNumericOrderFieldTest'
         '::test_a_non_number_is_refused_and_the_message_names_the_field',
         'ValueError not raised'),

    # The `# raises` marker used to skip a call. A documented example that
    # stopped raising would then have gone on claiming it does.
    Case('docs/raises-marker-is-asserted',
         'docs/order-builder.rst',
         "   equity_buy_market('AAPL', '10')     # raises: quantity does not take a str",
         "   equity_buy_market('AAPL', 10)     # raises: quantity does not take a str",
         'tests/packaging_test.py::DocExampleTest'
         '::test_every_documented_order_template_call_still_builds',
         'is marked `# raises` and did not'),

    # A numeric field taking a string: `set_price_offset('abc')` built
    # `{"priceOffset": "abc"}`. The string branch exists for the price fields,
    # so the caller has to say which kind of field it is guarding.
    Case('orders/string-on-a-numeric-field',
         'schwaby/orders/generic.py',
         "        if not price_field:",
         "        if False:",
         'tests/orders/generic_test.py::NonNumericOrderFieldTest'
         '::test_a_string_is_refused_on_a_field_schwab_types_as_a_number',
         # The pre-fix symptom itself: set_quantity('1.5') fell through to
         # `quantity <= 0` and raised this, naming neither field nor value.
         "not supported between instances of 'str'"),

    # Every client_from_* entry point funnels into this one, and no test took
    # its asyncio branch -- so the async token writer was neither reached nor
    # measured.
    Case('auth/async-entry-point',
         'schwaby/auth.py',
         '            wrapped_token_write_func(t, *args, **kwargs)\n'
         '        session_class = AsyncOAuth2Client\n'
         '        client_class = AsyncClient',
         '            wrapped_token_write_func(t, *args, **kwargs)\n'
         '        session_class = OAuth2Client\n'
         '        client_class = Client',
         'tests/auth_test.py::ClientFromAccessFunctionsTest'
         '::test_asyncio_builds_an_async_client_and_still_writes_the_token',
         'AsyncClient'),

    # The same async token writer as auth/async-entry-point, 140 lines away in
    # client_from_received_url. The first fix stopped at the first instance;
    # this is the second, found by grepping the shape.
    Case('auth/async-received-url-writes-token',
         'schwaby/auth.py',
         '            token_write_func(t, *args, **kwargs)\n'
         '        session_class = AsyncOAuth2Client',
         '            pass\n'
         '        session_class = AsyncOAuth2Client',
         'tests/auth_test.py::ClientFromReceivedUrl'
         '::test_async_update_token_still_writes_the_token',
         '2 != 1'),

    # OptionSymbol turns four arguments into a string naming a tradeable
    # contract; getting one wrong gives a different contract rather than an
    # error, so its rejections matter more than the line count suggests.
    Case('options/expiration-string-parses',
         'schwaby/orders/options.py',
         "datetime.datetime.strptime(expiration_date, '%y%m%d')",
         "datetime.datetime.strptime(expiration_date, '%Y-%m-%d')",
         'tests/orders/options_test.py::OptionSymbolInputTest'
         '::test_an_unparseable_expiration_string_is_refused',
         'ValueError not raised'),

    Case('options/contract-type-vocabulary',
         'schwaby/orders/options.py',
         "        elif contract_type in ('P', 'PUT'):",
         "        elif True:",
         'tests/orders/options_test.py::OptionSymbolInputTest'
         '::test_an_unknown_contract_type_is_refused',
         'ValueError not raised'),

    Case('options/expiration-type',
         'schwaby/orders/options.py',
         "        elif _is_instance(expiration_date, datetime.date):",
         "        elif True:",
         'tests/orders/options_test.py::OptionSymbolInputTest'
         '::test_an_expiration_of_the_wrong_type_is_refused',
         # Without the type dispatch the value reaches the constructor's own
         # trailing `assert isinstance(..., datetime.date)`, which is the
         # refusal arriving one line later rather than a red from elsewhere.
         'AssertionError'),

    # A datetime is accepted and narrowed to its date. Untested, and the
    # symbol it builds has to match the one the equivalent date builds.
    Case('options/datetime-expiration-narrows',
         'schwaby/orders/options.py',
         "        elif _is_instance(expiration_date, datetime.datetime):",
         "        elif _is_instance(expiration_date, type(None)):",
         'tests/orders/options_test.py::OptionSymbolInputTest'
         '::test_a_datetime_expiration_is_narrowed_to_its_date',
         '!= datetime.datetime'),

    # `convert_enum` next door was covered both ways; this one was covered
    # only where every element was already the right enum.
    Case('utils/enum-iterable-enforcement',
         'schwaby/utils.py',
         "            elif self.enforce_enums:\n"
         "                self.type_error(value, required_enum_type)",
         "            elif False:\n"
         "                self.type_error(value, required_enum_type)",
         'tests/utils_test.py::ConvertEnumIterableTest'
         '::test_a_wrong_element_is_refused_and_the_message_suggests_the_member',
         'ValueError not raised'),

    # The suggestion is only offered when the string appears in some member's
    # full name. Every existing test passed one that did.
    Case('utils/no-suggestion-when-nothing-matches',
         'schwaby/utils.py',
         "            if possible_members:",
         "            if True:",
         'tests/utils_test.py::ConvertEnumIterableTest'
         '::test_a_string_matching_no_member_gets_no_did_you_mean',
         'Did you mean'),

    # Runs on the failure path, where raising would replace a useful exception
    # with a useless one. [1,2,3] and "a string" are valid JSON and both raise
    # AttributeError on .get; nothing sent it either.
    Case('utils/error-body-is-not-an-object',
         'schwaby/utils.py',
         "        if not isinstance(body, dict):",
         "        if False:",
         'tests/utils_test.py::DescribeErrorTest'
         '::test_a_body_that_is_not_an_object_yields_no_suffix',
         'AssertionError',
         superseded='The whole body sits in `except Exception: return \'\'`, '
                    'so removing the isinstance check does not change what '
                    'the function returns -- `.get` on a list raises '
                    'AttributeError and the handler swallows it to the same '
                    'empty string. The check is intent rather than behaviour: '
                    'it says non-dict is expected instead of leaving it to an '
                    'exception. Kept because the test is still worth having '
                    'and because if that handler is ever narrowed, this case '
                    'goes red again and should.'),

    # Public, documented with automethod, and called by no test at all.
    # Anchored across the following def, because the assignment line on its
    # own also appears in __init__.
    Case('utils/set-account-hash',
         'schwaby/utils.py',
         "        self.account_hash = account_hash\n"
         "\n"
         "    def extract_order_id(self, place_order_response):",
         "        pass\n"
         "\n"
         "    def extract_order_id(self, place_order_response):",
         'tests/utils_test.py::SetAccountHashTest'
         '::test_it_is_the_hash_a_later_call_actually_sends',
         'AccountHashMismatch'),

    # A failure wrongly called terminal stops an application that only needed
    # to retry, and the two reads after this one are .find and .rfind.
    Case('client/refresh-error-description-type',
         'schwaby/client/base.py',
         "        if not isinstance(description, str):",
         "        if False:",
         'tests/client_test.py::ClientTest'
         '::test_a_description_that_is_not_a_string_is_not_terminal',
         'TokenRefreshError'),

    # warnings.warn reads 0 as "blame the frame which called warn", which is
    # this library -- the exact failure _caller_stacklevel exists to avoid.
    Case('client/stacklevel-never-zero',
         'schwaby/client/base.py',
         "                level += 1\n            return 1",
         "                level += 1\n            return 0",
         'tests/client_test.py::ClientTest'
         '::test_a_stack_entirely_inside_the_package_still_blames_somebody',
         '1 != 0'),

    Case('streaming/json-decoder-type',
         'schwaby/streaming.py',
         "        if not _is_instance(json_decoder, StreamJsonDecoder):",
         "        if False:",
         'tests/streaming_test.py::StreamClientTest'
         '::test_set_json_decoder_refuses_something_that_is_not_one',
         'ValueError not raised'),

    Case('streaming/receive-before-login',
         'schwaby/streaming.py',
         "    async def _receive_from_socket(self):\n"
         "        if self._socket is None:",
         "    async def _receive_from_socket(self):\n"
         "        if False:",
         'tests/streaming_test.py::StreamClientTest'
         '::test_receiving_before_login_says_so',
         "'NoneType' object has no attribute 'recv'"),

    # Order templates are called positionally in the documentation, and
    # `bad_keywords_in` inspects only keyword arguments -- so the exact shape
    # 4.1.0 broke, `equity_buy_market('AAPL', '10')`, was invisible to every
    # other check over these files.
    Case('docs/documented-template-calls-build',
         'docs/order-templates.rst',
         "      equity_buy_limit('GOOG', 1, '1250.00')\n",
         "      equity_buy_limit('GOOG', '1', '1250.00')\n",
         'tests/packaging_test.py::DocExampleTest'
         '::test_every_documented_order_template_call_still_builds',
         'does not take a str'),

    # The ACCT_ACTIVITY decimal decoder. Each of these is a wrong number
    # rather than an error, which is why the function exists at all.
    Case('contrib/decimal-mantissa-spans-three-fields',
         'schwaby/contrib/util.py',
         "        for name, shift in (('hi', 64), ('mid', 32), ('lo', 0)):",
         "        for name, shift in (('lo', 0),):",
         'tests/contrib/util_test.py::DecodeDecimalTest'
         '::test_the_mantissa_spans_three_fields',
         '705.032704'),

    Case('contrib/decimal-odd-signscale-is-negative',
         'schwaby/contrib/util.py',
         "            '-' if scale % 2 else '', mantissa, scale // 2))",
         "            '', mantissa, scale // 2))",
         'tests/contrib/util_test.py::DecodeDecimalTest'
         '::test_an_odd_signscale_is_negative',
         '-40'),

    Case('contrib/decimal-no-mantissa-is-zero',
         'schwaby/contrib/util.py',
         "        return decimal.Decimal(0)\n",
         "        return None\n",
         'tests/contrib/util_test.py::DecodeDecimalTest'
         '::test_a_mantissa_less_object_is_zero_not_unknown',
         'Decimal',
         superseded='This mutates the shortcut to `return None`, which is a '
                    'shape no implementation would have -- the realistic '
                    'counterfactual is falling through, and that is a '
                    'different case now. Until the scale validation moved '
                    'above it, falling through raised on `{}`, so the '
                    'shortcut was load-bearing for a reason this mutation '
                    'never named. Since it moved, the shortcut only '
                    'normalises: falling through gives 0.000000, or '
                    '-0.000000 on an odd scale, both equal to zero and the '
                    'second printing as a negative quantity. '
                    '`contrib/decimal-no-mantissa-has-no-sign` mutates the '
                    'realistic way and proves what is left.'),

    # The realistic counterfactual for the shortcut: not `return None`, but
    # falling through to the arithmetic, which reaches zero by another route
    # and prints it as -0.000000 on an odd scale.
    Case('contrib/decimal-no-mantissa-has-no-sign',
         'schwaby/contrib/util.py',
         "        return decimal.Decimal(0)\n",
         "        pass\n",
         'tests/contrib/util_test.py::DecodeDecimalTest'
         '::test_a_mantissa_less_object_is_zero_without_a_negative_sign',
         "'0' != '0.000000'"),

    # The scale is validated and bounded before the shortcut can return, so
    # corruption is refused whether or not a mantissa happens to be present.
    Case('contrib/decimal-scale-checked-before-the-shortcut',
         'schwaby/contrib/util.py',
         "    scale_member = fields.get('signScale')",
         "    if all(fields.get(k) is None for k in ('lo', 'mid', 'hi')):\n"
         "        return decimal.Decimal(0)\n"
         "    scale_member = fields.get('signScale')",
         'tests/contrib/util_test.py::DecodeDecimalTest'
         '::test_a_corrupt_scale_is_refused_even_with_no_mantissa_to_scale',
         'UnusableDecimalScale not raised'),

    # An explicit JSON null is an absence in every slot. The member loop
    # always treated one that way and the scale briefly did not.
    Case('contrib/decimal-null-scale-is-an-absent-scale',
         'schwaby/contrib/util.py',
         "    scale = _unsigned(0 if scale_member is None else scale_member,",
         "    scale = _unsigned(scale_member,",
         'tests/contrib/util_test.py::DecodeDecimalTest'
         '::test_an_explicit_null_is_an_absence_in_every_slot',
         'UnusableDecimalScale'),

    # An error message that raises on the value it describes replaces a
    # SchwabError with a bare ValueError -- the escape class this function
    # spent four rounds closing, arriving through the error path.
    Case('contrib/decimal-error-message-cannot-raise',
         'schwaby/contrib/util.py',
         "        text = '<{} that cannot be formatted>'"
         ".format(_type_name(value))",
         "        raise",
         'tests/contrib/util_test.py::DecodeDecimalTest'
         '::test_an_error_message_cannot_raise_on_what_it_describes',
         'Exceeds the limit'),

    # The lower half of the scale bound. Mutating the whole statement to
    # `if False:` cannot see which half is load-bearing, and the upper half
    # alone leaves every test green because `_unsigned` refuses a negative
    # scale first -- so this case exists to say that is *why*, rather than
    # leaving a reader to assume the lower half is doing something.
    Case('contrib/decimal-scale-lower-bound-is-subsumed',
         'schwaby/contrib/util.py',
         "    if not 0 <= scale <= MAX_SIGN_SCALE:",
         "    if not scale <= MAX_SIGN_SCALE:",
         'tests/contrib/util_test.py::DecodeDecimalTest'
         '::test_an_implausible_scale_raises_rather_than_underflowing_to_zero',
         'UnusableDecimalScale not raised',
         superseded='`_unsigned` refuses a negative scale before this '
                    'runs, so the lower half of the bound is unreachable and '
                    'this mutation is green. Kept rather than narrowed: the '
                    '`0 <=` is the statement of intent, and if the scale ever '
                    'stops going through `_unsigned` it becomes the only '
                    'thing standing between a negative scale and '
                    '`10 ** -n`.'),

    Case('contrib/decimal-refuses-to-guess-a-scale',
         'schwaby/contrib/util.py',
         "        raise UnusableDecimalScale(\n"
         "                'decimal object has a mantissa and no signScale, "
         "so its scale '",
         "        return decimal.Decimal(0)\n"
         "        raise UnusableDecimalScale(\n"
         "                'decimal object has a mantissa and no signScale, "
         "so its scale '",
         'tests/contrib/util_test.py::DecodeDecimalTest'
         '::test_a_mantissa_without_a_scale_is_refused_rather_than_guessed',
         'UnusableDecimalScale not raised',
         removed='The behaviour this proved was wrong, and this case is the '
                 'record of how confidently. A mantissa with no signScale '
                 'was refused because a missing scale cannot be told from a '
                 'lost one -- sound reasoning on a false premise. The '
                 'docstring called the shape unobserved; a real capture '
                 'carried eight, and `AskSize` {"lo": "19200"} sits '
                 'beside `Ask` {"lo": "13720000", "signScale": 12} in one '
                 'quote. An absent scale is zero, like an absent mantissa '
                 'member, and refusing it cost both sizes on every quote. '
                 'Replaced by `contrib/decimal-absent-scale-is-zero`. The '
                 'lesson: state a negative result as a reason, not as a '
                 'search.'),

    # An absent `signScale` is scale 0, by the same rule as an absent
    # mantissa member -- the serializer omits what is zero. Measured against
    # a real quote, which is what overturned the raise this replaces.
    Case('contrib/decimal-absent-scale-is-zero',
         'schwaby/contrib/util.py',
         "    scale = _unsigned(0 if scale_member is None else scale_member,",
         "    scale = _unsigned(12 if scale_member is None else scale_member,",
         'tests/contrib/util_test.py::DecodeDecimalTest'
         '::test_a_mantissa_without_a_scale_is_scale_zero',
         '19200'),

    # The bare-value path was the unvalidated sibling of the member path for
    # three rounds. `Decimal`'s parser takes PEP 515 underscores, surrounding
    # whitespace, a leading +, and every Unicode decimal digit -- so '1_0' was
    # ten and '\u0663' was three, on a money field, silently.
    Case('contrib/decimal-bare-value-is-ascii-number',
         'schwaby/contrib/util.py',
         "        if not (value.isascii() and _BARE_NUMBER.match(value)):",
         "        if False:",
         'tests/contrib/util_test.py::DecodeDecimalTest'
         '::test_a_bare_value_refuses_what_a_member_refuses',
         'UnusableDecimalScale not raised'),

    # A bare value carries no scale to bound, so the exponent is bounded
    # instead. 1E-999999999 compares neither == 0 nor <= 0 while behaving as
    # zero in arithmetic, which is the hazard MAX_SIGN_SCALE exists for
    # arriving by another door.
    Case('contrib/decimal-bare-exponent-is-bounded',
         'schwaby/contrib/util.py',
         "    if not (-MAX_EXPONENT <= exponent\n"
         "            and result.adjusted() <= MAX_EXPONENT):",
         "    if False:",
         'tests/contrib/util_test.py::DecodeDecimalTest'
         '::test_a_bare_exponent_is_bounded_like_a_scale',
         'UnusableDecimalScale not raised'),

    # Both halves of the same bound: the negative side is the one that
    # underflows to a silent zero, the positive side is the one whose
    # `Decimal` construction raises before any check can run.
    Case('contrib/decimal-bare-integer-width-is-bounded',
         'schwaby/contrib/util.py',
         "        if value.bit_length() > 256:",
         "        if False:",
         'tests/contrib/util_test.py::DecodeDecimalTest'
         '::test_a_bare_exponent_is_bounded_like_a_scale',
         'Exceeds the limit',
         superseded='This went red only because the exponent check below it '
                    'formatted the value with `{!r}`, which raised on a wide '
                    'integer -- so what it actually proved was the escape, '
                    'not the bound. `_safe_repr` closed that, and the '
                    'exponent check now refuses the same values on its own. '
                    'The bit_length guard is a cheap pre-check that stops a '
                    'huge `Decimal` being built at all; kept for that, and '
                    'kept as a case because its going green is the evidence '
                    'the escape is gone.'),

    # `int()` refuses a digit string past sys.get_int_max_str_digits() with a
    # bare ValueError, and the range check below it cannot run until int()
    # has returned -- so the guard written to stop members escaping had an
    # escape inside it.
    Case('contrib/decimal-member-width-precedes-int',
         'schwaby/contrib/util.py',
         "            ok = len(digits) <= len(str(2 ** bits - 1))",
         "            ok = True",
         'tests/contrib/util_test.py::DecodeDecimalTest'
         '::test_a_wide_digit_string_does_not_escape_as_a_bare_value_error',
         'Exceeds the limit'),

    # Two guards on one line and both load-bearing: the length check stops a
    # genuinely long member, and stripping leading zeros stops a padded one
    # being judged on its width rather than its value. A fixture of
    # '0' * 4300 + '1' satisfies the first through the second and leaves the
    # length check unproven -- which is how it went GREEN the first time.
    Case('contrib/decimal-member-width-ignores-padding',
         'schwaby/contrib/util.py',
         "            digits = member.lstrip('0') or '0'   # an all-zero member",
         "            digits = member",
         'tests/contrib/util_test.py::DecodeDecimalTest'
         '::test_a_wide_digit_string_does_not_escape_as_a_bare_value_error',
         'UnusableDecimalScale'),

    # A service Schwab adds cannot reach a handler -- there is none, and no
    # field table to relabel with -- so the message is dropped. Going quiet
    # means a whole feed can appear without anyone learning it exists.
    Case('streaming/unknown-service-is-reported',
         'schwaby/streaming.py',
         "        if service is not None and service not in _KNOWN_SERVICES:",
         "        if False:",
         'tests/streaming_test.py::StreamClientTest'
         '::test_a_service_this_version_does_not_know_is_reported',
         '1 != 0'),

    # The other half, and the one that decides whether this is usable: a
    # service the caller simply registered no handler for is their own
    # choice, and reporting it puts a line on every message of a feed
    # somebody deliberately ignored.
    Case('streaming/a-known-service-without-a-handler-is-not-news',
         'schwaby/streaming.py',
         "        if service is not None and service not in _KNOWN_SERVICES:",
         "        if service is not None and not handlers:",
         'tests/streaming_test.py::StreamClientTest'
         '::test_a_known_service_without_a_handler_stays_quiet',
         '0 != 2'),

    # A whole compartment this version does not read is a bigger thing than
    # an unnamed field: every message in it is being dropped.
    Case('streaming/unknown-channel-is-reported',
         'schwaby/streaming.py',
         "        unknown_channels = set(msg) - _KNOWN_CHANNELS",
         "        unknown_channels = set()",
         'tests/streaming_test.py::StreamClientTest'
         '::test_a_channel_this_version_does_not_read_is_reported',
         '1 != 0'),

    # Reported and then carried past. Refusing the frame over an unread
    # compartment would turn an addition into an outage, which is the whole
    # failure this work exists to avoid.
    Case('streaming/unknown-channel-does-not-drop-the-frame',
         'schwaby/streaming.py',
         "                    names, frame=msg, force=first)",
         "                    names, frame=msg, force=first)\n"
         "            return",
         'tests/streaming_test.py::StreamClientTest'
         '::test_a_channel_this_version_does_not_read_is_reported',
         '2 != 1'),

    # The declared set can go stale, and a service missing from it would be
    # reported as unknown on the very traffic it was added to receive.
    Case('streaming/known-service-set-is-not-stale',
         'schwaby/streaming.py',
         "    'CHART_FUTURES',\n",
         "",
         'tests/streaming_test.py::KnownServiceSetTest'
         '::test_the_known_service_set_matches_the_code_that_uses_it',
         'CHART_FUTURES'),

    # A field Schwab adds reaches a handler either way; the report is what
    # makes anyone aware it exists. Without it a schema change is invisible.
    Case('streaming/unknown-field-is-reported',
         'schwaby/streaming.py',
         "            elif text_key.isdigit():",
         "            elif False:",
         'tests/streaming_test.py::UnknownStreamFieldTest'
         '::test_an_unknown_field_is_reported_once_per_table_and_id',
         'no logs of level WARNING or higher triggered'),

    # `isdigit` is the discriminator, not "missing from the table". `key`,
    # `seq`, `delayed` and `assetMainType` all miss the table and none is a
    # field, so the looser test reports four per message -- a flood, which
    # hides the one line that matters.
    Case('streaming/non-numeric-keys-are-not-fields',
         'schwaby/streaming.py',
         "            elif text_key.isdigit():",
         "            elif True:",
         'tests/streaming_test.py::UnknownStreamFieldTest'
         '::test_the_keys_that_are_not_fields_are_not_reported',
         'WARNING'),

    # Once per table and id. A per-message line on a live feed is a flood.
    Case('streaming/unknown-field-is-reported-once',
         'schwaby/streaming.py',
         "    seen = (field_enum_type.__name__, field_id)\n"
         "    if (seen in _reported_fields\n"
         "            or len(_reported_fields) >= _MAX_REPORTED_FIELDS):",
         "    seen = (field_enum_type.__name__, field_id)\n"
         "    if False:",
         'tests/streaming_test.py::UnknownStreamFieldTest'
         '::test_an_unknown_field_is_reported_once_per_table_and_id',
         '1 != 3'),

    # An unknown id must still be *delivered*. Reporting it is the addition;
    # dropping it would be a regression dressed as a fix.
    Case('streaming/unknown-field-is-still-delivered',
         'schwaby/streaming.py',
         "                _report_unknown_field(cls, text_key[:64])",
         "                new_msg.pop(old_key)",
         'tests/streaming_test.py::UnknownStreamFieldTest'
         '::test_an_unknown_field_is_still_delivered_verbatim',
         'KeyError'),

    # An unknown key beside a *missing* component is ambiguous: it may be
    # that component renamed. Guessed at, a renamed scale turned a $6.86
    # limit price into $6,860,000 -- six orders of magnitude, silently, on
    # the field this feed exists to carry.
    Case('contrib/decimal-renamed-component-is-refused',
         'schwaby/contrib/util.py',
         "        if keys & _DECIMAL_KEYS and (\n"
         "                not present & _MANTISSA_KEYS or 'signScale' not in present):",
         "        if False:",
         'tests/contrib/util_test.py::DecodeDecimalTest'
         '::test_a_renamed_key_is_refused_in_either_direction',
         'UnusableDecimalScale not raised',
         removed="The version this mutates is gone. It tried to decode around an unrecognised key -- ignoring one on an object that looked complete, refusing one where a component was missing, on the reasoning that the unknown key might be that component renamed. Three review rounds each found a spelling of 'missing' it did not cover: the key absent, the key present and null, the key present and zero. Each was a $6.86 limit price decoding as $6,860,000. The third cannot be closed at all -- `signScale: 0` genuinely means scale 0. Measured against a real capture it also bought nothing: under a rename that keeps the old key at its default, 27 of 37 objects still decoded wrong, which is what no guard does. Any unrecognised key refuses the object now; `contrib/decimal-unknown-key-refuses` is the case. Kept because decoding around an unknown key is the obvious thing to reach for and was reached for three times."),


    # The guard and the decoder must mean the same thing by "present". Keyed
    # on mere presence, an explicit null slipped the guard and then met the
    # absent-scale rule, decoding $6.86 as $6,860,000 -- the exact defect the
    # refusal was added to close, back through a different door.
    Case('contrib/decimal-null-does-not-slip-the-rename-guard',
         'schwaby/contrib/util.py',
         "        present = {k for k in keys & _DECIMAL_KEYS\n"
         "                   if value.get(k) is not None}",
         "        present = keys & _DECIMAL_KEYS",
         'tests/contrib/util_test.py::DecodeDecimalTest'
         '::test_an_explicit_null_does_not_slip_the_rename_guard',
         'UnusableDecimalScale not raised',
         removed="The version this mutates is gone. It tried to decode around an unrecognised key -- ignoring one on an object that looked complete, refusing one where a component was missing, on the reasoning that the unknown key might be that component renamed. Three review rounds each found a spelling of 'missing' it did not cover: the key absent, the key present and null, the key present and zero. Each was a $6.86 limit price decoding as $6,860,000. The third cannot be closed at all -- `signScale: 0` genuinely means scale 0. Measured against a real capture it also bought nothing: under a rename that keeps the old key at its default, 27 of 37 objects still decoded wrong, which is what no guard does. Any unrecognised key refuses the object now; `contrib/decimal-unknown-key-refuses` is the case. Kept because decoding around an unknown key is the obvious thing to reach for and was reached for three times."),


    # A key is whatever the decoder produced, and `str()` of a wide integer
    # raises past the digit limit -- in the report and in the refusal
    # message, both of which name the unknown keys.
    Case('contrib/decimal-unnameable-key-does-not-escape',
         'schwaby/contrib/util.py',
         "        text = '<{} that cannot be named>'.format(_type_name(key))",
         "        raise",
         'tests/contrib/util_test.py::DecodeDecimalTest'
         '::test_a_key_that_cannot_be_named_does_not_escape',
         'Exceeds the limit'),

    # A case per call site, not per helper. `_safe_key` has three callers --
    # the report and the two refusal messages -- and a case on the helper's
    # own body proves only that *some* caller reaches it. Reverting both
    # refusal sites left the whole suite green.
    Case('contrib/decimal-refusal-message-cannot-raise',
         'schwaby/contrib/util.py',
         "                'is not visible from here: {}'.format(\n"
         "                    _safe_keys(unknown),",
         "                'is not visible from here: {}'.format(\n"
         "                    ', '.join(sorted(map(str, unknown))),",
         'tests/contrib/util_test.py::DecodeDecimalTest'
         '::test_a_refusal_message_cannot_raise_on_the_keys_it_names',
         'Exceeds the limit'),

    Case('contrib/decimal-not-an-object-message-cannot-raise',
         'schwaby/contrib/util.py',
         "                    'only {}: {}'.format(\n"
         "                        _safe_keys(unknown),",
         "                    'only {}: {}'.format(\n"
         "                        ', '.join(sorted(map(str, unknown))),",
         'tests/contrib/util_test.py::DecodeDecimalTest'
         '::test_a_refusal_message_cannot_raise_on_the_keys_it_names',
         'Exceeds the limit'),

    # Bounded in count as well as per key: the join did not cap, so 100,000
    # unknown keys produced a 2.9 MB exception message.
    Case('contrib/decimal-refusal-message-bounded-in-count',
         'schwaby/contrib/util.py',
         "    if len(names) <= 8:",
         "    if True:",
         'tests/contrib/util_test.py::DecodeDecimalTest'
         '::test_a_refusal_message_is_bounded_in_count_as_well',
         'not less than 1000'),

    # Bounded in length as well as in count: the set never shrinks, so a
    # 2 MB key would be retained forever and named in full in the log.
    Case('contrib/decimal-reported-key-length-is-bounded',
         'schwaby/contrib/util.py',
         "    return text if len(text) <= 64 else text[:61] + '...'",
         "    return text",
         'tests/contrib/util_test.py::DecodeDecimalTest'
         '::test_a_key_that_cannot_be_named_does_not_escape',
         'not less than or equal to'),

    # Same two hazards on the streaming side, where a million-digit id
    # passes isdigit().
    Case('streaming/unnameable-field-key-does-not-escape',
         'schwaby/streaming.py',
         "            except Exception:\n"
         "                # A key is whatever the decoder produced",
         "            except ZeroDivisionError:\n"
         "                # A key is whatever the decoder produced",
         'tests/streaming_test.py::StreamClientTest'
         '::test_a_field_key_that_cannot_be_named_does_not_escape',
         'Exceeds the limit'),

    Case('streaming/reported-field-id-length-is-bounded',
         'schwaby/streaming.py',
         "                _report_unknown_field(cls, text_key[:64])",
         "                _report_unknown_field(cls, text_key)",
         'tests/streaming_test.py::StreamClientTest'
         '::test_a_field_key_that_cannot_be_named_does_not_escape',
         'not less than or equal to'),

    # Both halves of that predicate, because one alone leaves the other
    # decoding a confident wrong number and the whole-statement mutation
    # above cannot tell which half is load-bearing.
    Case('contrib/decimal-renamed-scale-half',
         'schwaby/contrib/util.py',
         "        if keys & _DECIMAL_KEYS and (\n"
         "                not present & _MANTISSA_KEYS or 'signScale' not in present):",
         "        if keys & _DECIMAL_KEYS and not present & _MANTISSA_KEYS:",
         'tests/contrib/util_test.py::DecodeDecimalTest'
         '::test_a_renamed_key_is_refused_in_either_direction',
         'UnusableDecimalScale not raised',
         removed="The version this mutates is gone. It tried to decode around an unrecognised key -- ignoring one on an object that looked complete, refusing one where a component was missing, on the reasoning that the unknown key might be that component renamed. Three review rounds each found a spelling of 'missing' it did not cover: the key absent, the key present and null, the key present and zero. Each was a $6.86 limit price decoding as $6,860,000. The third cannot be closed at all -- `signScale: 0` genuinely means scale 0. Measured against a real capture it also bought nothing: under a rename that keeps the old key at its default, 27 of 37 objects still decoded wrong, which is what no guard does. Any unrecognised key refuses the object now; `contrib/decimal-unknown-key-refuses` is the case. Kept because decoding around an unknown key is the obvious thing to reach for and was reached for three times."),


    Case('contrib/decimal-renamed-mantissa-half',
         'schwaby/contrib/util.py',
         "        if keys & _DECIMAL_KEYS and (\n"
         "                not present & _MANTISSA_KEYS or 'signScale' not in present):",
         "        if keys & _DECIMAL_KEYS and 'signScale' not in present:",
         'tests/contrib/util_test.py::DecodeDecimalTest'
         '::test_a_renamed_key_is_refused_in_either_direction',
         'UnusableDecimalScale not raised',
         removed="The version this mutates is gone. It tried to decode around an unrecognised key -- ignoring one on an object that looked complete, refusing one where a component was missing, on the reasoning that the unknown key might be that component renamed. Three review rounds each found a spelling of 'missing' it did not cover: the key absent, the key present and null, the key present and zero. Each was a $6.86 limit price decoding as $6,860,000. The third cannot be closed at all -- `signScale: 0` genuinely means scale 0. Measured against a real capture it also bought nothing: under a rename that keeps the old key at its default, 27 of 37 objects still decoded wrong, which is what no guard does. Any unrecognised key refuses the object now; `contrib/decimal-unknown-key-refuses` is the case. Kept because decoding around an unknown key is the obvious thing to reach for and was reached for three times."),


    # The cap is justified by input nobody here controls, and `update`
    # before the check let one object carrying 500 unknown keys put all 500
    # into a set bounded at 32.
    Case('contrib/decimal-reported-keys-bounded-per-call',
         'schwaby/contrib/util.py',
         "    fresh = sorted({_safe_key(k) for k in unknown} "
         "- _reported_keys)[:room]",
         "    fresh = sorted({_safe_key(k) for k in unknown} - _reported_keys)",
         'tests/contrib/util_test.py::DecodeDecimalTest'
         '::test_the_unknown_key_report_is_bounded',
         'not less than or equal to'),

    # Key-shaped corruption reached the mantissa-less shortcut and decoded as
    # zero without anything having inspected it -- and zero is what a $0
    # commission and a completed fill look like.
    Case('contrib/decimal-object-keys-are-known',
         'schwaby/contrib/util.py',
         "        if not fields:",
         "        if False:",
         'tests/contrib/util_test.py::DecodeDecimalTest'
         '::test_an_object_that_is_not_a_decimal_is_refused_not_read_as_zero',
         'UnusableDecimalScale not raised',
         superseded='Both halves of this refuse now, so the branch chooses a '
                    'message rather than an outcome and the mutation cannot '
                    'change what a caller sees. It was load-bearing while an '
                    'unrecognised key could be ignored: an object carrying '
                    'none of the four fell through to the mantissa-less '
                    'shortcut and decoded as a genuine zero. Kept because '
                    'the two messages are different events for whoever reads '
                    'them -- the venue changing, and a caller passing the '
                    'wrong object -- and `test_the_refusal_says_it_is_a_'
                    'schema_change_not_corruption` pins that.'),

    # The report is the whole of what refusing-with-a-message adds over
    # refusing quietly, and its budget is bounded, module-level and never
    # cleared -- so a caller handing this function the wrong object must not
    # spend it. One ACCT_ACTIVITY frame walked generically filled all 32
    # slots, after which a genuine schema addition logged nothing.
    Case('contrib/decimal-wrong-object-does-not-spend-the-budget',
         'schwaby/contrib/util.py',
         "        if not fields:",
         "        _report_unknown_keys(unknown)\n"
         "        if not fields:",
         'tests/contrib/util_test.py::DecodeDecimalTest'
         '::test_the_wrong_object_does_not_consume_the_report_budget',
         'Unexpected logs found'),

    # A *second* distinct key earns its own line. Without the assertion this
    # names, "report the first key ever seen and then go permanently silent"
    # passes the entire suite.
    Case('contrib/decimal-second-added-key-is-reported-too',
         'schwaby/contrib/util.py',
         "    fresh = sorted({_safe_key(k) for k in unknown} "
         "- _reported_keys)[:room]",
         "    fresh = ([] if _reported_keys else\n"
         "             sorted({_safe_key(k) for k in unknown})[:room])",
         'tests/contrib/util_test.py::DecodeDecimalTest'
         '::test_an_added_key_is_still_reported_once_per_key',
         'no logs of level WARNING or higher triggered'),

    # The cap holds when the report works and when it never ran; only a
    # positive control tells those apart.
    Case('contrib/decimal-bounded-test-has-a-control',
         'schwaby/contrib/util.py',
         "    room = _MAX_REPORTED_KEYS - len(_reported_keys)",
         "    room = 0",
         'tests/contrib/util_test.py::DecodeDecimalTest'
         '::test_the_unknown_key_report_is_bounded',
         '32 != 0'),

    # The whole rule, in one line. Three attempts at something cleverer are
    # `removed=` above with the reason; this is what replaced them.
    Case('contrib/decimal-unknown-key-refuses',
         'schwaby/contrib/util.py',
         "    if unknown:",
         "    if False:",
         'tests/contrib/util_test.py::DecodeDecimalTest'
         '::test_any_unrecognised_key_refuses_the_object',
         'UnusableDecimalScale not raised'),

    # `_absorb` coalesces across service names, because its counter is keyed
    # on a fixed string. Five new services on one connection and the fourth
    # was never named, in no log line and no callback, over a thousand
    # frames. The field path already guaranteed a line per distinct id; the
    # worse event had the weaker guarantee.
    Case('streaming/every-unknown-service-is-named',
         'schwaby/streaming.py',
         "            first = self._report_new_shape('service', service)",
         "            first = False",
         'tests/streaming_test.py::StreamClientTest'
         '::test_every_unknown_service_is_named_at_least_once',
         'SVC_D'),

    Case('streaming/every-unknown-channel-is-named',
         'schwaby/streaming.py',
         "            first = any([self._report_new_shape('channel', n)\n"
         "                         for n in names])",
         "            first = False",
         'tests/streaming_test.py::StreamClientTest'
         '::test_every_unknown_channel_is_named_at_least_once',
         'chanD'),

    # `_pretty`'s whole purpose is that a formatting failure stays cosmetic,
    # and its `repr` fallback raised on a dict holding a wide integer key --
    # the third instance of that hazard in this module.
    Case('streaming/pretty-fallback-cannot-raise',
         'schwaby/streaming.py',
         "            try:\n"
         "                return repr(obj)\n"
         "            except Exception:",
         "            if True:\n"
         "                return repr(obj)\n"
         "            elif False:",
         'tests/streaming_test.py::StreamClientTest'
         '::test_a_channel_name_that_cannot_be_named_does_not_escape',
         'LazyLog object'),

    # A top-level frame key is whatever the decoder produced. `str()` of a
    # wide integer raises past the digit limit, and this ran on the channel
    # path in two places without the guard `_safe_repr` already had.
    Case('streaming/unnameable-channel-key-does-not-escape',
         'schwaby/streaming.py',
         "        text = '<{} that cannot be named>'.format("
         "_type_name(value))",
         "        raise",
         'tests/streaming_test.py::StreamClientTest'
         '::test_a_channel_name_that_cannot_be_named_does_not_escape',
         'no name for you'),

    # `json.loads` maps `1e999` and `Infinity` to `float('inf')`, and
    # `int(inf)` raises OverflowError -- in neither guard tuple, so an
    # ordinary JSON number literal ended the receive loop and killed the
    # in-flight request with a bare non-SchwabError. `NaN` raises
    # ValueError, which is why one of the two spellings was covered.
    Case('streaming/non-finite-requestid-validate',
         'schwaby/streaming.py',
         "        except (AttributeError, IndexError, KeyError, OverflowError,\n"
         "                TypeError, ValueError) as exc:",
         "        except (AttributeError, IndexError, KeyError,\n"
         "                TypeError, ValueError) as exc:",
         'tests/streaming_test.py::StreamClientTest'
         '::test_validate_response_refuses_a_non_finite_requestid_too',
         'OverflowError'),

    Case('streaming/non-finite-requestid-route',
         'schwaby/streaming.py',
         "        except (AttributeError, IndexError, KeyError, OverflowError,\n"
         "                TypeError, ValueError):",
         "        except (AttributeError, IndexError, KeyError,\n"
         "                TypeError, ValueError):",
         'tests/streaming_test.py::StreamClientTest'
         '::test_a_non_finite_requestid_does_not_end_the_receive_loop',
         'OverflowError'),

    # A case per call site, not per helper. Reverting the helper reds off
    # one site and reports the whole sweep proven -- three of the five were
    # green, including the routing-path one the work was justified by.
    Case('streaming/routing-path-response-value-is-safe',
         'schwaby/streaming.py',
         "                                _safe_value(code),\n"
         "                                _safe_value(content.get('msg')))),",
         "                                code, content.get('msg'))),",
         'tests/streaming_test.py::StreamClientTest'
         '::test_an_accepted_subscribe_survives_an_unformattable_sibling',
         'Exceeds the limit'),

    # The content must survive into the line, not just the line survive.
    Case('streaming/safe-value-keeps-its-content',
         'schwaby/streaming.py',
         "    text = str.__str__(text)\n    # Redacted before it is cut, so a secret across the cut leaves nothing.\n    text = _redact.before_cut(text, 200)\n"
         "    return _printable(text if len(text) <= 200 else text[:197] + '...', 200)",
         "    return ''",
         'tests/streaming_test.py::StreamClientTest'
         '::test_a_formatted_venue_value_keeps_its_content_and_its_bound',
         "'hello'"),

    Case('streaming/safe-value-is-bounded',
         'schwaby/streaming.py',
         "    text = str.__str__(text)\n    # Redacted before it is cut, so a secret across the cut leaves nothing.\n    text = _redact.before_cut(text, 200)\n"
         "    return _printable(text if len(text) <= 200 else text[:197] + '...', 200)",
         "    text = str.__str__(text)\n    # Redacted before it is cut, so a secret across the cut leaves nothing.\n    text = _redact.before_cut(text, 200)\n"
         "    return _printable(text, 10 ** 6)",
         'tests/streaming_test.py::StreamClientTest'
         '::test_a_formatted_venue_value_keeps_its_content_and_its_bound',
         'not less than or equal to'),

    # `_absorb`'s line is the complete record and the callback the
    # convenience, so losing the line whole is the wrong way round.
    Case('streaming/absorbed-offender-does-not-break-the-line',
         'schwaby/streaming.py',
         "                'connection.)%s', what, _safe_value(offender), n,",
         "                'connection.)%s', what, offender, n,",
         'tests/streaming_test.py::StreamClientTest'
         '::test_an_absorbed_offender_does_not_lose_its_own_log_line',
         # The line is lost either way. Since the redaction renders a message
         # lazily, inside the handler, the formatting failure that loses it
         # is raised there, and pytest's capture handler re-raises it where
         # logging would swallow it -- so the failure names the digit limit
         # rather than the missing line.
         'Exceeds the limit'),

    # `_is_mapping` is structural on purpose -- `get` and `__contains__`,
    # nothing about iteration -- and `set(msg)` requires iteration. The
    # channel check this series added ended the receive loop on shapes
    # 4.2.0 handled, through a documented public extension point.
    Case('streaming/unenumerable-mapping-still-dispatches',
         'schwaby/streaming.py',
         "        try:\n"
         "            unknown_channels = set(msg) - _KNOWN_CHANNELS\n"
         "        except Exception:",
         "        if True:\n"
         "            unknown_channels = set(msg) - _KNOWN_CHANNELS\n"
         "        elif False:",
         'tests/streaming_test.py::StreamClientTest'
         '::test_a_mapping_that_cannot_be_enumerated_still_dispatches',
         'not iterable'),

    # The three formats above the one that was swept. A case per site,
    # because reverting the shared helper reds off whichever site a test
    # happens to drive.
    Case('streaming/validate-response-service-is-bounded',
         'schwaby/streaming.py',
         "                resp, 'unexpected service: {}'.format(\n"
         "                    _safe_value(resp_service)))",
         "                resp, 'unexpected service: {}'.format(resp_service))",
         'tests/streaming_test.py::StreamClientTest'
         '::test_every_validate_response_message_is_bounded_and_quoted',
         'not less than 400'),

    Case('streaming/validate-response-command-is-bounded',
         'schwaby/streaming.py',
         "                resp, 'unexpected command: {}'.format(\n"
         "                    _safe_value(resp_command)))",
         "                resp, 'unexpected command: {}'.format(resp_command))",
         'tests/streaming_test.py::StreamClientTest'
         '::test_every_validate_response_message_is_bounded_and_quoted',
         'not less than 400'),

    Case('streaming/validate-response-requestid-is-bounded',
         'schwaby/streaming.py',
         "                resp, 'unexpected requestid: {}'.format(\n"
         "                    _safe_value(mismatched_id)))",
         "                resp, 'unexpected requestid: {}'.format(mismatched_id))",
         'tests/streaming_test.py::StreamClientTest'
         '::test_every_validate_response_message_is_bounded_and_quoted',
         'Exceeds the limit'),

    # `_safe_value` reprs, which is wrong beside an explicit type name.
    Case('streaming/absorbed-cause-names-its-type-once',
         'schwaby/streaming.py',
         "                else ' Cause: {}: {}'.format(\n"
         "                    _type_name(cause), _safe_str(cause)))",
         "                else ' Cause: {}: {}'.format(\n"
         "                    type(cause).__name__, _safe_value(cause)))",
         'tests/streaming_test.py::StreamClientTest'
         '::test_an_absorbed_cause_does_not_name_its_type_twice',
         'not less than or equal to'),

    # A caller catches the HTTP error by this name, so it must BE the class a
    # real call raises. `httpx2` shares no exception hierarchy with `httpx`, so
    # an alias pointing anywhere else leaves a handler that never matches and
    # says nothing. Mutated to `Exception`, which still catches everything --
    # so the end-to-end catch stays green and only identity can see it. A
    # mutation that broke the import would fail every test in the file, which
    # reads as a red and proves nothing.
    Case('utils/http-status-error-is-the-real-class',
         'schwaby/utils.py',
         "from httpx2 import HTTPStatusError\n",
         "HTTPStatusError = Exception\n",
         'tests/utils_test.py::HTTPStatusErrorTest'
         '::test_it_is_the_class_the_http_package_defines',
         'is not'),

    # `Status` is a plain Enum, so `Status.FILLED == 'FILLED'` is False. A set
    # of members would call the raw string a REST response carries in
    # `order['status']` not terminal -- silently, on a fill path. Mutated to
    # hold members, which is the obvious way to write it.
    Case('client/terminal-statuses-hold-values',
         'schwaby/client/base.py',
         "        TERMINAL_STATUSES = frozenset(status.value for status in (",
         "        TERMINAL_STATUSES = frozenset(status for status in (",
         'tests/client_test.py::OrderTerminalStatusesTest'
         '::test_a_raw_status_string_tests_directly',
         'not found in'),

    # A set that contained everything would pass the membership test above,
    # so it has to be shown to discriminate. Widened with a working status.
    Case('client/terminal-statuses-discriminate',
         'schwaby/client/base.py',
         "            Status.EXPIRED, Status.REPLACED))",
         "            Status.EXPIRED, Status.REPLACED, Status.WORKING))",
         'tests/client_test.py::OrderTerminalStatusesTest'
         '::test_a_working_order_is_not_terminal',
         'unexpectedly found in'),

    # An ACCT_ACTIVITY type nobody has captured is still delivered; what is new
    # is that it no longer passes silently. The override must call the report.
    Case('streaming/unobserved-message-type-is-reported',
         'schwaby/streaming.py',
         "                    _report_unknown_message_type(name)",
         "                    pass",
         'tests/streaming_test.py::AccountActivityMessageTypeTest'
         '::test_an_unobserved_type_is_still_delivered',
         'no logs of level WARNING or higher triggered'),

    # The docs say to match case-insensitively, so an upper-cased observed type
    # must not be reported as new. Mutated to an exact comparison against the
    # CamelCase set, which is the obvious way to write it.
    Case('streaming/message-type-match-is-case-insensitive',
         'schwaby/streaming.py',
         '                if not notice and name.casefold() not in known:',
         '                if not notice and name not in StreamClient.ACCOUNT_ACTIVITY_MESSAGE_TYPES:',
         'tests/streaming_test.py::AccountActivityMessageTypeTest::test_a_case_variant_of_an_observed_type_is_not_new',
         'Unexpected logs found'),

    # Once per type, not per message. A line per message would bury the one
    # that names the missing type.
    Case('streaming/unobserved-message-type-reported-once',
         'schwaby/streaming.py',
         "    if (folded in _reported_message_types",
         "    if (False",
         'tests/streaming_test.py::AccountActivityMessageTypeTest'
         '::test_it_is_reported_once_per_type_not_per_message',
         '1 != 4'),

    # The set never shrinks and what goes into it is the venue's to choose.
    Case('streaming/unobserved-message-types-are-bounded',
         'schwaby/streaming.py',
         "            or len(_reported_message_types) >= _MAX_REPORTED_MESSAGE_TYPES):",
         "            ):",
         'tests/streaming_test.py::AccountActivityMessageTypeTest'
         '::test_the_report_is_bounded',
         '64 != 192'),

    # The call's job is to hand the override a plain, bounded str that could
    # not raise getting there. The length half is now also enforced where the
    # name is kept -- the reporter bounds it after folding -- so mutating this
    # to `str(token)` left the length test green. What only this call covers is
    # the rest: a str subclass whose `__str__` returns itself reached the
    # override's `casefold`, which raised and dropped the element. Since
    # 4.7.2 the empty-type check reads it first, so its own `__len__` of 0
    # makes a real type look empty and go unreported instead: the same
    # override reached, one line earlier.
    Case('streaming/unobserved-message-type-name-is-bounded',
         'schwaby/streaming.py',
         "                name = _safe_name(token)",
         "                name = str(token)",
         'tests/streaming_test.py::AccountActivityMessageTypeTest'
         '::test_a_str_subclass_type_is_still_delivered',
         'no logs of level WARNING'),

    # `%r`, so a newline in a type cannot forge a second log line that reads as
    # a real WARNING from this logger.
    Case('streaming/unobserved-message-type-is-quoted',
         'schwaby/streaming.py',
         "            'ACCT_ACTIVITY carried message type %r, which is not among the '",
         "            'ACCT_ACTIVITY carried message type %s, which is not among the '",
         'tests/streaming_test.py::AccountActivityMessageTypeTest'
         '::test_a_newline_in_a_type_cannot_forge_a_log_line',
         "'\\nWARNING' unexpectedly found"),

    # Exactly the captured set. A token copied from a parser's fallback list
    # rather than a frame is the defect the observation log was just
    # corrected for, so the set is pinned. Mutated to the upper-case spelling
    # the earlier note used, which is exactly what the capture corrected.
    Case('streaming/message-type-vocabulary-is-the-captured-set',
         'schwaby/streaming.py',
         "        'OrderMonitorCreated', 'OrderMonitorCompleted',",
         "        'OrderMonitorCreated', 'ORDERMONITORCOMPLETED',",
         'tests/streaming_test.py::AccountActivityMessageTypeTest'
         '::test_it_is_exactly_the_observed_types',
         'Items in the first set but not the second'),

    # Reachable with ordinary JSON: a newline in a `service` on the
    # orphan-response path forged a second line reading as a real WARNING
    # from this logger. The `%s` sweep had stopped at the shape path.
    Case('streaming/response-log-cannot-be-forged',
         'schwaby/streaming.py',
         "                    _safe_value(response.get('service')),\n"
         "                    _safe_value(response.get('command')),\n"
         "                    _safe_value(code), _safe_value(content.get('msg')))",
         "                    response.get('service'),\n"
         "                    response.get('command'),\n"
         "                    code, content.get('msg'))",
         'tests/streaming_test.py::StreamClientTest'
         '::test_venue_text_cannot_forge_a_response_log_line',
         "'\\nWARNING' unexpectedly found"),

    # A subscribe Schwab *accepted* raised a bare ValueError out of the
    # message built for the late rejection beside it.
    Case('streaming/response-value-cannot-break-the-line',
         'schwaby/streaming.py',
         "    except Exception:\n"
         "        text = '<{} that cannot be formatted>'.format(\n"
         "                _type_name(value))\n",
         "    except ZeroDivisionError:\n"
         "        text = ''\n",
         'tests/streaming_test.py::StreamClientTest'
         '::test_a_response_value_that_cannot_be_formatted_is_survivable',
         'Exceeds the limit'),

    # The end-to-end half of the service guard: the receive loop survives and
    # the good element in the same frame still reaches its handler.
    Case('streaming/unnameable-service-does-not-end-the-loop',
         'schwaby/streaming.py',
         "        seen = (what, _safe_name(name))",
         "        seen = (what, str(name))",
         'tests/streaming_test.py::StreamClientTest'
         '::test_an_unnameable_service_does_not_end_the_receive_loop',
         'no name for you'),

    # `%r`, like `_absorb` ten lines away. With `%s` a newline in a
    # venue-controlled name forged a second log line indistinguishable from
    # a real WARNING from this logger.
    Case('streaming/venue-name-reaches-the-log-quoted',
         'schwaby/streaming.py',
         "                'Schwab sent a %s this version of schwaby does not "
         "know: %r. '",
         "                'Schwab sent a %s this version of schwaby does not "
         "know: %s. '",
         'tests/streaming_test.py::StreamClientTest'
         '::test_a_venue_name_reaches_the_log_quoted',
         "'\\nWARNING' unexpectedly found"),

    # The service path called bare `str()` while the channel path went
    # through `_safe_name`, three lines apart. A decoder producing such a
    # service escaped `handle_message` entirely.
    Case('streaming/unnameable-service-does-not-escape',
         'schwaby/streaming.py',
         "        seen = (what, _safe_name(name))",
         "        seen = (what, str(name)[:64])",
         'tests/streaming_test.py::StreamClientTest'
         '::test_a_service_name_that_cannot_be_named_does_not_escape',
         'no name for you'),

    # Going quiet at a cap is indistinguishable from nothing new arriving.
    Case('streaming/field-cap-says-so',
         'schwaby/streaming.py',
         "    if len(_reported_fields) >= _MAX_REPORTED_FIELDS:\n"
         "        # Said out loud, like the shapes cap.",
         "    if False:\n"
         "        # Said out loud, like the shapes cap.",
         'tests/streaming_test.py::StreamClientTest'
         '::test_the_field_cap_says_so_rather_than_going_quiet',
         'as many as schwaby will name'),

    # The guarantee must live where the callback lives: held module-wide it
    # deduped across clients, so a second client's handler heard about three
    # of five new services and never about the other two.
    Case('streaming/shape-budget-is-per-client',
         'schwaby/streaming.py',
         "        self._reported_shapes = set()",
         "        self._reported_shapes = _SHARED_SHAPES",
         'tests/streaming_test.py::StreamClientTest'
         '::test_a_second_client_hears_about_a_dropped_service_itself',
         'Items in the first set but not the second',
         also=("_MAX_REPORTED_SHAPES = 64",
               "_MAX_REPORTED_SHAPES = 64\n_SHARED_SHAPES = set()")),

    # Its own promises, all four of which were green: "reported once, not
    # per message", the cap, the truncation, and the kind discriminator.
    Case('streaming/new-shape-report-is-once-per-name',
         'schwaby/streaming.py',
         "        if seen in self._reported_shapes:\n"
         "            return False",
         "        if False:\n"
         "            return False",
         'tests/streaming_test.py::StreamClientTest'
         '::test_the_new_shape_report_keeps_its_own_promises',
         'True is not false'),

    Case('streaming/new-shape-report-is-bounded',
         'schwaby/streaming.py',
         "        if len(self._reported_shapes) >= _MAX_REPORTED_SHAPES:\n"
         "            return False",
         "        if False:\n"
         "            return False",
         'tests/streaming_test.py::StreamClientTest'
         '::test_the_new_shape_report_keeps_its_own_promises',
         '64 != 192'),

    Case('streaming/new-shape-name-is-bounded',
         'schwaby/streaming.py',
         "        seen = (what, _safe_name(name))",
         "        seen = (what, name)",
         'tests/streaming_test.py::StreamClientTest'
         '::test_the_new_shape_report_keeps_its_own_promises',
         'not less than or equal to'),

    # Fields and shapes must not share a budget: 254 declared ids against 64
    # slots meant the lesser event could consume the worse one's.
    Case('streaming/fields-and-shapes-have-separate-budgets',
         'schwaby/streaming.py',
         "        seen = (what, _safe_name(name))",
         "        seen = (what, _safe_name(name))\n"
         "        self._reported_shapes.update(_reported_fields)",
         'tests/streaming_test.py::StreamClientTest'
         '::test_fields_and_shapes_do_not_share_a_budget',
         'False is not true'),

    # The callback half. `_absorb` coalesces per kind, so without a forced
    # first sighting a fourth new service reaches no handler at all.
    Case('streaming/first-sighting-forces-the-callback',
         'schwaby/streaming.py',
         "        if not force and n > 3 and not _is_power_of_ten(n):",
         "        if n > 3 and not _is_power_of_ten(n):",
         'tests/streaming_test.py::StreamClientTest'
         '::test_every_unknown_service_is_named_at_least_once',
         "'SVC_B'"),

    # A notify frame is not required to name a service, and that is
    # documented rather than unexpected -- so the `is not None` half is
    # load-bearing and is the half a reader would drop.
    Case('streaming/a-nameless-notify-is-not-an-unknown-service',
         'schwaby/streaming.py',
         "        if service is not None and service not in _KNOWN_SERVICES:",
         "        if service not in _KNOWN_SERVICES:",
         'tests/streaming_test.py::StreamClientTest'
         '::test_a_notify_frame_naming_no_service_stays_quiet',
         '0 != 1'),

    # A key is not guaranteed to be a string: StreamJsonDecoder is a public
    # extension point and a decoder normalising field ids to ints is a
    # plausible thing to write. Keyed on the raw object such a message went
    # unrelabeled, and bare `.isdigit()` on it raised AttributeError, which
    # the relabel guard absorbed -- dropping a message 4.2.0 delivered.
    Case('streaming/a-non-string-field-key-is-not-dropped',
         'schwaby/streaming.py',
         "                text_key = str.__str__(str(old_key))",
         "                text_key = old_key",
         'tests/streaming_test.py::StreamClientTest'
         '::test_a_non_string_field_key_is_relabeled_not_dropped',
         'AttributeError'),

    # The other direction, and the one that matters operationally: a key
    # Schwab *adds* must not take the feed down. It appears on every decimal
    # object at once, so refusing it makes every money and quantity field
    # raise together.
    Case('contrib/decimal-added-key-is-ignored-not-refused',
         'schwaby/contrib/util.py',
         "        # Ignored, and said out loud once",
         "        pass  # gone",
         'tests/contrib/util_test.py::DecodeDecimalTest'
         '::test_any_unrecognised_key_refuses_the_object',
         'UnusableDecimalScale',
         removed="The version this mutates is gone. It tried to decode around an unrecognised key -- ignoring one on an object that looked complete, refusing one where a component was missing, on the reasoning that the unknown key might be that component renamed. Three review rounds each found a spelling of 'missing' it did not cover: the key absent, the key present and null, the key present and zero. Each was a $6.86 limit price decoding as $6,860,000. The third cannot be closed at all -- `signScale: 0` genuinely means scale 0. Measured against a real capture it also bought nothing: under a rename that keeps the old key at its default, 27 of 37 objects still decoded wrong, which is what no guard does. Any unrecognised key refuses the object now; `contrib/decimal-unknown-key-refuses` is the case. Kept because decoding around an unknown key is the obvious thing to reach for and was reached for three times."),


    # Ignored is not the same as unnoticed. Without the report a schema
    # change is invisible, which is the half of this that an operator needs.
    Case('contrib/decimal-added-key-is-reported',
         'schwaby/contrib/util.py',
         "    _reported_keys.update(fresh)\n"
         "    if len(_reported_keys)",
         "    _reported_keys.update(fresh)\n"
         "    return\n"
         "    if len(_reported_keys)",
         'tests/contrib/util_test.py::DecodeDecimalTest'
         '::test_an_added_key_is_still_reported_once_per_key',
         'no logs of level WARNING or higher triggered'),

    # Once per distinct key, not once per message. A per-message line on a
    # live feed is a log flood, which is its own way of hiding the message.
    Case('contrib/decimal-added-key-is-reported-once',
         'schwaby/contrib/util.py',
         "    fresh = sorted({_safe_key(k) for k in unknown} - _reported_keys)[:room]",
         "    fresh = sorted({_safe_key(k) for k in unknown})[:room]",
         'tests/contrib/util_test.py::DecodeDecimalTest'
         '::test_an_added_key_is_still_reported_once_per_key',
         '1 != 3'),

    # The set never shrinks and what goes into it is corruption-controlled.
    Case('contrib/decimal-reported-keys-are-bounded',
         'schwaby/contrib/util.py',
         "    room = _MAX_REPORTED_KEYS - len(_reported_keys)",
         "    room = _MAX_REPORTED_KEYS",
         'tests/contrib/util_test.py::DecodeDecimalTest'
         '::test_the_unknown_key_report_is_bounded',
         'not less than or equal to'),

    # The scale is bounded explicitly rather than left to the arithmetic:
    # scaleb's operand limit only trips far out, so every scale from roughly
    # two million to four million underflowed to a zero that compares equal to
    # zero -- the worst wrong answer available on a fill feed.
    Case('contrib/decimal-scale-is-bounded',
         'schwaby/contrib/util.py',
         "    if not 0 <= scale <= MAX_SIGN_SCALE:",
         "    if False:",
         'tests/contrib/util_test.py::DecodeDecimalTest'
         '::test_an_implausible_scale_raises_rather_than_underflowing_to_zero',
         'UnusableDecimalScale not raised'),

    # `scaleb` applies the caller's decimal context, so a caller setting
    # prec=6 to format money would get 1234.57 for a price of 1234.5678.
    Case('contrib/decimal-is-context-free',
         'schwaby/contrib/util.py',
         "    result = decimal.Decimal('{}E-{}'.format(mantissa, scale // 2))",
         "    result = decimal.Decimal(mantissa).scaleb(-(scale // 2))",
         'tests/contrib/util_test.py::DecodeDecimalTest'
         '::test_the_callers_decimal_context_cannot_change_the_answer',
         '1234.5678',
         removed='The construction this mutates lasted one commit. It built '
                 'the value from a string and then negated it, which is a '
                 'context-aware operation -- exact for positive values and '
                 'rounding for negative ones. The sign is built into the '
                 'string now, and `contrib/decimal-sign-is-context-free-too` '
                 'replaces this case by mutating the current line and '
                 'covering both branches. Kept because a string construction '
                 'followed by a separate negation is the obvious thing to '
                 'reach for again.'),

    # The mantissa-less guard keyed on `lo` alone, so a value carried only in
    # `mid` decoded as zero -- the slice-reading defect the function exists to
    # fix, in the guard immediately before it.
    Case('contrib/decimal-guard-reads-all-three-fields',
         'schwaby/contrib/util.py',
         "    if all(fields.get(k) is None for k in ('lo', 'mid', 'hi')):",
         "    if fields.get('lo') is None:",
         'tests/contrib/util_test.py::DecodeDecimalTest'
         '::test_a_mantissa_carried_only_in_mid_is_not_read_as_zero',
         '4294.967296'),

    # Every *operation* on a Decimal applies the caller's context; only
    # construction does not. Building the value from a string and then negating
    # it was exact for positive values and rounded negative ones -- the branch
    # EstimatedPrincipalAmount arrives on.
    Case('contrib/decimal-sign-is-context-free-too',
         'schwaby/contrib/util.py',
         "    return decimal.Decimal('{}{}E-{}'.format(\n"
         "            '-' if scale % 2 else '', mantissa, scale // 2))",
         "    result = decimal.Decimal('{}E-{}'.format(mantissa, scale // 2))\n"
         "    return -result if scale % 2 else result",
         'tests/contrib/util_test.py::DecodeDecimalTest'
         '::test_the_callers_decimal_context_cannot_change_the_answer',
         '-1234.5678'),

    # int() raises a bare ValueError on a non-numeric string and a TypeError on
    # a non-scalar -- neither a SchwabError, and the TypeError not even a
    # ValueError, so both escape the handlers the docstring names.
    Case('contrib/decimal-corrupt-member-is-catchable',
         'schwaby/contrib/util.py',
         "    except (TypeError, ValueError):",
         "    except (TypeError,):",
         'tests/contrib/util_test.py::DecodeDecimalTest'
         '::test_every_failure_is_catchable_by_one_except',
         'ValueError',
         removed='The block this mutates is gone. Enumerating which exceptions '
                 '`int()` can raise was wrong three rounds running -- each fix '
                 'added a type and the next round found a shape that raised a '
                 'different one, or worse, none at all: `int(1.9)` is 1 and '
                 "`int('1_0')` is 10. `_unsigned_32` validates the shape "
                 'instead, and the three cases below cover its branches. Kept '
                 'because coerce-and-catch is the obvious thing to write here '
                 'and was written three times.'),

    # `_unsigned`, a case per branch. A single case on the raise would prove
    # only that *some* branch reaches it.
    #
    # Branch one: what the member is. A bool passes `isinstance(x, int)`, and
    # `int(True)` is 1 -- so a JSON `true` in a mantissa decoded as a number
    # rather than as the corruption it is.
    Case('contrib/decimal-member-type-is-checked',
         'schwaby/contrib/util.py',
         "    if issubclass(kind, bool) or not issubclass(kind, (int, str)):",
         "    if not issubclass(kind, (int, str, bool, float, list, dict)):",
         'tests/contrib/util_test.py::DecodeDecimalTest'
         '::test_a_corrupt_member_is_refused_rather_than_coerced',
         'UnusableDecimalScale not raised'),

    # Branch two: what a *string* member is. `isdigit` is not ASCII --
    # '\u00b2'.isdigit() is True and `int` refuses it, while '\u0663'.isdigit()
    # is True and `int` decodes it as 3. Underscores are the same shape:
    # `int('1_0')` is 10, so PEP 515 turns a corrupt member into a number ten
    # times too large.
    Case('contrib/decimal-member-string-is-ascii-digits',
         'schwaby/contrib/util.py',
         "        ok = member.isascii() and member.isdigit()",
         "        ok = True",
         'tests/contrib/util_test.py::DecodeDecimalTest'
         '::test_a_corrupt_member_is_refused_rather_than_coerced',
         'UnusableDecimalScale not raised'),

    # Branch three: the range. A member past its width is not a member: a
    # `mid` or `hi` wider than 32 bits overlaps the next one, and a lone `lo`
    # past 96 bits is past what a System.Decimal can hold. The width is the
    # caller's; the check is here. (This comment once gave `{"lo": 4294967296}`
    # as the example of corruption. It is an ordinary fill principal.)
    Case('contrib/decimal-member-is-unsigned-32-bit',
         'schwaby/contrib/util.py',
         "    if not ok or not 0 <= member < 2 ** bits:",
         "    if not ok:",
         'tests/contrib/util_test.py::DecodeDecimalTest'
         '::test_a_corrupt_member_is_refused_rather_than_coerced',
         'UnusableDecimalScale not raised'),

    # The layout Schwab sends: `lo` alone, carrying the whole mantissa. Held to
    # 32 bits it refused 129 of 5,843 recorded objects, 33 of them principals
    # that equalled price times quantity once read whole.
    Case('contrib/decimal-lone-lo-is-the-whole-mantissa',
         'schwaby/contrib/util.py',
         "        mantissa = 0 if lo is None else _unsigned(lo, 'lo', value, 96)",
         "        mantissa = 0 if lo is None else _unsigned(lo, 'lo', value, 32)",
         'tests/contrib/util_test.py::DecodeDecimalTest'
         '::test_lo_carries_the_whole_mantissa_past_32_bits',
         'unsigned 32-bit integer'),

    # And bounded by what a System.Decimal can hold, rather than not at all.
    Case('contrib/decimal-lone-lo-is-bounded-at-96-bits',
         'schwaby/contrib/util.py',
         "        mantissa = 0 if lo is None else _unsigned(lo, 'lo', value, 96)",
         "        mantissa = 0 if lo is None else _unsigned(lo, 'lo', value, 128)",
         'tests/contrib/util_test.py::DecodeDecimalTest'
         '::test_a_lone_lo_is_bounded_by_the_dotnet_mantissa',
         'UnusableDecimalScale not raised'),

    # Beside `mid` or `hi`, each member is still 32 bits. A wider `lo` there is
    # two layouts at once, and reading it whole would overlap `mid`.
    Case('contrib/decimal-members-beside-mid-stay-32-bit',
         'schwaby/contrib/util.py',
         "            mantissa += _unsigned(member, name, value, 32) << shift",
         "            mantissa += _unsigned(member, name, value, 96) << shift",
         'tests/contrib/util_test.py::DecodeDecimalTest'
         '::test_beside_mid_or_hi_each_member_is_still_32_bits',
         'UnusableDecimalScale not raised'),

    # The layout is chosen on `mid` *and* `hi`. Keyed on `mid` alone,
    # `{"lo": ..., "hi": 1}` reads `lo` whole and drops `hi` without a word.
    Case('contrib/decimal-layout-is-chosen-on-mid-and-hi',
         'schwaby/contrib/util.py',
         "    if fields.get('mid') is None and fields.get('hi') is None:",
         "    if fields.get('mid') is None:",
         'tests/contrib/util_test.py::DecodeDecimalTest'
         '::test_beside_mid_or_hi_each_member_is_still_32_bits',
         'UnusableDecimalScale not raised'),

    # A plain int, not the subclass that arrived. The lone-lo path formats the
    # member straight into the result, so a subclass's own `format` decided
    # the number -- or raised past the one `except` callers write.
    Case('contrib/decimal-member-is-a-plain-int',
         'schwaby/contrib/util.py',
         "        member = int.__index__(member)",
         "        pass",
         'tests/contrib/util_test.py::DecodeDecimalTest'
         '::test_an_int_subclass_decodes_as_its_value',
         '0.000999'),

    # Widest member first, so a corrupt `mid` or `hi` is named as itself
    # rather than blamed on a ten-digit `lo` that is ordinary alone.
    Case('contrib/decimal-corrupt-member-is-named-as-itself',
         'schwaby/contrib/util.py',
         "        for name, shift in (('hi', 64), ('mid', 32), ('lo', 0)):",
         "        for name, shift in (('lo', 0), ('mid', 32), ('hi', 64)):",
         'tests/contrib/util_test.py::DecodeDecimalTest'
         '::test_beside_mid_or_hi_each_member_is_still_32_bits',
         'does not match'),

    # An exponent of 10**18 or more makes `decimal.Decimal` raise
    # InvalidOperation, which is neither a SchwabError nor a ValueError, before
    # the magnitude check can run. Bounded in the pattern instead.
    Case('contrib/decimal-bare-exponent-is-bounded-in-the-pattern',
         'schwaby/contrib/util.py',
         "        r'-?(?:[0-9]+(?:\\.[0-9]*)?|\\.[0-9]+)(?:[eE][-+]?0*[0-9]{1,4})?\\Z')",
         "        r'-?(?:[0-9]+(?:\\.[0-9]*)?|\\.[0-9]+)(?:[eE][-+]?[0-9]+)?\\Z')",
         'tests/contrib/util_test.py::DecodeDecimalTest::test_a_huge_bare_exponent_is_refused_not_escaped',
         'InvalidOperation'),

    # Leading zeros in the exponent are stripped before the four-digit bound, so
    # a padded exponent inside it still decodes.
    Case('contrib/decimal-bare-exponent-padding-is-stripped',
         'schwaby/contrib/util.py',
         "        r'-?(?:[0-9]+(?:\\.[0-9]*)?|\\.[0-9]+)(?:[eE][-+]?0*[0-9]{1,4})?\\Z')",
         "        r'-?(?:[0-9]+(?:\\.[0-9]*)?|\\.[0-9]+)(?:[eE][-+]?[0-9]{1,4})?\\Z')",
         'tests/contrib/util_test.py::DecodeDecimalTest::test_a_huge_bare_exponent_is_refused_not_escaped',
         'not a number'),

    # Key names are quoted in the refusal. Joined bare, a newline in one forged
    # a second line wherever the exception was logged.
    Case('contrib/decimal-refusal-quotes-key-names',
         'schwaby/contrib/util.py',
         '    quoted = repr(name)',
         '    quoted = name',
         'tests/contrib/util_test.py::DecodeDecimalTest::test_a_refusal_cannot_forge_a_log_line',
         'unexpectedly found'),

    # A str subclass member is reduced to plain text; its own `isdigit` and
    # `lstrip` decided the value.
    Case('contrib/decimal-member-text-is-plain-str',
         'schwaby/contrib/util.py',
         '        member = str.__str__(member)',
         '        pass',
         'tests/contrib/util_test.py::DecodeDecimalTest::test_subclasses_decode_as_their_plain_values',
         '0.000999'),

    # The mapping is read through its own `items()`. Through `dict.items`, a
    # dict subclass keeping its data elsewhere, as DotMap does, looked empty and
    # decoded a $5,000 principal as a clean zero.
    Case('contrib/decimal-mapping-read-through-its-items',
         'schwaby/contrib/util.py',
         '        pairs = [(key, member) for key, member in value.items()]',
         '        pairs = [(key, member) for key, member in dict.items(value)]',
         'tests/contrib/util_test.py::DecodeDecimalTest::test_a_mapping_that_keeps_its_data_elsewhere_decodes',
         '-5000'),

    # Only a plain str key can match a known name. Anything else is named but
    # never hashed or compared, so a key whose `__eq__` raises cannot escape.
    Case('contrib/decimal-only-plain-string-keys-match',
         'schwaby/contrib/util.py',
         '        name = str.__str__(key) if issubclass(type(key), str) else None',
         '        name = key',
         'tests/contrib/util_test.py::DecodeDecimalTest::test_a_mapping_is_read_once_from_what_it_stores',
         'boom'),

    # A bare Decimal subclass is copied to an exact Decimal; its own `is_finite`
    # raised past the one `except`.
    Case('contrib/decimal-bare-decimal-is-copied',
         'schwaby/contrib/util.py',
         '        value = result = decimal.Decimal(value)',
         '        result = value',
         'tests/contrib/util_test.py::DecodeDecimalTest::test_subclasses_decode_as_their_plain_values',
         'boom'),

    # A bare int subclass is reduced with `int.__index__`; its own `bit_length`
    # raised.
    Case('contrib/decimal-bare-int-is-plain',
         'schwaby/contrib/util.py',
         '        value = int.__index__(value)',
         '        pass',
         'tests/contrib/util_test.py::DecodeDecimalTest::test_subclasses_decode_as_their_plain_values',
         'boom'),

    # A bare float subclass is reduced with `float.__float__`; its own `__str__`
    # decided the number.
    Case('contrib/decimal-bare-float-is-plain',
         'schwaby/contrib/util.py',
         '        value = float.__float__(value)',
         '        pass',
         'tests/contrib/util_test.py::DecodeDecimalTest::test_subclasses_decode_as_their_plain_values',
         '999'),

    # A key's name is reduced to a plain str, so a subclass returned by
    # `__str__` cannot run its own `__len__` or `__hash__` inside the code that
    # names the problem.
    Case('contrib/decimal-key-name-is-plain-str',
         'schwaby/contrib/util.py',
         "    text = _redact.before_cut(str.__str__(text), 64)\n    return text if len(text) <= 64 else text[:61] + '...'",
         "    text = _redact.before_cut(text, 64)\n    return text if len(text) <= 64 else text[:61] + '...'",
         'tests/contrib/util_test.py::DecodeDecimalTest::test_subclasses_decode_as_their_plain_values',
         'boom',
         superseded='before_cut, which every bounded value now passes '
                    'through, makes the text a plain str itself'),

    # The same for a value's repr in a refusal message.
    Case('contrib/decimal-repr-is-plain-str',
         'schwaby/contrib/util.py',
         '        text = str.__str__(repr(value))',
         '        text = repr(value)',
         'tests/contrib/util_test.py::DecodeDecimalTest::test_subclasses_decode_as_their_plain_values',
         'boom',
         superseded='before_cut, which every bounded value now passes '
                    'through, makes the text a plain str itself'),

    # A MESSAGE_TYPE str subclass whose `__str__` returns itself kept its own
    # `casefold`, which raised and absorbed the whole element, valid items with
    # it.
    Case('streaming/safe-name-is-plain-str',
         'schwaby/streaming.py',
         "    text = _redact.before_cut(str.__str__(text), 64)\n    return text if len(text) <= 64 else text[:61] + '...'",
         "    text = _redact.before_cut(text, 64)\n    return text if len(text) <= 64 else text[:61] + '...'",
         'tests/streaming_test.py::AccountActivityMessageTypeTest::test_a_str_subclass_type_is_still_delivered',
         'no logs of level WARNING',
         superseded='before_cut, which every bounded value now passes '
                    'through, makes the text a plain str itself'),

    # Folded without cutting again. A second cut after folding made two
    # different types, 'ß'*33 + 'A' and 'ß'*33 + 'B', share one report; the name
    # is already bounded and folding at most triples one.
    Case('streaming/reported-type-folded-without-cutting-again',
         'schwaby/streaming.py',
         '    folded = name.casefold()',
         '    folded = _safe_name(name.casefold())',
         'tests/streaming_test.py::AccountActivityMessageTypeTest::test_folding_keeps_distinct_types_distinct_and_bounded',
         '2 != 1'),

    # Any Mapping is a decimal object, not only a dict: a UserDict or a
    # mappingproxy was refused as 'not a number'.
    Case('contrib/decimal-any-mapping-is-a-decimal-object',
         'schwaby/contrib/util.py',
         '        is_mapping = issubclass(type(value), collections.abc.Mapping)',
         '        is_mapping = issubclass(type(value), dict)',
         'tests/contrib/util_test.py::DecodeDecimalTest::test_a_mapping_that_keeps_its_data_elsewhere_decodes',
         'not a number'),

    # A mapping whose items() cannot be read is refused, not escaped and not
    # bypassed.
    Case('contrib/decimal-unreadable-mapping-is-refused',
         'schwaby/contrib/util.py',
         "    except Exception:\n        raise UnusableDecimalScale(\n                'a mapping whose items could not be read: {}'.format(",
         "    except KeyError:\n        raise UnusableDecimalScale(\n                'a mapping whose items could not be read: {}'.format(",
         'tests/contrib/util_test.py::DecodeDecimalTest::test_a_mapping_is_read_once_from_what_it_stores',
         'boom'),

    # Two keys with the same plain text are refused: only one of them can be the
    # member, and which one a dict keeps is not the decoder's to guess.
    Case('contrib/decimal-duplicate-key-text-is-refused',
         'schwaby/contrib/util.py',
         '        if name is not None and name in fields:',
         '        if False:',
         'tests/contrib/util_test.py::DecodeDecimalTest::test_a_str_subclass_key_is_its_plain_text',
         'UnusableDecimalScale not raised'),

    # A bare value is routed on its real type. `isinstance` believes
    # `__class__`, so a claimed int reached int's own conversion and escaped as
    # TypeError.
    Case('contrib/decimal-bare-value-routed-on-real-type',
         'schwaby/contrib/util.py',
         '    kind = type(value)   # not `isinstance`; see `decode_decimal`',
         '    kind = value.__class__',
         'tests/contrib/util_test.py::DecodeDecimalTest::test_a_claimed_class_is_not_believed',
         'TypeError'),

    # A member is routed on its real type, for the same reason: a claimed str
    # reached str's own conversion and escaped as TypeError.
    Case('contrib/decimal-member-routed-on-real-type',
         'schwaby/contrib/util.py',
         '    kind = type(member)   # not `isinstance`; see `decode_decimal`',
         '    kind = member.__class__',
         'tests/contrib/util_test.py::DecodeDecimalTest::test_a_claimed_class_is_not_believed',
         'TypeError'),

    # A bare str subclass is reduced to plain text; its own `isascii` ran before
    # the pattern and raised.
    Case('contrib/decimal-bare-text-is-plain-str',
         'schwaby/contrib/util.py',
         '        value = str.__str__(value)',
         '        pass',
         'tests/contrib/util_test.py::DecodeDecimalTest::test_subclasses_decode_as_their_plain_values',
         'boom'),

    # The type name in a refusal is read through type's own descriptor, inside a
    # guard: a metaclass whose __name__ raises escaped from the branch written
    # to be safe.
    Case('contrib/decimal-type-name-cannot-raise',
         'schwaby/contrib/util.py',
         "        name = str.__str__(type.__dict__['__name__'].__get__(type(value)))\n    except Exception:",
         '        name = type(value).__name__\n    except KeyError:',
         'tests/contrib/util_test.py::DecodeDecimalTest::test_an_unnameable_type_does_not_escape_the_message',
         'boom'),

    # Each quoted key name is bounded: repr escapes a character to as many as
    # ten, so eight names grew the refusal to several kilobytes.
    Case('contrib/decimal-quoted-key-names-are-bounded',
         'schwaby/contrib/util.py',
         "    return quoted if len(quoted) <= 80 else quoted[:77] + '...'",
         '    return quoted',
         'tests/contrib/util_test.py::DecodeDecimalTest::test_quoted_key_names_stay_bounded',
         'not less than 1500'),

    # _safe_value returns a plain str. A decoder's str subclass whose __repr__
    # returns itself kept its own __len__, which ended the receive loop when it
    # raised.
    Case('streaming/safe-value-is-plain-str',
         'schwaby/streaming.py',
         "    text = str.__str__(text)\n    # Redacted before it is cut, so a secret across the cut leaves nothing.\n    text = _redact.before_cut(text, 200)\n"
         "    return _printable(text if len(text) <= 200 else text[:197] + '...', 200)",
         "    # Redacted before it is cut, so a secret across the cut leaves nothing.\n    text = _redact.before_cut(text, 200)\n"
         "    return _printable(text if len(text) <= 200 else text[:197] + '...', 200)",
         'tests/streaming_test.py::AccountActivityMessageTypeTest::test_a_value_formatted_for_a_log_line_is_plain_and_bounded',
         'boom',
         superseded='before_cut, which every bounded value now passes '
                    'through, makes the text a plain str itself'),

    # A token response that is not a usable token is not written. authlib takes
    # any JSON object without an `error` key as the new token; {"message":
    # "Unauthorized"} replaced the stored token and every call after it failed
    # locally until a new login.
    Case('auth/unusable-token-response-is-not-stored',
         'schwaby/auth.py',
         '        if not _is_usable_token(t):',
         '        if False:',
         'tests/utils_test.py::HTTPStatusErrorTest::test_a_refresh_response_that_is_not_a_token_is_not_stored',
         'Lists differ',
         removed='The callback this mutates is gone. It checked the token authlib handed to update_token and restored the last good one, which could not work for every body: authlib keeps JSON that is not an object on the session and fails before update_token runs, and a client from a login built its own session without the callback. The check now runs as a refresh_token_response compliance hook, before authlib parses the response, on every session; auth/unusable-refresh-response-is-refused-before-parsing replaces this case.'),

    # And it is not kept in memory: the session goes back to the last good
    # token, which has expired, so the next call refreshes again instead of
    # failing locally.
    Case('auth/unusable-token-response-restores-the-last-token',
         'schwaby/auth.py',
         '            session.token = metadata.token',
         '            pass',
         'tests/utils_test.py::HTTPStatusErrorTest::test_a_refresh_response_that_is_not_a_token_is_not_stored',
         'TokenRefreshError',
         removed='The callback this mutates is gone. It checked the token authlib handed to update_token and restored the last good one, which could not work for every body: authlib keeps JSON that is not an object on the session and fails before update_token runs, and a client from a login built its own session without the callback. The check now runs as a refresh_token_response compliance hook, before authlib parses the response, on every session; auth/unusable-refresh-response-is-refused-before-parsing replaces this case.'),

    # A usable token needs an access token.
    Case('auth/usable-token-needs-an-access-token',
         'schwaby/auth.py',
         "        if not (isinstance(access_token, str) and access_token\n",
         "        if not (isinstance(access_token, str)\n",
         'tests/auth_test.py::UsableTokenTest::test_a_token_needs_an_access_token_and_its_type',
         'is not false'),

    # And the token's type, which the session needs to use the access token at
    # all.
    Case('auth/usable-token-needs-a-token-type',
         'schwaby/auth.py',
         "                and token_type.lower() == 'bearer'):",
         '                and True):',
         'tests/auth_test.py::UsableTokenTest::test_a_token_needs_an_access_token_and_its_type',
         'is not false'),

    # The Mapping check hashes the class, and a metaclass can make a class
    # unhashable; guarded so that is refused rather than escaping as TypeError.
    Case('contrib/decimal-unhashable-class-is-refused',
         'schwaby/contrib/util.py',
         "    except Exception:\n        # The ABC's cache hashes the class",
         "    except KeyError:\n        # The ABC's cache hashes the class",
         'tests/contrib/util_test.py::DecodeDecimalTest::test_an_unhashable_class_is_refused_not_escaped',
         'TypeError'),

    # A type name is reduced to a plain str: a class's __name__ may be a str
    # subclass, whose own __len__ ran outside the guard.
    Case('contrib/decimal-type-name-is-plain-str',
         'schwaby/contrib/util.py',
         "        name = str.__str__(type.__dict__['__name__'].__get__(type(value)))",
         "        name = type.__dict__['__name__'].__get__(type(value))",
         'tests/contrib/util_test.py::DecodeDecimalTest::test_an_unnameable_type_does_not_escape_the_message',
         'boom'),

    # A value's own __repr__ need not escape a newline, and every refusal ends
    # with it; control characters are escaped so it cannot forge a log line.
    Case('contrib/decimal-value-repr-cannot-forge-a-line',
         'schwaby/contrib/util.py',
         '    if not text.isprintable():',
         '    if False:',
         'tests/contrib/util_test.py::DecodeDecimalTest::test_a_values_own_repr_cannot_forge_a_log_line',
         'unexpectedly found'),

    # The once-per-key log line joins bounded quoted names too; unbounded,
    # thirty-two non-printable keys logged twenty kilobytes at once.
    Case('contrib/decimal-report-line-is-bounded',
         'schwaby/contrib/util.py',
         "            ', '.join(_quoted(k) for k in fresh))",
         "            ', '.join(repr(k) for k in fresh))",
         'tests/contrib/util_test.py::DecodeDecimalTest::test_the_once_per_key_report_line_is_bounded',
         'not less than 4000'),

    # _safe_str returns a plain str. str() of a KeyError is its key's repr, and
    # a decoder's key can hand back itself, whose own __len__ ended the receive
    # loop.
    Case('streaming/safe-str-is-plain-str',
         'schwaby/streaming.py',
         '    text = str.__str__(text)   # a plain str; `_safe_value` says why',
         '    pass',
         'tests/streaming_test.py::AccountActivityMessageTypeTest::test_a_str_formatted_for_a_log_line_is_plain_and_bounded',
         'boom',
         superseded='before_cut, which every bounded value now passes '
                    'through, makes the text a plain str itself'),

    # The streaming fallbacks read a type name through type's own descriptor;
    # `type(x).__name__` consults the metaclass first and could raise inside the
    # branch written not to.
    Case('streaming/type-name-cannot-raise',
         'schwaby/streaming.py',
         "        name = str.__str__(type.__dict__['__name__'].__get__(type(value)))\n    except Exception:",
         '        name = type(value).__name__\n    except KeyError:',
         'tests/streaming_test.py::AccountActivityMessageTypeTest::test_an_unnameable_type_does_not_escape_a_name',
         'boom'),

    # A refresh response that is not a usable token is refused before authlib
    # parses it, so nothing unusable is written or kept: an object without a
    # token was written to the token file, and a list or string was kept in
    # memory.
    Case('auth/unusable-refresh-response-is-refused-before-parsing',
         'schwaby/auth.py',
         "    if isinstance(body, dict) and ('error' in body or _is_usable_token(body)):",
         '    if True:',
         'tests/utils_test.py::HTTPStatusErrorTest::test_a_refresh_response_that_is_not_a_token_is_not_stored',
         'Lists differ'),

    # The hook only protects a session it is registered on.
    Case('auth/refresh-hook-is-registered',
         'schwaby/auth.py',
         "    session.register_compliance_hook(\n            'refresh_token_response', _refuse_unusable_token_response)",
         '    pass',
         'tests/utils_test.py::HTTPStatusErrorTest::test_a_refresh_response_that_is_not_a_token_is_not_stored',
         'Lists differ'),

    # A client from a login builds its session through the shared builder. It
    # built its own, without the check, and kept storing whatever the token
    # endpoint returned.
    Case('auth/login-client-uses-the-shared-session-builder',
         'schwaby/auth.py',
         '        _new_session(session_class, api_key, app_secret, token,\n                     oauth_client_update_token),\n        token_metadata=metadata_manager, enforce_enums=enforce_enums)',
         '        session_class(api_key, client_secret=app_secret, token=token,\n                      token_endpoint=TOKEN_ENDPOINT,\n                      update_token=oauth_client_update_token, leeway=300),\n        token_metadata=metadata_manager, enforce_enums=enforce_enums)',
         'tests/utils_test.py::HTTPStatusErrorTest::test_a_client_from_a_login_does_not_store_a_bad_refresh_response',
         '1 != 2'),

    # A JSON object carrying `error` is left to authlib, which reports it as
    # Schwab's rejection. Refused as unusable instead, a dead refresh token
    # would read as retryable.
    Case('auth/rejection-with-an-error-body-is-left-to-authlib',
         'schwaby/auth.py',
         "    if isinstance(body, dict) and ('error' in body or _is_usable_token(body)):",
         '    if isinstance(body, dict) and _is_usable_token(body):',
         'tests/utils_test.py::HTTPStatusErrorTest::test_a_refresh_rejected_with_an_error_body_is_a_refresh_error',
         'False is not true'),

    # The access token must be a string; a list is not one the session can send.
    Case('auth/usable-token-access-token-is-a-string',
         'schwaby/auth.py',
         "        if not (isinstance(access_token, str) and access_token\n",
         "        if not (access_token\n",
         'tests/auth_test.py::UsableTokenTest::test_a_token_needs_an_access_token_and_its_type',
         'is not false'),

    # A refusal's value text is bounded after escaping as well as before, since
    # escaping one character can take ten.
    Case('contrib/decimal-repr-bounded-after-escaping',
         'schwaby/contrib/util.py',
         "    return text if len(text) <= 200 else text[:197] + '...'",
         '    return text',
         'tests/contrib/util_test.py::DecodeDecimalTest::test_a_values_own_repr_cannot_forge_a_log_line',
         'not less than 400'),

    # An absorbed cause's type is named through _type_name; `type(x).__name__`
    # consults the metaclass first, and raising there ended the receive loop
    # with nothing logged.
    Case('streaming/absorbed-cause-type-name-cannot-raise',
         'schwaby/streaming.py',
         "                else ' Cause: {}: {}'.format(\n                    _type_name(cause), _safe_str(cause)))",
         "                else ' Cause: {}: {}'.format(\n                    type(cause).__name__, _safe_str(cause)))",
         'tests/streaming_test.py::StreamClientTest::test_an_absorbed_cause_whose_type_cannot_be_named_is_reported',
         'boom'),

    # The scale goes through the same validation. It reached `int()` directly
    # for three rounds, so a float scale truncated and a bool scale decoded as
    # 1 -- both of which change the answer by a factor of ten or more.
    Case('contrib/decimal-scale-is-validated-like-a-member',
         'schwaby/contrib/util.py',
         "    scale = _unsigned(0 if scale_member is None else scale_member,\n"
         "                      'signScale', value, 32)",
         "    scale = int(0 if scale_member is None else scale_member)",
         'tests/contrib/util_test.py::DecodeDecimalTest'
         '::test_a_corrupt_scale_is_refused_rather_than_coerced',
         'UnusableDecimalScale not raised'),

    # A token without a usable expires_in is refused: authlib never refreshes
    # a token with no expiry, so every call fails once it lapses.
    Case('auth/usable-token-needs-an-expiry',
         'schwaby/auth.py',
         "        if not _positive_seconds(token.get('expires_in')):",
         '        if False:',
         'tests/auth_test.py::UsableTokenTest::test_a_token_needs_an_access_token_and_its_type', 'is not false',
         removed="The check this mutates is gone. `_positive_seconds` validated expires_in by its own rules, which disagreed with authlib's: it refused tokens authlib refreshes (a second, half a second, True), and refusing one of those cannot stop the refresh on every call -- it only makes every call fail as well. It also never read expires_at, which authlib takes over expires_in, so a milliseconds expires_at was accepted and never refreshed. The expiry is now built with authlib's own OAuth2Token and must be an int within the bound; auth/usable-token-needs-an-expiry-authlib-acts-on and auth/expiry-is-bounded replace these cases."),

    # One case per branch of _positive_seconds. A bool is an int, and True
    # would pass as one second.
    Case('auth/expires-in-is-not-a-bool',
         'schwaby/auth.py',
         '    if isinstance(value, bool):\n        return False',
         '    if False:\n        return False',
         'tests/auth_test.py::UsableTokenTest::test_a_token_needs_an_access_token_and_its_type', 'is not false',
         removed="The check this mutates is gone. `_positive_seconds` validated expires_in by its own rules, which disagreed with authlib's: it refused tokens authlib refreshes (a second, half a second, True), and refusing one of those cannot stop the refresh on every call -- it only makes every call fail as well. It also never read expires_at, which authlib takes over expires_in, so a milliseconds expires_at was accepted and never refreshed. The expiry is now built with authlib's own OAuth2Token and must be an int within the bound; auth/usable-token-needs-an-expiry-authlib-acts-on and auth/expiry-is-bounded replace these cases."),

    Case('auth/expires-in-int-is-positive-and-bounded',
         'schwaby/auth.py',
         '        return 0 < value <= 10 ** 9',
         '        return True',
         'tests/auth_test.py::UsableTokenTest::test_a_token_needs_an_access_token_and_its_type', 'is not false',
         removed="The check this mutates is gone. `_positive_seconds` validated expires_in by its own rules, which disagreed with authlib's: it refused tokens authlib refreshes (a second, half a second, True), and refusing one of those cannot stop the refresh on every call -- it only makes every call fail as well. It also never read expires_at, which authlib takes over expires_in, so a milliseconds expires_at was accepted and never refreshed. The expiry is now built with authlib's own OAuth2Token and must be an int within the bound; auth/usable-token-needs-an-expiry-authlib-acts-on and auth/expiry-is-bounded replace these cases."),

    # The bound refuses inf and nan too, which is why there is no isfinite.
    Case('auth/expires-in-float-is-bounded',
         'schwaby/auth.py',
         '        return 1 <= value <= 10 ** 9',
         '        return True',
         'tests/auth_test.py::UsableTokenTest::test_a_token_needs_an_access_token_and_its_type', 'is not false',
         removed="The check this mutates is gone. `_positive_seconds` validated expires_in by its own rules, which disagreed with authlib's: it refused tokens authlib refreshes (a second, half a second, True), and refusing one of those cannot stop the refresh on every call -- it only makes every call fail as well. It also never read expires_at, which authlib takes over expires_in, so a milliseconds expires_at was accepted and never refreshed. The expiry is now built with authlib's own OAuth2Token and must be an int within the bound; auth/usable-token-needs-an-expiry-authlib-acts-on and auth/expiry-is-bounded replace these cases."),

    Case('auth/expires-in-str-is-digits',
         'schwaby/auth.py',
         "        return (value.isascii() and value.isdigit() and len(value) <= 10\n"
         "                and 0 < int(value) <= 10 ** 9)",
         '        return True',
         'tests/auth_test.py::UsableTokenTest::test_a_token_needs_an_access_token_and_its_type', 'is not false',
         removed="The check this mutates is gone. `_positive_seconds` validated expires_in by its own rules, which disagreed with authlib's: it refused tokens authlib refreshes (a second, half a second, True), and refusing one of those cannot stop the refresh on every call -- it only makes every call fail as well. It also never read expires_at, which authlib takes over expires_in, so a milliseconds expires_at was accepted and never refreshed. The expiry is now built with authlib's own OAuth2Token and must be an int within the bound; auth/usable-token-needs-an-expiry-authlib-acts-on and auth/expiry-is-bounded replace these cases."),

    # A refresh token that is present replaces the stored one, so an empty one
    # would erase a refresh token that still works.
    Case('auth/present-refresh-token-must-be-non-empty',
         'schwaby/auth.py',
         '            return isinstance(refresh_token, str) and bool(refresh_token)',
         '            return True',
         'tests/auth_test.py::UsableTokenTest::test_a_token_needs_an_access_token_and_its_type', 'is not false'),

    # A 5xx is left to authlib whatever its body. An empty 503 goes through the
    # not-JSON branch, so only a JSON body proves this one.
    Case('auth/server-error-with-a-json-body-is-left-to-authlib',
         'schwaby/auth.py',
         '    if response.status_code >= 500:\n        return response',
         '    if False:\n        return response',
         'tests/utils_test.py::HTTPStatusErrorTest::test_a_server_error_with_a_json_body_is_still_a_server_error',
         'unusable_token_response'),

    # The token a login exchanges its code for is checked too: it was written
    # unchecked, and {"message": "Unauthorized"} became the token file.
    Case('auth/login-exchange-hook-is-registered',
         'schwaby/auth.py',
         "    oauth.register_compliance_hook(\n"
         "            'access_token_response', _refuse_unusable_token_response)",
         '    pass',
         'tests/utils_test.py::HTTPStatusErrorTest::test_a_login_does_not_store_a_token_that_is_not_usable',
         'OAuthError not raised'),

    # A local UnsupportedTokenTypeError is terminal when the stored token has
    # no expiry: authlib never refreshes it, so every call fails the same way.
    Case('client/local-unsupported-token-type-without-expiry-is-terminal',
         'schwaby/client/base.py',
         '                    and not self._stored_token_will_be_refreshed())',
         '                    and False)',
         'tests/client_test.py::ClientTest::test_a_local_unsupported_token_type_without_expiry_is_terminal',
         'False is not true'),

    Case('client/local-token-error-is-reported-as-invalid',
         'schwaby/client/base.py',
         '            if local:\n                invalid = True',
         '            if local:\n                pass',
         'tests/client_test.py::ClientTest::test_a_local_unsupported_token_type_without_expiry_is_terminal',
         'False is not true'),

    # With an expiry it is refreshed once it lapses, so it stays retryable --
    # the classification 01c1fde pinned, which a blanket reclassification
    # broke.
    Case('client/stored-token-with-an-expiry-stays-retryable',
         'schwaby/client/base.py',
         "            return bool(_expiry_authlib_acts_on(token.get('expires_at'))\n"
         "                        and issubclass(type(refresh_token), str)\n"
         "                        and refresh_token)",
         '            return False',
         'tests/client_test.py::ClientTest::test_a_locally_raised_unsupported_token_type_is_not_confused',
         'True is not false'),

    Case('client/unreadable-stored-token-stays-retryable',
         'schwaby/client/base.py',
         '        except Exception:\n            return True\n\n    def _assert_type',
         '        except Exception:\n            return False\n\n    def _assert_type',
         'tests/client_test.py::ClientTest::test_a_local_unsupported_token_type_with_an_unreadable_token_stays_retryable',
         'True is not false'),

    # A value's own __repr__ and a class name can carry a newline a plain repr
    # would have escaped; both reach streaming log lines.
    Case('streaming/printable-escapes-control-characters',
         'schwaby/utils.py',
         '    if not text.isprintable():',
         '    if False:',
         'tests/streaming_test.py::AccountActivityMessageTypeTest::test_a_custom_repr_or_class_name_cannot_forge_a_log_line',
         'unexpectedly found'),

    Case('streaming/printable-is-bounded-after-escaping',
         'schwaby/utils.py',
         "    return text if len(text) <= bound else text[:bound - 3] + '...'",
         '    return text',
         'tests/streaming_test.py::AccountActivityMessageTypeTest::test_a_custom_repr_or_class_name_cannot_forge_a_log_line',
         'not less than or equal to'),

    Case('streaming/type-name-is-escaped',
         'schwaby/streaming.py',
         "    return _printable(name if len(name) <= 64 else name[:61] + '...', 64)",
         "    return name if len(name) <= 64 else name[:61] + '...'",
         'tests/streaming_test.py::AccountActivityMessageTypeTest::test_a_custom_repr_or_class_name_cannot_forge_a_log_line',
         'unexpectedly found'),

    # A class's __name__ can be a str subclass; its own __len__ ran next.
    Case('streaming/type-name-is-plain-str',
         'schwaby/streaming.py',
         "        name = str.__str__(type.__dict__['__name__'].__get__(type(value)))",
         "        name = type.__dict__['__name__'].__get__(type(value))",
         'tests/streaming_test.py::AccountActivityMessageTypeTest::test_a_type_name_that_is_a_str_subclass_is_read_as_plain_text',
         'boom'),

    # The expiry is built as authlib builds it, from expires_at or expires_in,
    # and must be one authlib acts on: without it the token is never
    # refreshed.
    Case('auth/usable-token-needs-an-expiry-authlib-acts-on',
         'schwaby/auth.py',
         "        if not _expiry_authlib_acts_on(expires_at):",
         "        if False:",
         'tests/auth_test.py::UsableTokenTest::test_a_token_needs_an_access_token_and_its_type', 'is not false'),

    # And within a bound: a milliseconds expires_at, or a huge expires_in, is
    # never reached.
    Case('auth/expiry-is-bounded',
         'schwaby/utils.py',
         "            and expires_at <= time.time() + _LONGEST_EXPIRY)",
         "            and True)",
         'tests/auth_test.py::UsableTokenTest::test_a_token_needs_an_access_token_and_its_type', 'is not false'),

    # authlib checks a stored expires_at only when it is an int, so a string
    # there is no expiry: that token is never refreshed.
    Case('client/stored-token-expiry-must-be-an-int',
         'schwaby/utils.py',
         "    return (isinstance(expires_at, int)\n",
         "    return (expires_at is not None\n",
         'tests/client_test.py::ClientTest::test_a_local_unsupported_token_type_without_expiry_is_terminal',
         'False is not true'),

    # A local failure with an expiry never reached Schwab; its message said
    # "Schwab did not say the refresh token itself is invalid".
    Case('client/local-failure-with-an-expiry-says-nothing-was-sent',
         'schwaby/client/base.py',
         "            elif isinstance(e, UnsupportedTokenTypeError):",
         "            elif False:",
         'tests/client_test.py::ClientTest::test_a_locally_raised_unsupported_token_type_is_not_confused',
         "'nothing was sent to Schwab' not found"),

    # _safe_str escapes too: str() of an exception is not a repr, and a
    # decoder's exception can quote venue text.
    Case('streaming/safe-str-is-printable',
         'schwaby/streaming.py',
         "    # An exception's own message can quote venue text, line breaks and all.\n    # Redacted before it is cut, so a secret across the cut leaves nothing.\n    text = _redact.before_cut(text, 200)\n"
         "    return _printable(text if len(text) <= 200 else text[:197] + '...', 200)",
         "    return text if len(text) <= 200 else text[:197] + '...'",
         'tests/streaming_test.py::AccountActivityMessageTypeTest::test_no_line_break_reaches_a_formatted_value',
         '1 != 2'),

    # \x85, U+2028 and U+2029 break a line past ASCII.
    Case('streaming/printable-escapes-line-breaks-past-ascii',
         'schwaby/utils.py',
         "        text = ''.join(c if c.isprintable() else repr(c)[1:-1] for c in text)",
         "        text = ''.join(c if (c.isprintable() or not c.isascii()) else repr(c)[1:-1] for c in text)",
         'tests/streaming_test.py::AccountActivityMessageTypeTest::test_no_line_break_reaches_a_formatted_value',
         '1 != 2'),

    # The stored-token classification asks the same helper as the response
    # check, bound included. It counted any int as an expiry, so a stored
    # token with a milliseconds expiry was reported retryable on every call
    # while the same expiry in a response was refused as never reached.
    Case('client/stored-expiry-must-be-reachable',
         'schwaby/utils.py',
         "            and expires_at <= time.time() + _LONGEST_EXPIRY)",
         "            and True)",
         'tests/client_test.py::ClientTest::test_a_local_unsupported_token_type_without_expiry_is_terminal',
         'False is not true'),

    # And base.py must actually ask it.
    Case('client/stored-token-classification-uses-the-shared-helper',
         'schwaby/client/base.py',
         "            return bool(_expiry_authlib_acts_on(token.get('expires_at'))\n",
         "            return bool(isinstance(token.get('expires_at'), int)\n",
         'tests/client_test.py::ClientTest::test_a_local_unsupported_token_type_without_expiry_is_terminal',
         'False is not true'),

    # authlib refreshes only with a refresh token, so a stored token it cannot
    # send, with a good expiry and no refresh token, is never replaced. It was
    # told "may clear on its own".
    Case('client/stored-token-needs-a-refresh-token-to-be-refreshed',
         'schwaby/client/base.py',
         "                        and issubclass(type(refresh_token), str)\n"
         "                        and refresh_token)",
         "                        and True)",
         'tests/client_test.py::ClientTest::test_a_local_unsupported_token_type_without_expiry_is_terminal',
         'False is not true'),

    # token_file_age reads the token through the same path the client does, so
    # the two cannot disagree. File mtime is the wrong clock: every refresh
    # rewrites the file.
    Case('auth/token-file-age-reads-the-creation-timestamp',
         'schwaby/auth.py',
         '    return metadata.token_age()\n',
         '    return int(time.time()) - int(os.path.getmtime(token_path))\n',
         'tests/auth_test.py::TokenFileAgeTest::test_the_file_modification_time_is_not_the_clock',
         '82 != 0'),

    # json.load returns whatever the file held; a list, null or a string failed
    # later with an indexing error that said nothing about the file.
    Case('auth/token-file-must-hold-a-json-object',
         'schwaby/auth.py',
         "        if not issubclass(type(token), collections.abc.Mapping):",
         "        if False:",
         'tests/auth_test.py::TokenFileAgeTest::test_a_file_that_is_not_a_json_object_is_refused',
         'does not match'),

    # find_account_hash refuses a number that is not a str, with a message
    # saying so. str.__str__ below also raises TypeError, but not one a caller
    # can act on, so the test pins the message.
    Case('utils/account-hash-number-must-be-str',
         'schwaby/utils.py',
         "    if not issubclass(type(account_number), str):\n        raise TypeError(",
         "    if False:\n        raise TypeError(",
         'tests/utils_test.py::FindAccountHashTest::test_an_account_number_that_is_not_a_str_is_refused',
         'does not match'),

    # Whitespace around the number would read as an account the token does not
    # cover, which the docs call an ordinary answer rather than a mistake.
    Case('utils/account-hash-number-must-be-ascii-digits',
         'schwaby/utils.py',
         "    if not (account_number.isascii() and account_number.isdigit()):",
         "    if False:",
         'tests/utils_test.py::FindAccountHashTest::test_an_account_number_that_is_not_ascii_digits_is_refused',
         'AccountNumberNotFoundError'),

    # isdigit alone accepts digits from other scripts, which Schwab's account
    # numbers are not.
    Case('utils/account-hash-number-digits-must-be-ascii',
         'schwaby/utils.py',
         "    if not (account_number.isascii() and account_number.isdigit()):",
         "    if not account_number.isdigit():",
         'tests/utils_test.py::FindAccountHashTest::test_an_account_number_that_is_not_ascii_digits_is_refused',
         'AccountNumberNotFoundError'),

    # Every type is checked through type(), because a class can satisfy
    # isinstance by faking __class__. One case per value checked.
    Case('utils/account-hash-number-type-not-faked',
         'schwaby/utils.py',
         "    if not issubclass(type(account_number), str):",
         "    if not isinstance(account_number, str):",
         'tests/utils_test.py::FindAccountHashTest::test_a_faked_class_is_not_taken_for_the_real_type',
         'does not match'),

    Case('utils/account-hash-list-type-not-faked',
         'schwaby/utils.py',
         "    if not issubclass(type(account_numbers), list):",
         "    if not isinstance(account_numbers, list):",
         'tests/utils_test.py::FindAccountHashTest::test_a_faked_class_is_not_taken_for_the_real_type',
         "descriptor '__getitem__'"),

    Case('utils/account-hash-entry-type-not-faked',
         'schwaby/utils.py',
         "        if not issubclass(type(entry), dict):",
         "        if not isinstance(entry, dict):",
         'tests/utils_test.py::FindAccountHashTest::test_a_faked_class_is_not_taken_for_the_real_type',
         "descriptor 'get'"),

    Case('utils/account-hash-entry-number-type-not-faked',
         'schwaby/utils.py',
         '        if not issubclass(type(number), str):\n',
         '        if not isinstance(number, str):\n',
         'tests/utils_test.py::FindAccountHashTest::test_a_faked_class_is_not_taken_for_the_real_type',
         "descriptor '__str__'"),

    Case('utils/account-hash-entry-hash-type-not-faked',
         'schwaby/utils.py',
         '        if issubclass(type(hash_value), str) and str.__str__(hash_value):\n',
         '        if isinstance(hash_value, str) and str.__str__(hash_value):\n',
         'tests/utils_test.py::FindAccountHashTest::test_a_faked_class_is_not_taken_for_the_real_type',
         "descriptor '__str__'"),

    # The refusal names the type through type's own descriptor, so a
    # metaclass whose __name__ raises cannot replace the TypeError.
    Case('utils/account-hash-type-name-ignores-metaclass',
         'schwaby/utils.py',
         "        return str.__str__(type.__dict__['__name__'].__get__(type(value)))\n    except Exception:",
         "        return type(value).__name__\n    except KeyError:",
         'tests/utils_test.py::FindAccountHashTest::test_a_type_name_is_read_without_trusting_a_metaclass',
         'metaclass raised'),

    # A str subclass is reduced to its text: its own __eq__ decided the match.
    Case('utils/account-hash-number-reduced-to-plain-str',
         'schwaby/utils.py',
         "    account_number = str.__str__(account_number)\n",
         "",
         'tests/utils_test.py::FindAccountHashTest::test_a_str_subclass_is_read_as_its_text',
         'AccountNumberNotFoundError'),

    Case('utils/account-hash-list-must-be-a-list',
         'schwaby/utils.py',
         "    if not issubclass(type(account_numbers), list):",
         "    if False:",
         'tests/utils_test.py::FindAccountHashTest::test_a_response_that_is_not_the_account_list_is_refused',
         "descriptor '__getitem__'"),

    Case('utils/account-hash-entry-number-must-be-str',
         'schwaby/utils.py',
         '        if not issubclass(type(number), str):\n',
         '        if False:\n',
         'tests/utils_test.py::FindAccountHashTest::test_a_response_that_is_not_the_account_list_is_refused',
         "descriptor '__str__'"),

    Case('utils/account-hash-entry-hash-must-be-str',
         'schwaby/utils.py',
         '        if issubclass(type(hash_value), str) and str.__str__(hash_value):\n',
         '        if str.__str__(hash_value):\n',
         'tests/utils_test.py::FindAccountHashTest::test_a_response_that_is_not_the_account_list_is_refused',
         "descriptor '__str__'"),

    Case('utils/account-hash-entry-hash-must-be-non-empty',
         'schwaby/utils.py',
         '        if issubclass(type(hash_value), str) and str.__str__(hash_value):\n',
         '        if issubclass(type(hash_value), str):\n',
         'tests/utils_test.py::FindAccountHashTest::test_a_response_that_is_not_the_account_list_is_refused',
         'UnusableAccountNumbersError not raised'),

    # A number listed twice cannot be trusted to name one account.
    Case('utils/account-hash-duplicate-refused',
         'schwaby/utils.py',
         "    if len(hashes) > 1:",
         "    if False:",
         'tests/utils_test.py::FindAccountHashTest::test_an_account_listed_twice_is_refused',
         'UnusableAccountNumbersError not raised'),

    # Not found is its own class: an ordinary answer, not a fault.
    Case('utils/account-hash-not-found-is-its-own-error',
         'schwaby/utils.py',
         "        raise AccountNumberNotFoundError(",
         "        raise UnusableAccountNumbersError(",
         'tests/utils_test.py::FindAccountHashTest::test_an_unlinked_account_is_its_own_error',
         'UnusableAccountNumbersError'),

    # No message names an account number, so each is safe to log.
    Case('utils/account-hash-message-names-no-number',
         'schwaby/utils.py',
         "                'none of the {} accounts listed has that account '\n"
         "                'number'.format(len(entries)))",
         "                'none of the {} accounts listed has that account '\n"
         "                'number {}'.format(len(entries), account_number))",
         'tests/utils_test.py::FindAccountHashTest::test_an_unlinked_account_is_its_own_error',
         'unexpectedly found'),

    # The count is of the entries read through list, not a subclass's __len__,
    # which could put any number in the message.
    Case('utils/account-hash-message-count-from-entries',
         'schwaby/utils.py',
         "                'number'.format(len(entries)))",
         "                'number'.format(len(account_numbers)))",
         'tests/utils_test.py::FindAccountHashTest::test_a_list_subclass_cannot_put_a_count_in_the_message',
         'unexpectedly found'),

    # parse_message_data: a dict passes through, so a second call is harmless.
    Case('contrib/message-data-dict-passes-through',
         'schwaby/contrib/util.py',
         "    if issubclass(type(value), dict):\n        return value\n    if not issubclass(type(value), str):",
         "    if not issubclass(type(value), str):",
         'tests/contrib/util_test.py::ParseMessageDataTest::test_a_dict_is_returned_unchanged',
         'must be a str or a dict'),

    # Anything else is refused with a message saying so; str.__str__ also
    # raises TypeError on it, but not one that names the field.
    Case('contrib/message-data-type-refused-by-message',
         'schwaby/contrib/util.py',
         "    if not issubclass(type(value), str):\n        raise TypeError(\n                'MESSAGE_DATA must be",
         "    if False:\n        raise TypeError(\n                'MESSAGE_DATA must be",
         'tests/contrib/util_test.py::ParseMessageDataTest::test_anything_else_is_refused',
         'does not match'),

    # Types through type(): a class can fake __class__ for isinstance.
    Case('contrib/message-data-dict-type-not-faked',
         'schwaby/contrib/util.py',
         "    if issubclass(type(value), dict):",
         "    if isinstance(value, dict):",
         'tests/contrib/util_test.py::ParseMessageDataTest::test_anything_else_is_refused',
         'TypeError not raised'),

    Case('contrib/message-data-str-type-not-faked',
         'schwaby/contrib/util.py',
         "    if not issubclass(type(value), str):",
         "    if not isinstance(value, str):",
         'tests/contrib/util_test.py::ParseMessageDataTest::test_anything_else_is_refused',
         'does not match'),

    # A str subclass is read as its text: its own strip decided the answer.
    Case('contrib/message-data-reduced-to-plain-str',
         'schwaby/contrib/util.py',
         "    text = str.__str__(value)\n    stripped = text.strip()",
         "    text = value\n    stripped = text.strip()",
         'tests/contrib/util_test.py::ParseMessageDataTest::test_a_str_subclass_is_read_as_its_text',
         "'Feature not supported' != None"),

    # The SUBSCRIBED ack, empty or only whitespace, is None.
    Case('contrib/message-data-empty-is-none',
         'schwaby/contrib/util.py',
         "    if not stripped:\n        return None",
         "    if False:\n        return None",
         'tests/contrib/util_test.py::ParseMessageDataTest::test_the_subscribed_ack_is_none',
         'is not None'),

    # A notice is returned as text and never raises.
    Case('contrib/message-data-notice-never-raises',
         'schwaby/contrib/util.py',
         "    if not stripped.startswith('{', start):\n        return text",
         "    if False:\n        return text",
         'tests/contrib/util_test.py::ParseMessageDataTest::test_a_notice_is_returned_as_its_text_and_never_raises',
         'UnparsableMessageData'),

    # Nesting past the interpreter's depth is refused like any other corrupt
    # payload, not escaped as RecursionError.
    Case('contrib/message-data-deep-nesting-refused',
         'schwaby/contrib/util.py',
         "    except (ValueError, RecursionError):\n        # ValueError covers",
         "    except ValueError:\n        # ValueError covers",
         'tests/contrib/util_test.py::ParseMessageDataTest::test_text_that_starts_like_an_object_but_is_not_one_raises',
         'RecursionError'),

    # Text that starts like an object but is not one raises instead of reading
    # as a notice.
    Case('contrib/message-data-corrupt-object-raises',
         'schwaby/contrib/util.py',
         "    if not isinstance(parsed, dict):\n        raise UnparsableMessageData(",
         "    if False:\n        raise UnparsableMessageData(",
         'tests/contrib/util_test.py::ParseMessageDataTest::test_text_that_starts_like_an_object_but_is_not_one_raises',
         'UnparsableMessageData not raised'),

    # The refusal does not repeat the payload, which carries account data.
    Case('contrib/message-data-message-omits-payload',
         'schwaby/contrib/util.py',
         "                'as one ({} characters)'.format(len(text)))",
         "                'as one ({} characters) {}'.format(len(text), text))",
         'tests/contrib/util_test.py::ParseMessageDataTest::test_text_that_starts_like_an_object_but_is_not_one_raises',
         'unexpectedly found'),

    # A token_read_func may hand back any mapping; 4.4.2 built a client from
    # a UserDict, and the dict-only check refused it.
    Case('auth/token-mapping-accepted',
         'schwaby/auth.py',
         "        if not issubclass(type(token), collections.abc.Mapping):",
         "        if not issubclass(type(token), dict):",
         'tests/auth_test.py::TokenFileAgeTest::test_a_token_mapping_that_is_not_a_dict_is_accepted',
         'not a JSON object'),

    # A creation timestamp that is not a number is refused at load, rather
    # than failing inside token_age() with an error about subtraction. One
    # case per branch: bool, the type, and a non-finite float.
    Case('auth/token-timestamp-bool-refused',
         'schwaby/auth.py',
         "        if (issubclass(type(creation_timestamp), bool)\n",
         "        if (False\n",
         'tests/auth_test.py::TokenFileAgeTest::test_a_malformed_token_raises_valueerror_and_nothing_else',
         'not raised'),

    Case('auth/token-timestamp-type-refused',
         'schwaby/auth.py',
         "                or not issubclass(type(creation_timestamp),\n"
         "                                  (numbers.Real, decimal.Decimal))\n",
         "",
         'tests/auth_test.py::TokenFileAgeTest::test_a_timestamp_that_is_not_a_real_number_is_refused',
         'not raised'),

    Case('auth/token-timestamp-must-be-finite',
         'schwaby/auth.py',
         "                or not _is_finite(creation_timestamp)):",
         "                or False):",
         'tests/auth_test.py::TokenFileAgeTest::test_a_malformed_token_raises_valueerror_and_nothing_else',
         'not raised'),

    # A Decimal is not a numbers.Real, and a token store such as DynamoDB hands
    # one back; token_age() has always accepted it.
    Case('auth/token-timestamp-decimal-accepted',
         'schwaby/auth.py',
         "(numbers.Real, decimal.Decimal))\n",
         "(numbers.Real,))\n",
         'tests/auth_test.py::TokenFileAgeTest::test_a_decimal_or_other_real_timestamp_is_accepted',
         'not a finite number'),

    # float() overflows on an int too large for it and raises on a signalling
    # NaN. Both are refused as not finite, rather than escaping.
    Case('auth/token-timestamp-overflow-refused',
         'schwaby/auth.py',
         "    except (ValueError, OverflowError):\n        # A signalling NaN",
         "    except ValueError:\n        # A signalling NaN",
         'tests/auth_test.py::TokenFileAgeTest::test_a_malformed_token_raises_valueerror_and_nothing_else',
         'OverflowError'),

    Case('auth/token-timestamp-signalling-nan-refused',
         'schwaby/auth.py',
         "    except (ValueError, OverflowError):\n        # A signalling NaN",
         "    except OverflowError:\n        # A signalling NaN",
         'tests/auth_test.py::TokenFileAgeTest::test_a_non_finite_decimal_timestamp_is_refused',
         'signaling NaN'),

    # A token file nested past the interpreter's depth is refused like any
    # other file this library did not write, not escaped as RecursionError.
    Case('auth/token-file-nested-too-deeply-refused',
         'schwaby/auth.py',
         "            except RecursionError:",
         "            except ZeroDivisionError:",
         'tests/auth_test.py::TokenFileAgeTest::test_a_token_file_nested_too_deeply_raises_valueerror',
         'RecursionError'),

    # json reads deeper than the redaction walk can recurse, so a token json
    # accepted still has to be refused where the walk runs.
    Case('auth/token-nested-too-deeply-to-redact-refused',
         'schwaby/auth.py',
         "        register_redactions(token)\n    except RecursionError:",
         "        register_redactions(token)\n    except ZeroDivisionError:",
         'tests/auth_test.py::TokenFileAgeTest::test_a_token_nested_too_deeply_to_redact_raises_valueerror',
         'RecursionError'),

    # A Decimal age, from a token store such as DynamoDB, cannot be divided by
    # a float, which turned the refresh failure into a TypeError.
    Case('client/refresh-advice-reads-a-decimal-age',
         'schwaby/client/base.py',
         ").format(float(age) / 86400.0) if age is not None else (",
         ").format(age / 86400.0) if age is not None else (",
         'tests/client_test.py::ClientTest::test_token_refresh_error_reports_a_decimal_token_age',
         'unsupported operand'),

    Case('auth/token-needs-a-token-entry',
         'schwaby/auth.py',
         "        if 'token' not in token:",
         "        if False:",
         'tests/auth_test.py::TokenFileAgeTest::test_a_malformed_token_raises_valueerror_and_nothing_else',
         'KeyError'),

    # token_file_age and a client built from the same file report the same
    # age. The earlier test compared token_file_age with the function it calls.
    Case('client/token-age-agrees-with-the-token-file',
         'schwaby/client/base.py',
         "        return self.token_metadata.token_age()",
         "        return self.token_metadata.token_age() + 1",
         'tests/auth_test.py::TokenFileAgeTest::test_the_age_agrees_with_a_client_built_from_the_same_file',
         '82 != 83'),

    # find_account_hash reads entries through dict and list themselves, and
    # reduces what it matches and returns to a plain str, so a subclass cannot
    # pass the check with one value and match or return another.
    Case('utils/account-hash-entry-read-through-dict',
         'schwaby/utils.py',
         "        number = dict.get(entry, 'accountNumber')",
         "        number = entry.get('accountNumber')",
         'tests/utils_test.py::FindAccountHashTest::test_an_entry_dict_subclass_is_read_through_dict_itself',
         'AccountNumberNotFoundError not raised'),

    Case('utils/account-hash-hash-read-through-dict',
         'schwaby/utils.py',
         "        hash_value = dict.get(entry, 'hashValue')",
         "        hash_value = entry.get('hashValue')",
         'tests/utils_test.py::FindAccountHashTest::test_a_hash_read_through_dict_itself_too',
         'HASH-OTHER'),

    Case('utils/account-hash-list-read-through-list',
         'schwaby/utils.py',
         "    entries = list.__getitem__(account_numbers, slice(None))",
         "    entries = account_numbers",
         'tests/utils_test.py::FindAccountHashTest::test_a_list_subclass_is_read_through_list_itself',
         'UnusableAccountNumbersError not raised'),

    Case('utils/account-hash-entry-number-reduced',
         'schwaby/utils.py',
         "        number = str.__str__(number)\n",
         "",
         'tests/utils_test.py::FindAccountHashTest::test_an_entry_str_subclass_is_read_as_its_text',
         'AccountNumberNotFoundError not raised'),

    Case('utils/account-hash-returned-hash-reduced',
         'schwaby/utils.py',
         '                hashes.append(str.__str__(hash_value))\n',
         '                hashes.append(hash_value)\n',
         'tests/utils_test.py::FindAccountHashTest::test_the_hash_returned_is_a_plain_str',
         ' is not '),

    # An invisible character before { must not turn a payload into a notice.
    Case('contrib/message-data-invisible-prefix-skipped',
         'schwaby/contrib/util.py',
         "           and unicodedata.category(stripped[start]) in skipped):",
         "           and False):",
         'tests/contrib/util_test.py::ParseMessageDataTest::test_invisible_characters_before_an_object_do_not_make_a_notice',
         '!='),

    # Each skipped category is load-bearing. str.strip has already stopped at
    # a format character, so a separator after one is not whitespace to it.
    Case('contrib/message-data-format-characters-skipped',
         'schwaby/contrib/util.py',
         "frozenset(('Cf', 'Cc', 'Zs', 'Zl', 'Zp'))",
         "frozenset(('Cc', 'Zs', 'Zl', 'Zp'))",
         'tests/contrib/util_test.py::ParseMessageDataTest::test_invisible_characters_before_an_object_do_not_make_a_notice',
         '!='),

    Case('contrib/message-data-control-characters-skipped',
         'schwaby/contrib/util.py',
         "frozenset(('Cf', 'Cc', 'Zs', 'Zl', 'Zp'))",
         "frozenset(('Cf', 'Zs', 'Zl', 'Zp'))",
         'tests/contrib/util_test.py::ParseMessageDataTest::test_invisible_characters_before_an_object_do_not_make_a_notice',
         '!='),

    Case('contrib/message-data-space-separators-skipped',
         'schwaby/contrib/util.py',
         "frozenset(('Cf', 'Cc', 'Zs', 'Zl', 'Zp'))",
         "frozenset(('Cf', 'Cc', 'Zl', 'Zp'))",
         'tests/contrib/util_test.py::ParseMessageDataTest::test_invisible_characters_before_an_object_do_not_make_a_notice',
         '!='),

    Case('contrib/message-data-line-separators-skipped',
         'schwaby/contrib/util.py',
         "frozenset(('Cf', 'Cc', 'Zs', 'Zl', 'Zp'))",
         "frozenset(('Cf', 'Cc', 'Zs', 'Zp'))",
         'tests/contrib/util_test.py::ParseMessageDataTest::test_invisible_characters_before_an_object_do_not_make_a_notice',
         '!='),

    Case('contrib/message-data-paragraph-separators-skipped',
         'schwaby/contrib/util.py',
         "frozenset(('Cf', 'Cc', 'Zs', 'Zl', 'Zp'))",
         "frozenset(('Cf', 'Cc', 'Zs', 'Zl'))",
         'tests/contrib/util_test.py::ParseMessageDataTest::test_invisible_characters_before_an_object_do_not_make_a_notice',
         '!='),

    # The stripped text is what gets parsed: str.strip removes whitespace JSON
    # does not accept.
    Case('contrib/message-data-parses-the-stripped-text',
         'schwaby/contrib/util.py',
         "        parsed = json.loads(stripped[start:])",
         "        parsed = json.loads(text[start:])",
         'tests/contrib/util_test.py::ParseMessageDataTest::test_whitespace_json_does_not_accept_is_still_stripped',
         'UnparsableMessageData'),

    # A notice comes back as the text it arrived as, whitespace included.
    Case('contrib/message-data-notice-keeps-its-whitespace',
         'schwaby/contrib/util.py',
         "    if not stripped.startswith('{', start):\n        return text",
         "    if not stripped.startswith('{', start):\n        return stripped",
         'tests/contrib/util_test.py::ParseMessageDataTest::test_a_notice_keeps_its_surrounding_whitespace',
         '!='),

    # parse_symbol reads fixed positions, so each part of the layout is
    # checked: the length, the padded root, and the digit fields.
    Case('options/symbol-must-be-21-characters',
         'schwaby/orders/options.py',
         "        if len(symbol) != 21:",
         "        if False:",
         'tests/orders/options_test.py::OptionSymbolLayoutTest::test_a_symbol_that_is_not_the_fixed_layout_is_refused',
         'not raised'),

    Case('options/symbol-must-be-a-str',
         'schwaby/orders/options.py',
         "        if not issubclass(type(symbol), str):\n            raise ValueError(layout_error_str)",
         "        if False:\n            raise ValueError(layout_error_str)",
         'tests/orders/options_test.py::OptionSymbolLayoutTest::test_a_symbol_that_is_not_the_fixed_layout_is_refused',
         "descriptor '__str__'"),

    Case('options/symbol-type-not-faked',
         'schwaby/orders/options.py',
         "        if not issubclass(type(symbol), str):",
         "        if not isinstance(symbol, str):",
         'tests/orders/options_test.py::OptionSymbolLayoutTest::test_a_str_subclass_cannot_fake_the_length',
         "descriptor '__str__'"),

    # A root of spaces alone is no root: the symbol would parse with an empty
    # underlying.
    Case('options/symbol-root-needs-a-character',
         'schwaby/orders/options.py',
         "_SYMBOL_ROOT = re.compile(r'[^\\s]{1,6} *')",
         "_SYMBOL_ROOT = re.compile(r'[^\\s]{0,6} *')",
         'tests/orders/options_test.py::OptionSymbolLayoutTest::test_a_symbol_that_is_not_the_fixed_layout_is_refused',
         'not raised'),

    # A str subclass is reduced to its text, so its own __len__ cannot pass a
    # symbol one digit short.
    Case('options/symbol-reduced-to-plain-str',
         'schwaby/orders/options.py',
         "        symbol = str.__str__(symbol)\n",
         "",
         'tests/orders/options_test.py::OptionSymbolLayoutTest::test_a_str_subclass_cannot_fake_the_length',
         'not raised'),

    Case('options/symbol-root-must-be-space-padded',
         'schwaby/orders/options.py',
         "        if not _SYMBOL_ROOT.fullmatch(symbol[:6]):",
         "        if False:",
         'tests/orders/options_test.py::OptionSymbolLayoutTest::test_a_symbol_that_is_not_the_fixed_layout_is_refused',
         'not raised'),

    Case('options/symbol-expiration-must-be-ascii-digits',
         'schwaby/orders/options.py',
         "        if not (_SYMBOL_DIGITS.fullmatch(symbol[6:12])\n",
         "        if not (True\n",
         'tests/orders/options_test.py::OptionSymbolLayoutTest::test_a_symbol_that_is_not_the_fixed_layout_is_refused',
         'AssertionError'),

    Case('options/symbol-strike-must-be-ascii-digits',
         'schwaby/orders/options.py',
         "                and _SYMBOL_DIGITS.fullmatch(symbol[13:])):",
         "                and True):",
         'tests/orders/options_test.py::OptionSymbolLayoutTest::test_a_symbol_that_is_not_the_fixed_layout_is_refused',
         'not raised'),

    # The contract-type refusal keeps its own message.
    Case('options/symbol-contract-type-message-kept',
         'schwaby/orders/options.py',
         "        if contract_type not in ('C', 'P'):",
         "        if False:",
         'tests/orders/options_test.py::OptionSymbolLayoutTest::test_the_other_refusals_keep_their_messages',
         'does not match'),

    # A code block nested in an admonition used to run on into the paragraph
    # after it, which dedents to a shallower margin than the code and makes
    # the whole block an `unexpected indent`. bad_keywords_in swallows a
    # SyntaxError, so three blocks were skipped and nothing counted them.
    # A literal inside bold renders its backticks. The check parses with
    # docutils; one case for the detector, one per source it reads, and one
    # for the count that says it read them.
    # authlib refreshes 300 seconds before the expiry, so the advice says the
    # token is replaced as the expiry nears, not once it passes.
    Case('client/refreshable-token-advice-says-as-expiry-nears',
         'schwaby/client/base.py',
         "'refresh token, so it is refreshed as that expiry '\n                          'nears, and",
         "'refresh token, so it is refreshed once that expiry '\n                          'passes, and",
         'tests/client_test.py::ClientTest::test_a_locally_raised_unsupported_token_type_is_not_confused',
         'as that expiry nears'),

    # A monitor polls token_file_age, so its read logs at DEBUG; a client
    # loading the token keeps the INFO line. One case for each side.
    Case('auth/token-file-age-reads-quietly',
         'schwaby/auth.py',
         '            __token_loader(token_path, logging.DEBUG)(), None)\n',
         '            __token_loader(token_path)(), None)\n',
         'tests/auth_test.py::TokenFileAgeTest::test_reading_the_age_does_not_log_at_info',
         'Unexpected logs found'),

    Case('auth/client-token-load-still-logs-at-info',
         'schwaby/auth.py',
         "def __token_loader(token_path, level=logging.INFO):",
         "def __token_loader(token_path, level=logging.DEBUG):",
         'tests/auth_test.py::TokenFileAgeTest::test_reading_the_age_does_not_log_at_info',
         'no logs of level INFO or higher'),

    # Streaming follow-ups. An unparsable frame is quoted and cut in the
    # message, and a frame json cannot read at all is still UnparsableMessage.
    Case('stream-fu/unparsable-message-quotes-the-frame',
         'schwaby/streaming.py',
         "                   'cut: ' + _safe_value(raw))",
         "                   'cut: ' + raw)",
         'tests/streaming_test.py::StreamClientTest::test_an_unparsable_frame_is_quoted_and_cut_in_its_message',
         'unexpectedly found'),

    Case('stream-fu/unparsable-covers-a-binary-frame',
         'schwaby/streaming.py',
         "        except (ValueError, RecursionError) as e:\n            # ValueError covers JSONDecodeError",
         "        except (json.decoder.JSONDecodeError, RecursionError) as e:\n            # ValueError covers JSONDecodeError",
         'tests/streaming_test.py::StreamClientTest::test_a_frame_json_cannot_read_at_all_is_unparsable',
         'UnicodeDecodeError'),

    Case('stream-fu/unparsable-covers-deep-nesting',
         'schwaby/streaming.py',
         "        except (ValueError, RecursionError) as e:\n            # ValueError covers JSONDecodeError",
         "        except ValueError as e:\n            # ValueError covers JSONDecodeError",
         'tests/streaming_test.py::StreamClientTest::test_a_frame_json_cannot_read_at_all_is_unparsable',
         'RecursionError'),

    # A key is reduced to a plain str before isdigit judges it a field.
    Case('stream-fu/relabel-key-is-plain-text',
         'schwaby/streaming.py',
         "                text_key = str.__str__(str(old_key))",
         "                text_key = str(old_key)",
         'tests/streaming_test.py::UnknownStreamFieldTest::test_a_key_is_read_as_plain_text_before_it_is_judged_a_field',
         'True is not false'),

    # A service name in an absorbed relabel report is escaped, and named
    # without calling its own __str__ unguarded. One case per half.
    Case('stream-fu/relabel-report-escapes-the-service',
         'schwaby/streaming.py',
         "                            % _printable(_safe_name(service), 64),\n",
         "                            % _safe_name(service),\n",
         'tests/streaming_test.py::StreamClientTest::test_a_service_name_cannot_forge_a_relabel_report',
         'unexpectedly found'),

    Case('stream-fu/relabel-report-survives-a-raising-name',
         'schwaby/streaming.py',
         "                            % _printable(_safe_name(service), 64),\n",
         "                            % _printable(str(service), 64),\n",
         'tests/streaming_test.py::StreamClientTest::test_a_service_name_that_raises_does_not_end_the_loop',
         'RuntimeError'),

    Case('stream-fu/handler-failure-line-escapes-the-service',
         'schwaby/streaming.py',
         "                        'The message it was handling has been dropped.',\n"
         "                        _printable(_safe_name(service), 64))",
         "                        'The message it was handling has been dropped.',\n"
         "                        service)",
         'tests/streaming_test.py::StreamClientTest::test_a_service_name_cannot_forge_the_handler_failure_line',
         'unexpectedly found'),

    # _pretty stays cosmetic: any serialisation failure falls back, and the
    # last fallback names the type without the metaclass.
    Case('stream-fu/pretty-falls-back-on-any-failure',
         'schwaby/streaming.py',
         "        except Exception:\n            # json.dumps names a value it cannot serialise",
         "        except (TypeError, ValueError):\n            # json.dumps names a value it cannot serialise",
         'tests/streaming_test.py::StreamClientTest::test_a_debug_line_survives_a_value_json_cannot_name',
         'class raised'),

    Case('stream-fu/pretty-names-the-type-without-the-metaclass',
         'schwaby/streaming.py',
         "cannot be formatted>'.format(\n                        _type_name(obj))",
         "cannot be formatted>'.format(\n                        type(obj).__name__)",
         'tests/streaming_test.py::StreamClientTest::test_a_debug_line_survives_a_type_whose_name_raises',
         'raised RuntimeError'),

    # The documented error-handler recipe survives a deeply nested service.
    Case('stream-fu/error-handler-recipe-survives-deep-nesting',
         'docs/streaming.rst',
         "      alert('schwaby stream: %s raised %r'\n"
         "            % (reprlib.repr(service), exception))",
         "      alert('schwaby stream: %r raised %r' % (service, exception))",
         'tests/packaging_test.py::DocErrorHandlerRecipeTest::test_the_recipe_escapes_and_survives_deep_nesting',
         'RecursionError'),

    # The expiry bound is the seven days a refresh token lasts: an expires_in
    # sent in milliseconds, 20.8 days read as seconds, is refused wherever the
    # bound is asked, response check and stored-token classification alike.
    Case('auth/expiry-bound-is-seven-days-for-a-response',
         'schwaby/utils.py',
         "_LONGEST_EXPIRY = 7 * 24 * 60 * 60",
         "_LONGEST_EXPIRY = 10 ** 9",
         'tests/auth_test.py::UsableTokenTest::test_a_token_needs_an_access_token_and_its_type',
         'is not false'),

    Case('auth/expiry-bound-is-seven-days-for-a-refresh',
         'schwaby/utils.py',
         "_LONGEST_EXPIRY = 7 * 24 * 60 * 60",
         "_LONGEST_EXPIRY = 10 ** 9",
         'tests/utils_test.py::HTTPStatusErrorTest::test_a_refresh_response_that_is_not_a_token_is_not_stored',
         'TokenRefreshError not raised'),

    Case('client/stored-expiry-bound-is-seven-days',
         'schwaby/utils.py',
         "_LONGEST_EXPIRY = 7 * 24 * 60 * 60",
         "_LONGEST_EXPIRY = 10 ** 9",
         'tests/client_test.py::ClientTest::test_a_local_unsupported_token_type_without_expiry_is_terminal',
         'False is not true'),

    # A retryable refusal past seven days says to alert someone, and stays
    # retryable. One case for the clause, one for the boundary, one for the
    # terminal refusal it must not touch, and one for the documented recipe.
    Case('client/refusal-past-seven-days-alerts',
         'schwaby/client/base.py',
         "            if not invalid and age is not None and age > 7 * 24 * 60 * 60:",
         "            if False:",
         'tests/client_test.py::ClientTest::test_a_retryable_refusal_past_seven_days_says_to_alert',
         'True != False'),

    Case('client/refusal-alert-starts-after-seven-days',
         'schwaby/client/base.py',
         "            if not invalid and age is not None and age > 7 * 24 * 60 * 60:",
         "            if not invalid and age is not None and age >= 7 * 24 * 60 * 60:",
         'tests/client_test.py::ClientTest::test_a_retryable_refusal_past_seven_days_says_to_alert',
         'False != True'),

    Case('client/terminal-refusal-adds-no-alert',
         'schwaby/client/base.py',
         "            if not invalid and age is not None and age > 7 * 24 * 60 * 60:",
         "            if age is not None and age > 7 * 24 * 60 * 60:",
         'tests/client_test.py::ClientTest::test_a_retryable_refusal_past_seven_days_says_to_alert',
         'unexpectedly found'),

    Case('docs/refresh-recipe-alerts-past-seven-days',
         'docs/auth.rst',
         "          if e.token_age is not None and e.token_age > 7 * 24 * 60 * 60:",
         "          if False:",
         'tests/packaging_test.py::DocRefreshRecipeTest::test_the_recipe_alerts_past_seven_days_and_keeps_retrying',
         '1 != 0'),

    # easy_client's notebook route passes asyncio on to the manual flow.
    Case('auth/easy-client-notebook-keeps-asyncio',
         'schwaby/auth.py',
         "                                    token_path, asyncio=asyncio,\n",
         "                                    token_path,\n",
         'tests/auth_test.py::EasyClientTest::test_a_notebook_login_keeps_asyncio',
         'KeyError'),

    # from_loaded_token checks types through type(): a faked __class__ passes
    # isinstance and then escapes as TypeError. One case per check.
    Case('auth/token-mapping-type-not-faked',
         'schwaby/auth.py',
         "        if not issubclass(type(token), collections.abc.Mapping):",
         "        if not isinstance(token, collections.abc.Mapping):",
         'tests/auth_test.py::TokenFileAgeTest::test_a_faked_class_is_not_taken_for_a_mapping_or_a_number',
         'TypeError'),

    Case('auth/token-timestamp-type-not-faked',
         'schwaby/auth.py',
         "                or not issubclass(type(creation_timestamp),\n",
         "                or not isinstance(creation_timestamp,\n",
         'tests/auth_test.py::TokenFileAgeTest::test_a_faked_class_is_not_taken_for_a_mapping_or_a_number',
         'TypeError'),

    # The stored token is checked before a session is built on it. One case
    # per branch of _stored_token_for_session, and two for the classifier.
    Case('auth/stored-token-entry-must-be-a-mapping',
         'schwaby/auth.py',
         "    if not issubclass(type(token), collections.abc.Mapping):\n        raise ValueError(\n",
         "    if False:\n        raise ValueError(\n",
         'tests/auth_test.py::StoredTokenShapeTest::test_a_token_authlib_cannot_use_is_refused_at_build',
         'does not match'),

    Case('auth/stored-token-mapping-is-converted',
         'schwaby/auth.py',
         "    token = dict(token)\n",
         "    token = token\n",
         'tests/auth_test.py::StoredTokenShapeTest::test_a_mapping_that_is_not_a_dict_is_used_as_one',
         'is_expired'),

    Case('auth/stored-token-type-and-refresh-token-are-strings',
         'schwaby/auth.py',
         "        elif not issubclass(type(token[key]), str):",
         "        elif False:",
         'tests/auth_test.py::StoredTokenShapeTest::test_a_token_authlib_cannot_use_is_refused_at_build',
         'not raised'),

    Case('auth/stored-token-expires-in-refreshes-first',
         'schwaby/auth.py',
         "        token['expires_at'] = int(time.time())\n",
         "        pass\n",
         'tests/auth_test.py::StoredTokenShapeTest::test_expires_in_without_expires_at_refreshes_on_the_first_call',
         '1 != 0'),

    Case('auth/stored-token-expires-in-needs-a-refresh-token',
         'schwaby/auth.py',
         "    if token.get('refresh_token') and _expiry_needs_refreshing_first(token):",
         "    if _expiry_needs_refreshing_first(token):",
         'tests/auth_test.py::StoredTokenShapeTest::test_expires_in_without_expires_at_refreshes_on_the_first_call',
         'Error'),

    Case('client/stored-refresh-token-must-be-a-string',
         'schwaby/client/base.py',
         "                        and issubclass(type(refresh_token), str)\n",
         "",
         'tests/client_test.py::ClientTest::test_a_local_unsupported_token_type_without_expiry_is_terminal',
         'False is not true'),

    Case('client/stored-refresh-token-must-not-be-empty',
         'schwaby/client/base.py',
         "                        and refresh_token)",
         "                        and refresh_token is not None)",
         'tests/client_test.py::ClientTest::test_a_local_unsupported_token_type_without_expiry_is_terminal',
         'False is not true'),

    # A null token_type or refresh token counts as absent; authlib's own
    # expiry fallback is mirrored for a null or unparseable expires_at; and
    # the token passed in is copied, not changed.
    Case('auth/stored-token-null-counts-as-absent',
         'schwaby/auth.py',
         "        if token[key] is None:",
         "        if False:",
         'tests/auth_test.py::StoredTokenShapeTest::test_a_null_token_type_or_refresh_token_counts_as_absent',
         'is not a string'),

    Case('auth/stored-token-null-expiry-counts-from-build',
         'schwaby/auth.py',
         "    if expires_at is None:\n        return True",
         "    if expires_at is None:\n        return False",
         'tests/auth_test.py::StoredTokenShapeTest::test_expires_in_without_expires_at_refreshes_on_the_first_call',
         '1 != 0'),

    Case('auth/stored-token-unparseable-expiry-counts-from-build',
         'schwaby/auth.py',
         "        expires_at = int(expires_at)\n    except ValueError:\n        return True",
         "        expires_at = int(expires_at)\n    except ValueError:\n        return False",
         'tests/auth_test.py::StoredTokenShapeTest::test_expires_in_without_expires_at_refreshes_on_the_first_call',
         '1 != 0'),

    Case('auth/stored-token-caller-object-is-not-changed',
         'schwaby/auth.py',
         "    token = dict(token)\n",
         "    token = token if issubclass(type(token), dict) else dict(token)\n",
         'tests/auth_test.py::StoredTokenShapeTest::test_expires_in_without_expires_at_refreshes_on_the_first_call',
         '!='),

    # HeuristicJsonDecoder lets the decode error stand on a bytes frame, so
    # the client reports it as a frame that would not parse.
    Case('contrib/heuristic-decoder-bytes-keeps-the-decode-error',
         'schwaby/contrib/util.py',
         "            if isinstance(raw, (bytes, bytearray)):",
         "            if False:",
         'tests/contrib/util_test.py::HeuristicJsonDecoderBytesTest::test_a_bytes_frame_that_is_not_json_raises_the_decode_error',
         'TypeError'),

    # The first-call refresh applies only where authlib counts the expiry
    # from expires_in: not without one, and not for an expires_at authlib
    # refuses itself. One case per guard.
    Case('auth/first-call-refresh-needs-an-expires-in',
         'schwaby/auth.py',
         "    if not token.get('expires_in'):\n        return False",
         "    if False:\n        return False",
         'tests/auth_test.py::StoredTokenShapeTest::test_an_expiry_authlib_would_not_act_on_refreshes_first',
         '0 != 1',
         removed='The guard is gone: 4.6.0 refreshes first whenever authlib would not act on the expiry, with or without expires_in, because a token with no expiry at all is never refreshed either. auth/a-missing-expiry-refreshes-first covers the rule that replaced it.'),

    Case('auth/first-call-refresh-leaves-an-unreadable-expiry-to-authlib',
         'schwaby/auth.py',
         "    except Exception:\n        # authlib raises on this itself, and it is left to do so.\n        return False",
         "    except Exception:\n        # authlib raises on this itself, and it is left to do so.\n        return True",
         'tests/auth_test.py::StoredTokenShapeTest::test_an_expiry_authlib_would_not_act_on_refreshes_first',
         'TypeError not raised'),

    # A stored expiry authlib reads but further off than the seven-day bound
    # is taken as now when there is a refresh token, through the shared bound.
    Case('auth/stored-expiry-past-the-bound-refreshes-first',
         'schwaby/auth.py',
         "            or _expiry_too_far_off(token)):",
         "            or False):",
         'tests/auth_test.py::StoredTokenShapeTest::test_a_stored_expiry_past_seven_days_refreshes_on_the_first_call',
         '1 != 0',
         removed='The separate _expiry_too_far_off check was folded into _expiry_needs_refreshing_first; auth/stored-expiry-past-the-bound-uses-the-shared-bound mutates the bound it now asks.'),

    Case('auth/stored-expiry-past-the-bound-uses-the-shared-bound',
         'schwaby/auth.py',
         "    return not _expiry_authlib_acts_on(expires_at)",
         "    return expires_at > time.time() + 10 ** 9",
         'tests/auth_test.py::StoredTokenShapeTest::test_a_stored_expiry_past_seven_days_refreshes_on_the_first_call',
         '1 != 0'),

    # A refused login code exchange is LoginExchangeError, keeps its cause,
    # and crosses a process boundary with its parts.
    Case('auth/login-exchange-refusal-is-wrapped',
         'schwaby/auth.py',
         "    except AuthlibBaseError as e:\n        # The endpoint's refusal",
         "    except ZeroDivisionError as e:\n        # The endpoint's refusal",
         'tests/auth_test.py::LoginExchangeErrorTest::test_a_refused_code_exchange_is_a_login_exchange_error',
         'is not an instance'),

    Case('auth/login-exchange-error-keeps-its-cause',
         'schwaby/auth.py',
         "                _venue_text(e.error), _venue_text(e.description), e.uri) from e",
         "                _venue_text(e.error), _venue_text(e.description), e.uri) from None",
         'tests/auth_test.py::LoginExchangeErrorTest::test_a_refused_code_exchange_is_a_login_exchange_error',
         'is not',
         removed='4.6.0 stopped chaining authlib\'s error to LoginExchangeError: '
                 'its text is not escaped, and a logged traceback printed it. '
                 'auth/login-exchange-error-is-not-chained proves the reverse.'),

    Case('utils/login-exchange-error-pickles-its-parts',
         'schwaby/utils.py',
         "        return (type(self), (self.error, self.description, self.uri))",
         "        return Exception.__reduce__(self)",
         'tests/utils_test.py::UtilsTest::test_every_exception_survives_a_process_boundary',
         '!=',
         removed='LoginExchangeError.__reduce__ was removed: SchwabError.__reduce__ already rebuilds it, and the override dropped __notes__. utils/exceptions reconstruction cases cover it.'),

    # A caller's type is named without trusting its metaclass, in the enum
    # refusal, in _assert_type, and in _type_name itself.
    Case('utils/enum-refusal-names-a-hostile-type',
         'schwaby/utils.py',
         "                _type_name(value),\n                possible_members_message))",
         "                type(value).__name__,\n                possible_members_message))",
         'tests/utils_test.py::ConvertEnumIterableTest::test_a_type_whose_name_raises_is_still_refused_by_name',
         'metaclass raised'),

    Case('client/assert-type-names-a-hostile-type',
         'schwaby/client/base.py',
         "        value_type_name = _qualified_type_name(value)",
         "        value_type_name = type(value).__module__ + '.' + type(value).__name__",
         'tests/client_test.py::ClientTest::test_a_wrong_type_whose_name_raises_is_still_refused_by_name',
         'metaclass raised'),

    Case('utils/qualified-type-name-reads-the-module',
         'schwaby/utils.py',
         "        module = str.__str__(\n                type.__dict__['__module__'].__get__(type(value)))",
         "        module = '?'",
         'tests/client_test.py::ClientTest::test_a_wrong_type_whose_name_raises_is_still_refused_by_name',
         'not found in'),

    Case('utils/type-name-is-read-past-the-metaclass',
         'schwaby/utils.py',
         "        return str.__str__(type.__dict__['__name__'].__get__(type(value)))\n    except Exception:\n        return 'object'",
         "        return type(value).__name__\n    except Exception:\n        return 'object'",
         'tests/utils_test.py::FindAccountHashTest::test_a_type_name_is_read_without_trusting_a_metaclass',
         'not found in'),

    # execution_totals: only EXECUTION/FILL counts, numbers come from their
    # text, the average is weighted by quantity, and each refusal and fallback
    # has its own case.
    Case('exec-totals/only-a-fill-counts',
         'schwaby/utils.py',
         "        if str.__str__(execution_type) != 'FILL':\n            continue",
         "        if False:\n            continue",
         'tests/utils_test.py::ExecutionTotalsTest::test_a_canceled_execution_is_not_a_fill',
         '!='),

    Case('exec-totals/only-an-execution-counts',
         'schwaby/utils.py',
         "        if str.__str__(activity_type) != 'EXECUTION':\n            continue",
         "        if False:\n            continue",
         'tests/utils_test.py::ExecutionTotalsTest::test_an_activity_that_is_not_an_execution_is_skipped',
         'executionType'),

    Case('exec-totals/a-float-is-read-from-its-text',
         'schwaby/utils.py',
         "        value = decimal.Decimal(float.__repr__(value))",
         "        value = decimal.Decimal(value)",
         'tests/utils_test.py::ExecutionTotalsTest::test_a_float_is_read_from_its_text',
         "'0.15' !="),

    Case('exec-totals/the-average-is-divided-by-quantity',
         'schwaby/utils.py',
         "                _AVERAGE_CONTEXT.divide(amounts[leg_id], quantity)\n",
         "                amounts[leg_id]\n",
         'tests/utils_test.py::ExecutionTotalsTest::test_two_executions_average_by_quantity',
         '!='),

    Case('exec-totals/the-average-is-weighted-by-quantity',
         'schwaby/utils.py',
         "                    leg_id, decimal.Decimal(0)) + quantity * price",
         "                    leg_id, decimal.Decimal(0)) + price",
         'tests/utils_test.py::ExecutionTotalsTest::test_two_executions_average_by_quantity',
         '!='),

    Case('exec-totals/no-quantity-has-no-average',
         'schwaby/utils.py',
         "                if quantity else None)",
         "                if True else None)",
         'tests/utils_test.py::ExecutionTotalsTest::test_a_leg_whose_fills_add_up_to_nothing_has_no_average_price',
         'DivisionUndefined'),

    Case('exec-totals/a-nonzero-mismarked-quantity-is-refused',
         'schwaby/utils.py',
         "                    and _order_number(leg, 'mismarkedQuantity', at) != 0):",
         "                    and False):",
         'tests/utils_test.py::ExecutionTotalsTest::test_a_shape_it_cannot_read_is_refused',
         'not raised'),

    Case('exec-totals/a-missing-mismarked-quantity-counts-as-zero',
         'schwaby/utils.py',
         "            if (dict.get(leg, 'mismarkedQuantity') is not None\n",
         "            if (True\n",
         'tests/utils_test.py::ExecutionTotalsTest::test_a_missing_mismarked_quantity_counts_as_zero',
         'mismarkedQuantity'),

    Case('exec-totals/a-negative-quantity-is-refused',
         'schwaby/utils.py',
         "            if quantity < 0:",
         "            if False:",
         'tests/utils_test.py::ExecutionTotalsTest::test_a_shape_it_cannot_read_is_refused',
         'not raised'),

    Case('exec-totals/a-number-must-be-finite',
         'schwaby/utils.py',
         "    if not value.is_finite():",
         "    if False:",
         'tests/utils_test.py::ExecutionTotalsTest::test_a_shape_it_cannot_read_is_refused',
         'not raised'),

    Case('exec-totals/a-bool-leg-id-is-refused',
         'schwaby/utils.py',
         "            if (issubclass(type(leg_id), bool)\n                    or not issubclass(type(leg_id), int)):",
         "            if (False\n                    or not issubclass(type(leg_id), int)):",
         'tests/utils_test.py::ExecutionTotalsTest::test_a_shape_it_cannot_read_is_refused',
         'not raised'),

    Case('exec-totals/a-bool-number-is-refused',
         'schwaby/utils.py',
         "    if (issubclass(type(value), bool) or not issubclass(",
         "    if (False or not issubclass(",
         'tests/utils_test.py::ExecutionTotalsTest::test_a_shape_it_cannot_read_is_refused',
         'not raised'),

    Case('exec-totals/no-activity-is-no-fills',
         'schwaby/utils.py',
         "    if activities is None:\n        return {}",
         "    if False:\n        return {}",
         'tests/utils_test.py::ExecutionTotalsTest::test_an_order_with_no_fills_gives_an_empty_dict',
         'not a list'),

    Case('exec-totals/an-order-type-is-not-faked',
         'schwaby/utils.py',
         "    if not issubclass(type(order), dict):\n        raise UnusableOrderActivityError(",
         "    if not isinstance(order, dict):\n        raise UnusableOrderActivityError(",
         'tests/utils_test.py::ExecutionTotalsTest::test_a_faked_class_is_not_taken_for_the_real_type',
         "descriptor 'get'"),

    Case('exec-totals/a-leg-type-is-not-faked',
         'schwaby/utils.py',
         "            if not issubclass(type(leg), dict):",
         "            if not isinstance(leg, dict):",
         'tests/utils_test.py::ExecutionTotalsTest::test_a_faked_class_is_not_taken_for_the_real_type',
         "descriptor 'get'"),

    # A login redirect authlib refuses before any exchange, or one carrying
    # the authorization server's refusal, is LoginExchangeError too.
    Case('auth/login-redirect-refusal-is-reported',
         'schwaby/auth.py',
         "    if error:\n        raise LoginExchangeError(",
         "    if False:\n        raise LoginExchangeError(",
         'tests/auth_test.py::LoginExchangeErrorTest::test_a_redirect_that_is_refused_before_any_exchange_is_one_too',
         'a request was sent'),

    Case('auth/login-wraps-what-authlib-refuses-before-asking',
         'schwaby/auth.py',
         "    except AuthlibBaseError as e:",
         "    except OAuthError as e:",
         'tests/auth_test.py::LoginExchangeErrorTest::test_a_redirect_that_is_refused_before_any_exchange_is_one_too',
         'MismatchingStateException'),

    Case('auth/login-exchange-error-carries-the-uri',
         'schwaby/auth.py',
         "        raise LoginExchangeError(error, description, e.uri) from None",
         "        raise LoginExchangeError(error, description) from None",
         'tests/auth_test.py::LoginExchangeErrorTest::test_a_refused_code_exchange_is_a_login_exchange_error',
         '!= None'),

    # An expiry authlib would not act on is refreshed first: missing, zero,
    # unreadable, or past the bound. One case per branch.
    Case('auth/a-missing-expiry-refreshes-first',
         'schwaby/auth.py',
         "    expires_at = token.get('expires_at')\n    if expires_at is None:\n        return True",
         "    expires_at = token.get('expires_at')\n    if expires_at is None:\n        return False",
         'tests/auth_test.py::StoredTokenShapeTest::test_an_expiry_authlib_would_not_act_on_refreshes_first',
         '1 != 0'),

    # The module is read through type's descriptor, past a metaclass.
    Case('utils/qualified-type-name-reads-the-module-past-the-metaclass',
         'schwaby/utils.py',
         "type.__dict__['__module__'].__get__(type(value)))",
         "type(value).__module__)",
         'tests/client_test.py::ClientTest::test_a_wrong_type_whose_module_raises_is_still_refused_by_name',
         'metaclass raised'),

    # execution_totals adds up in its own decimal context, refuses an overflow,
    # and reads every value through the built-in types.
    Case('exec-totals/arithmetic-runs-in-its-own-context',
         'schwaby/utils.py',
         "        with decimal.localcontext(_EXECUTION_CONTEXT):\n            return _add_up_fills(activities)",
         "        if True:\n            return _add_up_fills(activities)",
         'tests/utils_test.py::ExecutionTotalsTest::test_the_callers_decimal_context_does_not_change_the_totals',
         'add up exactly'),

    Case('exec-totals/an-inexact-sum-is-refused',
         'schwaby/utils.py',
         "    except decimal.Inexact:",
         "    except decimal.Overflow:",
         'tests/utils_test.py::ExecutionTotalsTest::test_a_sum_is_exact_or_refused',
         'Inexact'),

    Case('exec-totals/an-overflow-is-refused',
         'schwaby/utils.py',
         "    except decimal.Inexact:",
         "    except ZeroDivisionError:",
         'tests/utils_test.py::ExecutionTotalsTest::test_a_number_too_large_to_add_up_is_refused',
         'Overflow'),

    Case('exec-totals/sums-trap-inexact',
         'schwaby/utils.py',
         "               decimal.Overflow, decimal.Inexact])",
         "               decimal.Overflow])",
         'tests/utils_test.py::ExecutionTotalsTest::test_a_sum_is_exact_or_refused',
         'not raised'),

    Case('exec-totals/sums-hold-60-digits',
         'schwaby/utils.py',
         "_EXECUTION_CONTEXT = decimal.Context(\n        prec=60,",
         "_EXECUTION_CONTEXT = decimal.Context(\n        prec=28,",
         'tests/utils_test.py::ExecutionTotalsTest::test_a_sum_is_exact_or_refused',
         'add up exactly'),

    Case('exec-totals/average-holds-60-digits',
         'schwaby/utils.py',
         "_AVERAGE_CONTEXT = decimal.Context(\n        prec=60,",
         "_AVERAGE_CONTEXT = decimal.Context(\n        prec=28,",
         'tests/utils_test.py::ExecutionTotalsTest::test_the_average_is_divided_to_60_digits',
         '60 != 28'),

    Case('exec-totals/average-traps-underflow',
         'schwaby/utils.py',
         "               decimal.Overflow, decimal.Underflow])",
         "               decimal.Overflow])",
         'tests/utils_test.py::ExecutionTotalsTest::test_an_average_too_large_or_small_to_hold_is_refused',
         'not raised'),

    Case('exec-totals/average-traps-overflow',
         'schwaby/utils.py',
         "               decimal.Overflow, decimal.Underflow])",
         "               decimal.Underflow])",
         'tests/utils_test.py::ExecutionTotalsTest::test_an_average_too_large_or_small_to_hold_is_refused',
         'not raised'),

    Case('exec-totals/average-rounds-half-even',
         'schwaby/utils.py',
         "_AVERAGE_CONTEXT = decimal.Context(\n        prec=60, rounding=decimal.ROUND_HALF_EVEN,",
         "_AVERAGE_CONTEXT = decimal.Context(\n        prec=60, rounding=decimal.ROUND_DOWN,",
         'tests/utils_test.py::ExecutionTotalsTest::test_the_average_is_divided_to_60_digits',
         '!='),

    Case('exec-totals/average-rounds-half-even-not-half-up',
         'schwaby/utils.py',
         "_AVERAGE_CONTEXT = decimal.Context(\n        prec=60, rounding=decimal.ROUND_HALF_EVEN,",
         "_AVERAGE_CONTEXT = decimal.Context(\n        prec=60, rounding=decimal.ROUND_HALF_UP,",
         'tests/utils_test.py::ExecutionTotalsTest::test_the_average_is_divided_to_60_digits',
         '!='),

    Case('auth/login-reports-the-venue-refusal-ahead-of-a-fragment',
         'schwaby/auth.py',
         "    if error:\n        raise LoginExchangeError(",
         "    if error and '#' not in received_url:\n        raise LoginExchangeError(",
         'tests/auth_test.py::LoginExchangeErrorTest::test_a_redirect_that_is_refused_before_any_exchange_is_one_too',
         '!='),

    Case('exec-totals/average-is-divided-in-its-own-context',
         'schwaby/utils.py',
         "                _AVERAGE_CONTEXT.divide(amounts[leg_id], quantity)\n",
         "                (amounts[leg_id] / quantity)\n",
         'tests/utils_test.py::ExecutionTotalsTest::test_the_average_is_divided_to_60_digits',
         'add up exactly'),

    Case('exec-totals/activity-type-is-read-through-dict',
         'schwaby/utils.py',
         "        activity_type = dict.get(activity, 'activityType')",
         "        activity_type = activity.get('activityType')",
         'tests/utils_test.py::ExecutionTotalsTest::test_subclasses_are_read_through_the_built_in_types',
         'activityType string'),

    Case('exec-totals/execution-type-is-read-through-dict',
         'schwaby/utils.py',
         "        execution_type = dict.get(activity, 'executionType')",
         "        execution_type = activity.get('executionType')",
         'tests/utils_test.py::ExecutionTotalsTest::test_subclasses_are_read_through_the_built_in_types',
         'executionType string'),

    Case('exec-totals/execution-legs-are-read-through-dict',
         'schwaby/utils.py',
         "        legs = dict.get(activity, 'executionLegs')",
         "        legs = activity.get('executionLegs')",
         'tests/utils_test.py::ExecutionTotalsTest::test_subclasses_are_read_through_the_built_in_types',
         'executionLegs list'),

    Case('exec-totals/leg-id-is-read-through-dict',
         'schwaby/utils.py',
         "            leg_id = dict.get(leg, 'legId')",
         "            leg_id = leg.get('legId')",
         'tests/utils_test.py::ExecutionTotalsTest::test_subclasses_are_read_through_the_built_in_types',
         'integer legId'),

    Case('exec-totals/a-leg-number-is-read-through-dict',
         'schwaby/utils.py',
         "    value = dict.get(leg, key)",
         "    value = leg.get(key)",
         'tests/utils_test.py::ExecutionTotalsTest::test_subclasses_are_read_through_the_built_in_types',
         'quantity number'),

    Case('exec-totals/a-mismarked-quantity-is-read-through-dict',
         'schwaby/utils.py',
         "            if (dict.get(leg, 'mismarkedQuantity') is not None\n",
         "            if (leg.get('mismarkedQuantity') is not None\n",
         'tests/utils_test.py::ExecutionTotalsTest::test_subclasses_are_read_through_the_built_in_types',
         'not raised'),

    Case('exec-totals/an-int-subclass-is-read-by-value-not-int',
         'schwaby/utils.py',
         "        value = decimal.Decimal(value)\n",
         "        value = decimal.Decimal(int(value))\n",
         'tests/utils_test.py::ExecutionTotalsTest::test_subclasses_are_read_through_the_built_in_types',
         '!='),

    Case('exec-totals/an-int-subclass-is-read-by-value-not-str',
         'schwaby/utils.py',
         "        value = decimal.Decimal(value)\n",
         "        value = decimal.Decimal(str(value))\n",
         'tests/utils_test.py::ExecutionTotalsTest::test_subclasses_are_read_through_the_built_in_types',
         '!='),

    Case('exec-totals/a-null-mismarked-quantity-counts-as-zero',
         'schwaby/utils.py',
         "            if (dict.get(leg, 'mismarkedQuantity') is not None\n",
         "            if ('mismarkedQuantity' in leg\n",
         'tests/utils_test.py::ExecutionTotalsTest::test_a_null_mismarked_quantity_counts_as_zero_and_a_negative_is_not',
         'mismarkedQuantity'),

    Case('exec-totals/a-negative-mismarked-quantity-is-refused',
         'schwaby/utils.py',
         "                    and _order_number(leg, 'mismarkedQuantity', at) != 0):",
         "                    and _order_number(leg, 'mismarkedQuantity', at) > 0):",
         'tests/utils_test.py::ExecutionTotalsTest::test_a_null_mismarked_quantity_counts_as_zero_and_a_negative_is_not',
         'not raised'),

    Case('exec-totals/a-decimal-number-is-accepted',
         'schwaby/utils.py',
         "        # subclass's overrides.\n        value = decimal.Decimal(value)",
         "        # subclass's overrides.\n        raise UnusableOrderActivityError('refused')",
         'tests/utils_test.py::ExecutionTotalsTest::test_decimal_numbers_give_the_same_totals',
         'refused'),

    Case('exec-totals/an-empty-string-is-not-an-absent-collection',
         'schwaby/utils.py',
         "    if activities is None:\n        return {}",
         "    if not activities:\n        return {}",
         'tests/utils_test.py::ExecutionTotalsTest::test_an_empty_string_is_not_an_absent_collection',
         'not raised'),

    Case('exec-totals/activities-are-read-through-list',
         'schwaby/utils.py',
         "            list.__getitem__(activities, slice(None)), 1):",
         "            activities, 1):",
         'tests/utils_test.py::ExecutionTotalsTest::test_subclasses_are_read_through_the_built_in_types',
         'KeyError'),

    Case('exec-totals/legs-are-read-through-list',
         'schwaby/utils.py',
         "        for e, leg in enumerate(list.__getitem__(legs, slice(None)), 1):",
         "        for e, leg in enumerate(legs, 1):",
         'tests/utils_test.py::ExecutionTotalsTest::test_subclasses_are_read_through_the_built_in_types',
         'KeyError'),

    Case('exec-totals/activity-type-is-read-as-plain-text',
         'schwaby/utils.py',
         "        if str.__str__(activity_type) != 'EXECUTION':",
         "        if activity_type != 'EXECUTION':",
         'tests/utils_test.py::ExecutionTotalsTest::test_subclasses_are_read_through_the_built_in_types',
         '!='),

    Case('exec-totals/execution-type-is-read-as-plain-text',
         'schwaby/utils.py',
         "        if str.__str__(execution_type) != 'FILL':",
         "        if execution_type != 'FILL':",
         'tests/utils_test.py::ExecutionTotalsTest::test_subclasses_are_read_through_the_built_in_types',
         '!='),

    Case('exec-totals/a-float-subclass-is-read-through-float',
         'schwaby/utils.py',
         "        value = decimal.Decimal(float.__repr__(value))",
         "        value = decimal.Decimal(repr(value))",
         'tests/utils_test.py::ExecutionTotalsTest::test_subclasses_are_read_through_the_built_in_types',
         '!='),

    Case('exec-totals/the-order-is-read-through-dict',
         'schwaby/utils.py',
         "    activities = dict.get(order, 'orderActivityCollection')",
         "    activities = order.get('orderActivityCollection')",
         'tests/utils_test.py::ExecutionTotalsTest::test_subclasses_are_read_through_the_built_in_types',
         'KeyError'),

    Case('exec-totals/the-leg-id-comes-back-a-plain-int',
         'schwaby/utils.py',
         "            leg_id = int.__int__(leg_id)\n",
         "",
         'tests/utils_test.py::ExecutionTotalsTest::test_subclasses_are_read_through_the_built_in_types',
         'is not'),

    # A login redirect is refused before any request on authlib's own tests:
    # a fragment, no "code=" in the raw URL; and a query with no code key.
    # Refusal text from the redirect or the endpoint is escaped and cut, and
    # authlib's error is not chained.
    Case('auth/login-redirect-with-a-fragment-is-refused',
         'schwaby/auth.py',
         "    if '#' in received_url:\n",
         "    if False:\n",
         'tests/auth_test.py::LoginExchangeErrorTest::test_a_redirect_that_is_refused_before_any_exchange_is_one_too',
         'not raised'),

    Case('auth/login-redirect-without-code-in-the-raw-url-is-refused',
         'schwaby/auth.py',
         "    if 'code=' not in received_url or 'code' not in query:",
         "    if 'code' not in query:",
         'tests/auth_test.py::LoginExchangeErrorTest::test_a_redirect_that_is_refused_before_any_exchange_is_one_too',
         'a request was sent'),

    Case('auth/login-redirect-without-a-code-key-is-refused',
         'schwaby/auth.py',
         "    if 'code=' not in received_url or 'code' not in query:",
         "    if 'code=' not in received_url:",
         'tests/auth_test.py::LoginExchangeErrorTest::test_a_redirect_that_is_refused_before_any_exchange_is_one_too',
         'carries no authorization code'),

    Case('auth/login-redirect-text-is-escaped',
         'schwaby/auth.py',
         "                _venue_text(error),\n",
         "                error,\n",
         'tests/auth_test.py::LoginExchangeErrorTest::test_refusal_text_is_escaped_and_cut',
         '!='),

    Case('auth/login-endpoint-description-is-escaped',
         'schwaby/utils.py',
         '        description = _venue_text(error.description)\n    return _venue_text(error.error), description\n',
         '        description = error.description\n    return _venue_text(error.error), description\n',
         'tests/auth_test.py::LoginExchangeErrorTest::test_refusal_text_is_escaped_and_cut',
         '!='),

    Case('auth/login-endpoint-error-is-escaped',
         'schwaby/utils.py',
         '    return _venue_text(error.error), description\n',
         '    return error.error, description\n',
         'tests/auth_test.py::LoginExchangeErrorTest::test_refusal_text_is_escaped_and_cut',
         '!='),

    Case('auth/login-exchange-error-is-not-chained',
         'schwaby/auth.py',
         '        raise LoginExchangeError(error, description, e.uri) from None',
         '        raise LoginExchangeError(error, description, e.uri) from e',
         'tests/auth_test.py::LoginExchangeErrorTest::test_refusal_text_is_escaped_and_cut',
         'unexpectedly found'),

    Case('auth/venue-text-keeps-none',
         'schwaby/utils.py',
         '    if value is None:\n        return None\n    if issubclass(type(value), str):\n        text = str.__str__(value)',
         '    if issubclass(type(value), str):\n        text = str.__str__(value)',
         'tests/auth_test.py::LoginExchangeErrorTest::test_refusal_text_that_is_not_a_plain_string_is_escaped_and_cut',
         "'' != 'None'"),

    Case('auth/venue-text-reads-a-string-past-its-class',
         'schwaby/utils.py',
         '    if issubclass(type(value), str):\n        text = str.__str__(value)\n    else:\n        text = repr(value)',
         '    if isinstance(value, str):\n        text = str.__str__(value)\n    else:\n        text = repr(value)',
         'tests/auth_test.py::LoginExchangeErrorTest::test_refusal_text_that_is_not_a_plain_string_is_escaped_and_cut',
         "requires a 'str' object"),

    Case('auth/venue-text-reads-a-subclass-as-plain-text',
         'schwaby/utils.py',
         '        text = str.__str__(value)\n    else:\n        text = repr(value)',
         '        text = value\n    else:\n        text = repr(value)',
         'tests/auth_test.py::LoginExchangeErrorTest::test_refusal_text_that_is_not_a_plain_string_is_escaped_and_cut',
         '!=',
         superseded='before_cut, which every bounded value now passes '
                    'through, makes the text a plain str itself'),

    Case('auth/venue-text-takes-a-non-string-as-its-repr',
         'schwaby/utils.py',
         '    else:\n        text = repr(value)\n    # Redacted before it is cut',
         '    else:\n        return value\n    # Redacted before it is cut',
         'tests/auth_test.py::LoginExchangeErrorTest::test_refusal_text_that_is_not_a_plain_string_is_escaped_and_cut',
         'is not'),

    Case('auth/login-keeps-this-librarys-unusable-description',
         'schwaby/utils.py',
         '        description = error.description\n    else:',
         '        description = _venue_text(error.description)\n    else:',
         'tests/auth_test.py::LoginExchangeErrorTest::test_an_unusable_token_response_keeps_this_librarys_description',
         'False is not true'),

    Case('auth/login-tells-its-own-refusal-apart-by-its-mark',
         'schwaby/utils.py',
         "    if vars(error).get('_described_by_this_library') is True:",
         "    if error.error == 'unusable_token_response':",
         'tests/auth_test.py::LoginExchangeErrorTest::test_an_unusable_token_response_keeps_this_librarys_description',
         '!='),

    Case('auth/unusable-token-response-carries-its-mark',
         'schwaby/auth.py',
         "    # traceback prints.\n    refusal._described_by_this_library = True\n",
         "    # traceback prints.\n",
         'tests/auth_test.py::LoginExchangeErrorTest::test_an_unusable_token_response_keeps_this_librarys_description',
         'False is not true'),

    Case('auth/login-refusal-text-is-cut',
         'schwaby/utils.py',
         "    return _printable(text if len(text) <= 200 else text[:197] + '...', 200)",
         '    return _printable(text, 10 ** 9)',
         'tests/auth_test.py::LoginExchangeErrorTest::test_refusal_text_is_escaped_and_cut',
         'not less than or equal to 200'),

    # 4.6.1: a value whose __class__ raises is refused like any instance of
    # its class, one case per type check that reads a caller's value; and a
    # refresh failure escapes the endpoint's text without replacing the error
    # it chains.
    Case('hostile-class/utils-line-36-col-11',
         'schwaby/utils.py',
         '        if _is_instance(value, str):\n',
         '        if isinstance(value, str):\n',
         'tests/utils_test.py::HostileClassTest::test_a_class_that_raises_is_refused_like_any_other_instance',
         'RuntimeError'),

    Case('hostile-class/utils-line-60-col-11',
         'schwaby/utils.py',
         '        if _is_instance(value, required_enum_type):\n            return value.value\n',
         '        if isinstance(value, required_enum_type):\n            return value.value\n',
         'tests/utils_test.py::HostileClassTest::test_a_class_that_raises_is_refused_like_any_other_instance',
         'RuntimeError'),

    Case('hostile-class/utils-line-71-col-11',
         'schwaby/utils.py',
         '        if _is_instance(iterable, required_enum_type):\n',
         '        if isinstance(iterable, required_enum_type):\n',
         'tests/utils_test.py::HostileClassTest::test_a_class_that_raises_is_refused_like_any_other_instance',
         'RuntimeError'),

    Case('hostile-class/utils-line-76-col-15',
         'schwaby/utils.py',
         '            if _is_instance(value, required_enum_type):\n',
         '            if isinstance(value, required_enum_type):\n',
         'tests/utils_test.py::HostileClassTest::test_a_class_that_raises_is_refused_like_any_other_instance',
         'RuntimeError'),

    Case('hostile-class/auth-line-366-col-11',
         'schwaby/auth.py',
         '    if not _is_instance(value, str):\n',
         '    if not isinstance(value, str):\n',
         'tests/utils_test.py::HostileClassTest::test_a_class_that_raises_is_refused_like_any_other_instance',
         'RuntimeError'),

    Case('hostile-class/client-line-265-col-19',
         'schwaby/client/base.py',
         '        if not any(_is_instance(value, t) for t in exp_types):\n',
         '        if not any(isinstance(value, t) for t in exp_types):\n',
         'tests/utils_test.py::HostileClassTest::test_a_class_that_raises_is_refused_like_any_other_instance',
         'RuntimeError'),

    Case('hostile-class/client-line-797-col-11',
         'schwaby/client/base.py',
         '        if _is_instance(symbols, str):\n            symbols = [symbols]\n\n        params = {\n',
         '        if isinstance(symbols, str):\n            symbols = [symbols]\n\n        params = {\n',
         'tests/utils_test.py::HostileClassTest::test_a_class_that_raises_is_refused_like_any_other_instance',
         'RuntimeError'),

    Case('hostile-class/client-line-1543-col-11',
         'schwaby/client/base.py',
         '        if _is_instance(symbols, str):\n            symbols = [symbols]\n\n        projection = self.convert_enum(projection, self.Instrument.Projection)\n',
         '        if isinstance(symbols, str):\n            symbols = [symbols]\n\n        projection = self.convert_enum(projection, self.Instrument.Projection)\n',
         'tests/utils_test.py::HostileClassTest::test_a_class_that_raises_is_refused_like_any_other_instance',
         'RuntimeError'),

    Case('hostile-class/client-line-1562-col-15',
         'schwaby/client/base.py',
         '        if not _is_instance(cusip, str):\n',
         '        if not isinstance(cusip, str):\n',
         'tests/utils_test.py::HostileClassTest::test_a_class_that_raises_is_refused_like_any_other_instance',
         'RuntimeError'),

    Case('hostile-class/orders-line-14-col-7',
         'schwaby/orders/generic.py',
         '    if _is_instance(obj, (str, int, float)):\n',
         '    if isinstance(obj, (str, int, float)):\n',
         'tests/utils_test.py::HostileClassTest::test_a_class_that_raises_is_refused_like_any_other_instance',
         'RuntimeError'),

    Case('hostile-class/orders-line-27-col-9',
         'schwaby/orders/generic.py',
         '    elif _is_instance(obj, decimal.Decimal):\n',
         '    elif isinstance(obj, decimal.Decimal):\n',
         'tests/utils_test.py::HostileClassTest::test_a_class_that_raises_is_refused_like_any_other_instance',
         'RuntimeError'),

    Case('hostile-class/orders-line-39-col-9',
         'schwaby/orders/generic.py',
         '    elif _is_instance(obj, dict):\n',
         '    elif isinstance(obj, dict):\n',
         'tests/utils_test.py::HostileClassTest::test_a_class_that_raises_is_refused_like_any_other_instance',
         'RuntimeError'),

    Case('hostile-class/orders-line-41-col-9',
         'schwaby/orders/generic.py',
         '    elif _is_instance(obj, list):\n',
         '    elif isinstance(obj, list):\n',
         'tests/utils_test.py::HostileClassTest::test_a_class_that_raises_is_refused_like_any_other_instance',
         'RuntimeError'),

    Case('hostile-class/orders-line-96-col-7',
         'schwaby/orders/generic.py',
         '    if _is_instance(price, decimal.Decimal):\n        if price.is_nan() or price.is_infinite():\n',
         '    if isinstance(price, decimal.Decimal):\n        if price.is_nan() or price.is_infinite():\n',
         'tests/utils_test.py::HostileClassTest::test_a_class_that_raises_is_refused_like_any_other_instance',
         'RuntimeError'),

    Case('hostile-class/orders-line-111-col-7',
         'schwaby/orders/generic.py',
         '    if _is_instance(price, str):\n',
         '    if isinstance(price, str):\n',
         'tests/utils_test.py::HostileClassTest::test_a_class_that_raises_is_refused_like_any_other_instance',
         'RuntimeError'),

    Case('hostile-class/orders-line-189-col-11',
         'schwaby/orders/generic.py',
         '        if _is_instance(price, bool) or not _is_instance(price, (int, float)):\n',
         '        if isinstance(price, bool) or not _is_instance(price, (int, float)):\n',
         'tests/utils_test.py::HostileClassTest::test_a_class_that_raises_is_refused_like_any_other_instance',
         'RuntimeError'),

    Case('hostile-class/orders-line-189-col-44',
         'schwaby/orders/generic.py',
         '        if _is_instance(price, bool) or not _is_instance(price, (int, float)):\n',
         '        if _is_instance(price, bool) or not isinstance(price, (int, float)):\n',
         'tests/utils_test.py::HostileClassTest::test_a_class_that_raises_is_refused_like_any_other_instance',
         'RuntimeError'),

    Case('hostile-class/orders-line-226-col-7',
         'schwaby/orders/generic.py',
         '    if _is_instance(value, decimal.Decimal):\n',
         '    if isinstance(value, decimal.Decimal):\n',
         'tests/utils_test.py::HostileClassTest::test_a_class_that_raises_is_refused_like_any_other_instance',
         'RuntimeError'),

    Case('hostile-class/orders-line-266-col-7',
         'schwaby/orders/generic.py',
         "    if _is_instance(price, decimal.Decimal):\n        # format() renders NaN and the infinities as 'sNaN'/'Infinity', and\n",
         "    if isinstance(price, decimal.Decimal):\n        # format() renders NaN and the infinities as 'sNaN'/'Infinity', and\n",
         'tests/utils_test.py::HostileClassTest::test_a_class_that_raises_is_refused_like_any_other_instance',
         'RuntimeError'),

    Case('hostile-class/orders-line-279-col-11',
         'schwaby/orders/generic.py',
         '    if not _is_instance(price, str):\n',
         '    if not isinstance(price, str):\n',
         'tests/utils_test.py::HostileClassTest::test_a_class_that_raises_is_refused_like_any_other_instance',
         'RuntimeError'),

    Case('hostile-class/orders-line-723-col-11',
         'schwaby/orders/generic.py',
         '        if _is_instance(child_order_strategy, httpx2.Response):\n',
         '        if isinstance(child_order_strategy, httpx2.Response):\n',
         'tests/utils_test.py::HostileClassTest::test_a_class_that_raises_is_refused_like_any_other_instance',
         'RuntimeError'),

    Case('hostile-class/orders-line-729-col-16',
         'schwaby/orders/generic.py',
         '        if (not _is_instance(child_order_strategy, OrderBuilder)\n',
         '        if (not isinstance(child_order_strategy, OrderBuilder)\n',
         'tests/utils_test.py::HostileClassTest::test_a_class_that_raises_is_refused_like_any_other_instance',
         'RuntimeError'),

    Case('hostile-class/orders-line-730-col-24',
         'schwaby/orders/generic.py',
         '                and not _is_instance(child_order_strategy, dict)):\n',
         '                and not isinstance(child_order_strategy, dict)):\n',
         'tests/utils_test.py::HostileClassTest::test_a_class_that_raises_is_refused_like_any_other_instance',
         'RuntimeError'),

    Case('hostile-class/options-line-109-col-11',
         'schwaby/orders/options.py',
         '        if _is_instance(expiration_date, str):\n',
         '        if isinstance(expiration_date, str):\n',
         'tests/utils_test.py::HostileClassTest::test_a_class_that_raises_is_refused_like_any_other_instance',
         'RuntimeError'),

    Case('hostile-class/options-line-111-col-13',
         'schwaby/orders/options.py',
         '        elif _is_instance(expiration_date, datetime.datetime):\n',
         '        elif isinstance(expiration_date, datetime.datetime):\n',
         'tests/utils_test.py::HostileClassTest::test_a_class_that_raises_is_refused_like_any_other_instance',
         'RuntimeError'),

    Case('hostile-class/options-line-116-col-13',
         'schwaby/orders/options.py',
         '        elif _is_instance(expiration_date, datetime.date):\n',
         '        elif isinstance(expiration_date, datetime.date):\n',
         'tests/utils_test.py::HostileClassTest::test_a_class_that_raises_is_refused_like_any_other_instance',
         'RuntimeError'),

    Case('hostile-class/options-line-139-col-34',
         'schwaby/orders/options.py',
         '        if (strike is None or not _is_instance(strike_price_as_string, str)\n',
         '        if (strike is None or not isinstance(strike_price_as_string, str)\n',
         'tests/utils_test.py::HostileClassTest::test_a_class_that_raises_is_refused_like_any_other_instance',
         'RuntimeError'),

    Case('hostile-class/streaming-line-653-col-15',
         'schwaby/streaming.py',
         '        if not _is_instance(json_decoder, StreamJsonDecoder):\n',
         '        if not isinstance(json_decoder, StreamJsonDecoder):\n',
         'tests/utils_test.py::HostileClassTest::test_a_class_that_raises_is_refused_like_any_other_instance',
         'RuntimeError'),

    Case('hostile-class/the-real-type-answers-when-isinstance-raises',
         'schwaby/utils.py',
         '    except Exception:\n        return issubclass(type(value), types)\n',
         '    except Exception:\n        raise\n',
         'tests/utils_test.py::HostileClassTest::test_a_class_that_raises_is_refused_like_any_other_instance',
         'RuntimeError'),

    Case('hostile-class/isinstance-still-answers-first',
         'schwaby/utils.py',
         '    try:\n        return isinstance(value, types)\n    except Exception:\n        return issubclass(type(value), types)\n',
         '    try:\n        return issubclass(type(value), types)\n    except Exception:\n        return issubclass(type(value), types)\n',
         'tests/utils_test.py::HostileClassTest::test_a_mock_with_a_spec_still_passes',
         'False is not true'),

    Case('refresh/the-chained-error-prints-escaped-text',
         'schwaby/client/base.py',
         '            e.args = (text,)\n',
         '',
         'tests/client_test.py::ClientTest::test_token_refresh_error_escapes_the_endpoints_text',
         'unexpectedly found'),

    Case('refresh/the-message-escapes-the-endpoints-text',
         'schwaby/client/base.py',
         "            text = '{}: {}'.format(*_refusal_text(e))\n",
         "            text = '{}: {}'.format(e.error, e.description)\n",
         'tests/client_test.py::ClientTest::test_token_refresh_error_escapes_the_endpoints_text',
         'not found in'),

    Case('refresh/keeps-this-librarys-own-description',
         'schwaby/utils.py',
         '        description = error.description\n    else:\n        description = _venue_text(error.description)\n',
         '        description = _venue_text(error.description)\n    else:\n        description = _venue_text(error.description)\n',
         'tests/client_test.py::ClientTest::test_token_refresh_error_keeps_this_librarys_own_description',
         'not found in'),

    # 4.7.0: the public token-file writer leaves the file the library reads,
    # and refuses a path that is not one before any login.
    Case('auth/token-file-writer-writes-the-librarys-token-file',
         'schwaby/auth.py',
         '    return __make_update_token_func(token_path)\n',
         '    return lambda token, *args, **kwargs: None\n',
         'tests/auth_test.py::TokenFileWriterTest::test_a_login_finished_with_it_leaves_a_file_the_library_reads',
         'No such file or directory'),

    Case('auth/token-file-writer-refuses-a-non-path-at-once',
         'schwaby/auth.py',
         '    path = os.fspath(token_path)\n',
         '    path = token_path\n',
         'tests/auth_test.py::TokenFileWriterTest::test_a_path_that_is_not_a_path_is_refused_before_any_login',
         'TypeError'),

    # 4.7.0 round 1: the writer refuses bytes, empty and directory paths; the
    # date checks; and the fallback an abstract base needs.
    Case('auth/token-file-writer-refuses-a-bytes-path',
         'schwaby/auth.py',
         '    if not issubclass(type(path), str):\n        raise TypeError(',
         '    if False:\n        raise TypeError(',
         'tests/auth_test.py::TokenFileWriterTest::test_a_path_that_is_not_a_path_is_refused_before_any_login',
         'does not match'),

    Case('auth/token-file-writer-refuses-an-empty-path',
         'schwaby/auth.py',
         '    if not path or os.path.isdir(path):',
         '    if os.path.isdir(path):',
         'tests/auth_test.py::TokenFileWriterTest::test_a_path_that_is_not_a_path_is_refused_before_any_login',
         'ValueError not raised',
         removed='The empty-path clause was dropped in 4.7.0 round 3: an empty path resolves to the working directory, which the resolved-path directory check refuses.'),

    Case('auth/token-file-writer-refuses-a-directory',
         'schwaby/auth.py',
         '    resolved = os.path.realpath(path)\n    if os.path.isdir(resolved):\n',
         '    resolved = os.path.realpath(path)\n    if False:\n',
         'tests/auth_test.py::TokenFileWriterTest::test_a_path_that_is_not_a_path_is_refused_before_any_login',
         'ValueError not raised'),

    Case('hostile-class/client-naive-datetime-warning',
         'schwaby/client/base.py',
         '        if _is_instance(dt, self._DATETIME) '
         'and dt.utcoffset() is None:',
         '        if isinstance(dt, self._DATETIME) '
         'and dt.utcoffset() is None:',
         'tests/utils_test.py::HostileClassTest::test_a_class_that_raises_is_refused_like_any_other_instance',
         'RuntimeError'),

    # The widening moved to utils._format_iso_instant so the order builder can
    # share it; the test still reaches it through the client's date parameters.
    Case('hostile-class/client-iso-date-formatter',
         'schwaby/utils.py',
         '    if not _is_instance(dt, _DATETIME):',
         '    if not isinstance(dt, _DATETIME):',
         'tests/utils_test.py::HostileClassTest::test_a_class_that_raises_is_refused_like_any_other_instance',
         'RuntimeError'),

    # Had no case before it moved to utils. Dropping the conversion sends
    # another zone's wall clock labelled as UTC.
    Case('dates/aware-datetime-is-converted-to-utc',
         'schwaby/utils.py',
         '        dt = dt.astimezone(_datetime.timezone.utc)\n',
         '        pass\n',
         'tests/client_test.py::ClientTest::test_orders_aware_datetime_is_converted_to_utc',
         # The unconverted wall clock, which only a dropped conversion sends.
         '2024-06-05T00:03:02Z'),

    # The other branch of the same check. Converting a naive datetime reads it
    # as the host's local time, which on a UTC host -- every CI runner -- gives
    # the identical string, so the test pins a non-UTC zone itself.
    Case('dates/naive-datetime-is-sent-as-written',
         'schwaby/utils.py',
         '    if dt.utcoffset() is not None:\n        dt = dt.astimezone(',
         '    if True:\n        dt = dt.astimezone(',
         'tests/client_test.py::ClientTest::test_a_naive_datetime_is_sent_as_written_not_as_local_time',
         '2024-06-05T08:03:02Z'),

    # OrderBuilder.set_cancel_time. One case per branch of _cancel_time_string.
    Case('cancel-time/refuses-a-naive-datetime',
         'schwaby/orders/generic.py',
         '    if dt.utcoffset() is None:\n        raise ValueError(\n'
         '            "cancel time must carry a timezone',
         '    if False:\n        raise ValueError(\n'
         '            "cancel time must carry a timezone',
         'tests/orders/generic_test.py::OrderBuilderTest'
         '::test_cancel_time_refuses_a_naive_datetime',
         'ValueError not raised'),

    # Awareness is utcoffset(), not tzinfo: a tzinfo whose offset is None is
    # naive, and would otherwise reach the UTC conversion as local time.
    Case('cancel-time/awareness-is-the-offset-not-the-tzinfo',
         'schwaby/orders/generic.py',
         '    if dt.utcoffset() is None:\n',
         '    if dt.tzinfo is None:\n',
         'tests/orders/generic_test.py::OrderBuilderTest'
         '::test_cancel_time_refuses_a_tzinfo_that_gives_no_offset',
         'ValueError not raised'),

    # Widened to datetime.date (datetime's base). A date then passes the type
    # check and fails on utcoffset, which it does not have -- a crash where
    # the caller should have been told what to pass.
    Case('cancel-time/a-date-is-refused',
         'schwaby/orders/generic.py',
         '    if not _is_instance(dt, _DATETIME):\n        raise ValueError(\n'
         '            "cancel time',
         '    if not _is_instance(dt, _DATETIME.__base__):\n        raise ValueError(\n'
         '            "cancel time',
         'tests/orders/generic_test.py::OrderBuilderTest'
         '::test_cancel_time_refuses_a_date',
         "no attribute 'utcoffset'"),

    Case('hostile-class/cancel-time',
         'schwaby/orders/generic.py',
         '    if not _is_instance(dt, _DATETIME):\n        raise ValueError(\n'
         '            "cancel time',
         '    if not isinstance(dt, _DATETIME):\n        raise ValueError(\n'
         '            "cancel time',
         'tests/utils_test.py::HostileClassTest::test_a_class_that_raises_is_refused_like_any_other_instance',
         # The subtest header pytest prints, bracketed. The bare name also
         # appears in the test's own source, which the traceback lists, so any
         # failing subtest in that loop would have matched it.
         '[set_cancel_time]'),

    # Reading datetime.datetime at call time rather than the capture. Needs
    # the import the mutated line uses, or it fails on NameError and proves
    # nothing.
    Case('cancel-time/checks-the-class-captured-at-import',
         'schwaby/orders/generic.py',
         '    if not _is_instance(dt, _DATETIME):\n        raise ValueError(\n'
         '            "cancel time',
         '    if not _is_instance(dt, datetime.datetime):\n        raise ValueError(\n'
         '            "cancel time',
         'tests/orders/generic_test.py::OrderBuilderTest'
         '::test_cancel_time_accepts_a_datetime_under_the_class_patch',
         # The rendered exception rather than the message template, which the
         # traceback's source listing also shows.
         "got 'datetime.datetime'",
         also=('import decimal\nimport math\n',
               'import datetime\nimport decimal\nimport math\n')),

    # The builder must go through the shared conversion rather than format
    # the wall clock itself, or a non-UTC cancel time is sent hours off.
    Case('cancel-time/sent-through-the-shared-utc-conversion',
         'schwaby/orders/generic.py',
         '    return _format_iso_instant(dt)\n',
         "    return dt.strftime('%Y-%m-%dT%H:%M:%SZ')\n",
         'tests/orders/generic_test.py::OrderBuilderTest'
         '::test_cancel_time_is_sent_in_utc',
         '2027-06-18T16:00:00Z'),

    # Documented success codes. One case per code, and one per place that
    # decides success, so no path can disagree with another unnoticed.
    Case('success-codes/26-is-a-success',
         'schwaby/streaming.py',
         '_SUCCESS_CODES = (0, 26, 27, 28, 29)\n',
         '_SUCCESS_CODES = (0, 27, 28, 29)\n',
         'tests/streaming_test.py::StreamClientTest'
         '::test_response_code_26_is_a_success',
         'unexpected response code: 26'),

    Case('success-codes/27-is-a-success',
         'schwaby/streaming.py',
         '_SUCCESS_CODES = (0, 26, 27, 28, 29)\n',
         '_SUCCESS_CODES = (0, 26, 28, 29)\n',
         'tests/streaming_test.py::StreamClientTest'
         '::test_response_code_27_is_a_success',
         'unexpected response code: 27'),

    Case('success-codes/28-is-a-success',
         'schwaby/streaming.py',
         '_SUCCESS_CODES = (0, 26, 27, 28, 29)\n',
         '_SUCCESS_CODES = (0, 26, 27, 29)\n',
         'tests/streaming_test.py::StreamClientTest'
         '::test_response_code_28_is_a_success',
         'unexpected response code: 28'),

    Case('success-codes/29-is-a-success',
         'schwaby/streaming.py',
         '_SUCCESS_CODES = (0, 26, 27, 28, 29)\n',
         '_SUCCESS_CODES = (0, 26, 27, 28)\n',
         'tests/streaming_test.py::StreamClientTest'
         '::test_response_code_29_is_a_success',
         'unexpected response code: 29'),

    Case('success-codes/the-request-path-asks-the-predicate',
         'schwaby/streaming.py',
         '        if not _is_success_code(resp_code):\n',
         '        if resp_code != 0:\n',
         'tests/streaming_test.py::StreamClientTest'
         '::test_response_code_26_is_a_success',
         'unexpected response code: 26'),

    Case('success-codes/orphan-path-report',
         'schwaby/streaming.py',
         '                if code is not None and not _is_success_code(code):\n'
         '                    # msg, not response:',
         '                if code is not None and code != 0:\n'
         '                    # msg, not response:',
         'tests/streaming_test.py::StreamClientTest'
         '::test_a_late_documented_success_is_not_reported',
         '1 != 2'),

    Case('success-codes/orphan-path-log-level',
         'schwaby/streaming.py',
         '                log = (self.logger.info if _is_success_code(code)\n',
         '                log = (self.logger.info if code == 0\n',
         'tests/streaming_test.py::StreamClientTest'
         '::test_a_late_documented_success_is_not_reported',
         "'WARNING'"),

    Case('success-codes/batched-path-report',
         'schwaby/streaming.py',
         '            if code is not None and not _is_success_code(code):\n'
         '                self._pending_reports.append((',
         '            if code is not None and code != 0:\n'
         '                self._pending_reports.append((',
         'tests/streaming_test.py::StreamClientTest'
         '::test_a_batched_documented_success_is_not_reported',
         '1 != 2'),

    Case('success-codes/batched-path-log-level',
         'schwaby/streaming.py',
         '            # worked is routine and logs at INFO, a late rejection warns.\n'
         '            log = (self.logger.info if _is_success_code(code)\n',
         '            # worked is routine and logs at INFO, a late rejection warns.\n'
         '            log = (self.logger.info if code == 0\n',
         'tests/streaming_test.py::StreamClientTest'
         '::test_a_batched_documented_success_is_not_reported',
         "'WARNING'"),

    # NEWS_HEADLINE. Out of the known set, its frames are dropped as an unknown
    # service before any handler sees them.
    Case('news-headline/is-a-known-service',
         'schwaby/streaming.py',
         "    'NASDAQ_BOOK',\n    'NEWS_HEADLINE',\n",
         "    'NASDAQ_BOOK',\n",
         'tests/streaming_test.py::StreamClientTest'
         '::test_news_headline_handler',
         'a message for a service this version does not know'),

    # A field renumbered leaves it unlabelled. The JSON diff prints the raw key
    # in double quotes; the test source only has single quotes.
    Case('news-headline/headline-is-field-4',
         'schwaby/streaming.py',
         '        HEADLINE = 4\n',
         '        HEADLINE = 40\n',
         'tests/streaming_test.py::StreamClientTest'
         '::test_news_headline_handler',
         '"4": "Chipmaker announces a product"'),

    # Fields 2 and 5 were always equal live; the fixture makes them differ so
    # the names cannot be swapped unnoticed.
    Case('news-headline/the-two-identifiers-are-not-swapped',
         'schwaby/streaming.py',
         '        HEADLINE_ID = 2\n',
         '        HEADLINE_ID = 5\n',
         'tests/streaming_test.py::StreamClientTest'
         '::test_news_headline_handler',
         '"HEADLINE_ID": "tag_example_newsml_5"',
         also=('        STORY_ID = 5\n', '        STORY_ID = 2\n')),

    Case('news-headline/subscribes-with-every-field',
         'schwaby/streaming.py',
         "            symbols, 'NEWS_HEADLINE', 'SUBS', self.NewsHeadlineFields)\n",
         "            symbols, 'NEWS_HEADLINE', 'SUBS')\n",
         'tests/streaming_test.py::StreamClientTest'
         '::test_news_headline_subs_and_add_success_all_fields',
         # The request as sent, keys then a closing brace and no fields. The
         # test source always has a comma after the keys.
         "'keys': 'AAPL,MSFT'}"),

    # VIEW. No keys, SYMBOL added, fields handled as distinct numbers.
    # One definition of an aware datetime, shared by the client's warning and
    # the conversion both it and set_cancel_time go through.
    Case('utils/iso-conversion-keys-on-the-offset',
         'schwaby/utils.py',
         "    if dt.utcoffset() is not None:\n",
         "    if dt.tzinfo is not None:\n",
         'tests/client_test.py::ClientTest'
         '::test_a_tzinfo_with_no_offset_is_naive',
         "08:03:02Z"),

    Case('client/warns-on-a-tzinfo-with-no-offset',
         'schwaby/client/base.py',
         "        if _is_instance(dt, self._DATETIME) and dt.utcoffset() is None:\n",
         "        if _is_instance(dt, self._DATETIME) and dt.tzinfo is None:\n",
         'tests/client_test.py::ClientTest'
         '::test_a_tzinfo_with_no_offset_is_naive',
         "1 != 0"),

    Case('orders/cancel-time-is-in-the-accepted-types-table',
         'tests/orders/generic_test.py',
         "                         '_cancel_time_string'}:\n",
         "                         '_not_a_validator'}:\n",
         'tests/orders/generic_test.py::AcceptedTypesTableTest'
         '::test_the_documented_table_matches_the_validators',
         "set_cancel_time"),

    # Documentation coverage: one case per rule in DocCoverageTest.
    Case('doc-coverage/classmethod-compared-as-function',
         'tests/packaging_test.py',
         "            return obj.__func__ if inspect.ismethod(obj) else obj\n",
         "            return obj\n",
         'tests/packaging_test.py::DocCoverageTest'
         '::test_every_public_name_has_a_documentation_entry',
         'OptionSymbol.parse_symbol'),

    Case('doc-coverage/walk-starts-from-exported-modules',
         'tests/packaging_test.py',
         "                if id(obj) not in best or score > best[id(obj)][0]:\n",
         "                if score == 2 and (id(obj) not in best\n"
         "                                   or score > best[id(obj)][0]):\n",
         'tests/packaging_test.py::DocCoverageTest'
         '::test_every_public_name_has_a_documentation_entry',
         "'schwaby.client.Client.get_quote' not found"),

    Case('doc-coverage/override-covered-by-base',
         'tests/packaging_test.py',
         "            return (inspect.isclass(owner) and inspect.isfunction(obj)\n",
         "            return False and (inspect.isclass(owner)\n"
         "                              and inspect.isfunction(obj)\n",
         'tests/packaging_test.py::DocCoverageTest'
         '::test_every_public_name_has_a_documentation_entry',
         'decode_json_string'),

    Case('doc-coverage/internal-member-by-defining-path',
         'tests/packaging_test.py',
         "            return path in cls.INTERNAL or cls.defined_as(obj) in cls.INTERNAL\n",
         "            return path in cls.INTERNAL\n",
         'tests/packaging_test.py::DocCoverageTest'
         '::test_every_public_name_has_a_documentation_entry',
         'convert_enum'),

    # A prefix match on the defining path would let the internal base class
    # take its public set_enforce_enums along with it -- the defect this list
    # had before it went member by member.
    Case('doc-coverage/defining-path-match-is-exact',
         'tests/packaging_test.py',
         "            return path in cls.INTERNAL or cls.defined_as(obj) in cls.INTERNAL\n",
         "            return path in cls.INTERNAL or any(\n"
         "                cls.defined_as(obj).startswith(i) for i in cls.INTERNAL)\n",
         'tests/packaging_test.py::DocCoverageTest'
         # Hides set_enforce_enums (defined under EnumEnforcer) and not
         # all_fields, so one of the two inherited names is left.
         '::test_a_deleted_entry_is_reported',
         '2 != 1'),

    Case('doc-coverage/reported-path-match-is-exact',
         'tests/packaging_test.py',
         "            return path in cls.INTERNAL or cls.defined_as(obj) in cls.INTERNAL\n",
         "            return (any(path == i or path.startswith(i + '.')\n"
         "                        for i in cls.INTERNAL)\n"
         "                    or cls.defined_as(obj) in cls.INTERNAL)\n",
         'tests/packaging_test.py::DocCoverageTest'
         '::test_listing_a_class_does_not_exempt_its_members',
         'OptionSymbol.parse_symbol'),

    Case('doc-coverage/private-bases-are-walked',
         'tests/packaging_test.py',
         "                if not cls.is_ours(base):\n",
         "                if not cls.is_ours(base) or base.__name__.startswith('_'):\n",
         'tests/packaging_test.py::DocCoverageTest'
         '::test_a_deleted_entry_is_reported',
         '2 != 1'),

    Case('doc-coverage/modules-are-classified',
         'tests/packaging_test.py',
         "        'schwaby.version': 'holds the version string',\n",
         "",
         'tests/packaging_test.py::DocCoverageTest'
         '::test_every_module_is_walked_or_says_why_not',
         'schwaby.version'),

    Case('doc-coverage/unwalked-modules-define-nothing',
         'tests/packaging_test.py',
         "        'schwaby.client.base.BaseClient':\n"
         "            'the shared base; its methods are documented on Client',\n",
         "",
         'tests/packaging_test.py::DocCoverageTest'
         '::test_every_module_is_walked_or_says_why_not',
         "'BaseClient'"),

    Case('doc-coverage/sibling-package-separator',
         'tests/packaging_test.py',
         "        return home == 'schwaby' or home.startswith('schwaby.')\n",
         "        return home.startswith('schwaby')\n",
         'tests/packaging_test.py::DocCoverageTest'
         '::test_a_sibling_package_is_not_ours',
         'True is not false'),

    Case('doc-coverage/members-option-covers-members',
         'tests/packaging_test.py',
         "                if inspect.isclass(obj) and ':members:' in options:\n",
         "                if False:\n",
         'tests/packaging_test.py::DocCoverageTest'
         '::test_every_public_name_has_a_documentation_entry',
         # Documented only through `Client.Account`'s :members:, and the
         # first name pytest shows before it truncates the list.
         "'schwaby.client.Client.Account.Fields'"),

    Case('doc-coverage/internal-list-stays-honest',
         'tests/packaging_test.py',
         "    INTERNAL = {\n        'schwaby.auth.TokenMetadata':\n",
         "    INTERNAL = {\n        'schwaby.client.Client.get_quote': 'x',\n"
         "        'schwaby.auth.TokenMetadata':\n",
         'tests/packaging_test.py::DocCoverageTest'
         '::test_the_internal_list_is_current',
         'is documented now'),

    Case('doc-coverage/indented-automethod-is-relative',
         'tests/packaging_test.py',
         "                    target = enclosing.replace('::', '.') + '.' + target\n",
         "                    pass\n",
         'tests/packaging_test.py::DocCoverageTest'
         '::test_every_public_name_has_a_documentation_entry',
         'set_account_hash'),

    # The API index extension.
    Case('api-index/enum-members-counted-not-listed',
         'docs/_ext/api_index.py',
         "            if isinstance(_resolve(fullname, module), enum.Enum):\n",
         "            if False:\n",
         'tests/packaging_test.py::ApiIndexExtensionTest'
         '::test_the_index_lists_classes_callables_and_enum_counts',
         'Client.Quote.Fields.QUOTE'),

    Case('api-index/callables-marked',
         'docs/_ext/api_index.py',
         "                text = qual + ('()' if objtype in CALLABLE else '')\n",
         "                text = qual\n",
         'tests/packaging_test.py::ApiIndexExtensionTest'
         '::test_the_index_lists_classes_callables_and_enum_counts',
         '!= None'),

    Case('api-index/member-count-shown',
         'docs/_ext/api_index.py',
         "                          if count else '')\n",
         "                          if False else '')\n",
         'tests/packaging_test.py::ApiIndexExtensionTest'
         '::test_the_index_lists_classes_callables_and_enum_counts',
         '(5 members)'),

    Case('api-index/one-member-is-singular',
         'docs/_ext/api_index.py',
         "                suffix = (' ({} member{})'.format(count, '' if count == 1\n",
         "                suffix = (' ({} member{})'.format(count, 's' if count == 1\n",
         'tests/packaging_test.py::ApiIndexExtensionTest'
         '::test_the_index_lists_classes_callables_and_enum_counts',
         '(1 member)'),

    Case('api-index/members-nest-under-their-class',
         'docs/_ext/api_index.py',
         "            parent = qual.rsplit('.', 1)[0] if '.' in qual else None\n",
         "            parent = None\n",
         'tests/packaging_test.py::ApiIndexExtensionTest'
         '::test_members_are_nested_under_their_class',
         'Client has no nested list'),

    Case('api-index/links-reach-other-pages',
         'docs/_ext/api_index.py',
         "    uri = app.builder.get_relative_uri(fromdocname, docname) + '#' + anchor\n",
         "    uri = '#' + anchor\n",
         'tests/packaging_test.py::ApiIndexExtensionTest'
         '::test_the_index_lists_classes_callables_and_enum_counts',
         'other.html#schwaby.client.Client.get_quotes'),

    Case('api-index/reread-on-every-build',
         'docs/_ext/api_index.py',
         "        self.env.note_reread()\n",
         "",
         'tests/packaging_test.py::ApiIndexExtensionTest'
         '::test_an_incremental_build_updates_the_index',
         'Client.get_quotes()'),

    Case('docs-config/ignore-list-is-standard-library-only',
         'tests/packaging_test.py',
         "                ok = (module.split('.')[0] in sys.stdlib_module_names\n",
         "                ok = (not module.startswith('schwaby')\n",
         'tests/packaging_test.py::DocsConfigTest'
         '::test_the_ignore_check_refuses_names_of_ours',
         "'StreamClient'"),

    Case('docs-config/nitpicky',
         'docs/conf.py',
         "nitpicky = True\n",
         "nitpicky = False\n",
         'tests/packaging_test.py::DocsConfigTest'
         '::test_an_unresolved_cross_reference_fails_the_build',
         'True is not False'),

    # Secrets never logged: one case per layer, since the layers overlap and
    # the whole-session test stays green with any single one removed.
    Case('secrets/streaming-logger-is-protected',
         'schwaby/streaming.py',
         "    return _redact.get_logger(__name__)\n",
         "    return __import__('logging').getLogger(__name__)\n",
         'tests/redact_test.py::EveryLoggerTest'
         '::test_every_logger_this_library_creates_is_protected',
         "'schwaby.streaming'"),

    Case('secrets/frame-redacted-before-rendering',
         'schwaby/_redact.py',
         "            args = tuple(_RedactedFrame(a) if _is_frame(a) else a\n"
         "                         for a in args)\n",
         "            pass\n",
         'tests/redact_test.py::FrameElisionTest'
         '::test_no_size_leaves_part_of_a_registered_token',
         '!= [('),

    Case('secrets/frame-redacted-before-a-callers-filter',
         'schwaby/_redact.py',
         "            self._text = str(_redact_frame(self._frame))\n",
         "            self._text = str(self._frame)\n",
         'tests/redact_test.py::WebsocketLoggerTest'
         '::test_a_callers_filter_that_renders_the_record_sees_it_redacted',
         'unexpectedly found in'),

    Case('secrets/frame-redacted-only-when-rendered',
         'schwaby/_redact.py',
         "            args = tuple(_RedactedFrame(a) if _is_frame(a) else a\n",
         "            args = tuple(_redact_frame(a) if _is_frame(a) else a\n",
         'tests/redact_test.py::WebsocketLoggerTest'
         '::test_a_frame_no_handler_writes_is_not_redacted',
         'Lists differ'),

    Case('secrets/json-value-masked-unregistered',
         'schwaby/_redact.py',
         "        text = _SECRET_JSON_VALUE.sub(r'\\1<REDACTED>\\2', text)\n",
         "        pass\n",
         'tests/redact_test.py::FrameElisionTest'
         '::test_no_size_leaves_part_of_an_unregistered_token',
         '!= [('),

    Case('secrets/arriving-frame-registers-its-account',
         'schwaby/_redact.py',
         "            register_stream_accounts(json.loads(text))\n",
         "            pass\n",
         'tests/redact_test.py::FrameElisionTest'
         '::test_an_arriving_frame_registers_its_account_before_rendering',
         '!= [('),

    Case('secrets/session-stream-only-account',
         'schwaby/_redact.py',
         "            register_stream_accounts(json.loads(text))\n",
         "            pass\n",
         'tests/secret_logging_test.py::SecretLoggingTest'
         '::test_a_session_logs_no_secret_at_any_level',
         'stream-only account number: websockets.client'),

    Case('secrets/exception-dropped-after-redaction',
         'schwaby/_redact.py',
         "            record.exc_info = None\n",
         "            pass\n",
         'tests/redact_test.py::RedactingFilterTest'
         '::test_the_exception_is_dropped_once_its_text_is_redacted',
         'is not None'),

    Case('secrets/unformattable-arguments-redacted',
         'schwaby/_redact.py',
         "            text = '<a message that cannot be formatted>'\n        return redact(text)\n",
         "            text = '<a message that cannot be formatted>'\n        return text\n",
         'tests/redact_test.py::RedactingFilterTest'
         '::test_a_record_that_cannot_be_formatted_is_left_to_logging',
         '<REDACTED APP_SECRET>'),

    Case('secrets/unwritten-record-not-rendered',
         'schwaby/_redact.py',
         "            record.msg = _RedactedMessage(record.msg, record.args)\n",
         "            record.msg = str(_RedactedMessage(record.msg, record.args))\n",
         'tests/redact_test.py::RedactingFilterTest'
         '::test_a_record_no_handler_writes_is_not_rendered',
         '[] != [1]'),

    Case('secrets/message-redacted-when-rendered-anywhere',
         'schwaby/_redact.py',
         "            record.msg = _RedactedMessage(record.msg, record.args)\n"
         "            record.args = ()\n",
         "            pass\n",
         'tests/redact_test.py::RedactingFilterTest'
         '::test_a_record_rendered_outside_a_handler_is_redacted',
         'Lists differ'),

    Case('secrets/dependencies-protected-by-client',
         'schwaby/client/base.py',
         "        _redact.protect_dependencies()\n",
         "",
         'tests/redact_test.py::EveryLoggerTest'
         '::test_the_dependencies_loggers_are_protected',
         'httpcore2'),

    Case('secrets/dependencies-protected-at-import',
         'schwaby/__init__.py',
         "from . import _redact  # noqa: E402\n_redact.protect_dependencies()",
         "from . import _redact  # noqa: E402",
         'tests/redact_test.py::EveryLoggerTest'
         '::test_the_dependencies_loggers_are_protected_from_import',
         'AssertionError'),

    Case('secrets/token-label-capped-small',
         'schwaby/_redact.py',
         "_LABEL_LIMITS = {'ACCESS_TOKEN': 4,",
         "_LABEL_LIMITS = {'ACCESS_TOKEN': 64,",
         'tests/redact_test.py::RedactTest'
         '::test_a_token_label_keeps_only_its_newest_few',
         '<REDACTED ACCESS_TOKEN>'),

    Case('secrets/redaction-not-redacted-again',
         'schwaby/_redact.py',
         "client_secret)=)(?!<REDACTED)[^&#\\s\"\\'<]+')",
         "client_secret)=)[^&#\\s\"\\']+')",
         'tests/redact_test.py::RedactTest'
         '::test_a_redaction_is_not_redacted_again',
         'AUTHORIZATION_CODE'),

    Case('secrets/unparsable-frame-text-redacted',
         'schwaby/streaming.py',
         "    text = str.__str__(text)\n    # Redacted before it is cut, so a secret across the cut leaves nothing.\n    text = _redact.before_cut(text, 200)\n",
         "    text = str.__str__(text)\n",
         'tests/redact_test.py::RegistrationTest'
         '::test_an_unparsable_frame_is_quoted_redacted',
         "not found in 'Failed to parse"),

    Case('secrets/safe-str-redacts-before-the-cut',
         'schwaby/streaming.py',
         "    # An exception's own message can quote venue text, line breaks and all.\n    # Redacted before it is cut, so a secret across the cut leaves nothing.\n    text = _redact.before_cut(text, 200)\n",
         "    # An exception's own message can quote venue text, line breaks and all.\n",
         'tests/redact_test.py::RegistrationTest'
         '::test_no_bounded_text_leaves_the_head_of_a_secret',
         "helper='_safe_str'"),

    Case('secrets/safe-name-redacts-before-the-cut',
         'schwaby/streaming.py',
         "    text = _redact.before_cut(str.__str__(text), 64)\n",
         "    text = str.__str__(text)\n",
         'tests/redact_test.py::RegistrationTest'
         '::test_no_bounded_text_leaves_the_head_of_a_secret',
         "helper='_safe_name'"),

    Case('secrets/venue-text-redacts-before-the-cut',
         'schwaby/utils.py',
         "        text = repr(value)\n    # Redacted before it is cut, so a secret across the cut leaves nothing.\n    text = _redact.before_cut(text, 200)\n",
         "        text = repr(value)\n",
         'tests/redact_test.py::RegistrationTest'
         '::test_no_bounded_text_leaves_the_head_of_a_secret',
         "helper='_venue_text'"),

    Case('secrets/error-detail-redacts-before-the-cut',
         'schwaby/utils.py',
         "        detail = _redact.before_cut('; '.join(parts), _ERROR_DETAIL_LIMIT)\n",
         "        detail = '; '.join(parts)\n",
         'tests/redact_test.py::RegistrationTest'
         '::test_an_http_errors_detail_leaves_no_head_of_a_secret',
         'unexpectedly found in'),

    Case('secrets/contrib-repr-redacts-before-the-cut',
         'schwaby/contrib/util.py',
         "    text = _redact.before_cut(text, 200)\n    if len(text) > 200:\n",
         "    if len(text) > 200:\n",
         'tests/redact_test.py::RegistrationTest'
         '::test_no_bounded_text_leaves_the_head_of_a_secret',
         "helper='contrib _safe_repr'"),

    Case('secrets/contrib-key-redacts-before-the-cut',
         'schwaby/contrib/util.py',
         "    text = _redact.before_cut(str.__str__(text), 64)\n",
         "    text = str.__str__(text)\n",
         'tests/redact_test.py::RegistrationTest'
         '::test_no_bounded_text_leaves_the_head_of_a_secret',
         "helper='contrib _safe_key'"),

    Case('secrets/before-cut-makes-plain-str',
         'schwaby/_redact.py',
         "    text = str.__str__(text)\n    window = bound + _WINDOW\n",
         "    window = bound + _WINDOW\n",
         'tests/redact_test.py::RedactTest'
         '::test_before_cut_reads_a_str_subclass_as_plain_text',
         'boom'),

    Case('secrets/before-cut-keeps-a-window',
         'schwaby/_redact.py',
         "    window = bound + _WINDOW\n",
         "    window = bound\n",
         'tests/redact_test.py::RegistrationTest'
         '::test_no_bounded_text_leaves_the_head_of_a_secret',
         'unexpectedly found in',
         superseded='the window doubles until the redacted text keeps '
                    '_WINDOW characters past the bound, so where it starts no '
                    'longer decides; before-cut-window-survives-shrinking '
                    'pins the doubling'),

    Case('secrets/before-cut-window-survives-shrinking',
         'schwaby/_redact.py',
         "        if len(text) <= window or len(redacted) >= bound + _WINDOW:\n",
         "        if True:\n",
         'tests/redact_test.py::RegistrationTest'
         '::test_redaction_shrinking_the_window_leaves_no_head',
         'unexpectedly found in'),

    Case('secrets/malformed-item-skipped-not-fatal',
         'schwaby/_redact.py',
         "                value = item.get('1')\n            except Exception:\n                continue\n",
         "                value = item.get('1')\n            except Exception:\n                return\n",
         'tests/redact_test.py::RedactTest'
         '::test_a_malformed_item_does_not_stop_the_items_after_it',
         '<REDACTED ACCOUNT_NUMBER>'),

    Case('secrets/malformed-entry-skipped-not-fatal',
         'schwaby/_redact.py',
         "            items = list(entry.get('content') or ())\n        except Exception:\n            continue\n",
         "            items = list(entry.get('content') or ())\n        except Exception:\n            return\n",
         'tests/redact_test.py::RedactTest'
         '::test_a_malformed_item_does_not_stop_the_items_after_it',
         '<REDACTED ACCOUNT_NUMBER>'),

    Case('secrets/no-logger-created-at-import',
         'schwaby/streaming.py',
         "def get_logger():\n    return _redact.get_logger(__name__)\n",
         "def get_logger():\n    return _redact.get_logger(__name__)\nget_logger()\n",
         'tests/redact_test.py::EveryLoggerTest'
         '::test_a_default_dictconfig_after_import_leaves_logging_on',
         'True'),

    Case('secrets/werkzeug-logger-through-get-logger',
         'schwaby/auth.py',
         "        log = _redact.get_logger('werkzeug')\n",
         "        log = logging.getLogger('werkzeug')\n",
         'tests/redact_test.py::EveryLoggerTest'
         '::test_nothing_in_this_library_logs_around_get_logger',
         'auth.py:'),

    Case('secrets/filter-redacts-the-message',
         'schwaby/_redact.py',
         "            self._text = redact(text)\n",
         "            self._text = text\n",
         'tests/redact_test.py::RedactingFilterTest'
         '::test_an_argument_is_redacted_before_the_handler_formats_it',
         '<REDACTED APP_SECRET>'),

    Case('secrets/filter-redacts-a-traceback',
         'schwaby/_redact.py',
         "            record.exc_text = redact(record.exc_text)\n",
         "            pass\n",
         'tests/redact_test.py::RedactingFilterTest'
         '::test_a_traceback_is_redacted',
         'ValueError: refused <REDACTED APP_SECRET>'),

    Case('secrets/filter-redacts-a-stack',
         'schwaby/_redact.py',
         "            record.stack_info = redact(record.stack_info)\n",
         "            pass\n",
         'tests/redact_test.py::RedactingFilterTest'
         '::test_a_stack_is_redacted',
         'frame_<REDACTED APP_SECRET>'),

    Case('secrets/url-encoded-spellings-registered',
         'schwaby/_redact.py',
         "    forms = frozenset({value, urllib.parse.quote(value, safe=''),\n"
         "                       urllib.parse.quote_plus(value)})\n",
         "    forms = frozenset({value})\n",
         'tests/redact_test.py::RedactTest'
         '::test_its_url_encoded_spellings_are_replaced_too',
         '<REDACTED REFRESH_TOKEN>'),

    Case('secrets/digits-only-match-whole-numbers',
         'schwaby/_redact.py',
         "                        escaped = r'(?<!\\d)' + escaped + r'(?!\\d)'\n",
         "                        pass\n",
         'tests/redact_test.py::RedactTest'
         '::test_an_account_number_does_not_take_digits_out_of_a_longer_number',
         '<REDACTED ACCOUNT_NUMBER>'),

    Case('secrets/registry-capped-by-value',
         'schwaby/_redact.py',
         "        while limit is not None and len(entries) > limit:\n",
         "        while limit is not None and len(entries) > limit * 3:\n",
         'tests/redact_test.py::RedactTest'
         '::test_the_registry_forgets_old_values_per_label',
         '<REDACTED CAPPED-'),

    Case('secrets/account-numbers-never-dropped',
         'schwaby/_redact.py',
         "                 'ACCOUNT_NUMBER': None}",
         "                 'ACCOUNT_NUMBER': 64}",
         'tests/redact_test.py::RedactTest'
         '::test_no_account_number_is_ever_dropped',
         '<REDACTED ACCOUNT_NUMBER>'),

    Case('secrets/mask-survives-a-namedtuple',
         'schwaby/_redact.py',
         "        return tuple(masked) if type(obj) is tuple else masked\n",
         "        return type(obj)(masked)\n",
         'tests/redact_test.py::RedactTest'
         '::test_mask_handles_a_namedtuple',
         'TypeError'),

    Case('secrets/first-fragment-not-logged',
         'schwaby/_redact.py',
         "    if not frame.fin or getattr(frame.opcode, 'name', None) == 'CONT':\n",
         "    if getattr(frame.opcode, 'name', None) == 'CONT':\n",
         'tests/redact_test.py::WebsocketLoggerTest'
         '::test_a_fragment_is_not_logged',
         '2 != 1'),

    Case('secrets/continuation-not-logged',
         'schwaby/_redact.py',
         "    if not frame.fin or getattr(frame.opcode, 'name', None) == 'CONT':\n",
         "    if not frame.fin:\n",
         'tests/redact_test.py::WebsocketLoggerTest'
         '::test_a_fragment_is_not_logged',
         '2 != 1'),

    Case('secrets/received-url-registers-the-secret',
         'schwaby/auth.py',
         "    _redact.register(app_secret, 'APP_SECRET')\n    if _is_instance(received_url, str):\n",
         "    if _is_instance(received_url, str):\n",
         'tests/redact_test.py::RegistrationTest'
         '::test_a_code_exchange_registers_the_code_and_redirect',
         'unexpectedly found in'),

    Case('secrets/url-parameter-masked-unregistered',
         'schwaby/_redact.py',
         "        text = _SECRET_PARAMETER.sub(r'\\1<REDACTED>', text)\n",
         "        pass\n",
         'tests/redact_test.py::RedactTest'
         '::test_a_secret_url_parameter_is_masked_unregistered',
         'code=<REDACTED>'),

    Case('secrets/streamer-request-masked-by-key',
         'schwaby/streaming.py',
         "                LazyLog(lambda: self._pretty(_redact.mask(obj))))\n",
         "                LazyLog(lambda: self._pretty(obj)))\n",
         'tests/redact_test.py::RegistrationTest'
         '::test_a_streamer_request_masks_its_authorization_by_key',
         'unexpectedly found in',
         superseded='the filter masks the same keys in any JSON text it '
                    'renders, so the line stays masked without mask(); '
                    'mask() is kept because it also covers `code`, which '
                    'the JSON pattern leaves alone for response codes'),

    Case('secrets/app-key-registered-at-normalization',
         'schwaby/auth.py',
         "    _redact.register(value, label)\n    _redact.register(value.strip(), label)\n",
         "",
         'tests/redact_test.py::RegistrationTest'
         '::test_an_auth_flow_registers_the_app_key_and_secret',
         'unexpectedly found in'),

    Case('secrets/token-registered-on-load',
         'schwaby/auth.py',
         "        _register_token(token)\n        self.token = token\n",
         "        self.token = token\n",
         'tests/redact_test.py::RegistrationTest'
         '::test_loading_a_token_registers_it',
         'unexpectedly found in'),

    Case('secrets/token-registered-before-write',
         'schwaby/auth.py',
         "            # traceback of a write that fails -- can carry it.\n"
         "            _register_token(token)\n",
         "            # traceback of a write that fails -- can carry it.\n",
         'tests/redact_test.py::RegistrationTest'
         '::test_writing_a_refreshed_token_registers_it_first',
         'unexpectedly found in'),

    Case('secrets/code-registered-at-exchange',
         'schwaby/auth.py',
         "            _redact.register(code, 'AUTHORIZATION_CODE')\n",
         "            pass\n",
         'tests/redact_test.py::RegistrationTest'
         '::test_a_code_exchange_registers_the_code_and_redirect',
         'unexpectedly found in'),

    Case('secrets/websockets-given-a-protected-logger',
         'schwaby/streaming.py',
         "    args['logger'] = _redact.websocket_logger(args.get('logger'))\n",
         "",
         'tests/redact_test.py::WebsocketLoggerTest'
         '::test_websockets_is_given_a_protected_logger',
         "KeyError: 'logger'"),

    Case('secrets/callers-websocket-logger-wrapped',
         'schwaby/_redact.py',
         "        return FrameRedactingAdapter(logger, {})\n",
         "        return logger\n",
         'tests/redact_test.py::WebsocketLoggerTest'
         '::test_a_logger_built_directly_is_covered',
         'unexpectedly found in'),

    Case('secrets/adapter-keeps-the-callers-line',
         'schwaby/_redact.py',
         "        kwargs['stacklevel'] = kwargs.get('stacklevel', 1) + extra\n",
         "",
         'tests/redact_test.py::WebsocketLoggerTest'
         '::test_a_record_names_the_line_that_logged_it',
         '_redact.py'),

    Case('secrets/adapter-offset-only-before-3.11',
         'schwaby/_redact.py',
         "        extra = 2 if (sys.version_info < (3, 11) and isinstance(\n",
         "        extra = 2 if (True and isinstance(\n",
         'tests/redact_test.py::WebsocketLoggerTest'
         '::test_a_record_names_the_line_that_logged_it',
         'Lists differ'),

    Case('secrets/message-pickles-redacted',
         'schwaby/_redact.py',
         "        # would otherwise ship the raw message, or fail on a LazyLog.\n"
         "        return (str, (str(self),))\n",
         "        # would otherwise ship the raw message, or fail on a LazyLog.\n"
         "        return object.__reduce_ex__(self, 4)\n",
         'tests/redact_test.py::RedactingFilterTest'
         '::test_a_pickled_record_carries_only_redacted_text',
         'protocol='),

    Case('secrets/frame-pickles-redacted',
         'schwaby/_redact.py',
         "        # rendered there, against a registry that holds nothing.\n"
         "        return (str, (str(self),))\n",
         "        # rendered there, against a registry that holds nothing.\n"
         "        return object.__reduce_ex__(self, 4)\n",
         'tests/redact_test.py::RedactingFilterTest'
         '::test_a_pickled_record_carries_only_redacted_text',
         'protocol='),

    Case('secrets/filter-goes-first',
         'schwaby/_redact.py',
         "        logger.filters.insert(0, _FILTER)\n",
         "        logger.addFilter(_FILTER)\n",
         'tests/redact_test.py::RedactingFilterTest'
         '::test_a_callers_filter_put_there_first_sees_it_redacted',
         'is not'),

    Case('secrets/websockets-adapter-over-websockets-client',
         'schwaby/_redact.py',
         "                logging.getLogger('websockets.client'), {})\n",
         "                get_logger('websockets.client.schwaby'), {})\n",
         'tests/redact_test.py::WebsocketLoggerTest'
         '::test_configuration_of_websockets_client_still_applies',
         "'' != '> TEXT"),

    Case('secrets/frame-data-handed-over-redacted',
         'schwaby/_redact.py',
         "            return _redact_frame(self._frame).data\n",
         "            return self._frame.data\n",
         'tests/redact_test.py::WebsocketLoggerTest'
         '::test_a_frames_attributes_reach_a_callers_filter',
         'unexpectedly found in'),

    Case('secrets/rest-account-numbers-registered-first',
         'schwaby/client/base.py',
         "                _redact.register_account_numbers(resp.json())\n",
         "                pass\n",
         'tests/redact_test.py::RegistrationTest'
         '::test_a_rest_response_registers_its_account_numbers_first',
         'unexpectedly found in'),

    Case('secrets/stream-account-numbers-registered-first',
         'schwaby/streaming.py',
         "        _redact.register_stream_accounts(ret)\n",
         "",
         'tests/redact_test.py::RegistrationTest'
         '::test_receiving_account_activity_registers_the_account_first',
         'unexpectedly found in'),

    Case('secrets/only-account-activity-registers-accounts',
         'schwaby/_redact.py',
         "            if entry.get('service') != 'ACCT_ACTIVITY':\n",
         "            if False:\n",
         'tests/redact_test.py::RedactTest'
         '::test_a_quote_frame_registers_nothing',
         '<REDACTED ACCOUNT_NUMBER>'),

    # A refusal without a JSON body is TokenRefreshError under either authlib.
    Case('refresh/refusal-without-json-refused-here',
         'schwaby/auth.py',
         "        if response.status_code < 300:\n            return response\n"
         "        refusal = OAuthError(\n"
         "                error='http_{}'.format(response.status_code),\n",
         "        return response\n"
         "        refusal = OAuthError(\n"
         "                error='http_{}'.format(response.status_code),\n",
         'tests/utils_test.py::HTTPStatusErrorTest'
         '::test_a_refusal_without_a_json_body_is_a_token_refresh_error',
         'TokenRefreshError'),

    Case('refresh/success-without-json-left-to-authlib',
         'schwaby/auth.py',
         "        if response.status_code < 300:\n            return response\n",
         "        if response.status_code < 0:\n            return response\n",
         'tests/utils_test.py::HTTPStatusErrorTest'
         '::test_a_success_without_a_json_body_is_still_a_value_error',
         "is not an instance of <class 'ValueError'>"),

    # The book's per-exchange field 2 is a quote time, not a sequence number.
    # One case per enum: the bid and ask tables are separate classes and a
    # rename of one is invisible to the other's relabeling.
    Case('book/per-exchange-bid-quote-time',
         'schwaby/streaming.py',
         "        QUOTE_TIME = 2\n\n    class AskFields",
         "        SEQUENCE = 2\n\n    class AskFields",
         'tests/streaming_test.py::StreamClientTest'
         '::test_book_names_each_per_exchange_field',
         "'SEQUENCE': 63150257"),

    Case('book/per-exchange-ask-quote-time',
         'schwaby/streaming.py',
         "        QUOTE_TIME = 2\n\n    class _BookHandler",
         "        SEQUENCE = 2\n\n    class _BookHandler",
         'tests/streaming_test.py::StreamClientTest'
         '::test_book_names_each_per_exchange_field',
         "'SEQUENCE': 62767878"),

    Case('view/sends-no-keys',
         'schwaby/streaming.py',
         "        parameters = {'fields': ','.join(str(n) for n in sorted(numbers))}\n",
         "        parameters = {'keys': '', 'fields': ','.join(str(n) for n in sorted(numbers))}\n",
         'tests/streaming_test.py::StreamClientTest'
         '::test_view_sends_fields_and_no_keys',
         "'keys': ''"),

    Case('view/adds-symbol',
         'schwaby/streaming.py',
         "        numbers.add(field_type.SYMBOL.value)\n",
         "",
         'tests/streaming_test.py::StreamClientTest'
         '::test_view_sends_fields_and_no_keys',
         "'fields': '1,2'"),

    Case('view/sends-each-field-once',
         'schwaby/streaming.py',
         # A list that answers to .add, so every later statement still runs.
         "        numbers = set()\n",
         "        numbers = type('L', (list,), {'add': list.append})()\n",
         'tests/streaming_test.py::StreamClientTest'
         '::test_view_sends_symbol_once_when_the_caller_names_it',
         "'0,0,1,1'"),

    # Superseded by the rewrite: fields are converted and held as numbers, so
    # there is no list of the caller's to copy or append to.
    Case('view/copies-the-callers-list',
         'schwaby/streaming.py',
         "        fields = list(fields)\n        if not fields:\n",
         "        if not fields:\n",
         'tests/streaming_test.py::StreamClientTest'
         '::test_view_does_not_change_the_callers_list',
         '<LevelOneEquityFields.SYMBOL: 0>',
         removed='_view_op no longer builds a list from the caller\'s fields;'
                 ' conversion leaves the iterable untouched'),

    Case('view/refuses-no-fields',
         'schwaby/streaming.py',
         "        if not numbers:\n            raise ValueError('fields must name at least one field')\n",
         "",
         'tests/streaming_test.py::StreamClientTest'
         '::test_view_refuses_no_fields',
         'ValueError not raised'),

    Case('view/refuses-a-string',
         'schwaby/streaming.py',
         "        if _is_instance(fields, (str, bytes, bytearray, memoryview)):\n",
         "        if False:\n",
         'tests/streaming_test.py::StreamClientTest'
         '::test_view_refuses_values_that_are_not_field_numbers',
         'ValueError not raised'),

    Case('view/refuses-a-negative-number',
         'schwaby/streaming.py',
         "            if number < 0:\n",
         "            if number < -1:\n",
         'tests/streaming_test.py::StreamClientTest'
         '::test_view_refuses_values_that_are_not_field_numbers',
         'ValueError not raised'),

    Case('view/refuses-bytes',
         'schwaby/streaming.py',
         "        if _is_instance(fields, (str, bytes, bytearray, memoryview)):\n",
         "        if _is_instance(fields, (str, bytearray, memoryview)):\n",
         'tests/streaming_test.py::StreamClientTest'
         '::test_view_refuses_values_that_are_not_field_numbers',
         'ValueError not raised'),

    Case('view/refuses-a-name',
         'schwaby/streaming.py',
         "            except ValueError:\n                number = -1\n",
         "            except ValueError:\n                continue\n",
         'tests/streaming_test.py::StreamClientTest'
         '::test_view_refuses_values_that_are_not_field_numbers',
         'ValueError not raised'),

    # LevelOneEquityFields 52-56: measured, not in Schwab's table.
    Case('fields/equity-past-51-relabeled',
         'schwaby/streaming.py',
         "        TOTAL_VOLUME_FRACTIONAL = 54\n",
         "        TOTAL_VOLUME_FRACTIONAL = 57\n",
         'tests/streaming_test.py::StreamClientTest'
         '::test_level_one_equity_handler_names_the_fields_past_51',
         "'54': 1349842.996459"),

    Case('fields/equity-analogy-named-field-relabeled',
         'schwaby/streaming.py',
         "        BID_SIZE_FRACTIONAL = 55\n",
         "        BID_SIZE_FRACTIONAL = 57\n",
         'tests/streaming_test.py::StreamClientTest'
         '::test_level_one_equity_handler_names_the_fields_past_51',
         "'55': 100"),

    Case('fields/equity-default-requests-past-51',
         'schwaby/streaming.py',
         "        UNKNOWN_53 = 53\n",
         "",
         'tests/streaming_test.py::StreamClientTest'
         '::test_level_one_equity_subs_and_add_success_all_fields',
         "51,52,54,55,56"),

    Case('view/refuses-none',
         'schwaby/streaming.py',
         "        for value in values or ():\n",
         "        for value in values:\n",
         'tests/streaming_test.py::StreamClientTest'
         '::test_view_refuses_no_fields',
         'TypeError'),

    Case('view/refuses-a-memoryview',
         'schwaby/streaming.py',
         "        if _is_instance(fields, (str, bytes, bytearray, memoryview)):\n",
         "        if _is_instance(fields, (str, bytes, bytearray)):\n",
         'tests/streaming_test.py::StreamClientTest'
         '::test_view_refuses_values_that_are_not_field_numbers',
         'ValueError not raised'),

    Case('view/refuses-a-buffer',
         'schwaby/streaming.py',
         "        if _is_instance(fields, (str, bytes, bytearray, memoryview)):\n",
         "        if _is_instance(fields, (str, bytes)):\n",
         'tests/streaming_test.py::StreamClientTest'
         '::test_view_refuses_values_that_are_not_field_numbers',
         'ValueError not raised'),

    Case('view/refuses-a-non-iterable',
         'schwaby/streaming.py',
         "                iter(fields)\n",
         "                iter(())\n",
         'tests/streaming_test.py::StreamClientTest'
         '::test_view_refuses_values_that_are_not_field_numbers',
         'TypeError'),

    Case('view/enforces-the-enum',
         'schwaby/streaming.py',
         "        values = self.convert_enum_iterable(fields, field_type)\n",
         "        values = fields\n",
         'tests/streaming_test.py::StreamClientTest'
         '::test_view_enforces_the_enum',
         'fields must be LevelOneEquityFields members or field numbers'),

    # Each public method must name its own service; a copied block naming the
    # wrong one would VIEW a different subscription.
    Case('view/equity-method-names-its-service',
         'schwaby/streaming.py',
         "            'LEVELONE_EQUITIES', self.LevelOneEquityFields, fields)\n",
         "            'LEVELONE_OPTIONS', self.LevelOneEquityFields, fields)\n",
         'tests/streaming_test.py::StreamClientTest'
         '::test_view_sends_fields_and_no_keys',
         # The library's own response check fails first.
         'unexpected service'),

    # The failure codes adjacent to the success codes must stay failures.
    Case('success-codes/a-neighbouring-failure-code-is-not-a-success',
         'schwaby/streaming.py',
         '_SUCCESS_CODES = (0, 26, 27, 28, 29)\n',
         '_SUCCESS_CODES = (0, 25, 26, 27, 28, 29)\n',
         'tests/streaming_test.py::StreamClientTest'
         '::test_the_failure_codes_beside_them_are_not_successes',
         'UnexpectedResponseCode not raised'),

    # A set would hash the code, and a venue-sent unhashable one would raise
    # and end the receive loop.
    Case('success-codes/not-hashed',
         'schwaby/streaming.py',
         '_SUCCESS_CODES = (0, 26, 27, 28, 29)\n',
         '_SUCCESS_CODES = frozenset((0, 26, 27, 28, 29))\n',
         'tests/streaming_test.py::StreamClientTest'
         '::test_an_unhashable_late_code_is_a_rejection_not_a_crash',
         'unhashable type'),

    # Order leg quantityType. Emitted only when asked for, so a leg built
    # without one is byte-identical to every earlier version's.
    # Mutated to emit a default rather than to emit None: a None value crashes
    # build() before the assertion runs, which is a red for the wrong reason.
    # A default is also the regression that could actually ship.
    Case('quantity-type/absent-unless-given',
         'schwaby/orders/generic.py',
         "        if quantity_type is not None:\n"
         "            leg['quantityType'] = quantity_type\n",
         "        if True:\n"
         "            leg['quantityType'] = quantity_type or 'SHARES'\n",
         'tests/orders/generic_test.py::OrderBuilderTest'
         '::test_a_leg_without_a_quantity_type_carries_none',
         "'quantityType' unexpectedly found"),

    Case('quantity-type/equity-leg-enforces-the-enum',
         'schwaby/orders/generic.py',
         "        quantity_type = self.convert_enum(quantity_type, common.QuantityType)\n"
         "        return self.__add_order_leg(\n"
         "            instruction, common.EquityInstrument(symbol)",
         "        return self.__add_order_leg(\n"
         "            instruction, common.EquityInstrument(symbol)",
         'tests/orders/generic_test.py::OrderBuilderTest'
         '::test_add_equity_leg_quantity_type_wrong_type',
         'ValueError not raised'),

    Case('quantity-type/option-leg-enforces-the-enum',
         'schwaby/orders/generic.py',
         "        quantity_type = self.convert_enum(quantity_type, common.QuantityType)\n"
         "        return self.__add_order_leg(\n"
         "            instruction, common.OptionInstrument(symbol)",
         "        return self.__add_order_leg(\n"
         "            instruction, common.OptionInstrument(symbol)",
         # Its own test and the rendered failure. The earlier expect matched
         # only the test's source listing, over a build() crash.
         'tests/orders/generic_test.py::OrderBuilderTest'
         '::test_add_option_leg_quantity_type_wrong_type',
         'ValueError not raised'),

    Case('quantity-type/equity-leg-passes-it-on',
         'schwaby/orders/generic.py',
         "            instruction, common.EquityInstrument(symbol), quantity,\n"
         "            quantity_type)\n",
         "            instruction, common.EquityInstrument(symbol), quantity)\n",
         'tests/orders/generic_test.py::OrderBuilderTest'
         '::test_add_equity_leg_quantity_type',
         # The removed line of has_diff's JSON diff. The bare key also
         # appears among unchanged lines of any other mismatch.
         '-             "quantityType": "DOLLARS"'),

    # The only guard against relaxing quantity validation for a quantityType.
    # Anchored on the leg builder's comment, since set_quantity has the same
    # two validation lines.
    Case('quantity-type/quantity-still-required',
         'schwaby/orders/generic.py',
         "        # instruction and quantity_type are assumed to have been verified\n"
         "\n"
         "        _assert_finite('quantity', quantity)\n"
         "        if quantity <= 0:\n",
         "        # instruction and quantity_type are assumed to have been verified\n"
         "\n"
         "        _assert_finite('quantity', quantity)\n"
         "        if quantity <= 0 and quantity_type is None:\n",
         'tests/orders/generic_test.py::OrderBuilderTest'
         '::test_quantity_is_still_required_whatever_the_quantity_type',
         'ValueError not raised'),

    Case('quantity-type/option-leg-passes-it-on',
         'schwaby/orders/generic.py',
         "            instruction, common.OptionInstrument(symbol), quantity,\n"
         "            quantity_type)\n",
         "            instruction, common.OptionInstrument(symbol), quantity)\n",
         'tests/orders/generic_test.py::OrderBuilderTest'
         '::test_add_option_leg_quantity_type',
         "KeyError: 'quantityType'"),

    Case('hostile-class/an-abstract-base-answers-by-the-real-type',
         'schwaby/utils.py',
         '    except Exception:\n        return issubclass(type(value), types)\n',
         '    except Exception:\n        return False\n',
         'tests/utils_test.py::HostileClassTest::test_a_class_that_raises_is_refused_like_any_other_instance',
         'ValueError'),

    # 4.7.0 round 2: the writer refuses a path outside an existing directory.
    Case('auth/token-file-writer-refuses-a-path-outside-an-existing-directory',
         'schwaby/auth.py',
         '    directory = os.path.dirname(resolved)\n    if not os.path.isdir(directory):\n',
         '    directory = os.path.dirname(resolved)\n    if False:\n',
         'tests/auth_test.py::TokenFileWriterTest::test_a_path_that_is_not_a_path_is_refused_before_any_login',
         'ValueError not raised'),

    Case('auth/token-file-writer-checks-the-directory-a-symlink-resolves-to',
         'schwaby/auth.py',
         '    resolved = os.path.realpath(path)\n',
         '    resolved = os.path.abspath(path)\n',
         'tests/auth_test.py::TokenFileWriterTest::test_a_path_that_is_not_a_path_is_refused_before_any_login',
         'ValueError not raised'),

    # 4.7.0 round 3: a NUL is refused explicitly, since Windows' realpath lets
    # it through.
    Case('auth/token-file-writer-refuses-a-nul-itself',
         'schwaby/auth.py',
         "    if '\\0' in path:\n        raise ValueError('token_path must not contain a NUL')\n",
         "    if False:\n        raise ValueError('token_path must not contain a NUL')\n",
         'tests/auth_test.py::TokenFileWriterTest::test_a_nul_is_refused_where_realpath_would_let_it_through',
         'ValueError not raised'),

    # 4.7.1: token_file_age applies the client's token-shape check.
    Case('auth/token-file-age-refuses-what-the-client-refuses',
         'schwaby/auth.py',
         '    _stored_token_for_session(metadata.token)\n    return metadata.token_age()\n',
         '    return metadata.token_age()\n',
         'tests/auth_test.py::TokenFileAgeTest::test_a_token_the_client_refuses_is_refused_here_too',
         'ValueError not raised'),

    # 4.7.1: find_account_hash skips an unusable entry that provably names
    # another account, reports each kind once, and still refuses one that
    # could be the account asked for.
    Case('utils/find-account-hash-skips-an-unusable-entry-for-another-account',
         'schwaby/utils.py',
         '            _report_skipped_account_entry(entry)\n            continue\n',
         '            raise UnusableAccountNumbersError(malformed)\n',
         'tests/utils_test.py::FindAccountHashTest::test_an_unusable_entry_for_another_account_is_skipped_once_reported',
         'UnusableAccountNumbersError'),

    Case('utils/find-account-hash-refuses-an-unusable-entry-for-this-account',
         'schwaby/utils.py',
         '        if (number != account_number\n                and number.isascii()\n',
         '        if (True\n                and number.isascii()\n',
         'tests/utils_test.py::FindAccountHashTest::test_a_response_that_is_not_the_account_list_is_refused',
         'UnusableAccountNumbersError not raised'),

    Case('utils/find-account-hash-refuses-an-unusable-entry-not-ascii-digits',
         'schwaby/utils.py',
         '                and number.isascii()\n                and number.isdigit()):\n',
         '                and True\n                and True):\n',
         'tests/utils_test.py::FindAccountHashTest::test_a_response_that_is_not_the_account_list_is_refused',
         'UnusableAccountNumbersError not raised'),

    Case('utils/find-account-hash-reports-a-kind-of-entry-once',
         'schwaby/utils.py',
         '    if problem in _reported_account_entry_problems:\n        return\n',
         '',
         'tests/utils_test.py::FindAccountHashTest::test_an_unusable_entry_for_another_account_is_skipped_once_reported',
         'Unexpected logs found'),

    Case('utils/find-account-hash-reports-a-skipped-entry-as-a-warning',
         'schwaby/utils.py',
         "    get_logger().warning(\n            'find_account_hash skipped",
         "    get_logger().debug(\n            'find_account_hash skipped",
         'tests/utils_test.py::FindAccountHashTest::test_an_unusable_entry_for_another_account_is_skipped_once_reported',
         'no logs of level WARNING'),

    Case('utils/find-account-hash-names-the-missing-hash',
         'schwaby/utils.py',
         "        problem = 'has no hashValue'\n",
         "        problem = 'has a hashValue that is not a string'\n",
         'tests/utils_test.py::FindAccountHashTest::test_an_unusable_entry_for_another_account_is_skipped_once_reported',
         'has no hashValue'),

    # 4.7.1 round 1: the ASCII check, the warning's level and logger, and an
    # empty str-subclass hash.
    Case('utils/find-account-hash-refuses-an-unusable-entry-not-ascii',
         'schwaby/utils.py',
         '        if (number != account_number\n                and number.isascii()\n                and number.isdigit()):\n',
         '        if (number != account_number\n                and True\n                and number.isdigit()):\n',
         'tests/utils_test.py::FindAccountHashTest::test_a_response_that_is_not_the_account_list_is_refused',
         'UnusableAccountNumbersError not raised'),

    Case('utils/find-account-hash-warns-at-warning-not-error',
         'schwaby/utils.py',
         "    get_logger().warning(\n            'find_account_hash skipped",
         "    get_logger().error(\n            'find_account_hash skipped",
         'tests/utils_test.py::FindAccountHashTest::test_an_unusable_entry_for_another_account_is_skipped_once_reported',
         "['WARNING'] != ['ERROR']"),

    Case('utils/find-account-hash-warns-on-the-utils-logger',
         'schwaby/utils.py',
         '    _reported_account_entry_problems.add(problem)\n    get_logger().warning(',
         "    _reported_account_entry_problems.add(problem)\n    __import__('logging').getLogger('schwaby').warning(",
         'tests/utils_test.py::FindAccountHashTest::test_an_unusable_entry_for_another_account_is_skipped_once_reported',
         'no logs of level WARNING'),

    Case('utils/find-account-hash-reads-an-empty-hash-subclass-as-empty',
         'schwaby/utils.py',
         "    elif issubclass(type(dict.get(entry, 'hashValue')), str):\n",
         "    elif type(dict.get(entry, 'hashValue')) is str:\n",
         'tests/utils_test.py::FindAccountHashTest::test_an_unusable_entry_for_another_account_is_skipped_once_reported',
         'has an empty hashValue'),

    # 4.7.2: the empty MESSAGE_TYPE carrying Schwab's notices is not reported.
    Case('streaming/the-empty-message-type-is-not-reported',
         'schwaby/streaming.py',
         '                notice = not name and _is_notice_text(\n',
         '                notice = False and _is_notice_text(\n',
         'tests/streaming_test.py::AccountActivityMessageTypeTest::test_the_empty_type_carrying_a_notice_is_not_reported',
         'Unexpected logs found'),

    # 4.7.2 round 1: only an empty type carrying a text notice is exempt.
    Case('streaming/an-empty-type-with-a-payload-is-still-reported',
         'schwaby/streaming.py',
         '        return issubclass(type(parse_message_data(message_data)), str)\n',
         '        return True\n',
         'tests/streaming_test.py::AccountActivityMessageTypeTest::test_an_empty_type_carrying_a_payload_is_still_reported',
         'no logs of level WARNING'),

    Case('streaming/an-empty-type-with-broken-json-is-still-reported',
         'schwaby/streaming.py',
         '        return issubclass(type(parse_message_data(message_data)), str)\n    except Exception:\n        return False\n',
         '        return issubclass(type(parse_message_data(message_data)), str)\n    except Exception:\n        return True\n',
         'tests/streaming_test.py::AccountActivityMessageTypeTest::test_an_empty_type_carrying_a_payload_is_still_reported',
         'no logs of level WARNING'),

    # 4.7.2 round 2: a notice check that cannot run reports, and an empty type
    # with no text is still reported.
    Case('streaming/a-notice-that-cannot-be-checked-is-reported',
         'schwaby/streaming.py',
         '    try:\n        # contrib.util imports this module, so it is imported here rather than\n        # at the top, and inside the try: a notice that cannot be checked is\n        # reported, not dropped with the item it arrived in.\n        from .contrib.util import parse_message_data\n',
         '    from .contrib.util import parse_message_data\n    try:\n',
         'tests/streaming_test.py::AccountActivityMessageTypeTest::test_a_notice_that_cannot_be_checked_is_reported_not_dropped',
         'halted'),

    Case('streaming/an-empty-type-with-no-text-is-still-reported',
         'schwaby/streaming.py',
         '        return issubclass(type(parse_message_data(message_data)), str)\n',
         '        return issubclass(type(parse_message_data(message_data)),\n                          (str, type(None)))\n',
         'tests/streaming_test.py::AccountActivityMessageTypeTest::test_an_empty_type_carrying_a_payload_is_still_reported',
         'no logs of level WARNING'),

    Case('docs/bold-literal-detector-sees-one',
         'tests/packaging_test.py',
         "                if '``' in node.astext()]",
         "                if False]",
         'tests/packaging_test.py::DocBoldLiteralTest::test_the_check_sees_a_literal_inside_bold',
         'Lists differ'),

    Case('docs/bold-literal-in-a-page-is-found',
         'CONTRIBUTING.rst',
         "- ``-W`` **is not optional.**",
         "- **``-W`` is not optional.**",
         'tests/packaging_test.py::DocBoldLiteralTest::test_no_documentation_page_puts_a_literal_inside_bold',
         'CONTRIBUTING.rst: ``-W`` is not optional.'),

    Case('docs/bold-literal-in-a-docstring-is-found',
         'schwaby/utils.py',
         "    :raises TypeError: ``account_number`` is not a ``str``.",
         "    :raises TypeError: **``account_number`` is not a ``str``.**",
         'tests/packaging_test.py::DocBoldLiteralTest::test_no_docstring_puts_a_literal_inside_bold',
         'schwaby/utils.py'),

    Case('docs/bold-literal-check-reads-contributing',
         'tests/packaging_test.py',
         "        pages.append(os.path.join(REPO_ROOT, 'CONTRIBUTING.rst'))\n",
         "",
         'tests/packaging_test.py::DocBoldLiteralTest::test_no_documentation_page_puts_a_literal_inside_bold',
         'not greater than or equal to 11'),

    Case('docs/code-blocks-all-parse',
         'tests/packaging_test.py',
         'elif len(lines[j]) - len(lines[j].lstrip()) > base:',
         'elif len(lines[j]) - len(lines[j].lstrip()) >= base:',
         'tests/packaging_test.py::DocExampleTest'
         '::test_every_documentation_code_block_parses',
         'unexpected indent'),

    # bad_keywords_in resolves a call only when the name came from `schwaby`,
    # so a snippet still importing `schwab` is skipped rather than failed --
    # which is precisely what a package rename leaves behind.
    Case('docs/imports-resolve',
         'docs/index.rst',
         '  from schwaby.auth import client_from_token_file',
         '  from schwab.auth import client_from_token_file',
         'tests/packaging_test.py::DocExampleTest'
         '::test_every_import_in_the_documentation_resolves',
         'No module named'),

    Case('docs/no-old-package-name',
         'docs/index.rst',
         '  from schwaby.auth import client_from_token_file',
         '  from schwab.auth import client_from_token_file',
         'tests/packaging_test.py::DocExampleTest'
         '::test_the_documentation_imports_nothing_by_the_old_package_name',
         'index.rst'),

    # The README's uninstall-first instruction was correct through 3.0.3 and
    # strands anyone who still imports `schwab` from 4.0.0 on. Asserting that
    # the README merely *names* `schwab-py` cannot catch its return.
    Case('packaging/readme-does-not-say-uninstall-first',
         'README.md',
         '''```shell
pip install schwaby
```

That is the whole install.''',
         '''```shell
pip uninstall -y schwab-py && pip install schwaby
```

That is the whole install.''',
         'tests/packaging_test.py::LongDescriptionTest'
         '::test_the_install_command_is_in_the_readme',
         'uninstall'),

    # The port guard fetches a status path and takes a 200 as "that is my own
    # server". Through 3.0.3 the path was `schwab-py`'s, which serves it for
    # the same purpose -- so the one occupant the guard is most likely to meet
    # was the one it could not tell from itself.
    Case('auth/internal-status-path-is-ours',
         'schwaby/auth.py',
         "INTERNAL_STATUS_PATH = '/schwaby-internal/status'",
         "INTERNAL_STATUS_PATH = '/schwab-py-internal/status'",
         'tests/auth_test.py::ClientFromLoginFlowTest'
         '::test_the_internal_status_path_is_not_one_schwab_py_also_serves',
         'schwab-py'),

    # Without the separator, `.../schwabytools` starts with `.../schwaby`, so
    # a sibling package's frames are skipped as if they were ours and the
    # naive-datetime warning blames whoever called *them*.
    #
    # This case exists because the test went inert without anyone touching it.
    # Its fixture was a sibling called `schwabtools`, which collided with the
    # package root while that root was `.../schwab` and stopped colliding the
    # moment 4.0.0 renamed it -- so the mutation below went GREEN on a guard
    # that was still perfectly correct, and the suite still passed. A fixture
    # built around a name is one the next rename can empty.
    Case('client/sibling-package-prefix',
         'schwaby/client/base.py',
         'if not filename.startswith(cls._PACKAGE_ROOT + os.sep):',
         'if not filename.startswith(cls._PACKAGE_ROOT):',
         'tests/client_test.py::ClientTest'
         '::test_a_sibling_package_is_not_mistaken_for_ours',
         'should name the sibling package'),

    # find_packages() with no arguments matched `tests` as well, so every wheel
    # through 2.6.0 installed a top-level tests package into site-packages.
    Case('packaging/only-schwaby-is-shipped',
         'setup.py',
         "setuptools.find_packages(include=['schwaby', 'schwaby.*'])",
         'setuptools.find_packages()',
         'tests/packaging_test.py::ShippedFilesTest'
         '::test_setup_py_ships_that_package_list',
         'tests'),
    Case('codegen/credential-order',
         'schwaby/scripts/orders_codegen.py',
         'args.token_file, args.api_key, args.app_secret',
         'args.token_file, args.app_secret, args.api_key',
         'tests/scripts/orders_codegen_test.py::CredentialOrderTest'
         '::test_the_key_and_secret_are_not_transposed',
         'expected call not found',
         removed=('schwaby/scripts/orders_codegen.py was deleted in 3.0.0 with the code generator.')),

    # `pytest -n auto`: every test running the login flow starts a callback
    # server on its URL's port, so two on different workers collide and the
    # loser waits out the five-minute callback timeout. One case per branch of
    # the guard: the class, the mark, the setting, and the positive control.
    Case('suite/login-flow-class-outside-port-group',
         'tests/http_module_test.py',
         '@callback_port_group\nclass ConnectErrorSiblingsAreAbsorbedTest',
         'class ConnectErrorSiblingsAreAbsorbedTest',
         'tests/packaging_test.py::DocExampleTest'
         '::test_every_login_flow_test_is_in_the_callback_port_group',
         'ConnectErrorSiblingsAreAbsorbedTest runs the login flow outside'),
    Case('suite/port-group-is-an-xdist-group',
         'tests/utils.py',
         "pytest.mark.xdist_group('callback-port')",
         "pytest.mark.callback_port('callback-port')",
         'tests/packaging_test.py::DocExampleTest'
         '::test_every_login_flow_test_is_in_the_callback_port_group',
         "'xdist_group' != 'callback_port'"),
    Case('suite/setup-cfg-distributes-by-group',
         'setup.cfg',
         'addopts = --dist loadgroup',
         'addopts = --dist load',
         'tests/packaging_test.py::DocExampleTest'
         '::test_every_login_flow_test_is_in_the_callback_port_group',
         "'--dist loadgroup' not found"),
    Case('suite/port-group-guard-sees-the-calls',
         'tests/packaging_test.py',
         "                       == 'client_from_login_flow'",
         "                       == 'client_from_login_flow_'",
         'tests/packaging_test.py::DocExampleTest'
         '::test_every_login_flow_test_is_in_the_callback_port_group',
         'not less than or equal to'),

    # The auth tests register tokens 'a' and 'r' in the process-wide
    # redactor, so a test run after them found its logged line redacted letter
    # by letter. The file's own order hid it; `-n auto` did not keep that
    # order. Needs two nodes in sequence -- alone, the test passes either way.
    Case('debug/fresh-redactor-per-test',
         'tests/debug_test.py',
         "    for patcher in (patch.object(schwaby, 'LOG_REDACTOR',\n"
         "                                 schwaby.debug.LogRedactor()),",
         "    for patcher in (patch.object(schwaby, 'LOG_REDACTOR',\n"
         "                                 schwaby.LOG_REDACTOR),",
         ['tests/auth_test.py::StoredTokenShapeTest',
          'tests/debug_test.py::EnableDebugLoggingTest'
          '::test_writes_to_stderr_as_it_is_at_exit'],
         "'a line worth reporting' not found"),
    Case('debug/enable-logging-tests-are-isolated',
         'tests/debug_test.py',
         'class EnableDebugLoggingTest(unittest.TestCase):\n\n'
         '    def setUp(self):\n'
         '        isolate_bug_report_state(self)\n',
         'class EnableDebugLoggingTest(unittest.TestCase):\n',
         ['tests/auth_test.py::StoredTokenShapeTest',
          'tests/debug_test.py::EnableDebugLoggingTest'
          '::test_writes_to_stderr_as_it_is_at_exit'],
         "'a line worth reporting' not found"),
    Case('debug/isolation-restores-the-redactor',
         'tests/debug_test.py',
         '        patcher.start()\n        test.addCleanup(patcher.stop)',
         '        patcher.start()',
         'tests/debug_test.py::IsolateBugReportStateTest'
         '::test_both_globals_are_put_back',
         'the redactor was not put back'),
    Case('debug/isolation-restores-the-collection-flag',
         'tests/debug_test.py',
         "                    patch.object(schwaby.debug, "
         "'_COLLECT_RESPONSE_REDACTIONS',\n"
         "                                 "
         "schwaby.debug._COLLECT_RESPONSE_REDACTIONS)):",
         "                    ):",
         'tests/debug_test.py::IsolateBugReportStateTest'
         '::test_both_globals_are_put_back',
         'the collection flag was not put back'),

    # kill() only sends the signal. Without the join, a login flow started
    # straight after found the port still held by the server just killed:
    # 38 of 720 starts on a loaded machine, none with it.
    Case('auth/callback-server-is-waited-for',
         'schwaby/auth.py',
         '            server.join(10)',
         '            pass',
         'tests/auth_test.py::ClientFromLoginFlowTest'
         '::test_the_callback_server_has_exited_when_the_flow_returns',
         'still in the process table'),
]


# The harness's own guards. A kept verdict stands in for a run, so a guard here
# that stops working reports a broken check as PROVEN with nothing to show it.
HARNESS_CASES = [
    # What a child process reads is recorded nowhere.
    Case('harness/a-started-process-is-never-kept',
         'tools/redproof.py',
         "    if record['spawned']:",
         '    if False:',
         'tests/redproof_tool_test.py::ReadsTest'
         '::test_a_run_that_started_a_process_or_read_a_secret_is_not_kept',
         'started a process'),
    Case('harness/a-secret-file-is-never-digested',
         'tools/redproof.py',
         "        if SECRET_PATH.search(path):\n            return ('secret',)\n",
         '',
         'tests/redproof_tool_test.py::ClassifierTest'
         '::test_a_secret_holding_file_is_named_as_one',
         "('outside', '/home/u/.env')"),
    Case('harness/proc-is-never-kept',
         'tools/redproof.py',
         "        if self.under(path, '/proc') or self.under(path, '/sys'):",
         '        if False:',
         'tests/redproof_tool_test.py::ReadsTest'
         '::test_a_run_that_started_a_process_or_read_a_secret_is_not_kept',
         'read /proc'),
    # Where the directories above the interpreter live differs from rig to
    # rig, so keying them would key the machine.
    Case('harness/probes-above-a-root-key-nothing',
         'tools/redproof.py',
         "        if what == 'probe' and classifier.above(path):",
         '        if False:',
         'tests/redproof_tool_test.py::ReadsTest'
         '::test_keys_a_listing_by_names_and_a_probe_by_kind',
         'Items in the second set but not the first'),
    # The run read the mutated file, so that is what the key must hold.
    Case('harness/the-key-holds-the-mutated-bytes',
         'tools/redproof.py',
         "        data = mutated[1] if mutated and mutated[0] == rel else (\n"
         "            snapshot.content[rel])",
         '        data = snapshot.content[rel]',
         'tests/redproof_tool_test.py::ReadsTest'
         '::test_keys_a_copy_file_by_the_bytes_the_run_found',
         "'file:"),
    Case('harness/a-tracked-write-is-reported',
         'tools/redproof.py',
         "            if c[0] == 'copy' and c[1] in tracked:",
         '            if False:',
         'tests/redproof_tool_test.py::ReadsTest'
         '::test_a_write_to_a_tracked_file_is_reported',
         "['schwaby/__init__.py']"),
    Case('harness/a-tombstone-holds-nothing',
         'tools/redproof.py',
         "                if k.startswith(prefix) and not r.get('gone')}",
         '                if k.startswith(prefix)}',
         'tests/redproof_tool_test.py::StoreTest'
         '::test_prunes_by_last_use_and_skips_tombstones',
         "Items in the second set but not the first"),
    Case('harness/unused-records-are-pruned',
         'tools/redproof.py',
         '            if at < cutoff:',
         '            if False:',
         'tests/redproof_tool_test.py::StoreTest'
         '::test_prunes_by_last_use_and_skips_tombstones',
         "'k:1'"),
    # Another run may have saved since this one loaded; a save that writes
    # only its own records erases theirs.
    Case('harness/a-save-merges',
         'tools/redproof.py',
         '            records = current.records\n',
         '            records = {}\n',
         'tests/redproof_tool_test.py::StoreTest'
         '::test_saving_merges_onto_what_another_run_saved',
         "'a:1'"),
    Case('harness/memory-bounds-the-pool',
         'tools/redproof.py',
         '    size = max(1, min(cpus, by_memory))',
         '    size = cpus',
         'tests/redproof_tool_test.py::PoolSizeTest'
         '::test_memory_bounds_the_pool_and_says_so',
         '6 != 20'),
    Case('harness/two-at-a-time-unless-told-otherwise',
         'tools/redproof.py',
         '    cpus = args.jobs or (usable_cpus() if whole else 2)',
         '    cpus = args.jobs or usable_cpus()',
         'tests/redproof_tool_test.py::PoolSizeTest'
         '::test_two_at_a_time_unless_told_to_use_every_core',
         '2 !='),
    Case('harness/repeated-text-is-refused',
         'tools/redproof.py',
         '        if found != 1:',
         '        if found == 0:',
         'tests/redproof_tool_test.py::MutateTest'
         '::test_refuses_text_that_is_missing_or_repeated',
         'text appearing 2 times was mutated'),
    Case('harness/a-no-op-is-refused',
         'tools/redproof.py',
         '        if old == new:',
         '        if False:',
         'tests/redproof_tool_test.py::MutateTest::test_refuses_a_no_op',
         'a no-op mutation was applied'),
]

CASES += HARNESS_CASES


# The suite's configuration. `-n auto` and the per-test limit are both
# settings nothing else checks: get one wrong and the suite still passes,
# slower or able to hang.
SUITE_CASES = [
    # xdist's own answer is psutil's physical core count: half the machine
    # where there are two threads per core.
    Case('suite/auto-workers-come-from-the-hook',
         'tests/conftest.py',
         '    return usable_cpus()',
         '    return None',
         'tests/suite_config_test.py::AutoWorkersTest'
         '::test_auto_is_the_usable_core_count_not_the_machines',
         'did not start one worker per usable core'),
    Case('suite/auto-workers-are-the-process-cpu-count',
         'tests/conftest.py',
         '        return os.process_cpu_count() or 1',
         '        return os.cpu_count() or 1',
         'tests/suite_config_test.py::AutoWorkersTest'
         '::test_auto_is_the_usable_core_count_not_the_machines',
         'did not start one worker per usable core'),
    Case('suite/auto-workers-honour-the-affinity-mask',
         'tests/conftest.py',
         '        return len(os.sched_getaffinity(0))',
         '        return os.cpu_count() or 1',
         'tests/suite_config_test.py::AutoWorkersTest'
         '::test_before_3_13_the_affinity_mask_decides',
         'ignored the affinity mask'),
    Case('suite/the-worker-override-still-wins',
         'tests/conftest.py',
         "    if os.environ.get('PYTEST_XDIST_AUTO_NUM_WORKERS'):\n"
         "        return None\n",
         '',
         'tests/suite_config_test.py::AutoWorkersTest'
         '::test_the_environment_override_still_wins',
         'answered over PYTEST_XDIST_AUTO_NUM_WORKERS'),
    # pytest-timeout reads 0 as "no limit".
    Case('suite/every-test-has-a-limit',
         'setup.cfg',
         'timeout = 60\n',
         'timeout = 0\n',
         'tests/suite_config_test.py::test_every_test_runs_under_a_time_limit',
         'the per-test time limit is 0.0'),
    Case('suite/the-limit-plugin-is-in-dev',
         'setup.py',
         "            'pytest-timeout',\n",
         '',
         'tests/packaging_test.py::SetupPyTest'
         '::test_dev_has_the_plugins_setup_cfg_needs',
         'pytest-timeout is missing from [dev]'),
]

CASES += SUITE_CASES


# Report-delivery guards whose tests used to measure durations. Each test
# now waits on a state or an order, and each entry breaks the guard to prove
# the rewritten test still sees it.
TIMING_CASES = [
    Case('streaming/logout-report-lets-a-cancel-through',
         'schwaby/streaming.py',
         '                except asyncio.CancelledError:\n'
         '                    # Not swallowed with the rest. Discarding a '
         'cancellation\n'
         '                    # makes logout() refuse to die -- a supervisor',
         '                except ():\n'
         '                    # Not swallowed with the rest. Discarding a '
         'cancellation\n'
         '                    # makes logout() refuse to die -- a supervisor',
         'tests/streaming_test.py::StreamClientTest'
         '::test_logout_can_still_be_cancelled_while_reporting',
         'CancelledError not raised'),
    Case('streaming/a-report-is-awaited-not-scheduled',
         'schwaby/streaming.py',
         '                if inspect.isawaitable(result):\n'
         '                    await result\n',
         '                if inspect.isawaitable(result):\n'
         '                    asyncio.ensure_future(result)\n',
         'tests/streaming_test.py::StreamClientTest'
         '::test_a_slow_error_handler_holds_up_handle_message',
         'the report is no longer awaited inline'),
    # The design this replaced: delivering the batched rejection where it is
    # read, inside the response deadline with the read lock held.
    Case('streaming/a-batched-rejection-is-not-reported-inside-the-deadline',
         'schwaby/streaming.py',
         '                                self._read_and_route('
         'use_overflow=False),',
         '                                self._read_route_and_drain(),',
         'tests/streaming_test.py::StreamClientTest'
         '::test_a_slow_handler_cannot_fail_a_successful_subscribe',
         'timed out after 0.2s',
         also=('    async def _await_response(self, request_id, service, '
               'command):\n',
               '    async def _read_route_and_drain(self):\n'
               '        frame = await self._read_and_route('
               'use_overflow=False)\n'
               '        await self._drain_pending_reports()\n'
               '        return frame\n\n'
               '    async def _await_response(self, request_id, service, '
               'command):\n')),
    Case('streaming/a-cancelled-request-does-not-drain',
         'schwaby/streaming.py',
         '            if not cancelled:\n',
         '            if True:\n',
         'tests/streaming_test.py::StreamClientTest'
         '::test_a_slow_handler_does_not_delay_a_cancellation',
         'unwinding the cancel waited on the error handler'),
]

CASES += TIMING_CASES


# The commit hooks' light check. A check that lets everything through looks
# exactly like one that has nothing to complain about.
LIGHT_CHECK_CASES = [
    Case('light-check/only-new-warnings-fail',
         'tools/light_check.py',
         '    new = now - before\n',
         '    new = now\n',
         'tests/light_check_test.py::LightCheckTest'
         '::test_a_warning_moved_or_kept_is_not_new',
         'an old warning was blamed on this commit'),
    Case('light-check/new-warnings-fail',
         'tools/light_check.py',
         '    new = now - before\n',
         '    new = collections.Counter()\n',
         'tests/light_check_test.py::LightCheckTest'
         '::test_a_new_warning_is_refused_and_named',
         'a new warning was let through'),
    Case('light-check/a-new-file-has-no-baseline',
         'tools/light_check.py',
         '              if before_text is not None else collections.Counter())',
         '              if before_text is not None else now)',
         'tests/light_check_test.py::LightCheckTest'
         '::test_a_new_file_is_checked_whole',
         'a new file was not checked'),
    Case('light-check/a-syntax-error-is-a-warning',
         'tools/light_check.py',
         "        self.messages.append('syntax error: %s' % msg)",
         '        pass',
         'tests/light_check_test.py::LightCheckTest'
         '::test_a_file_that_does_not_parse_is_refused',
         'does not parse was let through'),
    Case('light-check/whitespace-errors-fail',
         'tools/light_check.py',
         '    if check.returncode != 0:',
         '    if False:',
         'tests/light_check_test.py::LightCheckTest'
         '::test_whitespace_errors_are_refused',
         'a whitespace error was let through'),
    # The index is what the commit will contain; the working tree may differ.
    Case('light-check/the-index-is-checked',
         'tools/light_check.py',
         "    now = warnings_in(blob(':' + path) or b'', path)",
         "    now = warnings_in(open(path, 'rb').read(), path)",
         'tests/light_check_test.py::LightCheckTest'
         '::test_the_index_is_checked_not_the_working_tree',
         'the working tree was checked instead of the index'),
]

LIGHT_CHECK_CASES += [
    # Round-one review: each of these let a commit through, or refused one,
    # wrongly.
    Case('light-check/a-rename-is-compared-with-its-old-path',
         'tools/light_check.py',
         "    before_text = blob('HEAD:' + before_path)",
         "    before_text = blob('HEAD:' + path)",
         'tests/light_check_test.py::LightCheckTest'
         '::test_a_renamed_file_keeps_its_old_warnings',
         'a rename was blamed'),
    Case('light-check/the-blob-is-read-as-bytes',
         'tools/light_check.py',
         '    return out.stdout if out.returncode == 0 else None',
         "    return (out.stdout.decode('utf-8', 'replace').encode('utf-8')\n"
         '            if out.returncode == 0 else None)',
         'tests/light_check_test.py::LightCheckTest'
         '::test_bytes_that_are_not_utf8_do_not_parse',
         'a file Python cannot decode was let through'),
    Case('light-check/a-syntax-error-is-never-subtracted',
         'tools/light_check.py',
         "        if message.startswith('syntax error:'):\n"
         '            new[message] = now[message]\n',
         '        pass\n',
         'tests/light_check_test.py::LightCheckTest'
         '::test_a_syntax_error_already_at_head_still_fails',
         'still does not parse'),
    Case('light-check/a-symlink-is-not-code',
         'tools/light_check.py',
         "        if path.endswith('.py') and meta[1] != '120000':",
         "        if path.endswith('.py'):",
         'tests/light_check_test.py::LightCheckTest'
         '::test_a_symlink_is_not_checked_as_code',
         'a symlink was checked as code'),
]

CASES += LIGHT_CHECK_CASES


# The lock covers every supported Python. Read without its markers, the pins
# for another interpreter count, the installed set never matches, and no
# verdict is ever kept or reused.
LOCK_CASES = [
    Case('harness/lock-markers-are-evaluated',
         'tools/redproof.py',
         '        if marker.strip() and not Marker(marker.strip()).evaluate():\n'
         '            continue\n',
         '',
         'tests/redproof_tool_test.py::LockTest'
         '::test_only_the_pins_whose_marker_holds_here_count',
         'a pin for another Python was counted'),
]

CASES += LOCK_CASES


# tools/repeat.py counts a flake by the test it names.
REPEAT_CASES = [
    Case('repeat/failures-are-named',
         'tools/repeat.py',
         '    return sorted(set(_FAILED.findall(output)))',
         '    return []',
         'tests/repeat_tool_test.py::FailuresTest'
         '::test_names_every_failed_and_erroring_test_once',
         'a failed test was not named'),
    Case('repeat/errors-count-as-failures',
         'tools/repeat.py',
         "    r'^(?:SUB)?(?:FAILED|ERROR)(?:\\([^)]*\\))? (.+?)(?: - .*)?$', "
         "re.MULTILINE)",
         "    r'^(?:SUB)?FAILED(?:\\([^)]*\\))? (.+?)(?: - .*)?$', "
         "re.MULTILINE)",
         'tests/repeat_tool_test.py::FailuresTest'
         '::test_names_every_failed_and_erroring_test_once',
         'a failed test was not named'),
]

REPEAT_CASES.append(
    # pytest reports a failing subTest as SUBFAILED(...), which a pattern for
    # FAILED alone never named: the flake vanished from the counts.
    Case('repeat/a-failing-subtest-is-named',
         'tools/repeat.py',
         "    r'^(?:SUB)?(?:FAILED|ERROR)(?:\\([^)]*\\))? (.+?)(?: - .*)?$', "
         "re.MULTILINE)",
         "    r'^(?:FAILED|ERROR)(?:\\([^)]*\\))? (.+?)(?: - .*)?$', "
         "re.MULTILINE)",
         'tests/repeat_tool_test.py::FailuresTest'
         '::test_a_failing_subtest_and_a_spaced_id_are_named',
         'a failing subtest was not named'))

CASES += REPEAT_CASES


# --shard divides one run between machines. A slice that depends on which
# entries are selected, or on anything but the name, misses some entries and
# runs others twice, and the union of the shards still reads as complete.
SHARD_CASES = [
    Case('harness/slices-cover-every-entry-once',
         'tools/redproof.py',
         "    return int(sha(name.encode('utf-8')), 16) % n == k - 1",
         "    return int(sha(name.encode('utf-8')), 16) % n <= k - 1",
         'tests/redproof_tool_test.py::ShardTest'
         '::test_the_slices_of_a_run_cover_every_entry_once',
         'missed or repeated an entry'),
    Case('harness/a-slice-is-chosen-by-name-alone',
         'tools/redproof.py',
         '        selected = [c for c in selected if in_shard(c.name, shard)]',
         '        selected = [c for i, c in enumerate(selected)\n'
         '                    if i % shard[1] == shard[0] - 1]',
         'tests/redproof_tool_test.py::ShardTest'
         '::test_an_entry_keeps_its_slice_whatever_else_is_selected',
         'depends on the others'),
    Case('harness/an-empty-slice-is-not-a-missing-name',
         'tools/redproof.py',
         '    if not selected:\n        return None\n'
         '    if shard:\n'
         '        selected = [c for c in selected if in_shard(c.name, shard)]\n',
         '    if shard:\n'
         '        selected = [c for c in selected if in_shard(c.name, shard)]\n'
         '    if not selected:\n        return None\n',
         'tests/redproof_tool_test.py::ShardTest'
         '::test_an_empty_slice_is_not_a_failed_name',
         'reported as a name matching nothing'),
]

CASES += SHARD_CASES


# The interpreter pins (.python-versions). A verdict is keyed on the minor
# version only, so it is the pin that makes every rig's build the same one;
# a rig on another patch release sharing verdicts is what the pin prevents.
PIN_CASES = [
    Case('harness/only-the-pinned-build-keeps-verdicts',
         'tools/redproof.py',
         "    if sys.implementation.name != 'cpython' or pinned != running:",
         '    if False:',
         'tests/redproof_tool_test.py::PinTest'
         '::test_only_the_pinned_build_keeps_verdicts',
         'a build other than the pinned one was accepted'),
    Case('suite/every-supported-python-is-pinned',
         '.python-versions',
         '3.12.15\n',
         '',
         'tests/suite_config_test.py::PythonPinsTest'
         '::test_every_supported_python_is_pinned_exactly_once',
         'the pinned Pythons are not the supported ones'),
    Case('suite/ci-tests-every-pinned-python',
         '.github/workflows/python.yml',
         'python: ["3.10", "3.11", "3.12", "3.13", "3.14"]',
         'python: ["3.10", "3.11", "3.12", "3.14"]',
         'tests/suite_config_test.py::PythonPinsTest'
         '::test_ci_tests_the_pinned_pythons',
         'does not test every pinned Python'),
    Case('suite/ci-installs-no-unpinned-python',
         '.github/workflows/python.yml',
         "came from the job's own setup either way,",
         'came from setup-python either way,',
         'tests/suite_config_test.py::PythonPinsTest'
         '::test_ci_tests_the_pinned_pythons',
         'installs a Python the pin does not choose'),
]

CASES += PIN_CASES


# Round-one review of the harness (2026-10-24). Each breaks one guard the
# review found untested, or one of its fixes.
HARNESS_REVIEW_CASES = [
    # The expect text appeared in the test source pytest echoes, so a failure
    # on another assertion read as PROVEN: 150 entries were exposed.
    Case('harness/expect-is-matched-against-what-failed',
         'tools/redproof.py',
         '    said = failure_text(result.output)',
         '    said = result.output',
         'tests/redproof_tool_test.py::JudgeTest'
         '::test_expect_must_be_in_what_the_failure_says',
         'expect text was in the echoed source'),
    # A record keyed on the mutated run alone stayed KEPT after a file only
    # the passing baseline read had changed.
    Case('harness/a-record-keys-the-baseline-too',
         'tools/redproof.py',
         "            and holds_one(reads['baseline'], snapshot, None))",
         ')',
         'tests/redproof_tool_test.py::ReadsTest'
         '::test_what_only_the_baseline_read_keys_the_record_too',
         'only the baseline read had changed'),
    Case('harness/untracked-secret-names-are-not-copied',
         'tools/redproof.py',
         '    return [f for f in files if f not in tracked and '
         'SECRET_PATH.search(f)]',
         '    return []',
         'tests/redproof_tool_test.py::SnapshotTest'
         '::test_untracked_secret_names_are_left_out',
         'would be copied and digested'),
    Case('harness/a-renewal-does-not-undo-a-tombstone',
         'tools/redproof.py',
         "            return new if cls.when(new, 'proved') > cls.when(current) "
         "else (",
         '            return new if True else (',
         'tests/redproof_tool_test.py::StoreTest'
         '::test_a_renewal_does_not_undo_a_tombstone',
         'a renewal brought back a retired record'),
    Case('harness/a-prune-spares-a-record-used-since',
         'tools/redproof.py',
         '                if k in records and cutoff is not None and (',
         '                if False and (',
         'tests/redproof_tool_test.py::StoreTest'
         '::test_a_record_renewed_elsewhere_survives_a_prune',
         'another run had just used'),
    Case('harness/the-key-names-the-lock',
         'tools/redproof.py',
         "        'lock': sha(snapshot.content.get(LOCK, b'')),",
         "        'lock': '',",
         'tests/redproof_tool_test.py::KeyTest'
         '::test_the_key_names_the_lock_the_harness_and_the_pinned_build',
         'the key ignores the lock'),
    Case('harness/the-key-names-the-pinned-build',
         'tools/redproof.py',
         '                   pinned_python(snapshot)],',
         '                   ],',
         'tests/redproof_tool_test.py::KeyTest'
         '::test_the_key_names_the_lock_the_harness_and_the_pinned_build',
         'the key ignores the pin'),
    Case('harness/an-environment-unlike-the-lock-keeps-nothing',
         'tools/redproof.py',
         '    if differ:\n',
         '    if False:\n',
         'tests/redproof_tool_test.py::KeyTest'
         '::test_a_set_other_than_the_lock_keeps_nothing',
         'an environment unlike the lock was accepted'),
    Case('harness/a-port-group-shares-one-lock',
         'tools/redproof.py',
         "                if item == node or item.startswith(node + '::'):",
         '                if False:',
         'tests/redproof_tool_test.py::GroupLockTest'
         '::test_entries_sharing_an_xdist_group_share_a_lock',
         'ran without its lock'),
    Case('harness/a-changed-tracked-file-is-named',
         'tools/redproof.py',
         '                    if fh.read() == expected:',
         '                    if True:',
         'tests/redproof_tool_test.py::ChangedTrackedTest'
         '::test_a_tracked_file_left_changed_is_named',
         'a changed tracked file was not named'),
    Case('harness/a-failing-baseline-is-not-judged',
         'tools/redproof.py',
         '    if baseline.code != 0:',
         '    if False:',
         'tests/redproof_tool_test.py::JudgeTest'
         '::test_a_baseline_that_fails_is_not_judged',
         'fails unmutated'),
    Case('harness/a-run-with-no-status-proves-nothing',
         'tools/redproof.py',
         "    if result.code is None:\n        return 'RED?', result.killed, "
         "None\n",
         '',
         'tests/redproof_tool_test.py::JudgeTest'
         '::test_a_run_with_no_exit_status_proves_nothing',
         'a timed-out run was judged'),
    Case('harness/a-restored-tracked-write-is-reported',
         'tools/redproof.py',
         '    if audit is not None:\n        changed = sorted(',
         '    if False:\n        changed = sorted(',
         'tests/redproof_tool_test.py::JudgeTest'
         '::test_a_restored_write_to_a_tracked_file_is_reported',
         'a write the run restored went unreported'),
]

CASES += HARNESS_REVIEW_CASES
