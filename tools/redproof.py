'''Mechanical red-proofing: break the code on purpose, and require the failure
to arrive on the assertion you named.

    .venv/bin/python -I -B tools/redproof.py                 # every stale entry
    .venv/bin/python -I -B tools/redproof.py --name auth/    # names containing it
    .venv/bin/python -I -B tools/redproof.py --all           # every entry, kept or not
    .venv/bin/python -I -B tools/redproof.py --jobs 4        # entries at once
    .venv/bin/python -I -B tools/redproof.py --shard 2/3     # one slice of three

A test that passes whether or not the fix is there is worse than none, because
it looks like protection. So revert the fix and watch the test fail. Doing that
by hand failed four times in one sitting here, every time producing a red, just
not from the assertion being claimed:

 * a mutation applied to a call site both branches reach, so it proved only
   that *some* branch was covered;
 * a restore inside the same second with an identical-length edit, so CPython
   reused stale bytecode and a correct file appeared to fail;
 * a test whose input never took the branch under test, so the guard it was
   written for never ran and the opposite mutation left it green;
 * a test rewritten by string index, silently deleting two others.

A correct red and a red for the wrong reason are indistinguishable unless
something forces you to read the message. That is all this does. Each entry in
tools/redproof_cases.py names the mutation, the test that must fail, and a
substring that must appear in the failure.

VERDICTS
    PROVEN      the test passed unmutated, failed mutated, and the output
                carries `expect`: the check works.
    KEPT        PROVEN by an earlier run whose every input still holds (below).
    GREEN       the test passed with the code broken. Two readings, needing
                different fixes: the test is asleep, or what was mutated is no
                longer load-bearing because something later covers it. If the
                second, mark the entry `superseded=` with the reason rather
                than deleting it.
    SUPERSEDED  GREEN, on an entry marked superseded: a real answer.
    REMOVED     the code the entry mutates was deleted, and the entry says so.
    RED?        it failed, but not on `expect`, or ran out of time, or was
                killed: proves nothing.
    SPEC        the entry cannot be judged: its text is missing or ambiguous,
                the mutation is a no-op, or the test fails unmutated.

POSIX only: each run is a process group of its own, waited for with
os.wait4 and killed with os.killpg.

Exit status 0 only when every selected entry is PROVEN, KEPT, SUPERSEDED or
REMOVED, and no run wrote to a tracked file; 2 when --name matches nothing.

SHARDS. `--shard K/N` judges one slice of N, so N machines can share one
run: the selected entries (after --name) are divided by a hash of each name,
and only the stale entries in slice K run. Every entry is in exactly one
slice, whichever entries are stale, so the slices of one run never overlap
and never miss one, and an entry stays in its slice from run to run. An empty
slice is not an error: it has nothing to run and exits 0.

THE WORKING TREE IS NEVER TOUCHED. The run snapshots the repository's own files
(tracked, plus untracked and not ignored) into a temporary directory, reads the
entries from the snapshot, and gives every test run a fresh copy of it.
Mutations and comparisons are in bytes. The tests run under
tools/redproof_audit.py, which puts the copy first on sys.path; before judging
anything the run proves that `import schwaby` in a copy resolves inside it,
because an import from anywhere else makes every verdict a false GREEN.

PARALLEL. Every usable core with REDPROOF_JOBS_ALL=1, which a job that holds
a machine to itself sets; two at a time otherwise, since a run on a machine
someone is using should stay modest. Never more than memory holds: the pool is
min(cores, (available memory - FLOOR) / the largest run measured so far),
printed with its reason. The first baseline runs alone to give that
measurement. `--jobs` overrides the core count. Entries whose tests carry the
same xdist_group mark never run at once: the login flow's callback server binds
a fixed port, 6969, and two copies of it fail each other.

KEPT VERDICTS. A PROVEN entry is kept in cache/redproof.json (gitignored) on
exactly what its two runs read, as recorded by the audit hook: the mutated run
and the unmutated baseline it depends on. The copy's files count by their bytes
as each run found them (the mutation applied in the one, not the other), the
directories listed by their names, and the paths only checked by what they
were (file, directory or absent). The interpreter counts as its implementation,
major.minor version and the build .python-versions pins for it, and the
installed libraries as requirements.lock, so a verdict is kept or reused only
when the interpreter is that exact build and the installed set is exactly the
lock. Never the machine: one store serves every rig. Never kept: a run that
started a process (what a child reads is not recorded), connected beyond
loopback, read /proc or /sys, read a file outside the copy that may hold a
secret (whose bytes are never digested), or wrote outside its own temporary
directory. An untracked file in the checkout whose name says it may hold a
secret is left out of the copies altogether. Kept verdicts need Python 3.11 or
newer, where pathlib looks os.stat up at each call; earlier versions were not
checked.

What the audit cannot see, a record cannot key, and these are known: files the
C library reads for itself (time zone data under TZ, locale files), an
os.open() relative to a directory descriptor (recorded at the wrong path), and
a probed path's size or permissions (only its kind is keyed).

A selected entry with a record that still holds is KEPT without running, and
the record's time is renewed. A record that has not held or been proved for 60
days is dropped. A run that finds an entry not PROVEN while a record says it
was retires every record that held, with a `gone` tombstone, and a save never
lets a renewal undo one: only a proof made after it does.
'''

import argparse
import concurrent.futures
import hashlib
import importlib.util
import json
import os
import platform
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CASES = 'tools/redproof_cases.py'
AUDIT = 'tools/redproof_audit.py'
LOCK = 'requirements.lock'
PYTHONS = '.python-versions'
STORE = os.path.join(ROOT, 'cache', 'redproof.json')
STORE_VERSION = 2
KEEP_DAYS = 60

#: The lowest a run may leave available, in bytes
FLOOR = 2 << 30
#: The bound on one test run, in seconds, before it is RED? by name
RUN_LIMIT = 600
#: The bound on a run's leftover processes going, once it has exited
REAP_LIMIT = 30
#: The bounds on the steps before any entry runs, and on the store's lock
GIT_LIMIT = 60
PREPARE_LIMIT = 300
LOCK_LIMIT = 120

DEVICES = ('/dev/null', '/dev/zero', '/dev/full', '/dev/random',
           '/dev/urandom', '/dev/tty')
#: Files that may hold a secret: a run that read one is never kept, so its
#: bytes are never digested into the store.
SECRET_PATH = re.compile(
    r'(^|/)(\.env[^/]*|\.netrc|\.pypirc|\.git-credentials|[^/]*token[^/]*'
    r'|[^/]*secret[^/]*|[^/]*credential[^/]*|[^/]*password[^/]*'
    r'|[^/]*\.(pem|key|p12|pfx)|id_[a-z0-9]+)$|(^|/)\.(config|ssh|gnupg)/',
    re.IGNORECASE)

_live = set()
_live_lock = threading.Lock()
_stopping = threading.Event()


def usable_cpus():
    if hasattr(os, 'process_cpu_count'):
        return os.process_cpu_count() or 1
    if hasattr(os, 'sched_getaffinity'):
        return len(os.sched_getaffinity(0))
    return os.cpu_count() or 1


def available_memory():
    '''MemAvailable, in bytes, or None where the kernel does not say.'''
    try:
        with open('/proc/meminfo', 'rb') as f:
            for line in f:
                if line.startswith(b'MemAvailable:'):
                    return int(line.split()[1]) * 1024
    except OSError:
        pass
    return None


def gib(n):
    if n < 1 << 30:
        return '%d MiB' % (n >> 20)
    return '%.1f GiB' % (n / (1 << 30))


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def sha(data):
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------- the entries

def load_cases(snapshot):
    spec = importlib.util.spec_from_file_location(
        'redproof_cases', os.path.join(snapshot, CASES))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.CASES


def tests_of(case):
    return list(case.test) if isinstance(case.test, (list, tuple)) else [
        case.test]


def pairs_of(case):
    pairs = [(case.old, case.new)]
    if case.also:
        pairs.append(tuple(case.also))
    return [(old.encode('utf-8'), new.encode('utf-8')) for old, new in pairs]


# A line of pytest's short summary: FAILED, ERROR or SUBFAILED with a subtest's
# [message] and (params), then the node id, then " - message" if any.
_SUMMARY = re.compile(
    r'^(?:SUB)?(?:FAILED|ERROR)(?P<label>\[.*?\])?\s*(?P<params>\(.*\))?'
    r'\s+\S*\.py\S*(?: - (?P<msg>.*))?$')
# A failure's header: underscores, the test, a subtest's label, underscores.
_HEADER = re.compile(r'^_{3,} \S+ (?P<sub>.+?) _{3,}$')


def failure_text(output):
    '''What pytest says the failures were: the `E` lines of each traceback,
    each short-summary message, and the label and params of each failing
    subTest, which pytest builds from the subtest itself and which are all
    that tells apart subtests failing with the same message. Not the source
    lines pytest echoes around a failure, and not node ids: either can
    contain an entry's `expect` whatever actually failed, which made a
    failure on the wrong assertion read as PROVEN.'''
    lines = []
    for line in output.splitlines():
        if line.startswith('E '):
            lines.append(line[2:])
            continue
        m = _SUMMARY.match(line)
        if m:
            lines.extend(part for part in m.group('label', 'params', 'msg')
                         if part)
            continue
        m = _HEADER.match(line)
        if m:
            lines.append(m.group('sub'))
    return '\n'.join(lines)


def mutate(case, original):
    '''The mutated bytes, or (None, why) when the entry cannot be judged.'''
    pairs = pairs_of(case)
    for old, new in pairs:
        if old == new:
            # A no-op mutation is necessarily GREEN, and GREEN reads as "the
            # test is asleep", which invites deleting a guard that works.
            return None, ('mutation text and replacement are identical, so '
                          'this entry changes nothing and can never fail')
    for old, _ in pairs:
        found = original.count(old)
        if found != 1:
            return None, ('mutation text %r appears %d times, expected once'
                          % (old[:40].decode('utf-8', 'replace'), found))
    mutated = original
    for old, new in pairs:
        mutated = mutated.replace(old, new)
    return mutated, None


def case_fields(case):
    return {
        'name': case.name, 'path': case.path, 'old': case.old,
        'new': case.new, 'test': tests_of(case), 'expect': case.expect,
        'also': list(case.also) if case.also else None,
        'superseded': case.superseded,
    }


# --------------------------------------------------------------- the snapshot

def repo_files():
    out = subprocess.run(
        ['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard'],
        cwd=ROOT, capture_output=True, check=True, timeout=GIT_LIMIT).stdout
    return sorted({f for f in out.decode('utf-8').split('\0')
                   if f and os.path.isfile(os.path.join(ROOT, f))})


def tracked_files():
    out = subprocess.run(['git', 'ls-files', '-z'], cwd=ROOT,
                         capture_output=True, check=True,
                         timeout=GIT_LIMIT).stdout
    return {f for f in out.decode('utf-8').split('\0') if f}


def secret_untracked(files, tracked):
    '''The untracked files whose names say they may hold a secret. They are
    not copied: a copy's reads are digested into the store, and nothing in
    the repository can depend on a file git does not have.'''
    return [f for f in files if f not in tracked and SECRET_PATH.search(f)]


def copy_tree(files, src, dest):
    for f in files:
        os.makedirs(os.path.dirname(os.path.join(dest, f)), exist_ok=True)
        shutil.copyfile(os.path.join(src, f), os.path.join(dest, f))


class Snapshot:
    '''The repository's own files at the start of the run, in bytes.'''

    def __init__(self, path):
        self.path = path
        files = repo_files()
        tracked = tracked_files()
        self.left_out = secret_untracked(files, tracked)
        self.files = [f for f in files if f not in self.left_out]
        self.tracked = tracked & set(self.files)
        copy_tree(self.files, ROOT, path)
        content = {}
        for f in self.files:
            with open(os.path.join(path, f), 'rb') as fh:
                content[f] = fh.read()
        self.index(content)

    def index(self, content):
        self.content = content
        self.dirs = {}
        for f in content:
            parts = f.split('/')
            for i in range(len(parts)):
                parent = '/'.join(parts[:i])
                kind = 'f' if i == len(parts) - 1 else 'd'
                self.dirs.setdefault(parent, set()).add((parts[i], kind))

    def copy(self, dest):
        copy_tree(self.files, self.path, dest)

    def kind(self, rel):
        if rel in self.content:
            return 'file'
        if rel in self.dirs:
            return 'dir'
        return 'absent'

    def listing(self, rel):
        return sha(json.dumps(sorted(self.dirs.get(rel, ()))).encode())


# --------------------------------------------------------------- running tests

def kill_group(pgid):
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def reap(pgid):
    '''Kill what the run left behind and wait until it is gone: a callback
    server outliving its test holds port 6969 against the next one.'''
    kill_group(pgid)
    deadline = time.monotonic() + REAP_LIMIT
    while True:
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return
        except PermissionError:
            return
        if time.monotonic() > deadline:
            raise RuntimeError('process group %d outlived its run by %d s'
                               % (pgid, REAP_LIMIT))
        time.sleep(0.05)


class Result:
    def __init__(self, code, output, seconds, peak, killed=None):
        self.code = code            # None: no exit status of its own
        self.output = output
        self.seconds = seconds
        self.peak = peak            # bytes, the run's largest resident set
        self.killed = killed        # why it has no exit status


def run_tests(workdir, copy, nodes, audit_script, record=None,
              limit=RUN_LIMIT):
    env = {
        'PATH': os.environ.get('PATH', '/usr/bin:/bin'),
        'HOME': os.path.join(workdir, 'home'),
        'TMPDIR': os.path.join(workdir, 'tmp'),
        'LANG': 'C.UTF-8',
        'PYTHONDONTWRITEBYTECODE': '1',
    }
    os.makedirs(env['HOME'], exist_ok=True)
    os.makedirs(env['TMPDIR'], exist_ok=True)
    if record:
        env['REDPROOF_AUDIT'] = record
    argv = [sys.executable, '-I', '-B', audit_script, '-q',
            '-p', 'no:cacheprovider'] + list(nodes)
    log = os.path.join(workdir, 'output.log')
    started = time.monotonic()
    timed_out = threading.Event()
    with open(log, 'wb') as out:
        proc = subprocess.Popen(argv, cwd=copy, env=env, stdin=subprocess.DEVNULL,
                                stdout=out, stderr=subprocess.STDOUT,
                                start_new_session=True)
    with _live_lock:
        _live.add(proc.pid)

    def expire():
        timed_out.set()
        kill_group(proc.pid)

    timer = threading.Timer(limit, expire)
    timer.start()
    try:
        _, status, usage = os.wait4(proc.pid, 0)
    finally:
        timer.cancel()
    proc.returncode = os.waitstatus_to_exitcode(status)
    seconds = time.monotonic() - started
    try:
        reap(proc.pid)
    finally:
        with _live_lock:
            _live.discard(proc.pid)
    with open(log, 'rb') as f:
        output = f.read().decode('utf-8', 'replace')
    peak = usage.ru_maxrss * 1024
    if timed_out.is_set():
        return Result(None, output, seconds, peak,
                      'ran past its limit of %d s' % limit)
    if proc.returncode < 0:
        sig = -proc.returncode
        why = 'killed by %s' % signal.Signals(sig).name
        if sig == signal.SIGKILL and not _stopping.is_set():
            why += ', which is what the kernel sends when memory runs out'
        return Result(None, output, seconds, peak, why)
    return Result(proc.returncode, output, seconds, peak)


# ------------------------------------------------------------- kept verdicts

def lock_pins(snapshot):
    '''The pins that apply to this interpreter. The lock covers every Python
    the project supports, with a marker where a version differs; a line whose
    marker does not hold here belongs to another interpreter's set.'''
    from packaging.markers import Marker
    pins = {}
    for line in snapshot.content.get(LOCK, b'').decode('utf-8').splitlines():
        requirement, _, marker = line.split('#')[0].strip().partition(';')
        if '==' not in requirement:
            continue
        if marker.strip() and not Marker(marker.strip()).evaluate():
            continue
        name, version = requirement.split('==', 1)
        pins[normalize(name)] = version.strip()
    return pins


def normalize(name):
    return re.sub(r'[-_.]+', '-', name).lower()


def pinned_python(snapshot):
    '''The exact release .python-versions pins for this interpreter's minor
    version, or None.'''
    minor = '%d.%d.' % sys.version_info[:2]
    for line in snapshot.content.get(PYTHONS, b'').decode('utf-8').split():
        if line.startswith(minor):
            return line
    return None


def environment_matches_lock(snapshot):
    '''None when the interpreter is the pinned build and the installed
    distributions are exactly the lock, otherwise why not. A verdict is keyed
    on the interpreter's minor version and on the lock, so one measured
    against anything else must not be kept, nor stand in for a run. The key
    stays loose and the environment is held to the pin, so every rig on the
    pin shares verdicts.'''
    pinned = pinned_python(snapshot)
    running = platform.python_version()
    if sys.implementation.name != 'cpython' or pinned != running:
        return 'Python %s %s is not the pinned %s' % (
            sys.implementation.name, running, pinned or 'build (none for '
            '%d.%d in %s)' % (sys.version_info[0], sys.version_info[1],
                               PYTHONS))
    if sys.version_info < (3, 11):
        # 3.11's pathlib calls os.stat at each call, which is what lets the
        # audit see its checks; earlier versions were not checked.
        return ('Python %d.%d: not checked that pathlib looks os.stat up at '
                'each call' % sys.version_info[:2])
    try:
        pins = lock_pins(snapshot)
    except ImportError:
        return 'packaging is not installed, so the lock cannot be read'
    if not pins:
        return 'no %s' % LOCK
    from importlib import metadata
    installed = {}
    for dist in metadata.distributions():
        name = dist.metadata['Name']
        if name:
            installed[normalize(name)] = dist.version
    # Neither is a test dependency: pip installs, and schwaby is under test.
    for own in ('pip', 'schwaby'):
        installed.pop(own, None)
    differ = sorted(n for n in set(pins) | set(installed)
                    if pins.get(n) != installed.get(n))
    if differ:
        shown = ', '.join('%s %s (lock: %s)' % (n, installed.get(n, 'absent'),
                                                pins.get(n, 'absent'))
                          for n in differ[:3])
        return '%d installed distributions differ from %s: %s%s' % (
            len(differ), LOCK, shown, ', ...' if len(differ) > 3 else '')
    return None


def entry_key(case, snapshot):
    harness = [sha(snapshot.content[f]) for f in (
        'tools/redproof.py', AUDIT)]
    material = {
        'version': STORE_VERSION,
        'entry': case_fields(case),
        'harness': harness,
        # The minor version, and the build .python-versions pins for it: a
        # pin bump is a new interpreter on every rig, so nothing measured on
        # the old one stands.
        'python': [sys.implementation.name, *sys.version_info[:2],
                   pinned_python(snapshot)],
        'lock': sha(snapshot.content.get(LOCK, b'')),
    }
    return sha(json.dumps(material, sort_keys=True).encode())


class Classifier:
    '''Sorts the paths a run touched into what keys it and what does not.'''

    def __init__(self, copy, own_roots):
        self.copy = os.path.abspath(copy)
        self.copy_real = os.path.realpath(copy)
        self.own = [os.path.realpath(r) for r in own_roots]
        interpreter = set()
        for p in (sys.prefix, sys.base_prefix, sys.exec_prefix,
                  sys.base_exec_prefix, os.path.dirname(sys.executable)):
            interpreter.add(os.path.abspath(p))
            interpreter.add(os.path.realpath(p))
        import sysconfig
        for name in ('stdlib', 'platstdlib', 'purelib', 'platlib'):
            p = sysconfig.get_path(name)
            if p:
                interpreter.add(os.path.abspath(p))
                interpreter.add(os.path.realpath(p))
        # Debian's interpreter reads its sitecustomize from here.
        interpreter.add('/etc/python%d.%d' % sys.version_info[:2])
        self.interpreter = sorted(interpreter)

    def above(self, path):
        '''Whether path is a directory above the copy, the run's own files or
        the interpreter. Resolving any path probes every directory above it,
        and where those live differs from rig to rig while saying nothing
        about the code: keying them would key the machine.'''
        prefix = path.rstrip('/') + '/'
        return any(root.startswith(prefix) for root in (
            [self.copy, self.copy_real] + self.own + self.interpreter))

    @staticmethod
    def under(path, root):
        return path == root or path.startswith(root.rstrip('/') + '/')

    def classify(self, path):
        '''('copy', relative) | ('own',) | ('interpreter',) | ('device',) |
        ('system',) | ('secret',) | ('outside', path)'''
        real = os.path.realpath(path)
        for copy in (self.copy, self.copy_real):
            if self.under(path, copy):
                rel = os.path.relpath(path, copy).replace(os.sep, '/')
                return ('copy', '' if rel == '.' else rel)
        for root in self.own:
            if self.under(path, root) or self.under(real, root):
                return ('own',)
        for root in self.interpreter:
            if self.under(path, root) or self.under(real, root):
                return ('interpreter',)
        if path in DEVICES or path.startswith('/dev/fd/') or path.startswith(
                '/dev/pts/'):
            return ('device',)
        if self.under(path, '/proc') or self.under(path, '/sys'):
            return ('system',)
        if SECRET_PATH.search(path):
            return ('secret',)
        return ('outside', path)


def outside_now(path, what):
    try:
        if what == 'probe':
            if os.path.isdir(path):
                return 'dir'
            return 'file' if os.path.exists(path) else 'absent'
        if os.path.isdir(path):
            return 'dir:' + sha(json.dumps(sorted(os.listdir(path))).encode())
        with open(path, 'rb') as f:
            return 'file:' + sha(f.read())
    except OSError:
        return 'absent'


def copy_value(snapshot, mutated, rel, what):
    if what == 'probe':
        return snapshot.kind(rel)
    kind = snapshot.kind(rel)
    if kind == 'file':
        data = mutated[1] if mutated and mutated[0] == rel else (
            snapshot.content[rel])
        return 'file:' + sha(data)
    if kind == 'dir':
        return 'dir:' + snapshot.listing(rel)
    return 'absent'


def reads_from(record, classifier, snapshot, mutated, tracked):
    '''The reads that key this run, or (None, why not kept, tracked writes).'''
    tracked_writes = []
    why = None
    if record['spawned']:
        why = 'started a process (%s)' % record['spawned'][0]
    elif record['connected']:
        why = 'connected beyond loopback'
    elif record['unresolved']:
        why = 'touched a path the audit could not work out'
    reads = {}
    for path, writing in record['opened'].items():
        c = classifier.classify(path)
        if writing:
            if c[0] == 'copy' and c[1] in tracked:
                tracked_writes.append(c[1])
            if c[0] not in ('own', 'device'):
                why = why or 'wrote %s' % path
            continue
        reads[path] = 'read'
    for path in record['listed']:
        reads.setdefault(path, 'read')
    for path in record['probed']:
        reads.setdefault(path, 'probe')
    keyed = {}
    for path, what in sorted(reads.items()):
        if what == 'probe' and classifier.above(path):
            continue
        c = classifier.classify(path)
        if c[0] in ('own', 'interpreter', 'device'):
            continue
        if c[0] == 'system':
            why = why or 'read %s' % path
            continue
        if c[0] == 'secret':
            # Never digested, whatever else the run did.
            why = why or 'read a file that may hold a secret'
            continue
        if c[0] == 'copy':
            keyed['copy:' + c[1]] = (what, copy_value(
                snapshot, mutated, c[1], what))
        else:
            keyed[path] = (what, outside_now(path, what))
    return (None if why else keyed), why, tracked_writes


def holds(reads, snapshot, mutated):
    '''Whether every read a record keys is still what the runs found: the
    mutated run's with the mutation applied, the unmutated baseline's
    without it. A record keyed on the mutated run alone stayed KEPT when
    something only the passing branch reads had changed.'''
    if not isinstance(reads, dict) or not {'mutated', 'baseline'} <= set(reads):
        return False
    return (holds_one(reads['mutated'], snapshot, mutated)
            and holds_one(reads['baseline'], snapshot, None))


def holds_one(reads, snapshot, mutated):
    for path, (what, value) in reads.items():
        if path.startswith('copy:'):
            now = copy_value(snapshot, mutated, path[len('copy:'):], what)
        else:
            if SECRET_PATH.search(path):
                return False
            now = outside_now(path, what)
        if now != value:
            return False
    return True


class Store:
    def __init__(self, path):
        self.path = path
        self.records = {}
        self.changed = {}
        self.removed = set()

    def load(self):
        try:
            with open(self.path, 'rb') as f:
                data = json.loads(f.read())
            if data.get('version') == STORE_VERSION:
                self.records = dict(data.get('records', {}))
        except (OSError, ValueError, AttributeError):
            self.records = {}

    def under(self, key):
        prefix = key + ':'
        return {k: r for k, r in self.records.items()
                if k.startswith(prefix) and not r.get('gone')}

    def put(self, record_id, record):
        self.records[record_id] = record
        self.changed[record_id] = record

    @staticmethod
    def when(record, field='at'):
        try:
            return datetime.fromisoformat(record[field])
        except (KeyError, TypeError, ValueError):
            return datetime.min.replace(tzinfo=timezone.utc)

    @classmethod
    def merged(cls, current, new):
        '''Which of two versions of one record a save keeps. A run that found
        the entry not PROVEN outranks one that only found the record still
        holding, whichever saved last; only a proof made after the tombstone
        brings a record back. Otherwise the most recently used wins.'''
        if current is None:
            return new
        if current.get('gone') and not new.get('gone'):
            return new if cls.when(new, 'proved') > cls.when(current) else (
                current)
        if new.get('gone') and not current.get('gone'):
            return current if cls.when(current, 'proved') > cls.when(new) else (
                new)
        return new if cls.when(new) >= cls.when(current) else current

    def prune(self):
        cutoff = datetime.now(timezone.utc) - timedelta(days=KEEP_DAYS)
        self.cutoff = cutoff
        for k, r in list(self.records.items()):
            try:
                at = datetime.fromisoformat(r['at'])
            except (KeyError, TypeError, ValueError):
                continue
            if at < cutoff:
                del self.records[k]
                self.changed.pop(k, None)
                self.removed.add(k)

    def save(self):
        if not self.changed and not self.removed:
            return
        # Imported here: POSIX only, like the process groups the runs use, and
        # importing it at the top stopped the suite collecting this module's
        # tests on Windows.
        import fcntl
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path + '.lock', 'w') as lock:
            deadline = time.monotonic() + LOCK_LIMIT
            while True:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() > deadline:
                        raise RuntimeError('%s.lock was held for %d s, so no '
                                           'verdict was saved'
                                           % (self.path, LOCK_LIMIT))
                    time.sleep(0.1)
            # Merge onto what is there now, record by record: another run may
            # have saved since this one loaded.
            current = Store(self.path)
            current.load()
            records = current.records
            for k, new in self.changed.items():
                records[k] = self.merged(records.get(k), new)
            cutoff = getattr(self, 'cutoff', None)
            for k in self.removed:
                # Unless another run has used it since this one loaded.
                if k in records and cutoff is not None and (
                        self.when(records[k]) >= cutoff):
                    continue
                records.pop(k, None)
            tmp = self.path + '.tmp'
            with open(tmp, 'wb') as f:
                f.write(json.dumps({'version': STORE_VERSION,
                                    'records': records},
                                   indent=1, sort_keys=True).encode('utf-8'))
            os.replace(tmp, self.path)


# ------------------------------------------------------------------ judging

class Run:
    def __init__(self, args):
        self.args = args
        self.tmp = tempfile.mkdtemp(prefix='schwaby-redproof-')
        try:
            self.snapshot = Snapshot(os.path.join(self.tmp, 'snapshot'))
        except BaseException:
            shutil.rmtree(self.tmp, ignore_errors=True)
            raise
        self.audit = os.path.join(self.snapshot.path, AUDIT)
        self.locks_dir = os.path.join(self.tmp, 'locks')
        os.makedirs(self.locks_dir)
        self.group_locks = {}
        self.groups = {}
        self.counter = 0
        self.counter_lock = threading.Lock()
        self.largest = 0
        self.lowest_available = available_memory()
        self.tracked_writes = {}

    def workdir(self):
        with self.counter_lock:
            self.counter += 1
            path = os.path.join(self.tmp, 'run%d' % self.counter)
        os.makedirs(path)
        return path

    def note_memory(self, result):
        self.largest = max(self.largest, result.peak)
        avail = available_memory()
        if avail is not None and (self.lowest_available is None
                                  or avail < self.lowest_available):
            self.lowest_available = avail

    def locks_for(self, nodes):
        names = set()
        for node in nodes:
            for item, group in self.groups.items():
                if item == node or item.startswith(node + '::'):
                    names.add(group)
        return [self.group_locks.setdefault(n, threading.Lock())
                for n in sorted(names)]

    def run(self, nodes, mutation=None, record=False):
        '''Runs nodes in a fresh copy, with the mutation (path, bytes) if any.
        Returns (Result, record dict or None, Classifier, tracked writes).'''
        work = self.workdir()
        copy = os.path.join(work, 'copy')
        self.snapshot.copy(copy)
        if mutation:
            with open(os.path.join(copy, mutation[0]), 'wb') as f:
                f.write(mutation[1])
        record_path = os.path.join(work, 'record.json') if record else None
        locks = self.locks_for(nodes)
        for lock in locks:
            lock.acquire()
        try:
            if _stopping.is_set():
                raise SystemExit(1)
            result = run_tests(work, copy, nodes, self.audit, record_path)
        finally:
            for lock in reversed(locks):
                lock.release()
        self.note_memory(result)
        changed = self.changed_tracked(copy, mutation)
        audit = None
        if record_path and os.path.exists(record_path):
            with open(record_path, 'rb') as f:
                audit = json.loads(f.read())
        classifier = Classifier(copy, [work, self.tmp])
        shutil.rmtree(work, ignore_errors=True)
        return result, audit, classifier, changed

    def changed_tracked(self, copy, mutation):
        '''Tracked files whose bytes differ after the run. A write that was
        restored before the run ended is caught by the audit instead (the
        tracked writes reads_from returns), when the run was recorded.'''
        changed = []
        for f in sorted(self.snapshot.tracked):
            expected = mutation[1] if mutation and mutation[0] == f else (
                self.snapshot.content[f])
            try:
                with open(os.path.join(copy, f), 'rb') as fh:
                    if fh.read() == expected:
                        continue
            except OSError:
                pass
            changed.append(f)
        return changed


def run_baseline(run, nodes):
    '''Runs nodes unmutated, under the audit. (Result, keyed reads or None
    when the run cannot be kept); a tracked file it wrote, restored or not,
    is noted against it.'''
    result, audit, classifier, changed = run.run(list(nodes), record=True)
    reads = None
    if audit is not None:
        keyed, why, writes = reads_from(audit, classifier, run.snapshot, None,
                                        run.snapshot.tracked)
        reads = keyed if why is None else None
        changed = sorted(set(changed) | set(writes))
    if changed:
        run.tracked_writes['the baseline of ' + ' '.join(nodes)] = changed
    return result, reads


def judge(run, case, baseline, baseline_reads=None):
    '''(verdict, detail, record reads or None). baseline_reads are the
    unmutated run's keyed reads, or None when that run cannot be kept.'''
    original = run.snapshot.content.get(case.path)
    if original is None:
        return 'SPEC', ('%s does not exist. If the code was deleted on '
                        'purpose, mark the entry removed= with the reason.'
                        % case.path), None
    mutated, why = mutate(case, original)
    if mutated is None:
        return 'SPEC', why, None
    if baseline.code != 0:
        return 'SPEC', 'test does not pass before the mutation%s' % (
            ' (%s)' % baseline.killed if baseline.killed else ''), None
    result, audit, classifier, changed = run.run(
        tests_of(case), (case.path, mutated), record=True)
    if audit is not None:
        changed = sorted(set(changed) | set(reads_from(
            audit, classifier, run.snapshot, (case.path, mutated),
            run.snapshot.tracked)[2]))
    if changed:
        run.tracked_writes[case.name] = changed
    if result.code is None:
        return 'RED?', result.killed, None
    if result.code == 0:
        if case.superseded:
            return 'SUPERSEDED', case.superseded, None
        return 'GREEN', ('the mutation changed nothing the test could see. '
                         'Either the test is asleep, or what was mutated is '
                         'no longer load-bearing; if the second, mark the '
                         'entry superseded= rather than deleting it.'), None
    said = failure_text(result.output)
    if case.expect not in said:
        first = [line for line in said.splitlines() if line.strip()]
        return 'RED?', ('failed, but not on %r: got %s' % (
            case.expect, first[0].strip() if first else '?')), None
    kept = None
    if audit is not None:
        reads, why, _ = reads_from(audit, classifier, run.snapshot,
                                   (case.path, mutated), run.snapshot.tracked)
        if why is None and baseline_reads is not None:
            kept = {'mutated': reads, 'baseline': baseline_reads}
    return 'PROVEN', '', kept


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--name', action='append', default=[],
                        help='only entries whose name contains this '
                        '(repeatable)')
    parser.add_argument('--all', action='store_true',
                        help='run every selected entry, kept or not')
    parser.add_argument('--jobs', type=int,
                        help='entries at once (default: every usable core '
                        'with REDPROOF_JOBS_ALL=1, two otherwise)')
    parser.add_argument('--shard', type=shard_arg, metavar='K/N',
                        help='judge only slice K of N (K from 1)')
    args = parser.parse_args(argv)

    started = time.monotonic()
    run = Run(args)
    try:
        if run.snapshot.left_out:
            print('redproof: left out of every copy, as names that may hold '
                  'a secret: %s' % ', '.join(run.snapshot.left_out),
                  flush=True)
        return judge_all(run, args, started)
    finally:
        shutil.rmtree(run.tmp, ignore_errors=True)


def shard_arg(text):
    try:
        k, n = (int(x) for x in text.split('/'))
    except ValueError:
        raise argparse.ArgumentTypeError('%r is not K/N' % text)
    if not 1 <= k <= n:
        raise argparse.ArgumentTypeError('%r: K must be 1 to N' % text)
    return k, n


def in_shard(name, shard):
    '''Whether the entry named `name` is in slice (K, N). By the name alone,
    so the answer does not depend on which entries are stale or selected.'''
    k, n = shard
    return int(sha(name.encode('utf-8')), 16) % n == k - 1


def select(cases, names, shard):
    '''The entries this run judges, or None when --name matches nothing.'''
    selected = [c for c in cases
                if not names or any(n in c.name for n in names)]
    if not selected:
        return None
    if shard:
        selected = [c for c in selected if in_shard(c.name, shard)]
    return selected


def judge_all(run, args, started):
    snapshot = run.snapshot
    cases = load_cases(snapshot.path)
    selected = select(cases, args.name, args.shard)
    if selected is None:
        print('redproof: no entry name contains %s' % ' or '.join(
            map(repr, args.name)))
        return 2

    unkept = environment_matches_lock(snapshot)
    store = Store(STORE)
    if unkept is None:
        store.load()
    keys = {c.name: entry_key(c, snapshot) for c in selected}

    removed = [c for c in selected if c.removed]
    live = [c for c in selected if not c.removed]
    held = {}
    if unkept is None:
        for c in live:
            records = store.under(keys[c.name])
            if not records:
                continue
            original = snapshot.content.get(c.path)
            mutated = mutate(c, original)[0] if original is not None else None
            holding = [k for k, r in records.items()
                       if mutated is not None and holds(
                           r['reads'], snapshot, (c.path, mutated))]
            if holding:
                held[c.name] = holding
    stale = [c for c in live if args.all or c.name not in held]
    kept = [c for c in live if c not in stale]

    print('redproof: %d selected, %d kept, %d removed, %d to run%s' % (
        len(selected), len(kept), len(removed), len(stale),
        ' (shard %d/%d)' % args.shard if args.shard else ''), flush=True)
    if unkept:
        print('redproof: verdicts are not kept or reused in this run: %s'
              % unkept, flush=True)

    when = now_iso()
    for c in kept:
        for k in held[c.name]:
            r = dict(store.records[k], at=when)
            store.put(k, r)

    verdicts = {}
    for c in removed:
        verdicts[c.name] = ('REMOVED', c.removed, 0.0)
    for c in kept:
        verdicts[c.name] = ('KEPT', '', 0.0)

    if stale:
        verdicts.update(run_stale(run, args, stale, store, keys, held, unkept))

    if unkept is None:
        store.prune()
        store.save()

    for c in removed:
        print('REMOVED     %s  (0 s)' % c.name)
    accounted = ('PROVEN', 'KEPT', 'SUPERSEDED', 'REMOVED')
    bad = [c.name for c in selected if verdicts[c.name][0] not in accounted]
    for name, files in sorted(run.tracked_writes.items()):
        print('redproof: %s wrote to tracked files: %s' % (
            name, ', '.join(files)))
    print('redproof: %d accounted for, %d not%s, %d s' % (
        len(selected) - len(bad), len(bad),
        (': ' + '; '.join('%s (%s)' % (n, verdicts[n][0]) for n in bad))
        if bad else '', time.monotonic() - started), flush=True)
    return 1 if bad or run.tracked_writes else 0


def pool_size(args, per_task):
    whole = os.environ.get('REDPROOF_JOBS_ALL') == '1'
    cpus = args.jobs or (usable_cpus() if whole else 2)
    cpu_reason = ('--jobs %d' % args.jobs if args.jobs else
                  '%d usable cores, REDPROOF_JOBS_ALL=1' % cpus if whole else
                  'two at a time without REDPROOF_JOBS_ALL=1 (%d usable '
                  'cores)' % usable_cpus())
    avail = available_memory()
    if avail is None or not per_task:
        return cpus, '%s; memory not measured' % cpu_reason
    by_memory = max(1, int((avail - FLOOR) // per_task))
    size = max(1, min(cpus, by_memory))
    return size, '%s; memory holds %d (%s available - %s floor) / %s a run' % (
        cpu_reason, by_memory, gib(avail), gib(FLOOR), gib(per_task))


def run_stale(run, args, stale, store, keys, held, unkept):
    snapshot = run.snapshot

    # The copy has to be what the tests import, or nothing below means
    # anything.
    work = run.workdir()
    copy = os.path.join(work, 'copy')
    snapshot.copy(copy)
    where = subprocess.run(
        [sys.executable, '-I', '-B', run.audit, '--where'], cwd=copy,
        env={'PATH': os.environ.get('PATH', ''), 'HOME': work,
             'LANG': 'C.UTF-8'},
        capture_output=True, text=True, timeout=PREPARE_LIMIT)
    imported = where.stdout.strip()
    if where.returncode != 0 or not Classifier.under(
            imported, os.path.realpath(copy)):
        print('redproof: a copy imports schwaby from %r, not from the copy: '
              'every verdict would be a false GREEN, so none is judged\n%s'
              % (imported, where.stderr))
        raise SystemExit(1)

    collect = os.path.join(work, 'groups.json')
    out = subprocess.run(
        [sys.executable, '-I', '-B', run.audit, '--collect', collect,
         '-p', 'no:cacheprovider', 'tests/'], cwd=copy,
        env={'PATH': os.environ.get('PATH', ''), 'HOME': work,
             'LANG': 'C.UTF-8', 'PYTHONDONTWRITEBYTECODE': '1'},
        capture_output=True, text=True, timeout=PREPARE_LIMIT)
    if out.returncode != 0 or not os.path.exists(collect):
        print('redproof: collecting the suite failed, so the port groups '
              'are unknown and nothing is judged\n%s%s'
              % (out.stdout[-2000:], out.stderr[-2000:]))
        raise SystemExit(1)
    with open(collect, 'rb') as f:
        run.groups = json.loads(f.read())
    shutil.rmtree(work, ignore_errors=True)

    # Baselines: every distinct test, unmutated. The first alone, to measure
    # what a run costs before deciding how many fit.
    distinct = []
    for c in stale:
        t = tuple(tests_of(c))
        if t not in distinct:
            distinct.append(t)
    total = len(distinct) + len(stale)
    done = [0]
    done_lock = threading.Lock()

    def progress(verdict, label, detail, seconds):
        with done_lock:
            done[0] += 1
            n = done[0]
        print('[%d/%d] %-10s %s  (%.1f s)%s' % (
            n, total, verdict, label, seconds,
            ('\n           ' + detail) if detail else ''), flush=True)

    baselines = {}
    baseline_reads = {}

    def baseline(t):
        result, baseline_reads[t] = run_baseline(run, t)
        baselines[t] = result
        if result.code == 0:
            progress('baseline', ' '.join(t), '', result.seconds)
        else:
            progress('BASELINE', ' '.join(t), result.killed or (
                'fails unmutated: %s' % last_lines(result.output)),
                result.seconds)

    baseline(distinct[0])
    size, reason = pool_size(args, run.largest)
    print('redproof: %d at once: %s' % (size, reason), flush=True)

    parallel(baseline, distinct[1:], size)
    size, reason = pool_size(args, run.largest)
    print('redproof: %d at once for the entries: %s' % (size, reason),
          flush=True)

    verdicts = {}

    def one(c):
        t0 = time.monotonic()
        t = tuple(tests_of(c))
        verdict, detail, reads = judge(run, c, baselines[t],
                                       baseline_reads.get(t))
        seconds = time.monotonic() - t0
        verdicts[c.name] = (verdict, detail, seconds)
        progress(verdict, c.name, detail, seconds)
        if unkept is not None:
            return
        # When this verdict was reached, not when the phase began: a proof
        # and a disproof from overlapping runs are ordered by these, and a
        # stamp taken early made a later disproof look older than a proof.
        when = now_iso()
        key = keys[c.name]
        if verdict == 'PROVEN':
            # Proved again: what held is renewed.
            for k in held.get(c.name, ()):
                store.put(k, dict(store.records[k], at=when, proved=when))
            if reads is not None:
                digest = sha(json.dumps(reads, sort_keys=True).encode())[:16]
                store.put('%s:%s' % (key, digest), {
                    'at': when, 'proved': when, 'name': c.name,
                    'reads': reads,
                    # What judging the entry cost (its copy and its mutated
                    # run), for dividing a run between machines. Metadata,
                    # like the platform below: never part of the key.
                    'seconds': round(seconds, 1),
                    # Metadata only: never part of the key.
                    'platform': {'machine': platform.machine(),
                                 'system': platform.system(),
                                 'python': platform.python_version()},
                })
        else:
            # This run disproves every record that held.
            for k in held.get(c.name, ()):
                store.put(k, {'gone': True, 'at': when, 'name': c.name})

    parallel(one, stale, size)

    avail = run.lowest_available
    print('redproof: memory: largest run %s, lowest available %s' % (
        gib(run.largest), gib(avail) if avail is not None else 'unknown'),
        flush=True)
    return verdicts


def parallel(fn, items, size):
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=size)
    try:
        return list(pool.map(fn, items))
    finally:
        # On a signal, what has not started never starts.
        pool.shutdown(wait=True, cancel_futures=True)


def last_lines(output, n=3):
    lines = [line for line in output.splitlines() if line.strip()]
    return ' | '.join(lines[-n:])


def stop(signum, frame):
    _stopping.set()
    with _live_lock:
        for pid in list(_live):
            kill_group(pid)
    raise SystemExit(128 + signum)


if __name__ == '__main__':
    for s in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(s, stop)
    sys.exit(main(sys.argv[1:]))
