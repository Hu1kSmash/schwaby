import atexit
import httpx2
import json
import logging
import sys
import schwaby


def get_logger():
    return logging.getLogger(__name__)


class LogRedactor:
    '''
    Collects strings that should not be emitted and replaces them with safe
    placeholders.
    '''

    def __init__(self):
        from collections import defaultdict

        self.redacted_strings = {}
        self.label_counts = defaultdict(int)

    def register(self, string, label):
        '''
        Registers a string that should not be emitted and the label with with
        which it should be replaced.
        '''
        string = str(string)
        if string not in self.redacted_strings:
            self.label_counts[label] += 1
            self.redacted_strings[string] = (label, self.label_counts[label])

    def redact(self, msg):
        '''
        Scans the string for secret strings and returns a sanitized version with
        the secrets replaced with placeholders.
        '''
        for string, label in self.redacted_strings.items():
            label, count = label
            msg = msg.replace(string, '<REDACTED {}{}>'.format(
                label, '-{}'.format(count) if
                self.label_counts[label] > 1 else ''))
        return msg


# Response redaction is only useful when the logs are going to be shared, and it
# is not free: it parses and walks every successful response, and every value it
# finds is remembered for the life of the process so it can be scrubbed from
# future log lines. enable_bug_report_logging turns it on, and says in its own
# documentation that it carries a performance penalty and does not belong in
# production.
_COLLECT_RESPONSE_REDACTIONS = False


def register_redactions_from_response(resp):
    '''
    Convenience method that calls ``register_redactions`` if resp represents a
    successful response. Note this method assumes that resp has a JSON contents.

    Does nothing unless :func:`enable_bug_report_logging` has been called, since
    that is the only context in which the collected values are used.
    '''
    if not _COLLECT_RESPONSE_REDACTIONS:
        return

    if resp.status_code == httpx2.codes.OK:
        try:
            register_redactions(resp.json())
        except json.decoder.JSONDecodeError:
            pass


def register_redactions(obj, key_path=None,
                        # Note these are matched as substrings of the key, so
                        # a pattern which is short or common catches unrelated
                        # fields, and redaction is plain string substitution
                        # over the whole log. 'account' would match
                        # accountValue and accountColor and take every balance
                        # and the word 'Green' with it, so the two Schwab
                        # identifiers worth hiding are named in full.
                        bad_patterns=[
                            'accountnumber', 'acl', 'auth', 'displayname',
                            'hashvalue', 'id', 'key', 'token'],
                        whitelisted=set([
                            'requestid',
                            'token_type',
                            'legid',
                            'bidid',
                            'askid',
                            'lastid',
                            'bidsizeinlong',
                            'bidsizeindouble',
                            'bidpriceindouble'])):
    '''
    Recursively iterates through the leaf elements of ``obj`` and registers
    elements with keys matching a blacklist with the global ``Redactor``.
    '''
    if key_path is None:
        key_path = []

    if isinstance(obj, list):
        for idx, value in enumerate(obj):
            key_path.append(str(idx))
            register_redactions(value, key_path, bad_patterns, whitelisted)
            key_path.pop()
    elif isinstance(obj, dict):
        for key, value in obj.items():
            key_path.append(key)
            register_redactions(value, key_path, bad_patterns, whitelisted)
            key_path.pop()
    else:
        if key_path:
            last_key = key_path[-1].lower()
            if last_key in whitelisted:
                return
            elif any(bad in last_key for bad in bad_patterns):
                schwaby.LOG_REDACTOR.register(obj, '-'.join(key_path))


def enable_bug_report_logging():
    '''
    Turns on bug report logging. Will collect all logged output, redact out
    anything that should be kept secret, and emit the result at program exit.

    Notes:
     * This method does a best effort redaction. Never share its output
       without verifying that all secret information is properly redacted.
     * Because this function records all logged output, it has a performance
       penalty. It should not be called in production code.
    '''
    _enable_bug_report_logging()


def _enable_bug_report_logging(output=None, loggers=None):
    '''
    Module-internal version of :func:`enable_bug_report_logging`, intended for
    use in tests. ``output`` defaults to ``sys.stderr`` as it is at program
    exit, when the logs are actually written.
    '''
    global _COLLECT_RESPONSE_REDACTIONS
    _COLLECT_RESPONSE_REDACTIONS = True

    if loggers is None:
        loggers = (
            schwaby.auth.get_logger(),
            schwaby.client.base.get_logger(),
            schwaby.streaming.get_logger(),
            get_logger())

    class RecordingHandler(logging.Handler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.messages = []

        def emit(self, record):
            self.messages.append(self.format(record))

    handler = RecordingHandler()
    handler.setFormatter(logging.Formatter(
        '[%(filename)s:%(lineno)s:%(funcName)s] %(message)s'))

    for logger in loggers:
        logger.setLevel(logging.DEBUG)
        logger.addHandler(handler)

    def write_logs():
        # sys.stderr is looked up here rather than captured as a default
        # argument, which would bind whatever it was when this module was
        # imported. These logs are written at program exit, so the stream that
        # matters is the one the program is using then.
        out = sys.stderr if output is None else output

        try:
            print(file=out)
            print(' ### BEGIN REDACTED LOGS ###', file=out)
            print(file=out)

            for msg in handler.messages:
                msg = schwaby.LOG_REDACTOR.redact(msg)
                print(msg, file=out)
        except (ValueError, OSError):
            # The stream is gone. Closed before the interpreter shut down
            # (ValueError), or the far end of a pipe went away (OSError, and
            # in practice BrokenPipeError -- `python bot.py 2>&1 | head` is
            # enough). There is nowhere left to write the report, and a
            # traceback out of an atexit handler is a worse last word than
            # nothing.
            pass
    atexit.register(write_logs)

    get_logger().debug('schwab-api version %s', schwaby.__version__)

    return write_logs
