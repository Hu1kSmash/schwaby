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
and records what the run writes: an ``open()`` or ``os.open()`` for writing,
a rename, a removal, a permission or time change, a truncation. Each is
recorded by its path and by the file it touched (its device and inode), so a
write through a symbolic or hard link to a tracked file, or through a
descriptor, names that file as surely as a write by its own name. A link or
a directory made or removed is not recorded: none can be a tracked file,
which is removed, and recorded, before anything else can stand at its path.
``os.open`` is wrapped, since its audit event carries no ``dir_fd``; the
wrapper records the file it opened and the event inside it records nothing.
The record is written to that path when pytest returns. The harness
compares the copy with the snapshot after the run, which catches a tracked
file left changed; this catches one changed and put back. A write the hook
could not place is recorded as unresolved, and the harness fails the run for
it, as it does for a run that left no record. Recording never raises into
the run: an exception there would fail the call being audited, and a test
failing for that reason is a red the mutation did not cause. What it cannot
see: a write by a process the test started, and an ``os.open`` a caller took
before the hook was put in, which is recorded at the wrong path.

``--collect`` writes ``{node id: xdist group}`` for every collected test with
an ``xdist_group`` mark. The harness reads it to keep two entries that share a
fixed port (the login flow's callback server, on 6969) from running at once.
'''

import json
import os
import sys
import threading

_OUT = os.environ.get('REDPROOF_AUDIT')

_written = set()
_inodes = set()
_unresolved = []
# Set while the os.open wrapper runs, so the open event it raises is left to
# the wrapper, which knows where the file is.
_wrapped = threading.local()

_WRITE = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
# tempfile.TemporaryFile, which pytest's output capture uses: a file with no
# name, which nothing else can read
_ANONYMOUS = getattr(os, 'O_TMPFILE', 0)

# Events that change a file, with where the path (or descriptor) and its
# directory descriptor sit among the event's arguments, and whether the event
# acts on a link itself rather than what it points to. A rename or an unlink
# writes as surely as an open for writing does.
_CHANGES = {
    'os.rename': ((0, 2, False), (1, 3, False)),
    'os.remove': ((0, 1, False),),
    'os.chmod': ((0, 2, True),),
    'os.chown': ((0, 3, True),),
    'os.utime': ((0, 3, True),),
    'os.truncate': ((0, None, True),),
}


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


def _identity(value, dir_fd=None, follow=True):
    '''Notes the file a path or descriptor names, by device and inode: the
    same file under any name. Nothing, for what does not exist yet.'''
    try:
        if isinstance(value, int):
            st = os.fstat(value)
        else:
            st = os.stat(value, dir_fd=dir_fd if isinstance(dir_fd, int)
                         and dir_fd >= 0 else None, follow_symlinks=follow)
    except Exception:
        return
    _inodes.add((st.st_dev, st.st_ino))


def _hook(event, args):
    try:
        _record(event, args)
    except Exception:
        _unresolved.append(event)


def _writes(flags):
    return isinstance(flags, int) and bool(flags & _WRITE) and not (
        _ANONYMOUS and (flags & _ANONYMOUS) == _ANONYMOUS)


def _record(event, args):
    if event == 'open':
        if getattr(_wrapped, 'active', False):
            return
        mode = args[1] if len(args) > 1 else None
        flags = args[2] if len(args) > 2 else 0
        if isinstance(flags, int) and _ANONYMOUS and (
                flags & _ANONYMOUS) == _ANONYMOUS:
            return
        if not ((isinstance(mode, str) and any(c in mode for c in 'wax+'))
                or _writes(flags)):
            return
        path = _at(args[0])
        if path == '':
            _unresolved.append(event)
        elif path is not None:
            _written.add(path)
        # Before the open: a file it truncates is still the same file.
        _identity(args[0])
    elif event in _CHANGES:
        for at, fd, follow in _CHANGES[event]:
            if at >= len(args):
                continue
            dir_fd = args[fd] if fd is not None and fd < len(args) else None
            path = _at(args[at], dir_fd)
            if path == '':
                _unresolved.append(event)
            elif path is not None:
                _written.add(path)
            _identity(args[at], dir_fd, follow)


def _install():
    sys.addaudithook(_hook)

    import posix
    real = os.open

    def audited_open(path, flags, *args, **kwargs):
        # The audit event names a dir_fd-relative path as if relative to
        # the working directory; this records where it really is, once the
        # call has succeeded (a call that fails wrote nothing), and the
        # event inside it records nothing.
        _wrapped.active = True
        try:
            fd = real(path, flags, *args, **kwargs)
        finally:
            _wrapped.active = False
        try:
            if _writes(flags):
                p = _at(path, kwargs.get('dir_fd'))
                if p == '':
                    _unresolved.append('os.open')
                elif p is not None:
                    _written.add(p)
                _identity(fd)
        except Exception:
            _unresolved.append('os.open')
        return fd

    if real in os.supports_dir_fd:
        os.supports_dir_fd.add(audited_open)
    os.open = posix.open = audited_open


def write_record(path):
    # Copies first: a thread the run left behind may still be adding.
    record = {'written': sorted(set(_written)),
              'inodes': sorted(set(_inodes)),
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
