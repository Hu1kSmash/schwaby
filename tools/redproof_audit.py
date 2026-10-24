'''Runs pytest for tools/redproof.py, recording what the run wrote.

    python -I -B tools/redproof_audit.py <pytest arguments>
    python -I -B tools/redproof_audit.py --where
    python -I -B tools/redproof_audit.py --collect <out.json> <pytest arguments>

Run from the root of a copy of the repository: the copy's directory goes first
on ``sys.path``, as ``python -m pytest`` would put it, so the tests import the
copy's ``schwaby``. ``--where`` prints the file ``import schwaby`` resolves to,
which is how the harness proves that before it judges anything. Without that a
mutated copy and an import from somewhere else look the same: every verdict a
false GREEN.

With ``REDPROOF_AUDIT`` set, an audit hook goes in before pytest is imported
and records every path the run writes: an ``open()`` or ``os.open()`` for
writing, and a rename, removal, ``mkdir``, permission or time change, link or
truncation. ``os.open`` is wrapped as well as audited: its audit event does
not carry ``dir_fd``, so a name relative to a directory descriptor was
recorded at the wrong path. The record is written to that path when pytest
returns. The harness compares the copy with the snapshot after the run,
which catches a tracked file left changed; this catches one changed and put
back. A write the hook could not place is recorded as unresolved, and the
harness fails the run for it, as it does for a run that left no record.
Recording never raises into the run: an exception there would fail the call
being audited, and a test failing for that reason is a red the mutation did
not cause. What it cannot see is a write by a process the test started.

``--collect`` writes ``{node id: xdist group}`` for every collected test with
an ``xdist_group`` mark. The harness reads it to keep two entries that share a
fixed port (the login flow's callback server, on 6969) from running at once.
'''

import json
import os
import sys

_OUT = os.environ.get('REDPROOF_AUDIT')

_written = set()
_unresolved = []

_WRITE = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
# tempfile.TemporaryFile, which pytest's output capture uses: a file with no
# name, which nothing else can read
_ANONYMOUS = getattr(os, 'O_TMPFILE', 0)

# Events that change a path, with where the path and its directory descriptor
# sit among the event's arguments. A rename or an unlink writes as surely as an
# open for writing does.
_CHANGES = {
    'os.rename': ((0, 2), (1, 3)),
    'os.remove': ((0, 1),),
    'os.rmdir': ((0, 1),),
    'os.mkdir': ((0, 2),),
    'os.chmod': ((0, 2),),
    'os.chown': ((0, 3),),
    'os.utime': ((0, 3),),
    'os.symlink': ((1, 2),),
    'os.link': ((1, 3),),
    'os.truncate': ((0, None),),
    'shutil.rmtree': ((0, 1),),
}
# The real one, for the hook's own lookups.
_LSTAT = os.lstat


def _at(value, dir_fd=None):
    '''The absolute path an event names; None for a file descriptor, whose
    open was recorded when it was made; '' when it cannot be worked out.'''
    if isinstance(value, int):
        return None
    try:
        name = os.fsdecode(os.fspath(value))
        if isinstance(dir_fd, int) and dir_fd >= 0 and not os.path.isabs(name):
            name = os.path.join(os.readlink('/proc/self/fd/%d' % dir_fd), name)
        return os.path.abspath(name)
    except Exception:
        return ''


def _exists(path):
    try:
        _LSTAT(path)
    except OSError:
        return False
    return True


def _hook(event, args):
    try:
        _record(event, args)
    except Exception:
        _unresolved.append(event)


def _record(event, args):
    if event == 'open':
        mode = args[1] if len(args) > 1 else None
        flags = args[2] if len(args) > 2 else 0
        if isinstance(flags, int) and _ANONYMOUS and (
                flags & _ANONYMOUS) == _ANONYMOUS:
            return
        if not ((isinstance(mode, str) and any(c in mode for c in 'wax+'))
                or (isinstance(flags, int) and flags & _WRITE)):
            return
        path = _at(args[0])
        if path == '':
            _unresolved.append(event)
        elif path is not None:
            _written.add(path)
    elif event in _CHANGES:
        for at, fd in _CHANGES[event]:
            if at >= len(args):
                continue
            dir_fd = args[fd] if fd is not None and fd < len(args) else None
            path = _at(args[at], dir_fd)
            if path == '':
                _unresolved.append(event)
            # A mkdir of what already exists changes nothing; pytest makes its
            # directories with exist_ok=True.
            elif path is not None and not (
                    event == 'os.mkdir' and _exists(path)):
                _written.add(path)


def _install():
    sys.addaudithook(_hook)

    import posix
    real = os.open

    def audited_open(path, flags, *args, **kwargs):
        # The audit event names a dir_fd-relative path as if relative to
        # the working directory; this records where it really is. The event
        # still records its own reading too, in case a caller took os.open
        # before this was put in: a wrong path costs a false alarm, a
        # missing one a tracked write unseen.
        try:
            if isinstance(flags, int) and flags & _WRITE and not (
                    _ANONYMOUS and (flags & _ANONYMOUS) == _ANONYMOUS):
                p = _at(path, kwargs.get('dir_fd'))
                if p == '':
                    _unresolved.append('os.open')
                elif p is not None:
                    _written.add(p)
        except Exception:
            _unresolved.append('os.open')
        return real(path, flags, *args, **kwargs)

    if real in os.supports_dir_fd:
        os.supports_dir_fd.add(audited_open)
    os.open = posix.open = audited_open


def write_record(path):
    # Copies first: a thread the run left behind may still be adding.
    record = {'written': sorted(set(_written)),
              'unresolved': list(_unresolved)}
    with open(path + '.tmp', 'w', encoding='utf-8') as f:
        json.dump(record, f)
    os.replace(path + '.tmp', path)


class _Collector:
    def __init__(self, out):
        self.out = out

    def pytest_collection_finish(self, session):
        groups = {}
        for item in session.items:
            mark = item.get_closest_marker('xdist_group')
            if mark is not None:
                groups[item.nodeid] = (mark.args[0] if mark.args
                                       else mark.kwargs.get('name', ''))
        with open(self.out, 'w', encoding='utf-8') as f:
            json.dump(groups, f)


def main(argv):
    # As `python -m pytest` would have it: the copy first.
    sys.path.insert(0, os.getcwd())

    if argv[:1] == ['--where']:
        import schwaby
        print(os.path.realpath(schwaby.__file__))
        return 0

    plugins = []
    if argv[:1] == ['--collect']:
        plugins.append(_Collector(argv[1]))
        argv = ['--collect-only', '-q'] + argv[2:]

    import pytest
    code = int(pytest.main(argv, plugins=plugins))
    if _OUT:
        try:
            write_record(_OUT)
        except Exception as e:
            # The exit status is pytest's. With no record, a write put back
            # before the end goes unseen; one left in place is still caught.
            print('redproof_audit: no record (%s: %s)' % (type(e).__name__, e),
                  file=sys.stderr)
    return code


if _OUT:
    _install()

if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
