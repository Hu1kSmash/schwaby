from abc import ABC, abstractmethod
from collections import defaultdict, deque
from enum import Enum

import asyncio
import copy
import httpx2
import inspect
import json
import logging

import websockets.asyncio.client as ws_client

from .utils import EnumEnforcer, LazyLog, SchwabError


class StreamJsonDecoder(ABC):
    @abstractmethod
    def decode_json_string(self, raw):
        '''
        Parse a JSON-formatted string into a proper object. Raises
        ``JSONDecodeError`` on parse failure.
        '''
        raise NotImplementedError()


class NaiveJsonStreamDecoder(StreamJsonDecoder):
    def decode_json_string(self, raw):
        return json.loads(raw)


def get_logger():
    return logging.getLogger(__name__)


class _BaseFieldEnum(Enum):
    @classmethod
    def all_fields(cls):
        return list(cls)

    @classmethod
    def key_mapping(cls):
        try:
            return cls._key_mapping
        except AttributeError:
            # Iterate the enum rather than __members__, which also yields
            # aliases. An alias is a second spelling of a field, not a second
            # field, and it must not decide what the field is labeled.
            cls._key_mapping = dict(
                (str(enum.value), enum.name) for enum in cls)
            return cls._key_mapping

    @classmethod
    def relabel_message(cls, old_msg, new_msg):
        # Make a copy of the items so we can modify the dict during iteration
        for old_key, value in list(old_msg.items()):
            if old_key in cls.key_mapping():
                new_key = cls.key_mapping()[old_key]
                new_msg[new_key] = new_msg.pop(old_key)


class UnexpectedResponse(SchwabError):
    def __init__(self, response, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.response = response


class UnexpectedResponseCode(SchwabError):
    def __init__(self, response, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.response = response


class UnparsableMessage(SchwabError):
    '''A frame whose JSON did not decode.

    **This one still ends the caller's receive loop**, unlike
    :class:`UnusableMessage`, which is absorbed. Both are reported to
    ``add_error_handler``; only this one is also raised.

    The difference is what is known about what was missed. A structurally
    unusable element can be skipped precisely: the elements beside it are still
    delivered, and the caller is told which one went. A frame which will not
    parse has unknown contents -- it might have carried a fill -- so continuing
    means silently accepting a gap of unknown size. Tearing the stream down
    causes a reconnect and a re-subscribe, which is the one thing that can
    recover state; skipping cannot.

    That is a deliberate choice rather than an oversight, but it was made after
    2.4.0 rather than during it, and the argument is not one-sided.
    ``schwab.contrib.util.HeuristicJsonDecoder`` exists because Schwab really
    does emit JSON this library cannot parse, which is evidence for a
    frame-level quirk rather than a dead stream. If your feed hits this often
    and a reconnect is worse for you than a gap, set that decoder before
    concluding the stream is broken.

    ``raw_msg`` is what arrived, and reaches ``add_error_handler`` as the
    ``message`` argument. It can be the empty string --- an empty text frame
    gives ``json.loads`` nothing to parse --- so such a report is
    distinguishable from the logout-close failure under ``is None`` but *not*
    under a falsy test. Discriminate on the exception type.
    '''
    def __init__(self, raw_msg, json_parse_exception, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.raw_msg = raw_msg
        self.json_parse_exception = json_parse_exception


class UnusableMessage(SchwabError):
    '''A message which decoded successfully but which this client cannot use.

    Distinct from :class:`UnparsableMessage`, which means the JSON itself did
    not decode and carries the parse exception. Here the JSON is fine and the
    structure is not what the protocol allows -- a frame which is not an
    object, an element of ``data`` which is not an object, a ``service`` which
    is not a name. Reusing ``UnparsableMessage`` would make one type mean two
    shapes, with ``json_parse_exception`` set for one of them and ``None`` for
    the other.

    ``message`` is the offending value, exactly as it arrived.

    ``cause`` is the exception which made it unusable, where there was one --
    a relabeling failure carries the ``KeyError`` from the field tables, which
    is the only thing that says *which* field moved. ``None`` where the message
    was rejected on inspection rather than by failing.

    ``count`` and ``total`` are how many of this kind, and how many in all,
    have been absorbed on this connection. These are suppressed after the first
    few, so the numbers are the only way to see the true scale -- as integers
    rather than only in the message text, since a consumer alerting on drop
    volume should not have to parse prose.
    '''
    def __init__(self, message, *args, cause=None, count=None, total=None,
                 **kwargs):
        super().__init__(*args, **kwargs)
        self.message = message
        self.cause = cause
        self.count = count
        self.total = total


class _Handler:
    def __init__(self, func, field_enum_type):
        self._func = func
        self._field_enum_type = field_enum_type

    def __call__(self, *args, **kwargs):
        return self._func(*args, **kwargs)

    def label_message(self, msg):
        if 'content' in msg:
            new_msg = copy.deepcopy(msg)
            for idx in range(len(msg['content'])):
                self._field_enum_type.relabel_message(msg['content'][idx],
                                                      new_msg['content'][idx])
            return new_msg
        else:
            return msg


# Kwargs which websockets 14.0 renamed when it introduced the asyncio
# implementation, mapped to their current names.
_RENAMED_CONNECT_ARGS = {
    'extra_headers': 'additional_headers',
}

# Kwargs which websockets 14.0 removed outright, with no equivalent in the
# asyncio implementation.
_REMOVED_CONNECT_ARGS = ('create_protocol', 'read_limit')


def _prepare_connect_args(websocket_connect_args):
    '''
    Checks user-provided ``websocket_connect_args`` against the ``websockets``
    asyncio client, and returns a copy for this library to add to.

    ``StreamClient.login`` documents ``websocket_connect_args`` as a
    passthrough to ``connect()``, so a caller may still be passing a name which
    was valid before websockets 14.0. Both the renamed and the removed names
    are refused here with a message naming the replacement, rather than being
    left to surface as an opaque ``TypeError`` from inside ``websockets``.

    Earlier versions translated the renames automatically and warned. That kept
    working code working, but it also meant the passthrough this argument
    promises was not a passthrough: the name the caller wrote was not the name
    ``websockets`` received, which is a confusing thing for a library to do to
    an argument documented as going straight through.

    The copy matters -- ``login`` adds ``ssl`` to what it gets back, and the
    dict belongs to the caller.
    '''
    for old_name, new_name in _RENAMED_CONNECT_ARGS.items():
        if old_name not in websocket_connect_args:
            continue

        # Both names is the likelier half-migration: the new key was added and
        # the old one left behind. Telling that caller to "pass
        # additional_headers instead" names what they are already passing.
        if new_name in websocket_connect_args:
            raise ValueError(
                'websocket_connect_args contains both {!r} and {!r}. {!r} is '
                'the name websockets has used since 14.0; please drop '
                '{!r}.'.format(old_name, new_name, new_name, old_name))

        raise ValueError(
            'websocket_connect_args[{!r}] was renamed to {!r} in '
            'websockets 14.0. Please pass {!r} instead.'.format(
                old_name, new_name, new_name))

    for removed in _REMOVED_CONNECT_ARGS:
        if removed in websocket_connect_args:
            raise ValueError(
                'websocket_connect_args[{!r}] was removed in websockets 14.0 '
                'and has no equivalent in the asyncio implementation. Please '
                'remove it.'.format(removed))

    return dict(websocket_connect_args)


class ResponseTimeoutError(SchwabError):
    '''
    Raised when the streaming server accepts a request but does not send a
    response to it within the configured timeout. Distinct from the connection
    failing, which surfaces as a ``websockets`` exception.
    '''
    def __init__(self, service, command, timeout, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.service = service
        self.command = command
        self.timeout = timeout


# Returned by _read_and_route for a frame it delivered to a waiting request.
# A plain None cannot serve: a top-level JSON null is a frame a server can
# send, and treating it as "already routed" dropped it silently.
ROUTED = object()


def _is_mapping(obj):
    '''Whether ``obj`` can be read the way a decoded JSON object is read.

    Structural, not ``isinstance(obj, Mapping)``. ``set_json_decoder`` is a
    public hook promising only "the decoded JSON", and the reads it replaced --
    ``'response' in frame``, ``msg.get('data')`` -- work on anything providing
    them. An ABC matches only real subclasses and explicitly registered types,
    so a lightweight mapping-like object worked before the type checks were
    added and would have had every frame dropped afterwards.
    '''
    return hasattr(obj, 'get') and hasattr(obj, '__contains__')


def _is_power_of_ten(n):
    while n >= 10 and n % 10 == 0:
        n //= 10
    return n == 1


def _is_sequence(obj):
    '''Whether ``obj`` can be used the way a decoded JSON array is.

    Indexable, not merely iterable. Routing reads ``frame['response'][0]`` and
    validation reads it again, and handlers are given the frame afterwards, so
    a single-pass iterable cannot serve however tolerant the iteration is: a
    decoder returning generators made every response frame fall into the "no
    usable request id" branch and every subscribe time out. Tuples work, which
    is the realistic custom-decoder case; generators never could.

    Strings and bytes are indexable and are not arrays; a mapping is iterable
    and yields its keys, which is how ``{'notify': {...}}`` would have reached
    handlers as a message per key.
    '''
    return (hasattr(obj, '__getitem__')
            and hasattr(obj, '__len__')
            and not isinstance(obj, (str, bytes, bytearray))
            and not _is_mapping(obj))


class StreamClient(EnumEnforcer):

    #: Seconds to wait for the streaming server to respond to a request before
    #: giving up. A request which is never answered would otherwise wait
    #: forever, and only one request is outstanding at a time, so it would also
    #: block every subsequent one.
    DEFAULT_RESPONSE_TIMEOUT = 60.0

    def __init__(self, client, *,
                 enforce_enums=True, ssl_context=None,
                 response_timeout=DEFAULT_RESPONSE_TIMEOUT):
        super().__init__(enforce_enums)

        self._ssl_context = ssl_context
        self._client = client
        self._response_timeout = response_timeout

        # Set by the login() function
        self._account = None
        self._stream_correl_id = None
        self._stream_customer_id = None
        self._stream_channel = None
        self._stream_function_id = None
        self._socket = None

        # Internal fields
        self._request_id = 0
        self._handlers = defaultdict(list)

        # Callbacks for failures this client absorbs rather than raising. See
        # add_error_handler.
        self._error_handlers = []

        # Tasks for handlers which turned out to be coroutines. The event loop
        # only holds a weak reference to a running task, so a task which nothing
        # else refers to can be garbage collected before it finishes. Hold onto
        # them until they complete.
        self._handler_tasks = set()

        # When listening for responses, we sometimes encounter non-response
        # messages. Since this happens outside the context of the handler
        # dispatcher, we cannot handle these messages. However, we still need to
        # deliver these messages. This list records the messages that were read
        # from the stream but not handled yet. Messages should be read from this
        # list before they are read from the stream.
        self._overflow_items = deque()

        # Rejections found riding along in a frame which answered an
        # outstanding request, waiting to be reported. They are found while the
        # read lock is held -- on the request path, the request lock and the
        # response deadline too -- which is no place to run a user handler, so
        # they are set aside and reported from handle_message the way an
        # overflow frame is. Bounded because nothing guarantees handle_message
        # is ever called again; the log line written when each is found is the
        # durable record, and the callback is the convenience, so dropping the
        # oldest loses nothing that was not already written down.
        self._pending_reports = deque(maxlen=64)

        # How many messages this connection could not use, in total and by
        # kind. The count is per kind so that a flood of one does not silence
        # a different one; the total keeps the log line honest about scale.
        self._absorbed = 0
        self._absorbed_kinds = defaultdict(int)

        # Logging-related fields
        self.logger = get_logger()
        self.request_number = 0

        # Initialize the JSON parser to be the naive parser which directly calls
        # ``json.loads``
        self.json_decoder = NaiveJsonStreamDecoder()

        # Guarantees a single reader. websockets raises if two coroutines call
        # recv() concurrently, so exactly one coroutine may be inside a read at
        # a time -- but unlike the lock this replaces, it is not held while
        # waiting for a response, so a request does not block message handling.
        self._read_lock = asyncio.Lock()

        # Keeps one request outstanding at a time, so a response can be matched
        # against the request it answers without ambiguity.
        self._request_lock = asyncio.Lock()

        # (request_id, service, command, future) for the request currently
        # awaiting a response, or None. Whoever is reading routes the matching
        # response here rather than the reader having to be the requester.
        self._pending_request = None

    def set_json_decoder(self, json_decoder):
        '''
        Sets a custom JSON decoder.

        :param json_decoder: Custom JSON decoder to use for to decode all
                             incoming JSON strings. See
                             :class:`StreamJsonDecoder` for details.
        '''
        # The local name, not schwab.contrib.util's. They are the same class
        # -- contrib.util imports it from here -- but reaching it through the
        # package attribute raises AttributeError unless the caller happens to
        # have imported schwab.contrib.util, which someone subclassing the
        # class where it is actually defined has no reason to have done.
        if not isinstance(json_decoder, StreamJsonDecoder):
            raise ValueError('Custom JSON parser must be a subclass of ' +
                             'schwab.contrib.util.StreamJsonDecoder')
        self.json_decoder = json_decoder

    @staticmethod
    def _pretty(obj):
        """json.dumps for a debug line, falling back to repr.

        set_json_decoder is a public hook promising only "the decoded JSON", so
        a decoder returning a Mapping which is not a dict, or tuples for
        arrays, produces something json.dumps refuses.

        This is cosmetic, not a crash fix: logging swallows a formatting
        failure and prints a "--- Logging error ---" block to stderr, so the
        stream survives either way. What it costs without this is the content
        of every debug line replaced by a traceback, for exactly the people who
        customised the decoder and are most likely to be debugging it.

        The exception does not end the receive loop. Note that a test run can
        suggest otherwise, because pytest's capture handler re-raises where
        logging would have swallowed it.
        """
        try:
            return json.dumps(obj, indent=4)
        except (TypeError, ValueError):
            return repr(obj)

    def req_num(self):
        self.request_number += 1
        return self.request_number

    async def _send(self, obj):
        if self._socket is None:
            raise ValueError(
                'Socket not open. Did you forget to call login()?')

        self.logger.debug('Send %s: Sending %s',
                self.req_num(), LazyLog(lambda: self._pretty(obj)))

        await self._socket.send(json.dumps(obj))

    async def _receive_from_socket(self):
        if self._socket is None:
            raise ValueError(
                'Socket not open. Did you forget to call login()?')

        raw = await self._socket.recv()
        try:
            ret = self.json_decoder.decode_json_string(raw)
        except json.decoder.JSONDecodeError as e:
            msg = ('Failed to parse message. This often happens with ' +
                   'unknown symbols or other error conditions. Full ' +
                   'message text: ' + raw)
            raise UnparsableMessage(raw, e, msg)

        self.logger.debug(
            'Receive %s: Returning message from stream: %s',
            self.req_num(), LazyLog(lambda: self._pretty(ret)))

        return ret

    async def _receive(self):
        if self._socket is None:
            raise ValueError(
                'Socket not open. Did you forget to call login()?')

        if len(self._overflow_items) > 0:
            ret = self._overflow_items.pop()

            self.logger.debug(
                'Receive %s: Returning message from overflow: %s',
                self.req_num(), LazyLog(lambda: self._pretty(ret)))

            return ret

        return await self._receive_from_socket()

    async def _init_from_preferences(self, prefs, websocket_connect_args):
        # Record streamer subscription keys
        stream_info = prefs['streamerInfo'][0]

        self._stream_correl_id = stream_info['schwabClientCorrelId']
        self._stream_customer_id = stream_info['schwabClientCustomerId']
        self._stream_channel = stream_info['schwabClientChannel']
        self._stream_function_id = stream_info['schwabClientFunctionId']

        # Initialize socket
        wss_url = stream_info['streamerSocketUrl']

        websocket_connect_args = _prepare_connect_args(websocket_connect_args)

        if self._ssl_context:
            websocket_connect_args['ssl'] = self._ssl_context

        # Close whatever is being replaced. After a ConnectionClosed the old
        # socket is already gone and this is a no-op, but a caller who calls
        # login() on a healthy client -- a re-auth, a preferences refresh --
        # would otherwise drop a live websocket and its reader on the floor
        # with nothing closing it. Failure to close is logged, not raised: the
        # login is the operation the caller asked for.
        previous, self._socket = self._socket, None
        if previous is not None:
            try:
                await previous.close()
            except Exception:
                self.logger.exception(
                        'Failed to close the previous stream connection while '
                        'logging in again. Continuing with the new one.')

        self._socket = await ws_client.connect(
                wss_url, **websocket_connect_args)

        # Cleared *after* the new socket is in place, not before. Anything
        # still queued belongs to the connection being replaced: its exception
        # carries a frame from that session, and a stale data frame would reach
        # handlers as though it were live. Clearing first left a hole -- a
        # concurrent reader on the old socket appends to _overflow_items while
        # this coroutine is suspended in close() or connect(), and that frame
        # would survive into the new session.
        #
        # close() clears both too, but a caller reconnecting after a
        # ConnectionClosed may call login() again without closing first, and
        # the guarantee has to hold whichever teardown they used.
        self._pending_reports.clear()
        self._overflow_items.clear()
        self._absorbed = 0
        self._absorbed_kinds.clear()


    def _make_request(self, *, service, command, parameters):
        request_id = self._request_id
        self._request_id += 1

        request = {
            'service': service,
            'requestid': str(request_id),
            'command': command,
            'SchwabClientCustomerId': self._stream_customer_id,
            'SchwabClientCorrelId': self._stream_correl_id,
            'parameters': parameters,
        }

        return request, request_id

    @staticmethod
    def _validate_response(resp, request_id, service, command):
        '''
        Checks a response frame against the request it is supposed to answer.
        Returns ``None`` if it matches, or the exception to fail that request
        with if it does not.

        A frame too malformed to check is *returned as* an exception rather
        than raising one. Raising here reaches _read_and_route, which hands it
        to _fail_pending_request and re-raises -- so one unreadable field
        failed the request with a bare KeyError and ended the caller's receive
        loop as well. Failing just the request, with an exception that says
        what was wrong, is what the caller can act on: the answer to their
        request really was unusable.
        '''
        try:
            first = resp['response'][0]
            resp_request_id = int(first['requestid'])

            # Compared before the rest is read. Reading all five first meant a
            # frame carrying an id this client never issued *and* missing some
            # other field was reported as "malformed response frame: KeyError:
            # 'service'". The id mismatch is the more diagnostic fact -- it
            # means the server and this client disagree about what was asked --
            # and it was hidden behind whichever field happened to be absent.
            mismatched_id = (resp_request_id
                             if resp_request_id != request_id else None)

            resp_service = first['service'] if mismatched_id is None else None
            resp_command = first['command'] if mismatched_id is None else None
            content = first['content'] if mismatched_id is None else {}
            resp_code = content['code'] if mismatched_id is None else 0
        except (AttributeError, IndexError, KeyError, TypeError,
                ValueError) as exc:
            # Only the reads are inside the try. Wrapping the whole check
            # would catch a bug of ours -- a signature change missing this
            # call site raises TypeError -- and report it to the caller as a
            # malformed frame from Schwab, carrying that frame as evidence.
            # Every subscribe would fail with the investigation pointed at the
            # venue rather than at this library.
            return UnexpectedResponse(
                resp, 'malformed response frame: {}: {}'.format(
                    type(exc).__name__, exc))

        # Built outside the try above, which exists to turn *reads* of a
        # malformed frame into a clean error. Constructing the exception in
        # there would mean a signature change to UnexpectedResponse reported
        # every id mismatch as "malformed response frame: TypeError", blaming
        # the venue for a bug of ours -- the exact misattribution that try is
        # narrow to avoid.
        if mismatched_id is not None:
            return UnexpectedResponse(
                resp, 'unexpected requestid: {}'.format(mismatched_id))

        # Validate service
        if resp_service != service:
            return UnexpectedResponse(
                resp, 'unexpected service: {}'.format(resp_service))

        # Validate command
        if resp_command != command:
            return UnexpectedResponse(
                resp, 'unexpected command: {}'.format(resp_command))

        # `msg` is read with .get: a rejection which carries a code but no
        # message is still a rejection, and reporting it as an unreadable
        # frame instead would lose the code -- the one part the caller can act
        # on.
        if resp_code != 0:
            return UnexpectedResponseCode(
                resp,
                'unexpected response code: {}, msg is \'{}\''.format(
                    resp_code, content.get('msg')))

        return None

    async def _read_and_route(self, *, use_overflow=True):
        '''
        Reads one frame and routes it. The caller must hold ``_read_lock``,
        which is what guarantees a single reader: ``websockets`` raises if two
        coroutines call ``recv()`` concurrently.

        Returns the frame if it is for the caller to deal with, or ``ROUTED``
        if it was a response which has been delivered to the operation waiting
        on it.

        ``ROUTED`` rather than ``None`` because ``None`` is a frame a server
        can send: a top-level JSON ``null`` was indistinguishable from a routed
        response, so it was dropped without the warning every other unusable
        frame gets.

        ``use_overflow`` is ``False`` for a caller waiting on a response, which
        must read from the socket rather than from the queue of frames set
        aside for ``handle_message`` -- otherwise it would take back the frames
        it just deferred, and never get past them.
        '''
        frame = (await self._receive() if use_overflow
                 else await self._receive_from_socket())

        # `'response' not in frame` is itself a read which assumes a container,
        # and raises on a top-level JSON number. Handed back rather than
        # dropped here, so handle_message logs it in one place along with every
        # other frame this cannot route.
        if not _is_mapping(frame):
            return frame

        if 'response' not in frame:
            return frame

        pending = self._pending_request
        if pending is None:
            # A response nobody is waiting for. Hand it back so the caller can
            # complain about it.
            return frame

        request_id, service, command, future = pending

        # A response carrying an id we issued *earlier* is the late answer to a
        # request which was abandoned -- timed out, or cancelled. It is not
        # this request's answer and says nothing about it.
        #
        # Failing the pending request with it would be wrong twice over. The
        # innocent request dies, and the abandoned request's answer is consumed
        # in the process, so the next request reads the *next* stale answer and
        # dies the same way. One timeout would wedge every subsequent request
        # for the life of the stream. Hand it back instead, and let
        # handle_message log it as the orphan it is.
        #
        # An id we have never issued is a different matter. That is not
        # lateness, it is the server and this client disagreeing about what was
        # asked, and the request being waited on has no better prospect than
        # the one which just arrived. Let that fail below, as it always has.
        # Read defensively. Everything downstream of here parses through
        # _iter_responses, which logs a malformed element and skips it -- but
        # that guard is unreachable if reading the id raises first. A frame
        # whose `response` is not a list, or whose element 0 has no usable
        # requestid, would take out the in-flight request through
        # _fail_pending_request and end the caller's receive loop, while the
        # identical frame arriving with nothing pending was logged and
        # harmless. That is the framing dependence this whole change exists to
        # remove, one level below where it was fixed.
        #
        # Handed back rather than raised: handle_message logs and skips it,
        # which is what it does with the same frame today when no request is
        # outstanding.
        try:
            response_id = int(frame['response'][0]['requestid'])
        except (AttributeError, IndexError, KeyError, TypeError, ValueError):
            # Through _absorb like every other unusable message, so it is
            # counted, coalesced and reported. Logged directly, this was the
            # one such path with no programmatic signal at all -- and it
            # repeats per re-read, so a venue emitting a non-numeric requestid
            # produced a continuous warning stream inside a single subscribe.
            self._absorb('a response frame with no usable request id',
                         frame.get('response'), frame=frame)
            return frame

        if response_id != request_id and response_id < self._request_id:
            return frame

        if not future.done():
            error = self._validate_response(frame, request_id, service, command)
            if error is None:
                future.set_result(frame)
            else:
                future.set_exception(error)

            # Element 0 went to the waiter, so it is accounted for.
            first_unclaimed = 1
        else:
            # The future was already resolved -- the waiter timed out, or was
            # cancelled, and this frame arrived in the window before the
            # pending slot was cleared. Nothing is waiting for element 0
            # either, so it is as unclaimed as the rest. Without this, a
            # rejection at element 0 would be dropped in silence while a
            # rejection at element 1 of the same frame was reported, which is
            # the inverse of the framing-independence this exists for.
            first_unclaimed = 0

        # Everything not claimed above is logged and, if it is a rejection,
        # queued for report. The protocol treats element 0 as the answer to the
        # outstanding request, and _validate_response reads only that one, so a
        # frame carrying a second response has always handed it to the waiter
        # unexamined.
        #
        # Logged here, but NOT reported here. This runs holding the read
        # lock, on the request path holding the request lock too, and inside
        # the response deadline -- so calling a user handler here would let a
        # slow one turn a subscription that SUCCEEDED into a
        # ResponseTimeoutError, and a handler that re-subscribed would block on
        # a lock its own caller holds. The report is queued and delivered from
        # handle_message, which holds neither lock and runs under no deadline.
        self._log_extra_responses(frame, first_unclaimed)

        return ROUTED

    def _iter_responses(self, frame, start=0):
        '''Yields ``(response, code, content)`` for the responses in a frame.

        Shared by both framings of the same event -- a late rejection in a
        frame of its own, and one batched behind the answer to a live request
        -- because the contract is that they behave alike, and two loops
        parsing the same JSON drift. One was hardened against a malformed
        element and the other was not, so an identical payload was skipped with
        a warning in one framing and ended the caller's receive loop with an
        AttributeError in the other.

        Nothing here raises. On the routing path this runs after the waiter's
        future is resolved, so an exception escaping would reach the caller of
        a request which SUCCEEDED; in handle_message it would end the receive
        loop over one bad element among many.
        '''
        responses = frame.get('response')
        if not _is_sequence(responses):
            self._absorb('a response frame whose "response" is not a list',
                         responses, frame=frame)
            return

        for response in responses[start:]:
            if not _is_mapping(response):
                self._absorb('an unparseable response element', response,
                             frame=frame)
                continue

            # Normalised, not just defaulted. The key is often present with a
            # JSON null, which .get(key, {}) returns as None -- and `or {}`
            # alone is not enough either, because a *truthy* non-mapping (a
            # string) survives it and is then handed to callers which call
            # .get on it. handle_message's orphan loop does exactly that.
            content = response.get('content')
            if not _is_mapping(content):
                content = {}

            code = content.get('code')

            yield response, code, content

    def _absorb(self, what, offender, frame=None, service=None,
                cause=None):
        """Records a message this client cannot use.

        Two things happen to it, and each earns its place:

        * a WARNING, so it is visible without code changes -- but only for the
          first few. A systematically malformed high-volume channel would
          otherwise emit one line per element per tick indefinitely, turning a
          data outage into a log-volume incident as well. Powers of ten, so the
          running total stays visible without the flood.
        * a report through add_error_handler, because that callback's whole
          purpose is that a failure this client absorbs should not be visible
          only in a log. Queued the same way a batched rejection is, and
          delivered from the same drain, so no user code runs here.
        """
        self._absorbed += 1
        self._absorbed_kinds[what] += 1
        n = self._absorbed_kinds[what]

        # Counted per kind, not across all of them. One shared counter meant a
        # framing change on a few hundred symbols drove the count past a
        # thousand within two ticks, after which a *different* fault -- a
        # notify frame whose service is a list -- was neither logged nor
        # reported, so an operator investigating the first outage saw no trace
        # of the second.
        #
        # The first few of each kind, then powers of ten. A fixed modulus would
        # go quiet after the third occurrence and say nothing more until the
        # thousandth, hiding the shape of an early burst.
        if n > 3 and not _is_power_of_ten(n):
            return

        self.logger.warning(
                'Ignoring %s: %r. (%d of these, %d in all, on this '
                'connection.)%s', what, offender, n, self._absorbed,
                '' if cause is None
                else ' Cause: {}: {}'.format(type(cause).__name__, cause))

        # Reported on the same schedule as the log, not on every occurrence.
        # These share _pending_reports with the late rejections, which is a
        # bounded deque: a frame carrying a few hundred bad elements would
        # otherwise evict every rejection queued beside it -- and a rejection
        # of an abandoned request is the one thing nothing else will ever
        # report. Coalescing keeps this callback from starving the reports it
        # was built for.
        self._pending_reports.append((
                UnusableMessage(
                    offender,
                    'Ignoring {} ({} of these, {} in all, on this '
                    'connection){}'.format(
                        what, n, self._absorbed,
                        '' if cause is None
                        else ': {}: {}'.format(type(cause).__name__, cause)),
                    cause=cause, count=n, total=self._absorbed),
                # Usually None -- a message whose shape could not be read has
                # no service name to be had -- but not always: a relabeling
                # failure knows exactly which subscription went dark, and a
                # handler routing alerts by service needs it.
                service,
                # The containing frame, not the offending value, because the
                # value is often None -- a null channel, a null element -- and
                # a report of (None, None) is the signature the logout-close
                # failure already uses. A consumer discriminating on "both
                # None" would file a dead feed under "could not close after
                # logout". Where there is no containing frame, the offender is
                # the frame, and `isinstance(exception, UnusableMessage)` is
                # the discriminator the docs now point at.
                frame if frame is not None else offender))

    def _iter_channel(self, msg, channel):
        '''Yields the well-formed elements of ``msg[channel]``.

        The data and notify counterpart of _iter_responses, and added for the
        same reason: ``d.get('service')`` is evaluated at the call site,
        *outside* the try in _dispatch_to_handlers, so a non-dict element or a
        non-list channel ended the caller's receive loop with an AttributeError
        or a TypeError. The response path was hardened first and this one --
        three lines below it, and carrying far more traffic -- was not.

        A bad element is logged and skipped; the good ones beside it are still
        dispatched.
        '''
        if channel not in msg:
            return

        # Distinguished from absence deliberately: `{"data": null}` is a
        # malformed channel and used to be indistinguishable from a frame with
        # no data at all, so it was the one shape dropped without a word while
        # `{"data": 5}` was reported.
        elements = msg.get(channel)

        if not _is_sequence(elements):
            self._absorb('a %s frame whose %r is not a list'
                         % (channel, channel), elements, frame=msg)
            return

        for element in elements:
            if not _is_mapping(element):
                self._absorb('an unparseable %s element' % channel, element,
                             frame=msg)
                continue

            yield element

    def _log_extra_responses(self, frame, start=1):
        '''Logs every response past the first, and queues the rejections.

        Because _request_lock keeps one request outstanding at a time, a second
        response in the same frame cannot be an answer to anything this client
        is currently waiting on. It is a late answer to a request which was
        abandoned -- exactly what the orphan path in handle_message reports.
        Whether Schwab sends that in a frame of its own or batches it behind
        the answer to a live request is the server's choice, so reporting only
        the first would make add_error_handler fire or stay silent for the same
        event depending on framing the caller cannot see or predict.
        '''
        for response, code, content in self._iter_responses(frame, start):

            # Matches the orphan path: a late acknowledgement of something that
            # worked is routine and logs at INFO, a late rejection warns.
            log = self.logger.info if code == 0 else self.logger.warning
            log('Response frame carried an additional %s/%s response: code %s, '
                'msg %r. It answers no request this client is waiting on.',
                response.get('service'), response.get('command'),
                code, content.get('msg'))

            # `is not None` as well, for the same reason as the orphan path: a
            # response with no code at all is neither a rejection nor a
            # success, and reporting it would page someone over `code None`.
            if code is not None and code != 0:
                self._pending_reports.append((
                        UnexpectedResponseCode(
                            frame,
                            'Schwab rejected a request which had already '
                            'been abandoned: code {}, msg {!r}'.format(
                                code, content.get('msg'))),
                        response.get('service'),
                        response))

    async def _report_unparsable(self, exc):
        """Reports a frame whose JSON did not decode, then lets it propagate.

        Called by *both* readers. There are two coroutines which can be holding
        the read lock when a frame arrives -- handle_message and a request
        waiting on its response -- and the first version of this reported from
        only one of them. Which one reads any given frame is decided by a lock
        race the caller cannot see, so a callback wired on one path and not the
        other fires or does not fire for reasons nothing in the API explains.

        The caller must invoke this with the read lock released. Reporting from
        inside the block that holds it would run a user handler under the lock,
        which is the hazard the queue-and-drain design exists to avoid.

        Reports each exception once. handle_message hands the same object to
        the waiting request through _fail_pending_request before reporting it,
        so it arrives here twice for one bad frame -- and both reports carry an
        identical (None, exc, raw_msg), which a consumer cannot tell from two
        genuinely distinct bad frames. Marking the object is what makes "once"
        true regardless of which coroutine won the read; the first version of
        this fix turned "fires or does not fire" into "fires once or twice",
        which is the same lock race wearing a different symptom.
        """
        if getattr(exc, '_schwab_reported', False):
            return
        try:
            exc._schwab_reported = True
        except AttributeError:      # pragma: no cover -- Exception allows it
            pass

        try:
            # message=raw, not nothing. There is no service to name -- nothing
            # decoded, so there is no frame to read one from -- and reporting
            # neither would give this the same shape as the logout-close
            # failure. Note raw can be the empty string for an empty frame, so
            # the pair is distinguishable under `is None` but not under a
            # falsy test; the documented discriminator is the exception type.
            await self._report_error(exc, message=exc.raw_msg)
        except asyncio.CancelledError:
            raise
        except BaseException:
            # Must not replace the parse failure the caller needs to see, for
            # the same reason logout() guards its own report.
            self.logger.exception(
                    'Error handler raised while reporting an unparsable '
                    'message. Ignoring it.')

    async def _drain_pending_reports(self):
        '''Delivers reports queued by _log_extra_responses.

        Called from two places, both with the read lock released, no request
        lock held and no deadline running: the top of handle_message's loop,
        and the end of _request_response. Whichever coroutine read the frame
        delivers what it queued, so a report is not stranded behind a reader
        parked in recv(). A user handler can therefore run inside a subscribe.

        Each entry is popped before it is awaited, so a handler which re-enters
        handle_message finds an empty queue rather than reporting the same
        rejection twice.

        '''
        while self._pending_reports:
            exception, service, message = self._pending_reports.popleft()
            await self._report_error(
                    exception, service=service, message=message)

    def _fail_pending_request(self, exception):
        '''Hands an exception to the operation waiting on a response, if any.

        Used when the read fails rather than the response: without this, a
        connection which drops while a request is outstanding would leave that
        request waiting for a reply which can never arrive.
        '''
        pending = self._pending_request
        if pending is not None and not pending[3].done():
            pending[3].set_exception(exception)

    async def _await_response(self, request_id, service, command):
        '''
        Waits for the response to a request which has already been sent.

        The wait is over whichever of two things happens first: the response is
        routed to us by whoever is currently reading the socket, or the socket
        becomes free and we read it ourselves. That is what stops a subscription
        from blocking behind a ``handle_message`` which is parked on a quiet
        stream -- the reader delivers our response as soon as it arrives,
        without ever having to give up the socket.
        '''
        loop = asyncio.get_running_loop()
        future = self._pending_request[3]

        deadline = (None if self._response_timeout is None
                    else loop.time() + self._response_timeout)

        def remaining():
            return None if deadline is None else max(deadline - loop.time(), 0)

        def timed_out():
            return ResponseTimeoutError(
                    service, command, self._response_timeout,
                    'timed out after {}s waiting for a response to {}/{}'.format(
                        self._response_timeout, service, command))

        while not future.done():
            acquire = asyncio.ensure_future(self._read_lock.acquire())
            became_reader = False

            # Every exit from this block goes through the finally, including
            # being cancelled while waiting. Without that, a cancellation
            # delivered after the acquire had already succeeded would leave the
            # read lock held by nobody and wedge the client permanently.
            try:
                done, _ = await asyncio.wait(
                        {acquire, future}, timeout=remaining(),
                        return_when=asyncio.FIRST_COMPLETED)

                became_reader = acquire in done and not acquire.cancelled()

                if became_reader:
                    if future.done():
                        break
                    try:
                        frame = await asyncio.wait_for(
                                self._read_and_route(use_overflow=False),
                                timeout=remaining())
                    except asyncio.TimeoutError:
                        raise timed_out()
                    except BaseException as exc:
                        # The read failed, so nothing will ever answer this
                        # request. Fail it with the same error.
                        self._fail_pending_request(exc)
                        raise
                    if frame is not ROUTED:
                        # Not ours, so it belongs to handle_message. Set it
                        # aside as it arrives rather than at the end: a
                        # concurrent handle_message reads between our reads, so
                        # holding frames back would let a later one be
                        # dispatched first. appendleft here, pop from the right
                        # in _receive, so arrival order is preserved.
                        self._overflow_items.appendleft(frame)
                elif not done:
                    raise timed_out()
            finally:
                if became_reader:
                    self._read_lock.release()
                else:
                    # We did not become the reader, or we are unwinding. Give up
                    # the attempt, handling the race where it succeeded just as
                    # we stopped waiting for it.
                    acquire.cancel()
                    if acquire.done() and not acquire.cancelled():
                        self._read_lock.release()

        return future.result()

    async def _request_response(self, request, request_id, service, command):
        '''Sends a request and waits for the response which answers it.

        ``_request_lock`` keeps one request outstanding at a time, which is what
        lets a single pending slot be enough and keeps the response validation
        unambiguous. Note it does not cover reading, so message handling
        continues while a request is in flight.
        '''
        cancelled = False

        try:
            async with self._request_lock:
                future = asyncio.get_running_loop().create_future()
                self._pending_request = (request_id, service, command, future)
                try:
                    await self._send({'requests': [request]})
                    await self._await_response(request_id, service, command)
                finally:
                    self._pending_request = None
                    # Nobody will look at this future again. Cancel it if it is
                    # still open, so a reader which grabbed a reference to it
                    # just before it was cleared cannot complete it into the
                    # void, and retrieve any exception so asyncio does not
                    # report it as never having been retrieved.
                    if not future.done():
                        future.cancel()
                    elif not future.cancelled():
                        future.exception()
        except UnparsableMessage as exc:
            # The other reader. _await_response releases the read lock in its
            # own finally and the request lock is released by the `async with`
            # above, so this runs with neither held.
            try:
                await self._report_unparsable(exc)
            except asyncio.CancelledError:
                # Sibling except clauses do not see each other, so a
                # cancellation arriving inside the report would leave
                # `cancelled` False and let the finally drain while unwinding
                # -- blocking a close() or a TaskGroup shutdown for a user
                # handler's full duration, which is exactly what the flag
                # exists to prevent.
                cancelled = True
                raise
            raise
        except asyncio.CancelledError:
            # Noted so the finally can skip the drain. Running a user handler
            # to completion while unwinding a cancellation makes close(), a
            # wait_for, or a TaskGroup shutdown block for as long as that
            # handler takes -- measured at the handler's full duration. The
            # reports stay queued for handle_message, and are discarded by
            # close() or a fresh login() like anything else from a session
            # being torn down.
            cancelled = True
            raise
        finally:
            # Outside the `async with`, so neither lock is held and the
            # response deadline has passed. Whoever read the frame delivers
            # what it queued: when this coroutine was the reader,
            # handle_message may be parked in recv() having already drained for
            # its iteration, and the report would otherwise wait for the next
            # inbound message -- unbounded on a quiet stream.
            #
            # In a `finally`, so a request which *failed* still delivers what
            # it read. The caller's exception is element 0's rejection and says
            # nothing about element 1, and a failed subscribe is usually
            # followed by tearing the client down rather than reading it again
            # -- so draining only on success dropped the batched rejection
            # exactly when it was least likely to be reported another way.
            if not cancelled:
                try:
                    await self._drain_pending_reports()
                except asyncio.CancelledError:
                    # Not swallowed with the rest, for the reason logout()
                    # gives: discarding a cancellation makes this refuse to
                    # die.
                    raise
                except BaseException:
                    # BaseException is deliberately left to propagate out of
                    # _report_error everywhere else. Not here. This is a
                    # finally clause, so a handler raising SystemExit would
                    # replace the request's own exception -- the caller would
                    # never learn that Schwab refused their subscribe, which is
                    # the more useful error by far. Same guard, same reason, as
                    # the one in logout().
                    self.logger.exception(
                            'Error handler raised while reporting a rejection '
                            'carried alongside a response. Ignoring it.')

    async def _service_op(self, symbols, service, command, field_type=None,
                          *, fields=None):
        parameters = {
            'keys': ','.join(symbols)
        }

        if field_type is not None:
            if fields is None:
                fields = field_type.all_fields()

            fields = sorted(self.convert_enum_iterable(fields, field_type))
            parameters['fields'] = ','.join(str(f) for f in fields)

        request, request_id = self._make_request(
            service=service, command=command,
            parameters=parameters)

        await self._request_response(request, request_id, service, command)

    def add_error_handler(self, error_handler):
        '''
        Registers a callback for failures this client absorbs rather than
        raising.

        Four things reach it, all of them failures this client is right to
        absorb and none of which the caller could otherwise react to:

        * a stream handler which raises, synchronous or coroutine, because one
          misbehaving handler must not stop the others or drop the connection;
        * a response arriving for a request nobody is waiting on which carries
          a rejection, because the request was abandoned and nothing else will
          ever say Schwab refused it. Note the ``UnexpectedResponseCode``
          raised here carries the whole frame, and a frame can hold several
          responses -- so ``exception.response['response'][0]`` is not
          necessarily the rejected one, unlike the same exception raised from a
          request you are waiting on. The rejected element is what arrives as
          ``message``;
        * a connection which fails to close after logout, because the logout
          itself succeeded;
        * a message this client cannot use at all -- a frame which is not an
          object, an element of ``data`` or ``notify`` which is not an object,
          a ``service`` which is not a name. These arrive as
          :class:`UnusableMessage`, whose ``message`` is the offending value as
          it arrived. These are *coalesced*: the first three on a connection,
          then powers of ten, with the running count in the message. A
          systematically malformed channel produces one of these per element
          per tick, which would otherwise be a log-volume incident on top of
          the outage -- and, because these share a bounded queue with the late
          rejections above, would push out the one thing nothing else will ever
          report.

        Each is logged as it was before. The callback is what makes them
        something other than a log record.

        The second of those covers a rejection however Schwab frames it. It
        may arrive in a frame of its own, or riding along behind the answer to
        a request you are waiting on -- only one request is outstanding at a
        time, so anything past the first response in a frame is a late answer
        to an abandoned one either way. Which framing you get is the server's
        choice, and both report identically.

        The batched one is not reported from where it is found. Routing a
        frame happens holding the read lock, on the request path holding the
        request lock too, and inside the response deadline: a slow handler
        called there would turn a subscription that succeeded into a
        ``ResponseTimeoutError``, and one which re-subscribed would block on a
        lock its own caller holds. The report is queued instead, and delivered
        by whichever coroutine read the frame once it has released its locks --
        before :meth:`handle_message` returns, or before the request which read
        it returns. Both run with neither lock held and no deadline.

        That means a handler can be called from inside a subscribe, so a slow
        one delays that call returning. It cannot fail it: by then the response
        has already been matched.

        A request delivers what it read even when it fails, since the
        exception the caller gets describes the response which answered *their*
        request and says nothing about the others in the frame. Two limits
        there: a handler's ``BaseException`` is logged and swallowed rather
        than replacing that exception, and a request which is *cancelled*
        skips the report entirely, so a shutdown is not delayed by the length
        of a handler. Anything skipped stays queued for
        :meth:`handle_message`.

        The queue is bounded, and is cleared both by :meth:`close` and by a
        fresh :meth:`login`, so a rejection from a torn-down session is never
        reported against a new one. The log line written when each rejection is
        found is the complete record; this callback is the convenience.

        That matters most where a fallback covers for the stream. If a
        subscription quietly stops delivering, and a REST poll is authoritative
        anyway, nothing is wrong until something the poll does not cover
        finally breaks. A silent failure a fallback hides is the one worth
        having a signal for.

        ``error_handler`` is called as ``error_handler(service, exception,
        message)``:

        * ``service`` is the stream service the failure belongs to, or ``None``
          where it has none -- the close failure, or a message which named no
          service. **Do not discriminate on ``service`` and ``message`` both
          being ``None``.** The close failure is the only report which leaves
          both unset by design, and every other site passes at least one:
          :class:`UnusableMessage` carries the containing frame and
          :class:`UnparsableMessage` carries the raw text. But that is a
          property of today's call sites, not a contract --- a category added
          later with nothing to say would join the signature for free, which is
          how this warning came to be written twice. **Discriminate on the
          exception type.**
        * ``exception`` is the exception that was raised.
        * ``message`` is the message being handled, or ``None``.

        It may be a coroutine function, in which case it is awaited before the
        call which reported the failure continues. Unlike a coroutine stream
        handler, it is not scheduled as a task: a report which is awaited needs
        nothing keeping it alive at shutdown. The cost is that a slow error
        handler delays whatever was reporting to it, so keep it short --
        enqueue and return rather than doing work inline.

        If it raises an ``Exception``, that is logged and swallowed: a callback
        for absorbed failures must not become a way to fail. A ``BaseException``
        -- ``CancelledError`` during shutdown, or ``SystemExit`` -- is left to
        propagate, because swallowing those breaks cancellation and process
        exit, which are worse outcomes than the masking.

        Where it propagates *to* depends on which failure was being reported.
        Reporting a synchronous handler's failure happens inside
        ``handle_message``, so it reaches your receive loop. Reporting an
        asynchronous handler's happens inside that handler's own task, so it
        reaches whoever awaits the task, or the event loop's exception handler
        if nobody does. A ``SystemExit`` from an error handler therefore exits
        the process in the first case and not necessarily in the second.

        Handlers are called in registration order, and registering none keeps
        the current behaviour exactly.
        '''
        if not callable(error_handler):
            raise ValueError('error handler must be callable')

        # Checked here rather than discovered at report time. Every other
        # add_*_handler on this class takes a one-argument callback, so passing
        # one here is the natural mistake -- and it would raise TypeError on
        # every single report, inside the except clause that exists to stop an
        # error handler failing the stream. The result is a handler that never
        # runs, forever, with nothing but a log line to say so: the exact state
        # this callback is meant to replace.
        try:
            signature = inspect.signature(error_handler)
        except ValueError:
            # Some C-implemented callables and proxies have no retrievable
            # signature. Accepted rather than refused: the check exists to
            # catch a wrong-arity Python function, not to exclude anything it
            # cannot introspect.
            signature = None

        if signature is not None:
            try:
                signature.bind(None, None, None)
            except TypeError as exc:
                raise ValueError(
                        'error handler must accept three positional arguments '
                        '(service, exception, message); {!r} does not: '
                        '{}'.format(error_handler, exc)) from None

        self._error_handlers.append(error_handler)

    async def _report_error(self, exception, *, service=None, message=None):
        '''Delivers ``exception`` to every registered error handler.

        Alongside the logging rather than instead of it, so nothing regresses
        for a caller who registers nothing.

        Awaited rather than scheduled. A coroutine error handler therefore
        finishes before the call that reported the failure returns, which is
        what makes the report reliable without any machinery to keep the task
        alive: nothing has to be drained at shutdown, so nothing can deadlock
        draining it, time out draining it, or be cancelled half-drained. An
        earlier version scheduled these and grew a drain to protect them; the
        drain went on to leak the socket, deadlock against itself, deadlock
        against a second copy of itself, and finally to skip the report it
        existed to protect. Awaiting is the version with none of those.
        '''
        for error_handler in self._error_handlers:
            try:
                result = error_handler(service, exception, message)
                if inspect.isawaitable(result):
                    await result
            except Exception:
                # Logged, not reported: reporting an error handler's failure
                # through the error handlers is a loop, and the whole point of
                # this callback is that it cannot take the stream down.
                self.logger.exception(
                        'Error handler raised while reporting an error. '
                        'Ignoring it.')

    def _on_handler_task_done(self, task):
        self._handler_tasks.discard(task)

    async def _run_handler(self, awaitable, service, payload):
        '''Awaits a coroutine handler and reports its failure from inside its
        own task.

        The alternative is a done callback, which is synchronous and so cannot
        await a coroutine error handler -- it has to schedule one, and then
        something has to keep that alive until it finishes. Reporting here
        keeps the report inside the task the caller can already see and wait
        for.
        '''
        try:
            await awaitable
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.logger.error(
                    'Asynchronous stream handler raised an exception. The '
                    'message it was handling has been dropped.',
                    exc_info=exc)
            await self._report_error(exc, service=service, message=payload)
        except BaseException as exc:
            # Logged and re-raised, not absorbed. SystemExit and friends are
            # control flow rather than a stream failure, so they are not
            # reported to the error handlers -- but the done callback used to
            # log them and no longer does, and losing them silently is how a
            # handler calling sys.exit() becomes a mystery.
            self.logger.error(
                    'Asynchronous stream handler raised %s. The message it '
                    'was handling has been dropped, and the exception is '
                    'being propagated.', type(exc).__name__, exc_info=exc)
            raise

    async def _dispatch_to_handlers(self, service, msg, *, relabel):
        '''
        Delivers ``msg`` to every handler registered for ``service``.

        A handler which raises is logged and skipped: one misbehaving handler,
        or one message whose shape the library does not expect, must not prevent
        the other handlers from seeing the message, and must not escape into the
        caller's receive loop where it is indistinguishable from the stream
        having failed.

        Handlers may be synchronous or coroutine functions, and errors are
        reported the same way for both.
        '''
        try:
            handlers = self._handlers.get(service, ())
        except TypeError:
            # An unhashable service -- a list or a dict where a name belongs --
            # raises from the lookup itself, which is evaluated in the `for`
            # header and so sits outside the per-handler try below. Guarded
            # here rather than in each caller: this is the funnel they all go
            # through, and adding it to one of them is how the response path
            # got hardened while the data path did not.
            self._absorb('a message whose service is not a name', service,
                         frame=msg)
            return

        relabel_failed = False

        for handler in handlers:
            try:
                # Relabeling reads into the message, so a message with an
                # unexpected shape fails here rather than in the handler.
                payload = handler.label_message(msg) if relabel else msg
            except Exception as exc:
                # Absorbed, not reported as a handler failure. Relabeling is
                # this library's work, not the caller's: reporting it as
                # "your handler raised" blamed the consumer's code for a shape
                # the venue sent, and did it once per registered handler,
                # uncounted and uncoalesced. Once per message, and only the
                # first handler pays for it.
                if not relabel_failed:
                    relabel_failed = True
                    self._absorb(
                            'a %s message which could not be relabeled'
                            % service, msg, frame=msg, service=service,
                            cause=exc)
                continue

            try:
                result = handler(payload)
            except Exception as exc:
                self.logger.exception(
                        'Stream handler for service %s raised an exception. '
                        'The message it was handling has been dropped.',
                        service)
                # payload, not msg: the handler was given the relabeled
                # message, so that is the one it failed on, and the one an
                # error handler reading fields by name can make sense of.
                await self._report_error(
                        exc, service=service, message=payload)
                continue

            # Check if the result is an awaitable, if so schedule it. This
            # allows for both sync and async handlers. It is wrapped so that a
            # failure is reported from inside the task rather than from a done
            # callback, which cannot await a coroutine error handler.
            if inspect.isawaitable(result):
                task = asyncio.ensure_future(
                        self._run_handler(result, service, payload))
                self._handler_tasks.add(task)
                task.add_done_callback(self._on_handler_task_done)

    async def handle_message(self):
        # Read until something arrives which is actually ours. A response to a
        # request that is in flight is routed to whoever is waiting for it and
        # is not a message to be handled, so keep reading in that case rather
        # than returning nothing.
        while True:
            # Before the read, not after it. No lock is held here and no
            # deadline is running, which is what makes it safe to call user
            # code -- and draining after the read would hold every queued
            # report until the *next* message arrived, which on a quiet stream
            # is indefinitely. A matched frame read here returns None and comes
            # back round, so its report goes out on the following iteration.
            #
            # This is not the only drain. When a request is the reader, it
            # queues the report and this loop may already be parked in recv(),
            # so _request_response drains once it has released its locks.
            was_open = self._socket is not None
            await self._drain_pending_reports()

            # An error handler is allowed to tear the stream down -- that is a
            # reasonable reaction to a rejection, and every other path which
            # calls a handler returns rather than reading again afterwards.
            # Without this, closing from a queued report would be the one place
            # it raised "Socket not open" out of the call the handler was
            # running in.
            #
            # Conditioned on the socket having been open *before* the drain, so
            # this covers only a handler closing it. A client which was never
            # logged in, or which the caller closed itself, still raises
            # "Socket not open" from the read below rather than returning
            # quietly for one iteration.
            if was_open and self._socket is None:
                return

            try:
                async with self._read_lock:
                    try:
                        msg = await self._read_and_route()
                    except BaseException as exc:
                        # A read failure is also a failure for any request
                        # waiting on a response, which will otherwise wait for
                        # a reply that can no longer arrive.
                        self._fail_pending_request(exc)
                        raise
            except UnparsableMessage as exc:
                # Outside the `async with`, so the read lock is released before
                # any user code runs.
                await self._report_unparsable(exc)
                raise

            if msg is not ROUTED:
                break

        # Every read below assumes a mapping. `'data' in msg` happened to
        # tolerate a top-level JSON array or string; `msg.get('data')` does
        # not, so hardening the channels made a non-dict frame raise where it
        # used to be ignored. Guarded once, here, rather than at each read.
        if not _is_mapping(msg):
            self._absorb('a message which is not an object', msg)
            return

        # response
        if 'response' in msg:
            # A response with nothing waiting for it is not a reason to end the
            # caller's receive loop. It happens whenever a request was
            # abandoned -- cancelled, or given up on -- and the server answered
            # afterwards, and the answer is frequently a successful one. Ending
            # the session over a late acknowledgement of something that worked
            # loses every message queued behind it, and does so precisely when
            # the server is slow, which is when a stream is least worth
            # dropping. Log it and carry on.
            for response, code, content in self._iter_responses(msg):
                # A late acknowledgement of something that worked is routine.
                # A late *rejection* is not: the request was abandoned, so
                # nothing else will ever report that Schwab refused it, and at
                # INFO it would not be seen at all. Losing the raise is right;
                # losing the news is not.
                log = self.logger.info if code == 0 else self.logger.warning

                log('Received a response to %s/%s with no request '
                    'outstanding: code %s, msg \'%s\'. Ignoring it.',
                    response.get('service'), response.get('command'),
                    code, content.get('msg'))

                # A late rejection is reported, a late success is not. This is
                # the third kind of absorbed failure: the request it answers
                # was abandoned, so nothing else will ever say Schwab refused
                # it. A caller who registered an error handler to replace
                # scraping the log would otherwise stop seeing exactly that.
                # `is not None` as well: a response with no code at all is
                # neither a late rejection nor a late success, and reporting it
                # as a rejection pages someone over `code None, msg None`.
                if code is not None and code != 0:
                    # msg, not response: _validate_response builds this same
                    # exception from the whole frame, and handing it a single
                    # element would make one type mean two shapes -- the
                    # KeyError that followed would be swallowed by
                    # _report_error's except clause, leaving nothing but a log
                    # line, which is what this callback exists to replace.
                    #
                    # The frame can carry several responses and this reports
                    # per element, so exc.response['response'][0] is not
                    # necessarily the one that was rejected. The rejected
                    # element is what reaches the handler as `message`.
                    await self._report_error(
                            UnexpectedResponseCode(
                                msg,
                                'Schwab rejected a request which had already '
                                'been abandoned: code {}, msg {!r}'.format(
                                    code, content.get('msg'))),
                            service=response.get('service'),
                            message=response)
            return

        # data
        for d in self._iter_channel(msg, 'data'):
            await self._dispatch_to_handlers(
                    d.get('service'), d, relabel=True)

        # notify
        for d in self._iter_channel(msg, 'notify'):
            if 'heartbeat' in d:
                pass
            else:
                # Not every notify message is guaranteed to name a service,
                # and one that does not must not raise out of here.
                await self._dispatch_to_handlers(
                        d.get('service'), d, relabel=False)

    ##########################################################################
    # LOGIN

    async def login(self, websocket_connect_args=None):
        '''

        Performs initial stream setup:
         * Fetches streaming information from the HTTP client's
           :meth:`~schwab.client.Client.get_user_preferences` method
         * Initializes the socket
         * Builds and sends and authentication request
         * Waits for response indicating login success

        All stream operations are available after this method completes.

        :param websocket_connect_args: ``dict`` of additional arguments to pass
                                       to the websocket ``connect`` call. Useful
                                       for setting timeouts and other connection
                                       parameters. See `the official
                                       documentation <https://websockets.readthedocs.io/en/stable/reference/asyncio/client.html#websockets.asyncio.client.connect>`__
                                       for details.

                                       Note that websockets 14.0 renamed
                                       ``extra_headers`` to
                                       ``additional_headers`` and removed
                                       ``create_protocol`` and ``read_limit``.
                                       Passing any of the three raises
                                       ``ValueError``: for ``extra_headers``
                                       naming the replacement, and for the
                                       other two saying there is none.
        '''

        # Fetch required data and initialize the client
        r = self._client.get_user_preferences()

        # We don't actually know whether the client is synchronous or
        # asynchronous, so work around by awaiting the response if necessary
        if inspect.iscoroutine(r):
            r = await r
        assert r.status_code == httpx2.codes.OK, r.raise_for_status()
        r = r.json()

        await self._init_from_preferences(
                r, websocket_connect_args if websocket_connect_args else {})

        # Build and send the request object
        request_parameters = {
                'Authorization': self._client.token_metadata.token['access_token'],
                'SchwabClientChannel': self._stream_channel,
                'SchwabClientFunctionId': self._stream_function_id,
        }

        request, request_id = self._make_request(
            service='ADMIN', command='LOGIN',
            parameters=request_parameters)
        await self._request_response(request, request_id, 'ADMIN', 'LOGIN')

    ##########################################################################
    # LOGOUT

    async def logout(self):
        '''
        Performs a logout operation on the stream and closes the underlying
        connection. After this method is called, no further stream operations
        are possible. The client must be re-initialized with :meth:`login` to
        perform further operations.
        '''
        request, request_id = self._make_request(
            service='ADMIN', command='LOGOUT',
            parameters={})
        try:
            await self._request_response(
                    request, request_id, 'ADMIN', 'LOGOUT')
        finally:
            # The stream is unusable after a logout whether or not the venue
            # acknowledged it, so the socket is closed either way. A failure to
            # close is logged rather than raised: it must not replace whatever
            # went wrong with the logout itself, which is the more useful error.
            try:
                await self.close()
            except Exception as exc:
                self.logger.exception(
                        'Failed to close the stream connection after logout.')
                try:
                    await self._report_error(exc)
                except asyncio.CancelledError:
                    # Not swallowed with the rest. Discarding a cancellation
                    # makes logout() refuse to die -- a supervisor or TaskGroup
                    # calling cancel() during shutdown would find this task
                    # running to completion regardless, which is a worse
                    # outcome than the masking the guard below prevents.
                    raise
                except BaseException:
                    # BaseException is deliberately left to propagate out of
                    # _report_error everywhere else. Not here: this is the
                    # finally clause whose whole point is that a close failure
                    # must not replace whatever went wrong with the logout, and
                    # an error handler raising SystemExit would do exactly
                    # that. Reporting is the least important thing happening
                    # here.
                    self.logger.exception(
                            'Error handler raised while reporting a close '
                            'failure. Ignoring it.')


    async def close(self):
        '''
        Closes the connection to the streaming server without logging out.

        Safe to call more than once, and safe to call on a client which was
        never logged in. Prefer :meth:`logout` where the stream is still
        healthy; this exists for shutting down a client whose connection has
        already failed, or which is being torn down without ceremony.

        The client must be re-initialized with :meth:`login` to perform further
        operations.
        '''
        socket, self._socket = self._socket, None

        # Anything still queued belongs to the session being torn down. Its
        # exception carries a frame from that connection, so delivering it
        # after a later login() would report a dead session's rejection against
        # a live one. Each was logged when it was found, so nothing unwritten
        # is lost.
        #
        # _overflow_items for the same reason, and it is the older half of the
        # problem: it holds frames read but not yet handled, including the late
        # rejections the orphan path reports and data frames handlers would be
        # given. Clearing only the reports would have left the standalone
        # framing leaking across sessions while the batched one did not, which
        # is the asymmetry this whole change exists to remove.
        self._pending_reports.clear()
        self._overflow_items.clear()
        self._absorbed = 0
        self._absorbed_kinds.clear()

        if socket is not None:
            await socket.close()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    ##########################################################################
    # ACCT_ACTIVITY

    class AccountActivityFields(_BaseFieldEnum):
        '''

        Data fields for equity account activity. Primarily an implementation detail
        and not used in client code. Provided here as documentation for key
        values stored returned in the stream messages.
        '''

        #: Passed back to the client from the request to identify a subscription this response belongs to.
        SUBSCRIPTION_KEY = 0

        #: Account Number that the activity occurred on.
        ACCOUNT = 1

        #: Message Type that dictates the format of the Message Data field.
        MESSAGE_TYPE = 2

        #: The core data for the message. Either JSON-formatted data describing the update, NULL in some cases,
        #: or plain text in case of ERROR.
        MESSAGE_DATA = 3

    async def account_activity_sub(self):
        '''

        Subscribe to account activity for the account id associated with this
        streaming client. See :class:`AccountActivityFields` for more info.
        '''
        await self._service_op(
            [self._stream_correl_id], 'ACCT_ACTIVITY', 'SUBS',
            self.AccountActivityFields)

    async def account_activity_unsubs(self):
        '''

        Un-Subscribe to account activity for the account id associated with this
        streaming client. See :class:`AccountActivityFields` for more info.
        '''
        await self._service_op([self._stream_correl_id], 'ACCT_ACTIVITY', 'UNSUBS')

    def add_account_activity_handler(self, handler):
        '''
        Adds a handler to the account activity subscription. See
        :ref:`registering_handlers` for details.
        '''
        self._handlers['ACCT_ACTIVITY'].append(_Handler(handler,
                                                        self.AccountActivityFields))

    ##########################################################################
    # CHART_EQUITY

    class ChartEquityFields(_BaseFieldEnum):
        '''

        Data fields for equity OHLCV data. Primarily an implementation detail
        and not used in client code. Provided here as documentation for key
        values stored returned in the stream messages.
        '''

        #: Ticker symbol
        SYMBOL = 0

        #: Sequence number
        SEQUENCE = 1

        #: Today's open price
        OPEN_PRICE = 2

        #: Today's high price
        HIGH_PRICE = 3

        #: Today's low price
        LOW_PRICE = 4

        #: Previous day's close price
        CLOSE_PRICE = 5

        #: Today's trading volume
        VOLUME = 6

        #: Chart timestamp
        CHART_TIME_MILLIS = 7

        #: Chart day
        CHART_DAY = 8

    async def chart_equity_subs(self, symbols):
        '''

        Subscribe to equity charts. Behavior is undefined if called multiple
        times.

        :param symbols: Equity symbols to subscribe to.'''
        await self._service_op(
            symbols, 'CHART_EQUITY', 'SUBS', self.ChartEquityFields,
            fields=self.ChartEquityFields.all_fields())

    async def chart_equity_unsubs(self, symbols):
        '''

        Un-Subscribe to equity charts. Behavior is undefined if called multiple
        times.

        :param symbols: Equity symbols to subscribe to.'''
        await self._service_op(symbols, 'CHART_EQUITY', 'UNSUBS')

    async def chart_equity_add(self, symbols):
        '''

        Add a symbol to the equity charts subscription. Behavior is undefined
        if called before :meth:`chart_equity_subs`.

        :param symbols: Equity symbols to add to the subscription.
        '''
        await self._service_op(
            symbols, 'CHART_EQUITY', 'ADD', self.ChartEquityFields,
            fields=self.ChartEquityFields.all_fields())

    def add_chart_equity_handler(self, handler):
        '''
        Adds a handler to the equity chart subscription. See
        :ref:`registering_handlers` for details.
        '''
        self._handlers['CHART_EQUITY'].append(_Handler(handler,
                                                       self.ChartEquityFields))

    ##########################################################################
    # CHART_FUTURES

    class ChartFuturesFields(_BaseFieldEnum):
        '''

        Data fields for equity OHLCV data. Primarily an implementation detail
        and not used in client code. Provided here as documentation for key
        values stored returned in the stream messages.
        '''

        #: Ticker symbol in upper case.
        SYMBOL = 0

        #: Milliseconds since Epoch
        CHART_TIME_MILLIS = 1

        #: Opening price for the minute
        OPEN_PRICE = 2

        #: Highest price for the minute
        HIGH_PRICE = 3

        #: Chart's lowest price for the minute
        LOW_PRICE = 4

        #: Closing price for the minute
        CLOSE_PRICE = 5

        #: Total volume for the minute
        VOLUME = 6

    async def chart_futures_subs(self, symbols):
        '''

        Subscribe to futures charts. Behavior is undefined if called multiple
        times.

        :param symbols: Futures symbols to subscribe to.
        '''
        await self._service_op(
            symbols, 'CHART_FUTURES', 'SUBS', self.ChartFuturesFields,
            fields=self.ChartFuturesFields.all_fields())

    async def chart_futures_unsubs(self, symbols):
        '''

        Un-Subscribe to futures charts. Behavior is undefined if called multiple
        times.

        :param symbols: Futures symbols to subscribe to.
        '''
        await self._service_op(symbols, 'CHART_FUTURES', 'UNSUBS')

    async def chart_futures_add(self, symbols):
        '''

        Add a symbol to the futures chart subscription. Behavior is undefined
        if called before :meth:`chart_futures_subs`.

        :param symbols: Futures symbols to add to the subscription.
        '''
        await self._service_op(
            symbols, 'CHART_FUTURES', 'ADD', self.ChartFuturesFields,
            fields=self.ChartFuturesFields.all_fields())

    def add_chart_futures_handler(self, handler):
        '''
        Adds a handler to the futures chart subscription. See
        :ref:`registering_handlers` for details.
        '''
        self._handlers['CHART_FUTURES'].append(_Handler(handler,
                                                        self.ChartFuturesFields))

    ##########################################################################
    # LEVELONE_EQUITIES

    class LevelOneEquityFields(_BaseFieldEnum):
        '''

        Fields for equity quotes.
        '''

        #: Ticker symbol
        SYMBOL = 0

        #: Bid price
        BID_PRICE = 1

        #: Ask price
        ASK_PRICE = 2

        #: Last trade price
        LAST_PRICE = 3

        #: Size of the highest bid
        BID_SIZE = 4

        #: Size of the lowest ask
        ASK_SIZE = 5

        #: Exchange ID of the lowest ask
        ASK_ID = 6

        #: Exchange ID of the highest bid
        BID_ID = 7

        #: Total volume trade to date
        TOTAL_VOLUME = 8

        #: Size of the last trade
        LAST_SIZE = 9

        #: Daily high price
        HIGH_PRICE = 10

        #: Daily low price
        LOW_PRICE = 11

        #: Previous close price
        CLOSE_PRICE = 12

        #: Exchange ID
        EXCHANGE_ID = 13

        #: Is this equity marginable?
        MARGINABLE = 14

        #: Description
        DESCRIPTION = 15

        #: Exchange ID of the last trade
        LAST_ID = 16

        #: Today's open price
        OPEN_PRICE = 17

        #: Net change
        NET_CHANGE = 18

        #: 52 week high price
        HIGH_PRICE_52_WEEK = 19

        #: 52 week low price
        LOW_PRICE_52_WEEK = 20

        #: P/E ratio
        PE_RATIO = 21

        #: Dividend amount
        DIVIDEND_AMOUNT = 22

        #: Dividend yield
        DIVIDEND_YIELD = 23

        #: ETF net asset value
        NAV = 24

        #: Exchange name
        EXCHANGE_NAME = 25

        #: Dividend date
        DIVIDEND_DATE = 26

        #: Is this a regular market quote?
        REGULAR_MARKET_QUOTE = 27

        #: Is this a regular market trade?
        REGULAR_MARKET_TRADE = 28

        #: Regular market last price
        REGULAR_MARKET_LAST_PRICE = 29

        #: Regular market last size
        REGULAR_MARKET_LAST_SIZE = 30

        #: Regular market net change
        REGULAR_MARKET_NET_CHANGE = 31

        #: Security status
        SECURITY_STATUS = 32

        #: Mark
        MARK = 33

        #: Quote time in milliseconds
        QUOTE_TIME_MILLIS = 34

        #: Last trade time in milliseconds
        TRADE_TIME_MILLIS = 35

        #: Regular market trade time in milliseconds
        REGULAR_MARKET_TRADE_MILLIS = 36

        #: Bid time in millis
        BID_TIME_MILLIS = 37

        #: Ask time in millis
        ASK_TIME_MILLIS = 38

        #: Ask MIC ID
        ASK_MIC_ID = 39

        #: Bid MIC ID
        BID_MIC_ID = 40

        #: Last trade MIC ID
        LAST_MIC_ID = 41

        #: Net change in percent
        NET_CHANGE_PERCENT = 42

        #: Regular market change in percent
        REGULAR_MARKET_CHANGE_PERCENT = 43

        #: Mark change
        MARK_CHANGE = 44

        #: Mark change in percent
        MARK_CHANGE_PERCENT = 45

        #: HTB quantity
        HTB_QUANTITY = 46

        #: HTB rate
        HTB_RATE = 47

        #: Is this equity hard to borrow?
        HARD_TO_BORROW = 48

        #: Is this equity shortable
        IS_SHORTABLE = 49

        #: Post market net change
        POST_MARKET_NET_CHANGE = 50

        #: Post market net change percent
        POST_MARKET_NET_CHANGE_PERCENT = 51

    async def level_one_equity_subs(self, symbols, *, fields=None):
        '''

        Subscribe to level one equity quote data.

        :param symbols: Equity symbols to receive quotes for
        :param fields: Iterable of :class:`LevelOneEquityFields` representing
                       the fields to return in streaming entries. If unset, all
                       fields will be requested.
        '''
        if fields and self.LevelOneEquityFields.SYMBOL not in fields:
            fields.append(self.LevelOneEquityFields.SYMBOL)
        await self._service_op(
            symbols, 'LEVELONE_EQUITIES', 'SUBS', self.LevelOneEquityFields,
            fields=fields)

    async def level_one_equity_unsubs(self, symbols):
        '''

        Un-Subscribe to level one equity quote data.

        :param symbols: Equity symbols to receive quotes for
        '''

        await self._service_op(symbols, 'LEVELONE_EQUITIES', 'UNSUBS')

    async def level_one_equity_add(self, symbols, *, fields=None):
        '''

        Add symbols to the list to receive quotes for.

        :param symbols: Equity symbols to receive quotes for
        :param fields: Iterable of :class:`LevelOneEquityFields` representing
                       the fields to return in streaming entries. If unset, all
                       fields will be requested.
        '''
        if fields and self.LevelOneEquityFields.SYMBOL not in fields:
            fields.append(self.LevelOneEquityFields.SYMBOL)
        await self._service_op(
            symbols, 'LEVELONE_EQUITIES', 'ADD',
            self.LevelOneEquityFields, fields=fields)

    def add_level_one_equity_handler(self, handler):
        '''
        Register a function to handle level one equity quotes as they are sent.
        See :ref:`registering_handlers` for details.
        '''
        self._handlers['LEVELONE_EQUITIES'].append(
                _Handler(handler, self.LevelOneEquityFields))

    ##########################################################################
    # LEVELONE_OPTIONS

    class LevelOneOptionFields(_BaseFieldEnum):
        '''
        '''

        #: Option symbol
        SYMBOL = 0

        #: Description
        DESCRIPTION = 1

        #: Highest bid price
        BID_PRICE = 2

        #: Lowest ask price
        ASK_PRICE = 3

        #: Last trade price
        LAST_PRICE = 4

        #: Today's high price
        HIGH_PRICE = 5

        #: Today's low price
        LOW_PRICE = 6

        #: Last close price
        CLOSE_PRICE = 7

        #: Today's total volume
        TOTAL_VOLUME = 8

        #: Open interest
        OPEN_INTEREST = 9

        #: Volatility
        VOLATILITY = 10

        #: Money intrinsic value
        MONEY_INTRINSIC_VALUE = 11

        #: Expiration year
        EXPIRATION_YEAR = 12

        #: Multiplier
        MULTIPLIER = 13

        #: Digits
        DIGITS = 14

        #: Open price
        OPEN_PRICE = 15

        #: Highest bid size
        BID_SIZE = 16

        #: Lowest ask size
        ASK_SIZE = 17

        #: Last trade size
        LAST_SIZE = 18

        #: Net change
        NET_CHANGE = 19

        #: Contract strike price
        STRIKE_PRICE = 20

        #: Contract type
        CONTRACT_TYPE = 21

        #: Underlying symbol
        UNDERLYING = 22

        #: Expiration month
        EXPIRATION_MONTH = 23

        #: Deliverables
        DELIVERABLES = 24

        #: Time value
        TIME_VALUE = 25

        #: Expiration day
        EXPIRATION_DAY = 26

        #: Days to expiration
        DAYS_TO_EXPIRATION = 27

        #: Delta
        DELTA = 28

        #: Gamma
        GAMMA = 29

        #: Theta
        THETA = 30

        #: Vega
        VEGA = 31

        #: Rho
        RHO = 32

        #: Security status
        SECURITY_STATUS = 33

        #: Theoretical option value
        THEORETICAL_OPTION_VALUE = 34

        #: Underlying price
        UNDERLYING_PRICE = 35

        #: UV expiration type
        UV_EXPIRATION_TYPE = 36

        #: Mark
        MARK = 37

        #: Quote time in millis
        QUOTE_TIME_MILLIS = 38

        #: Last trade time in millis
        TRADE_TIME_MILLIS = 39

        #: Exchange ID
        EXCHANGE_ID = 40

        #: Exchange name
        EXCHANGE_NAME = 41

        #: Last trading day
        LAST_TRADING_DAY = 42

        #: Settlement type
        SETTLEMENT_TYPE = 43

        #: Net percent change
        NET_PERCENT_CHANGE = 44

        #: Mark change
        MARK_CHANGE = 45

        #: Mark change in percent
        MARK_CHANGE_PERCENT = 46

        #: Implied yield
        IMPLIED_YIELD = 47

        #: Is penny stock?
        IS_PENNY = 48

        #: Option root
        OPTION_ROOT = 49

        #: 52 week high price
        HIGH_PRICE_52_WEEK = 50

        #: 52 week low price
        LOW_PRICE_52_WEEK = 51

        #: Indicative asking price
        INDICATIVE_ASKING_PRICE = 52

        #: Indicative bid price
        INDICATIVE_BID_PRICE = 53

        #: Indicative quote time
        INDICATIVE_QUOTE_TIME = 54

        #: Exercise type
        EXERCISE_TYPE = 55

    async def level_one_option_subs(self, symbols, *, fields=None):
        '''

        Subscribe to level one option quote data.

        :param symbols: Option symbols to receive quotes for
        :param fields: Iterable of :class:`LevelOneOptionFields` representing
                       the fields to return in streaming entries. If unset, all
                       fields will be requested.
        '''
        if fields and self.LevelOneOptionFields.SYMBOL not in fields:
            fields.append(self.LevelOneOptionFields.SYMBOL)
        await self._service_op(
            symbols, 'LEVELONE_OPTIONS', 'SUBS', self.LevelOneOptionFields,
            fields=fields)

    async def level_one_option_unsubs(self, symbols):
        '''

        Un-Subscribe to level one option quote data.

        :param symbols: Option symbols to receive quotes for
        '''
        await self._service_op(symbols, 'LEVELONE_OPTIONS', 'UNSUBS')

    async def level_one_option_add(self, symbols, *, fields=None):
        '''

        Add symbols to the list to receive quotes for.

        :param symbols: Option symbols to add to list to receive quotes for
        :param fields: Iterable of :class:`LevelOneOptionFields` representing
                       the fields to return in streaming entries. If unset, all
                       fields will be requested.
        '''
        if fields and self.LevelOneOptionFields.SYMBOL not in fields:
            fields.append(self.LevelOneOptionFields.SYMBOL)
        await self._service_op(
            symbols, 'LEVELONE_OPTIONS', 'ADD',
            self.LevelOneOptionFields, fields=fields)

    def add_level_one_option_handler(self, handler):
        '''
        Register a function to handle level one options quotes as they are sent.
        See :ref:`registering_handlers` for details.
        '''
        self._handlers['LEVELONE_OPTIONS'].append(
                _Handler(handler, self.LevelOneOptionFields))

    ##########################################################################
    # LEVELONE_FUTURES

    class LevelOneFuturesFields(_BaseFieldEnum):
        '''
        '''

        #: Ticker symbol in upper case.
        SYMBOL = 0

        #: Current Best Bid Price
        BID_PRICE = 1

        #: Current Best Ask Price
        ASK_PRICE = 2

        #: Price at which the last trade was matched
        LAST_PRICE = 3

        #: Number of contracts for bid
        BID_SIZE = 4

        #: Number of contracts for ask
        ASK_SIZE = 5

        #: Exchange with the best bid
        BID_ID = 6

        #: Exchange with the best ask
        ASK_ID = 7

        #: Aggregated contracts traded throughout the day, including pre/post market hours.
        TOTAL_VOLUME = 8

        #: Number of contracts traded with last trade
        LAST_SIZE = 9

        #: Time of the last quote in milliseconds since epoch
        QUOTE_TIME_MILLIS = 10

        #: Time of the last trade in milliseconds since epoch
        TRADE_TIME_MILLIS = 11

        #: Day's high trade price
        HIGH_PRICE = 12

        #: Day's low trade price
        LOW_PRICE = 13

        #: Previous day's closing price
        CLOSE_PRICE = 14

        #: Primary "listing" Exchange
        EXCHANGE_ID = 15

        #: Description of the product
        DESCRIPTION = 16

        #: Exchange where last trade was executed
        LAST_ID = 17

        #: Day's Open Price
        OPEN_PRICE = 18

        #: Current Last-Prev Close
        NET_CHANGE = 19

        #: Current percent change
        FUTURE_CHANGE_PERCENT = 20

        #: Name of exchange
        EXCHANGE_NAME = 21

        #: Trading status of the symbol
        SECURITY_STATUS = 22

        #: The total number of futures contracts that are not closed or delivered on a particular day
        OPEN_INTEREST = 23

        #: Mark-to-Market value is calculated daily using current prices to determine profit/loss
        MARK = 24

        #: Minimum price movement
        TICK = 25

        #: Minimum amount that the price of the market can change
        TICK_AMOUNT = 26

        #: Futures product
        PRODUCT = 27

        #: Display in fraction or decimal format.
        FUTURE_PRICE_FORMAT = 28

        #: Trading hours
        FUTURE_TRADING_HOURS = 29

        #: Flag to indicate if this future contract is tradable
        FUTURE_IS_TRADABLE = 30

        #: Point value
        FUTURE_MULTIPLIER = 31

        #: Indicates if this contract is active
        FUTURE_IS_ACTIVE = 32

        #: Closing price
        FUTURE_SETTLEMENT_PRICE = 33

        #: Symbol of the active contract
        FUTURE_ACTIVE_SYMBOL = 34

        #: Expiration date of this contract
        FUTURE_EXPIRATION_DATE = 35

        #: Expiration Style
        EXPIRATION_STYLE = 36

        #: Time of the last ask-side quote in milliseconds since epoch
        ASK_TIME_MILLIS = 37

        #: Time of the last bid-side quote in milliseconds since epoch
        BID_TIME_MILLIS = 38

        #: Indicates if this contract has quoted during the active session
        QUOTED_IN_SESSION = 39

        #: Expiration date of this contract
        SETTLEMENT_DATE = 40

    async def level_one_futures_subs(self, symbols, *, fields=None):
        '''

        Subscribe to level one futures quote data.

        :param symbols: Futures symbols to receive quotes for
        :param fields: Iterable of :class:`LevelOneFuturesFields` representing
                       the fields to return in streaming entries. If unset, all
                       fields will be requested.
        '''
        if fields and self.LevelOneFuturesFields.SYMBOL not in fields:
            fields.append(self.LevelOneFuturesFields.SYMBOL)
        await self._service_op(
            symbols, 'LEVELONE_FUTURES', 'SUBS', self.LevelOneFuturesFields,
            fields=fields)

    async def level_one_futures_unsubs(self, symbols):
        '''

        Un-Subscribe to level one futures quote data.

        :param symbols: Futures symbols to receive quotes for
        '''

        await self._service_op(symbols, 'LEVELONE_FUTURES', 'UNSUBS')

    async def level_one_futures_add(self, symbols, *, fields=None):
        '''

        Add symbols to the list to receive quotes for.

        :param symbols: Futures symbols to add to the list to receive quotes for
        :param fields: Iterable of :class:`LevelOneFuturesFields` representing
                       the fields to return in streaming entries. If unset, all
                       fields will be requested.
        '''
        if fields and self.LevelOneFuturesFields.SYMBOL not in fields:
            fields.append(self.LevelOneFuturesFields.SYMBOL)
        await self._service_op(
            symbols, 'LEVELONE_FUTURES', 'ADD',
            self.LevelOneFuturesFields, fields=fields)

    def add_level_one_futures_handler(self, handler):
        '''
        Register a function to handle level one futures quotes as they are sent.
        See :ref:`registering_handlers` for details.
        '''
        self._handlers['LEVELONE_FUTURES'].append(
            _Handler(handler, self.LevelOneFuturesFields))

    ##########################################################################
    # LEVELONE_FOREX

    class LevelOneForexFields(_BaseFieldEnum):
        '''
        '''

        #: Ticker symbol in upper case.
        SYMBOL = 0

        #: Current Bid Price
        BID_PRICE = 1

        #: Current Ask Price
        ASK_PRICE = 2

        #: Price at which the last trade was matched
        LAST_PRICE = 3

        #: Number of currency pairs for bid
        BID_SIZE = 4

        #: Number of currency pairs for ask
        ASK_SIZE = 5

        #: Aggregated currency pairs traded throughout the day, including pre/post market hours.
        TOTAL_VOLUME = 6

        #: Number of currency pairs traded with last trade
        LAST_SIZE = 7

        #: Trade time of the last quote in milliseconds since epoch
        QUOTE_TIME_MILLIS = 8

        #: Trade time of the last trade in milliseconds since epoch
        TRADE_TIME_MILLIS = 9

        #: Day's high trade price
        HIGH_PRICE = 10

        #: Day's low trade price
        LOW_PRICE = 11

        #: Previous day's closing price
        CLOSE_PRICE = 12

        #: Exchange Id
        EXCHANGE_ID = 13

        #: Description of the product
        DESCRIPTION = 14

        #: Day's Open Price
        OPEN_PRICE = 15

        #: Current Last-Prev Close
        NET_CHANGE = 16

        #: Current percent change
        CHANGE_PERCENT = 17

        #: Name of exchange
        EXCHANGE_NAME = 18

        #: Valid decimal points
        DIGITS = 19

        #: Trading status of the symbol
        SECURITY_STATUS = 20

        #: Minimum price movement
        TICK = 21

        #: Minimum amount that the price of the market can change
        TICK_AMOUNT = 22

        #: Product name
        PRODUCT = 23

        #: Trading hours
        TRADING_HOURS = 24

        #: Flag to indicate if this forex is tradable
        IS_TRADABLE = 25

        #: Market Maker
        MARKET_MAKER = 26

        #: Highest price traded in the past 12 months, or 52 weeks
        HIGH_PRICE_52_WEEK = 27

        #: Lowest price traded in the past 12 months, or 52 weeks
        LOW_PRICE_52_WEEK = 28

        #: Mark-to-Market value is calculated daily using current prices to determine profit/loss
        MARK = 29

    async def level_one_forex_subs(self, symbols, *, fields=None):
        '''

        Subscribe to level one forex quote data.

        :param symbols: Forex symbols to receive quotes for
        :param fields: Iterable of :class:`LevelOneForexFields` representing
                       the fields to return in streaming entries. If unset, all
                       fields will be requested.
        '''
        if fields and self.LevelOneForexFields.SYMBOL not in fields:
            fields.append(self.LevelOneForexFields.SYMBOL)
        await self._service_op(
            symbols, 'LEVELONE_FOREX', 'SUBS', self.LevelOneForexFields,
            fields=fields)

    async def level_one_forex_unsubs(self, symbols):
        '''

        Un-Subscribe to level one forex quote data.

        :param symbols: Forex symbols to receive quotes for
        '''

        await self._service_op(symbols, 'LEVELONE_FOREX', 'UNSUBS')

    async def level_one_forex_add(self, symbols, *, fields=None):
        '''

        Add symbols to the list to receive quotes for.

        :param symbols: Forex symbols to add to list to receive quotes for
        :param fields: Iterable of :class:`LevelOneForexFields` representing
                       the fields to return in streaming entries. If unset, all
                       fields will be requested.

        '''
        if fields and self.LevelOneForexFields.SYMBOL not in fields:
            fields.append(self.LevelOneForexFields.SYMBOL)
        await self._service_op(
            symbols, 'LEVELONE_FOREX', 'ADD',
            self.LevelOneForexFields, fields=fields)

    def add_level_one_forex_handler(self, handler):
        '''
        Register a function to handle level one forex quotes as they are sent.
        See :ref:`registering_handlers` for details.
        '''
        self._handlers['LEVELONE_FOREX'].append(_Handler(handler,
                                                         self.LevelOneForexFields))

    ##########################################################################
    # LEVELONE_FUTURES_OPTIONS

    class LevelOneFuturesOptionsFields(_BaseFieldEnum):
        '''
        '''

        #: Ticker symbol in upper case.
        SYMBOL = 0

        #: Current Bid Price
        BID_PRICE = 1

        #: Current Ask Price
        ASK_PRICE = 2

        #: Price at which the last trade was matched
        LAST_PRICE = 3

        #: Number of contracts for bid
        BID_SIZE = 4

        #: Number of contracts for ask
        ASK_SIZE = 5

        #: Exchange with the bid
        BID_ID = 6

        #: Exchange with the ask
        ASK_ID = 7

        #: Aggregated contracts traded throughout the day, including pre/post market hours.
        TOTAL_VOLUME = 8

        #: Number of contracts traded with last trade
        LAST_SIZE = 9

        #: Trade time of the last quote in milliseconds since epoch
        QUOTE_TIME_MILLIS = 10

        #: Trade time of the last trade in milliseconds since epoch
        TRADE_TIME_MILLIS = 11

        #: Day's high trade price
        HIGH_PRICE = 12

        #: Day's low trade price
        LOW_PRICE = 13

        #: Previous day's closing price
        CLOSE_PRICE = 14

        #: Exchange where last trade was executed
        LAST_ID = 15

        #: Description of the product
        DESCRIPTION = 16

        #: Day's Open Price
        OPEN_PRICE = 17

        #: Open Interest
        OPEN_INTEREST = 18

        #: Mark-to-Market value is calculated daily using current prices to determine profit/loss
        MARK = 19

        #: Minimum price movement
        TICK = 20

        #: Minimum amount that the price of the market can change
        TICK_AMOUNT = 21

        #: Point value
        FUTURE_MULTIPLIER = 22

        #: Closing price
        FUTURE_SETTLEMENT_PRICE = 23

        #: Underlying symbol
        UNDERLYING_SYMBOL = 24

        #: Strike Price
        STRIKE_PRICE = 25

        #: Expiration date of this contract
        FUTURE_EXPIRATION_DATE = 26

        #: Expiration Style
        EXPIRATION_STYLE = 27

        #: Contract Type
        CONTRACT_TYPE = 28

        #: Security Status
        SECURITY_STATUS = 29

        #: Exchange character
        EXCHANGE_ID = 30

        #: Display name of exchange
        EXCHANGE_NAME = 31

    async def level_one_futures_options_subs(self, symbols, *, fields=None):
        '''

        Subscribe to level one futures options quote data.

        :param symbols: Futures options symbols to receive quotes for
        :param fields: Iterable of :class:`LevelOneFuturesOptionsFields`
                       representing the fields to return in streaming entries.
                       If unset, all fields will be requested.
        '''
        if fields and self.LevelOneFuturesOptionsFields.SYMBOL not in fields:
            fields.append(self.LevelOneFuturesOptionsFields.SYMBOL)
        await self._service_op(
            symbols, 'LEVELONE_FUTURES_OPTIONS', 'SUBS',
            self.LevelOneFuturesOptionsFields, fields=fields)

    async def level_one_futures_options_unsubs(self, symbols):
        '''

        Un-Subscribe to level one futures options quote data.

        :param symbols: Futures options symbols to receive quotes for
        '''

        await self._service_op(symbols, 'LEVELONE_FUTURES_OPTIONS', 'UNSUBS')

    async def level_one_futures_options_add(self, symbols, *, fields=None):
        '''

        Add symbols to the list to receive quotes for.

        :param symbols: Futures options symbols add to list to receive quotes for
        :param fields: Iterable of :class:`LevelOneFuturesOptionsFields`
                       representing the fields to return in streaming entries.
                       If unset, all fields will be requested.
        '''
        if fields and self.LevelOneFuturesOptionsFields.SYMBOL not in fields:
            fields.append(self.LevelOneFuturesOptionsFields.SYMBOL)
        await self._service_op(
            symbols, 'LEVELONE_FUTURES_OPTIONS', 'ADD',
            self.LevelOneFuturesOptionsFields, fields=fields)

    def add_level_one_futures_options_handler(self, handler):
        '''
        Register a function to handle level one futures options quotes as they
        are sent. See :ref:`registering_handlers` for details.
        '''
        self._handlers['LEVELONE_FUTURES_OPTIONS'].append(
            _Handler(handler, self.LevelOneFuturesOptionsFields))

    ##########################################################################
    # Common book utilities

    class BookFields(_BaseFieldEnum):
        SYMBOL = 0
        BOOK_TIME = 1
        BIDS = 2
        ASKS = 3

    class BidFields(_BaseFieldEnum):
        BID_PRICE = 0
        TOTAL_VOLUME = 1
        NUM_BIDS = 2
        BIDS = 3

    class PerExchangeBidFields(_BaseFieldEnum):
        EXCHANGE = 0
        BID_VOLUME = 1
        SEQUENCE = 2

    class AskFields(_BaseFieldEnum):
        ASK_PRICE = 0
        TOTAL_VOLUME = 1
        NUM_ASKS = 2
        ASKS = 3

    class PerExchangeAskFields(_BaseFieldEnum):
        EXCHANGE = 0
        ASK_VOLUME = 1
        SEQUENCE = 2

    class _BookHandler(_Handler):
        def label_message(self, msg):
            # Relabel top-level fields
            new_msg = super().label_message(msg)

            # Relabel bids
            for content in new_msg['content']:
                if 'BIDS' in content:
                    for bid in content['BIDS']:
                        # Relabel top-level bids
                        StreamClient.BidFields.relabel_message(bid, bid)

                        # Relabel per-exchange bids
                        for e_bid in bid['BIDS']:
                            StreamClient.PerExchangeBidFields.relabel_message(
                                e_bid, e_bid)

            # Relabel asks
            for content in new_msg['content']:
                if 'ASKS' in content:
                    for ask in content['ASKS']:
                        # Relabel top-level asks
                        StreamClient.AskFields.relabel_message(ask, ask)

                        # Relabel per-exchange bids
                        for e_ask in ask['ASKS']:
                            StreamClient.PerExchangeAskFields.relabel_message(
                                e_ask, e_ask)

            return new_msg

    ##########################################################################
    # NYSE_BOOK

    async def nyse_book_subs(self, symbols):
        '''
        Subscribe to the NYSE level two order book.

        :param symbols: NYSE symbols to subscribe to.
        '''
        await self._service_op(
            symbols, 'NYSE_BOOK', 'SUBS',
            self.BookFields, fields=self.BookFields.all_fields())

    async def nyse_book_unsubs(self, symbols):
        '''
        Un-Subscribe to the NYSE level two order book.

        :param symbols: NYSE symbols to unsubscribe from.
        '''
        await self._service_op(symbols, 'NYSE_BOOK', 'UNSUBS')

    async def nyse_book_add(self, symbols):
        '''
        Add to the NYSE level two order book.

        :param symbols: NYSE symbols to add to the subscription.
        '''
        await self._service_op(symbols, 'NYSE_BOOK', 'ADD', self.BookFields)

    def add_nyse_book_handler(self, handler):
        '''
        Register a function to handle level two NYSE book data as it is updated
        See :ref:`registering_handlers` for details.
        '''
        self._handlers['NYSE_BOOK'].append(
            self._BookHandler(handler, self.BookFields))

    ##########################################################################
    # NASDAQ_BOOK

    async def nasdaq_book_subs(self, symbols):
        '''
        Subscribe to the NASDAQ level two order book.

        :param symbols: NASDAQ symbols to subscribe to.
        '''
        await self._service_op(symbols, 'NASDAQ_BOOK', 'SUBS',
                               self.BookFields,
                               fields=self.BookFields.all_fields())

    async def nasdaq_book_unsubs(self, symbols):
        '''
        Un-Subscribe to the NASDAQ level two order book.

        :param symbols: NASDAQ symbols to unsubscribe from.
        '''
        await self._service_op(symbols, 'NASDAQ_BOOK', 'UNSUBS')

    async def nasdaq_book_add(self, symbols):
        '''
        Add to the NASDAQ level two order book.

        :param symbols: NASDAQ symbols to add to the subscription.
        '''
        await self._service_op(symbols, 'NASDAQ_BOOK', 'ADD', self.BookFields)

    def add_nasdaq_book_handler(self, handler):
        '''
        Register a function to handle level two NASDAQ book data as it is
        updated See :ref:`registering_handlers` for details.
        '''
        self._handlers['NASDAQ_BOOK'].append(
            self._BookHandler(handler, self.BookFields))

    ##########################################################################
    # OPTIONS_BOOK

    async def options_book_subs(self, symbols):
        '''
        Subscribe to the level two order book for options.

        :param symbols: Option symbols to subscribe to.
        '''
        await self._service_op(symbols, 'OPTIONS_BOOK', 'SUBS',
                               self.BookFields,
                               fields=self.BookFields.all_fields())

    async def options_book_unsubs(self, symbols):
        '''
        Un-Subscribe to the level two order book for options.

        :param symbols: Option symbols to unsubscribe from.
        '''
        await self._service_op(symbols, 'OPTIONS_BOOK', 'UNSUBS')

    async def options_book_add(self, symbols):
        '''
        Add to the level two order book for options.

        :param symbols: Option symbols to add to the subscription.
        '''
        await self._service_op(symbols, 'OPTIONS_BOOK', 'ADD', self.BookFields)

    def add_options_book_handler(self, handler):
        '''
        Register a function to handle level two options book data as it is
        updated See :ref:`registering_handlers` for details.
        '''
        self._handlers['OPTIONS_BOOK'].append(
            self._BookHandler(handler, self.BookFields))

    ##########################################################################
    # SCREENER_EQUITY/SCREENER_OPTION

    class ScreenerFields(_BaseFieldEnum):
        #: The symbol used to look up either actives, gainers or losers
        SYMBOL = 0

        #: Market snapshot timestamp in milliseconds since Epoch
        TIMESTAMP = 1

        #: Field to sort on
        SORT_FIELD = 2

        #: Frequency of data to sort
        FREQUENCY = 3

        #: Array of fields
        ITEMS = 4

    async def screener_equity_subs(self, symbols):
        '''
        Subscribe to Screener Equity.

        The subscription key is **not** a stock symbol. It is
        ``(PREFIX)_(SORTFIELD)_(FREQUENCY)``, for example ``NASDAQ_VOLUME_5``
        or ``$SPX_PERCENT_CHANGE_UP_60``. Passing a ticker subscribes to
        nothing and reports no error. See :ref:`screener` for the
        full set of prefixes, sort fields and frequencies.

        :param symbols: Screener keys to subscribe to.
        '''
        await self._service_op(symbols, 'SCREENER_EQUITY', 'SUBS', self.ScreenerFields)

    async def screener_equity_unsubs(self, symbols):
        '''
        Un-Subscribe to Screener Equity.

        :param symbols: Screener keys to unsubscribe from, in the
                        ``(PREFIX)_(SORTFIELD)_(FREQUENCY)`` form.
        '''
        await self._service_op(symbols, 'SCREENER_EQUITY', 'UNSUBS')

    async def screener_equity_add(self, symbols):
        '''
        Add keys to the Screener Equity subscription.

        :param symbols: Screener keys to add, in the
                        ``(PREFIX)_(SORTFIELD)_(FREQUENCY)`` form.
        '''
        await self._service_op(symbols, 'SCREENER_EQUITY', 'ADD', self.ScreenerFields)

    def add_screener_equity_handler(self, handler):
        '''
        Register a function to handle Screener Equity data as it is
        updated See :ref:`registering_handlers` for details.
        '''
        self._handlers['SCREENER_EQUITY'].append(
            _Handler(handler, self.ScreenerFields))

    async def screener_option_subs(self, symbols):
        '''
        Subscribe to Screener Option.

        The subscription key is **not** a stock symbol. It is
        ``(PREFIX)_(SORTFIELD)_(FREQUENCY)``, for example ``OPTION_CALL_VOLUME_5``
        or ``OPTION_ALL_TRADES_60``. Passing a ticker subscribes to
        nothing and reports no error. See :ref:`screener` for the
        full set of prefixes, sort fields and frequencies.

        :param symbols: Screener keys to subscribe to.
        '''
        await self._service_op(symbols, 'SCREENER_OPTION', 'SUBS', self.ScreenerFields)

    async def screener_option_unsubs(self, symbols):
        '''
        Un-Subscribe to Screener Option.

        :param symbols: Screener keys to unsubscribe from, in the
                        ``(PREFIX)_(SORTFIELD)_(FREQUENCY)`` form.
        '''
        await self._service_op(symbols, 'SCREENER_OPTION', 'UNSUBS')

    async def screener_option_add(self, symbols):
        '''
        Add keys to the Screener Option subscription.

        :param symbols: Screener keys to add, in the
                        ``(PREFIX)_(SORTFIELD)_(FREQUENCY)`` form.
        '''
        await self._service_op(symbols, 'SCREENER_OPTION', 'ADD', self.ScreenerFields)

    def add_screener_option_handler(self, handler):
        '''
        Register a function to handle Screener Option data as it is
        updated See :ref:`registering_handlers` for details.
        '''
        self._handlers['SCREENER_OPTION'].append(
            _Handler(handler, self.ScreenerFields))
