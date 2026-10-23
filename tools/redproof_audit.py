'''Runs pytest for tools/redproof.py, recording what the run read.

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
and the record is written to that path when pytest returns. Python's audit
hooks see every ``open()`` (an import's source too, through ``io.open_code``),
every directory listing, every process started and every socket connected.
They do not see an existence check, which opens nothing, so ``os.stat``,
``os.lstat``, ``os.access`` and ``os.readlink`` are wrapped where ``os.path``,
``pathlib`` and the import system look them up. A kept verdict has to stop
holding when a file it only checked for appears or goes, as surely as when one
it read changes.

What the hook cannot see, it says. A process started from the run reads files
nothing here records, so the record names it and the harness never keeps that
run. A path the hook could not work out is recorded as unresolved, with the
same effect. Recording never raises into the run: an exception there would fail
the call being audited, and a test failing for that reason is a red the
mutation did not cause.

``--collect`` writes ``{node id: xdist group}`` for every collected test with
an ``xdist_group`` mark. The harness reads it to keep two entries that share a
fixed port (the login flow's callback server, on 6969) from running at once.
'''

import functools
import json
import os
import sys

_OUT = os.environ.get('REDPROOF_AUDIT')

_opened = set()
_written = set()
_listed = set()
_probed = set()
_spawned = []
_connected = []
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
_SPAWN = ('subprocess.Popen', 'os.posix_spawn', 'os.exec', 'os.spawn',
          'os.system', 'os.fork', 'os.forkpty', 'pty.spawn')

# The real ones, for the hook's own lookups: those must not be recorded as
# the run's probes.
_READLINK = os.readlink
_LSTAT = os.lstat


def _at(value, dir_fd=None):
    '''The absolute path an event names; None for a file descriptor, whose
    open was recorded when it was made; '' when it cannot be worked out.'''
    if isinstance(value, int):
        return None
    try:
        name = os.fsdecode(os.fspath(value))
        if isinstance(dir_fd, int) and dir_fd >= 0 and not os.path.isabs(name):
            name = os.path.join(_READLINK('/proc/self/fd/%d' % dir_fd), name)
        return os.path.abspath(name)
    except Exception:
        return ''


def _exists(path):
    try:
        _LSTAT(path)
    except OSError:
        return False
    return True


def _loopback(address):
    if isinstance(address, tuple) and address and isinstance(address[0], str):
        return address[0] in ('localhost', '::1') or address[0].startswith('127.')
    # A Unix socket: a file on this machine, not the network
    return isinstance(address, (str, bytes))


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
        path = _at(args[0])
        if path == '':
            _unresolved.append(event)
        elif path is not None:
            _opened.add(path)
            if ((isinstance(mode, str) and any(c in mode for c in 'wax+'))
                    or (isinstance(flags, int) and flags & _WRITE)):
                _written.add(path)
    elif event in ('os.listdir', 'os.scandir'):
        # A listing by descriptor (shutil.rmtree's, os.fwalk's) was preceded
        # by the open that made the descriptor, and that open was recorded.
        path = _at(args[0] if args and args[0] is not None else '.')
        if path == '':
            _unresolved.append(event)
        elif path is not None:
            _listed.add(path)
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
                _opened.add(path)
                _written.add(path)
    elif event in _SPAWN:
        _spawned.append(event)
    elif (event == 'socket.connect' and len(args) > 1
            and not _loopback(args[1])):
        _connected.append(repr(args[1]))


def _probe(real):
    @functools.wraps(real)
    def probe(path, *args, **kwargs):
        try:
            p = _at(path, kwargs.get('dir_fd'))
            if p == '':
                _unresolved.append(real.__name__)
            elif p is not None:
                _probed.add(p)
        except Exception:
            _unresolved.append(real.__name__)
        return real(path, *args, **kwargs)
    return probe


def _install():
    sys.addaudithook(_hook)

    import posix

    # os.path, pathlib and importlib look these up at each call, so they see
    # the wrappers. Each wrapper also stands where its original stood in the
    # os.supports_* sets, which shutil reads when it is imported.
    supports = (os.supports_dir_fd, os.supports_fd,
                os.supports_follow_symlinks, os.supports_effective_ids)
    for name in ('stat', 'lstat', 'access', 'readlink'):
        real = getattr(os, name)
        wrapper = _probe(real)
        for s in supports:
            if real in s:
                s.add(wrapper)
        setattr(os, name, wrapper)
        setattr(posix, name, wrapper)

    # multiprocessing's spawn and forkserver start their processes through
    # util.spawnv_passfds, which calls this; a spawn start raises no audit
    # event at all (measured on 3.14). A start is a start, whatever makes it.
    import _posixsubprocess
    fork_exec = _posixsubprocess.fork_exec

    def counted_fork_exec(*args, **kwargs):
        _spawned.append('_posixsubprocess.fork_exec')
        return fork_exec(*args, **kwargs)

    _posixsubprocess.fork_exec = counted_fork_exec


def write_record(path):
    # Copies first: a thread the run left behind may still be adding.
    opened, written = set(_opened), set(_written)
    record = {
        'opened': {p: p in written for p in sorted(opened | written)},
        'listed': sorted(set(_listed)),
        'probed': sorted(set(_probed)),
        'spawned': list(_spawned),
        'connected': list(_connected),
        'unresolved': list(_unresolved),
    }
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
            # The exit status is pytest's. With no record the run is never
            # kept, which costs a rerun and nothing else.
            print('redproof_audit: no record (%s: %s)' % (type(e).__name__, e),
                  file=sys.stderr)
    return code


if _OUT:
    _install()

if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
