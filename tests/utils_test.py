from unittest.mock import MagicMock


class PicklableResponse:
    '''Stands in for a response where the test really pickles.

    MagicMock is not picklable, so a round-trip test built on one can only ever
    call copy() -- which is in-process, and therefore not the thing the fix is
    about. Module level so pickle can find it by name.
    '''

    def __init__(self, marker='response'):
        self.marker = marker

    def __eq__(self, other):
        return (isinstance(other, PicklableResponse)
                and other.marker == self.marker)

from schwaby.utils import (
    AccountHashMismatchException,
    HTTPStatusError,
    MissingLocationHeaderError,
    OrderIdNotFoundError,
    SchwabError,
    UnrecognizedLocationError,
    UnsuccessfulOrderException,
    Utils,
)
from schwaby.utils import EnumEnforcer, _describe_error
from .utils import no_duplicates, MockResponse

import enum
import unittest


class HTTPStatusErrorTest(unittest.TestCase):
    """The name a consumer catches must be the class a real call raises.

    `httpx2` shares no exception hierarchy with `httpx`, so a handler written
    against the wrong package does not match and says nothing. The alias only
    helps if it is the class that actually escapes -- so this drives a real
    client through its real session stack rather than asserting a name.
    """

    @staticmethod
    def _client(status):
        import httpx2
        from schwaby.auth import client_from_access_functions

        token = {'access_token': 'a', 'refresh_token': 'r',
                 'token_type': 'Bearer', 'expires_in': 3600,
                 'expires_at': 9999999999}
        client = client_from_access_functions(
                'api-key', 'app-secret',
                lambda: {'creation_timestamp': 9999999999, 'token': token},
                lambda *args, **kwargs: None)
        client.session._transport = httpx2.MockTransport(
                lambda request: httpx2.Response(status, request=request))
        # A proxy variable routes requests through mounts, not `_transport`,
        # which would send this test to the network.
        client.session._mounts = {}
        return client

    @staticmethod
    def _refreshing_client(token_response, writes=None):
        # An access token that has already expired, so the session refreshes
        # it on the way past -- through the real authlib path, which is what
        # the documented behaviour depends on.
        import httpx2
        from schwaby.auth import client_from_access_functions

        token = {'access_token': 'a', 'refresh_token': 'r',
                 'token_type': 'Bearer', 'expires_in': 3600, 'expires_at': 1}
        requests = []

        def handler(request):
            requests.append(request.url.path)
            if request.url.path.endswith('/oauth/token'):
                return token_response(request)
            return httpx2.Response(200, json={}, request=request)

        client = client_from_access_functions(
                'api-key', 'app-secret',
                lambda: {'creation_timestamp': 9999999999, 'token': token},
                (lambda *args, **kwargs: None) if writes is None
                else (lambda written, *args, **kwargs: writes.append(written)))
        client.session._transport = httpx2.MockTransport(handler)
        client.session._mounts = {}   # see `_client`
        return client, requests

    @no_duplicates
    def test_a_server_error_refreshing_the_token_raises_out_of_the_call(self):
        # Documented: a 5xx from the token endpoint escapes the call itself,
        # before any response is handed back, and describes the token request
        # rather than the one that was made.
        import httpx2
        client, requests = self._refreshing_client(
                lambda request: httpx2.Response(503, request=request))
        with self.assertRaises(HTTPStatusError) as caught:
            client.get_quote('F')
        self.assertTrue(
                str(caught.exception.response.url).endswith('/oauth/token'))
        self.assertEqual(['/v1/oauth/token'], requests)

    @no_duplicates
    def test_a_refresh_rejected_with_an_error_body_is_a_refresh_error(self):
        import httpx2
        from schwaby.utils import TokenRefreshError
        client, requests = self._refreshing_client(
                lambda request: httpx2.Response(
                    400, json={'error': 'invalid_grant'}, request=request))
        with self.assertRaises(TokenRefreshError) as caught:
            client.get_quote('F')
        # Schwab's rejection, translated -- not a token that failed locally,
        # and not a call that never reached the endpoint.
        self.assertTrue(caught.exception.refresh_token_invalid)
        self.assertEqual(['/v1/oauth/token'], requests)

    @no_duplicates
    def test_a_refresh_response_that_is_not_a_token_is_not_stored(self):
        # authlib takes any JSON object without an `error` key as the new
        # token. {"message": "Unauthorized"} was written and kept, so every
        # call after it failed locally without contacting Schwab, until a new
        # login. Now it is neither: the next call refreshes again, and
        # recovers once the endpoint does.
        import httpx2
        from schwaby.utils import TokenRefreshError
        answers = [(401, {'message': 'Unauthorized'}),
                   (200, {'access_token': 'NEW', 'refresh_token': 'r2',
                          'token_type': 'Bearer', 'expires_in': 1800})]
        writes = []
        client, requests = self._refreshing_client(
                lambda request: httpx2.Response(
                    answers[0][0], json=answers.pop(0)[1], request=request),
                writes=writes)
        with self.assertRaises(TokenRefreshError) as caught:
            client.get_quote('F')
        self.assertFalse(caught.exception.refresh_token_invalid)
        self.assertEqual([], writes)
        self.assertEqual(200, client.get_quote('F').status_code)
        self.assertEqual(['/v1/oauth/token', '/v1/oauth/token',
                          '/marketdata/v1/F/quotes'], requests)
        self.assertEqual(1, len(writes))
        self.assertEqual('NEW', writes[0]['token']['access_token'])

    @no_duplicates
    def test_a_refresh_rejected_without_a_json_body_is_neither(self):
        # Documented as a limit, so pinned: the token response is parsed as
        # JSON, and an empty 4xx body is not JSON. If this ever becomes a
        # TokenRefreshError, the docs should say so.
        import httpx2
        import json
        from schwaby.utils import TokenRefreshError
        client, _ = self._refreshing_client(
                lambda request: httpx2.Response(401, request=request))
        with self.assertRaises(Exception) as caught:
            client.get_quote('F')
        self.assertIsInstance(caught.exception, json.JSONDecodeError)
        self.assertNotIsInstance(caught.exception, TokenRefreshError)
        self.assertNotIsInstance(caught.exception, HTTPStatusError)

    @no_duplicates
    def test_it_is_the_class_the_http_package_defines(self):
        import httpx2
        self.assertIs(httpx2.HTTPStatusError, HTTPStatusError)

    @no_duplicates
    def test_it_catches_what_a_real_client_call_raises(self):
        response = self._client(429).get_quote('F')
        with self.assertRaises(HTTPStatusError) as caught:
            response.raise_for_status()
        self.assertEqual(429, caught.exception.response.status_code)

    @no_duplicates
    def test_a_successful_call_does_not_raise(self):
        # Positive control: the same stub answering 200 does not raise, so the
        # refusal above comes from the status and not from the stub itself.
        self._client(200).get_quote('F').raise_for_status()

    @no_duplicates
    def test_it_is_not_a_schwab_error(self):
        # Stated in the docs, because `except SchwabError` is documented as
        # one name for everything this library defines, and this is not one.
        self.assertFalse(issubclass(HTTPStatusError, SchwabError))


class EnumEnforcerTest(unittest.TestCase):

    class TestClass(EnumEnforcer):
        def test_enforcement(self, value):
            self.convert_enum(value, EnumEnforcerTest.TestEnum)


    class TestEnum(enum.Enum):
        VALUE_1 = 1
        VALUE_2 = 2


    def test_valid_enum(self):
        t = self.TestClass(enforce_enums=True)
        t.test_enforcement(self.TestEnum.VALUE_1)

    def test_invalid_enum_passed_as_string(self):
        t = self.TestClass(enforce_enums=True)
        with self.assertRaisesRegex(
                ValueError, 'tests.utils_test.TestEnum.VALUE_1'):
            t.test_enforcement('VALUE_1')

    def test_invalid_enum_passed_as_not_string(self):
        t = self.TestClass(enforce_enums=True)
        with self.assertRaises(ValueError):
            t.test_enforcement(123)


class ConvertEnumIterableTest(unittest.TestCase):
    """`convert_enum_iterable`, which had no test for the rejecting branch.

    `convert_enum` next to it was covered both ways. This one was covered only
    where every element was already the right enum, so the arm that refuses a
    wrong element -- the reason `enforce_enums` exists -- was never taken.
    """

    class TestClass(EnumEnforcer):
        def convert(self, values):
            return self.convert_enum_iterable(
                    values, ConvertEnumIterableTest.TestEnum)

    class TestEnum(enum.Enum):
        VALUE_1 = 1
        VALUE_2 = 2

    @no_duplicates
    def test_a_wrong_element_is_refused_and_the_message_suggests_the_member(
            self):
        t = self.TestClass(enforce_enums=True)
        with self.assertRaisesRegex(
                ValueError, 'tests.utils_test.TestEnum.VALUE_1'):
            t.convert([self.TestEnum.VALUE_2, 'VALUE_1'])

    @no_duplicates
    def test_a_wrong_element_is_refused_even_without_a_suggestion(self):
        t = self.TestClass(enforce_enums=True)
        with self.assertRaises(ValueError):
            t.convert([123])

    @no_duplicates
    def test_a_string_matching_no_member_gets_no_did_you_mean(self):
        # `type_error` only offers a suggestion when the string appears in
        # some member's full name. Every existing test passed a string that
        # did, so the branch where nothing matches -- which is the common case
        # for a genuine typo -- was never taken, and a broken suggestion
        # builder would have gone unnoticed for exactly those callers.
        t = self.TestClass(enforce_enums=True)
        with self.assertRaises(ValueError) as ctx:
            t.convert(['nothing_like_a_member_name'])

        self.assertNotIn('Did you mean', str(ctx.exception))
        # Still says what was wrong, which is the part that has to survive.
        self.assertIn('TestEnum', str(ctx.exception))
        self.assertIn('str', str(ctx.exception))

    @no_duplicates
    def test_the_same_element_passes_through_when_enforcement_is_off(self):
        # The control that makes the two above mean something: they must fail
        # because enforcement rejected the value, not because the value could
        # never get through at all.
        t = self.TestClass(enforce_enums=False)
        self.assertEqual([2, 'VALUE_1'],
                         t.convert([self.TestEnum.VALUE_2, 'VALUE_1']))

    @no_duplicates
    def test_correct_elements_are_converted(self):
        t = self.TestClass(enforce_enums=True)
        self.assertEqual([1, 2],
                         t.convert([self.TestEnum.VALUE_1,
                                    self.TestEnum.VALUE_2]))


class DescribeErrorTest(unittest.TestCase):
    """`_describe_error` against a body that is not an object.

    It runs on the failure path, where a formatter that raises would replace a
    useful exception with a useless one -- so it yields no suffix rather than
    an error of its own. `[1,2,3]`, `"a string"` and `null` are all valid JSON
    and all raise AttributeError on `.get`, which is the shape the guard is
    for and which nothing sent it.
    """

    @no_duplicates
    def test_a_body_that_is_not_an_object_yields_no_suffix(self):
        for payload in ([1, 2, 3], 'a string', None, 42, True):
            with self.subTest(payload=payload):
                self.assertEqual(
                        '', _describe_error(MockResponse(payload, 400)))

    @no_duplicates
    def test_an_object_body_still_yields_its_message(self):
        # Positive control. Every assertion above is satisfied by a function
        # that returns '' unconditionally.
        described = _describe_error(
                MockResponse({'message': 'Account not found'}, 400))
        self.assertIn('Account not found', described)


class SetAccountHashTest(unittest.TestCase):
    """`Utils.set_account_hash`, which no test called.

    Public, documented on the util page with `automethod`, and reached by
    nothing -- so whether it takes effect on the next call was unverified.
    """

    @no_duplicates
    def test_the_new_hash_is_the_one_used_afterwards(self):
        client = MagicMock()
        u = Utils(client, '0xf1rsth45h')
        self.assertEqual('0xf1rsth45h', u.account_hash)

        u.set_account_hash('0x53c0ndh45h')
        self.assertEqual('0x53c0ndh45h', u.account_hash)

    @no_duplicates
    def test_it_is_the_hash_a_later_call_actually_sends(self):
        # The assertion above only checks the attribute. This checks that the
        # value is the one that leaves the process, which is the thing a
        # caller switching accounts is relying on.
        client = MagicMock()
        u = Utils(client, '0xf1rsth45h')
        u.set_account_hash('0x53c0ndh45h')

        # A real response rather than a MagicMock: `extract_order_id`
        # starts with `if place_order_response.is_error`, and every attribute
        # of a MagicMock is truthy, so a mock takes the rejection path no
        # matter what is set on it.
        response = MockResponse(
                None, 201,
                {'Location':
                 'https://api.schwabapi.com/trader/v1/accounts/'
                 '0x53c0ndh45h/orders/123456789'})
        self.assertEqual(123456789, u.extract_order_id(response))


class UtilsTest(unittest.TestCase):

    def setUp(self):
        self.mock_client = MagicMock()
        self.account_hash = '0xacc0unth45h'
        self.utils = Utils(self.mock_client, self.account_hash)

        self.order_id = 1

        self.maxDiff = None

    ##########################################################################
    # extract_order_id tests

    @no_duplicates
    def test_extract_order_id_order_not_ok(self):
        # assertRaises' `msg` is the message printed when the *assertion*
        # fails, not a pattern the exception has to match -- so the version of
        # this test that passed `msg='order not successful'` asserted nothing
        # about the exception at all and would have passed on any wording.
        response = MockResponse({}, 403)
        with self.assertRaisesRegex(
                UnsuccessfulOrderException, 'order not successful: status 403'):
            self.utils.extract_order_id(response)

    @no_duplicates
    def test_a_rejection_carries_schwabs_own_explanation(self):
        # A status code does not distinguish a malformed order from one the
        # account cannot afford. Schwab types an error body as
        # {"message": ..., "errors": [...]}, and that is the only place the
        # reason appears.
        response = MockResponse(
                {'message': 'Order validation failed',
                 'errors': ['Insufficient buying power']}, 400)

        with self.assertRaises(UnsuccessfulOrderException) as cm:
            self.utils.extract_order_id(response)

        self.assertIn('Order validation failed', str(cm.exception))
        self.assertIn('Insufficient buying power', str(cm.exception))
        self.assertIs(response, cm.exception.response)

    @no_duplicates
    def test_a_rejection_with_no_readable_body_still_raises(self):
        # The formatter runs on the failure path, so anything it cannot read
        # has to yield no detail rather than an exception of its own --
        # replacing a useful error with a useless one is worse than terse.
        class Unreadable(MockResponse):
            def json(self):
                raise ValueError('not json')

        response = Unreadable({}, 500)
        with self.assertRaisesRegex(
                UnsuccessfulOrderException, 'order not successful: status 500'):
            self.utils.extract_order_id(response)

    @no_duplicates
    def test_a_long_explanation_is_bounded(self):
        # This lands in a log line. The whole body stays reachable on
        # .response for anyone who wants the rest of it.
        response = MockResponse({'message': 'x' * 5000}, 400)

        with self.assertRaises(UnsuccessfulOrderException) as cm:
            self.utils.extract_order_id(response)

        self.assertLess(len(str(cm.exception)), 700)
        self.assertIn('truncated', str(cm.exception))
        self.assertEqual(5000, len(cm.exception.response.json()['message']))

    @no_duplicates
    def test_no_location_header_raises_rather_than_returning_none(self):
        # Both of these used to return None, which is also what plenty of
        # harmless things return, so `if order_id:` skipped an order Schwab had
        # very likely placed.
        response = MockResponse({}, 200, headers={})

        with self.assertRaises(MissingLocationHeaderError) as cm:
            self.utils.extract_order_id(response)

        self.assertIsNone(cm.exception.location)
        self.assertIs(response, cm.exception.response)
        self.assertIn('may be live', str(cm.exception))

    @no_duplicates
    def test_unparsable_location_raises_and_carries_the_header(self):
        response = MockResponse({}, 200, headers={'Location': 'not-a-match'})

        with self.assertRaises(UnrecognizedLocationError) as cm:
            self.utils.extract_order_id(response)

        # The header is the whole of the evidence for a bug report.
        self.assertEqual('not-a-match', cm.exception.location)
        self.assertIn('not-a-match', str(cm.exception))

    @no_duplicates
    def test_both_are_catchable_as_one_thing(self):
        # A caller that just wants "I have no order id" should not have to name
        # both, and should not accidentally catch a rejection with them.
        for headers in ({}, {'Location': 'not-a-match'}):
            with self.assertRaises(OrderIdNotFoundError):
                self.utils.extract_order_id(MockResponse({}, 200, headers=headers))

        self.assertFalse(
                issubclass(UnsuccessfulOrderException, OrderIdNotFoundError))

    @no_duplicates
    def test_a_broad_except_valueerror_does_not_swallow_it(self):
        # The point of raising was that a live, untracked order must not be
        # silent. Inheriting ValueError would have handed that back: it is the
        # idiom people reach for around int() and float(), and order specs
        # coerce exactly those a few lines from this call.
        self.assertFalse(issubclass(OrderIdNotFoundError, ValueError))

        with self.assertRaises(OrderIdNotFoundError):
            try:
                self.utils.extract_order_id(MockResponse({}, 200, headers={}))
            except ValueError:                       # pragma: no cover
                self.fail('a broad except ValueError swallowed it')

    @no_duplicates
    def test_the_two_siblings_keep_valueerror(self):
        # They had it before SchwabError existed, and code catching them that
        # way predates this release. Dropping it would break that for no gain;
        # they describe a caller mistake, which is what ValueError means.
        self.assertTrue(issubclass(UnsuccessfulOrderException, ValueError))
        self.assertTrue(issubclass(AccountHashMismatchException, ValueError))

    @no_duplicates
    def test_schwab_error_covers_every_exception_the_library_defines(self):
        # A base that covers most of them is worse than none: it invites
        # `except SchwabError` as a complete guard and is quietly not one.
        #
        # Walked, not listed. The first version named seven modules, so an
        # exception added to any module outside that list -- schwaby.debug, say
        # -- was simply not looked at, and the test went on passing while the
        # guarantee it states stopped being true.
        import importlib, inspect, pkgutil
        import schwaby

        # Seeded with `schwaby` itself: walk_packages yields only SUBmodules,
        # and schwaby/__init__.py already runs module-level code, so an
        # exception defined there would never be looked at. Verified by hand
        # rather than by mutation, because nothing in __init__.py raises today
        # so a mutation of the seed is green either way: with an exception
        # added there, the seeded walk fails and the unseeded one passes.
        #
        # onerror re-raises rather than defaulting to None, which silently
        # drops a subpackage out of the walk if one of its imports ever fails.
        # That one is defensive and cannot be exercised while every module
        # imports cleanly, which is the point of having it.
        def _boom(name):
            self.fail('could not import %s while walking' % name)

        modules, found, missing = ['schwaby'], {}, []
        for info in pkgutil.walk_packages(schwaby.__path__, 'schwaby.',
                                          onerror=_boom):
            modules.append(info.name)
        for name in modules:
            module = importlib.import_module(name)
            for attr, obj in vars(module).items():
                if (inspect.isclass(obj) and issubclass(obj, BaseException)
                        and obj.__module__ == name):
                    found['%s.%s' % (name, attr)] = obj
                    if not issubclass(obj, SchwabError):
                        missing.append('%s.%s' % (name, attr))

        # Controls for the walk itself, since an empty walk satisfies the
        # assertion below. Name specific classes from three separate modules
        # rather than counting: a count survives a whole module dropping out.
        self.assertGreater(len(modules), 8)
        for expected in ('schwaby.utils.OrderIdNotFoundError',
                         'schwaby.auth.RedirectTimeoutError',
                         'schwaby.streaming.ResponseTimeoutError',
                         'schwaby.orders.common.InvalidOrderException'):
            self.assertIn(expected, found)

        self.assertEqual([], missing)

    @no_duplicates
    def test_schwab_error_is_not_claimed_to_cover_bare_value_errors(self):
        # The library raises plain ValueError for argument validation in about
        # thirty places, so `except SchwabError` is NOT everything it can
        # throw. This pins the BEHAVIOUR; it does not read the docstring, so it
        # cannot stop the wording drifting back on its own -- the assertion
        # below does that part.
        from schwaby.orders.generic import OrderBuilder

        for label, bad in (('set_quantity(-1)',
                            lambda: OrderBuilder().set_quantity(-1)),
                           ('set_price(0.1)',
                            lambda: OrderBuilder().set_price(0.1))):
            with self.subTest(call=label):
                with self.assertRaises(ValueError) as cm:
                    bad()
                self.assertNotIsInstance(cm.exception, SchwabError)

        # Assert the positive statement rather than the absence of one
        # phrasing: "not everything" can be reworded a dozen ways, but the
        # docstring has to keep saying that builtin ValueError is still raised.
        # Skipped under -OO, where docstrings are stripped and __doc__ is None.
        if SchwabError.__doc__ is None:                  # pragma: no cover
            self.skipTest('docstrings stripped (-OO)')
        self.assertIn('ValueError', SchwabError.__doc__)
        self.assertIn('not', SchwabError.__doc__)

    @no_duplicates
    def test_every_exception_survives_a_process_boundary(self):
        # These carry the thing they are about as a leading positional and pass
        # only the message to BaseException, so the default reconstruction
        # called __init__ with the message alone: TypeError for most, and for
        # UnsuccessfulOrderException a copy that bound the message to
        # `response` and lost the message. Nothing in this library crosses a
        # process boundary with an exception; this is for callers who do -- a
        # ProcessPoolExecutor over placements -- where the one saying an order
        # is live on the wrong account must not arrive as a TypeError about
        # argument counts.
        import copy, importlib, inspect, pickle, pkgutil
        import schwaby

        r = PicklableResponse
        samples = {
            'SchwabError': ('m',),          # the base is a class too
            'UnexpectedResponse': (r(), 'm'),
            'UnexpectedResponseCode': (r(), 'm'),
            'UnparsableMessage': ('raw', ValueError('x'), 'm'),
            # ('m',) alone binds to the offending value, not to BaseException,
            # so str() would be '' and the message assertion below would
            # compare '' to '' and pass regardless.
            'UnusableMessage': ('frame', 'm'),
            'ResponseTimeoutError': ('svc', 'cmd', 60, 'm'),
            'UnsuccessfulOrderException': (r(), 'm'),
            'OrderIdNotFoundError': (r(), None, 'm'),
            'MissingLocationHeaderError': (r(), None, 'm'),
            'UnrecognizedLocationError': (r(), 'loc', 'm'),
            'AccountHashMismatchException': (r(), 123, 'BBBB', 'm'),
            'TokenRefreshError': ('m',),
            'RedirectTimeoutError': ('m',),
            'RedirectServerExitedError': ('m',),
            'InvalidOrderException': ('m',),
            'UnusableDecimalScale': ('m',),
        }

        # Seeded with `schwaby` for the same reason as the walk above: an
        # exception defined in schwaby/__init__.py is not a submodule, so it
        # would get no sample, never be round-tripped, and the count control
        # below would still pass because it counts only what the walk found.
        seen, modules = 0, ['schwaby']
        modules.extend(i.name for i in pkgutil.walk_packages(
                schwaby.__path__, 'schwaby.',
                onerror=lambda n: self.fail('could not import %s' % n)))

        for name in modules:
            module = importlib.import_module(name)
            for attr, obj in vars(module).items():
                if not (inspect.isclass(obj) and issubclass(obj, BaseException)
                        and obj.__module__ == name):
                    continue
                self.assertIn(attr, samples, 'new exception, add a sample')
                seen += 1
                exc = obj(*samples[attr])
                with self.subTest(exception=attr):
                    # pickle is the one that matters -- copy is in-process, so
                    # a test built only on it does not cross a boundary at all.
                    for rebuilt in (copy.copy(exc), copy.deepcopy(exc),
                                    pickle.loads(pickle.dumps(exc))):
                        self.assertIs(type(rebuilt), obj)
                        self.assertEqual(str(exc), str(rebuilt))
                        self.assertNotEqual('', str(rebuilt))

        # The walk found them, rather than the loop never running.
        self.assertEqual(len(samples), seen)

    @no_duplicates
    def test_the_attributes_survive_it_too(self):
        # A message that survives while .order_id does not would be the same
        # bug wearing a different face: the handler is told an order is live
        # and cannot reach it.
        import copy, pickle
        response = PicklableResponse('the original')
        exc = AccountHashMismatchException(
                response, 987, 'BBBB', 'm', expected_account_hash='AAAA')

        for rebuilt in (copy.copy(exc), pickle.loads(pickle.dumps(exc))):
            self.assertEqual(987, rebuilt.order_id)
            self.assertEqual('BBBB', rebuilt.account_hash)
            self.assertEqual('AAAA', rebuilt.expected_account_hash)
            # The historical defect was not a LOST response but a WRONG one --
            # the message bound into the response slot. assertIsNotNone cannot
            # see that; equality can.
            self.assertEqual(response, rebuilt.response)
            self.assertEqual('m', str(rebuilt))

    @no_duplicates
    def test_the_two_causes_are_distinguishable(self):
        # Without this, aliasing the two classes together passes every other
        # test here: each raise site names its own class, so both
        # assertRaises calls still match. Found by mutation, not by reading.
        self.assertIsNot(MissingLocationHeaderError, UnrecognizedLocationError)

        missing = MockResponse({}, 200, headers={})
        unparsable = MockResponse({}, 200, headers={'Location': 'nope'})

        # Catching one must not catch the other. A caller reconciling a
        # possibly-live order may want to treat a changed URL format -- which
        # is our bug -- differently from a header Schwab never sent.
        with self.assertRaises(MissingLocationHeaderError):
            self.utils.extract_order_id(missing)
        with self.assertRaises(UnrecognizedLocationError):
            try:
                self.utils.extract_order_id(unparsable)
            except MissingLocationHeaderError:      # pragma: no cover
                self.fail('unparsable Location raised the missing-header type')

    @no_duplicates
    def test_get_order_nonmatching_account_hash(self):
        response = MockResponse({}, 200, headers={
            'Location':
            'https://api.schwabapi.com/trader/v1/accounts/badhash/orders/123'})

        with self.assertRaises(AccountHashMismatchException) as cm:
            self.utils.extract_order_id(response)

        # This fires only after the response came back successful AND an order
        # id parsed out of it, so an order really was placed -- on an account
        # the caller was not expecting to trade. Everything needed to go and
        # cancel it is on the exception rather than left in the message for
        # somebody to re-derive with a regex.
        self.assertEqual(123, cm.exception.order_id)
        self.assertEqual('badhash', cm.exception.account_hash)
        # Both hashes: a handler far from the call site has no Utils left to
        # ask which account it meant, and should not have to parse the message.
        self.assertEqual(self.utils.account_hash,
                         cm.exception.expected_account_hash)
        self.assertNotEqual(cm.exception.account_hash,
                            cm.exception.expected_account_hash)
        self.assertIs(response, cm.exception.response)
        self.assertIn('is live', str(cm.exception))

    @no_duplicates
    def test_the_mismatch_says_the_order_exists(self):
        # The old message was "order request account hash != Utils.account_hash",
        # and the docstring called it a wiring problem "rather than the order".
        # Both read as a configuration complaint. An order had been placed.
        response = MockResponse({}, 200, headers={
            'Location':
            'https://api.schwabapi.com/trader/v1/accounts/badhash/orders/123'})

        with self.assertRaises(AccountHashMismatchException) as cm:
            self.utils.extract_order_id(response)

        message = str(cm.exception)
        self.assertIn('123', message)        # which order
        self.assertIn('badhash', message)    # on which account

    @no_duplicates
    def test_get_order_success_200(self):
        order_id = 123456
        response = MockResponse({}, 200, headers={
            'Location':
            'https://api.schwabapi.com/trader/v1/accounts/{}/orders/{}'.format(
                self.account_hash, order_id)})
        self.assertEqual(order_id, self.utils.extract_order_id(response))

    @no_duplicates
    def test_get_order_success_201(self):
        order_id = 123456
        response = MockResponse({}, 201, headers={
            'Location':
            'https://api.schwabapi.com/trader/v1/accounts/{}/orders/{}'.format(
                self.account_hash, order_id)})
        self.assertEqual(order_id, self.utils.extract_order_id(response))


