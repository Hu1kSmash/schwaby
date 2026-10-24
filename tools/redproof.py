'''Mechanical red-proofing: break the code on purpose, and require the failure
to arrive on the assertion you named.

    .venv/bin/python -I -B tools/redproof.py                 # every entry
    .venv/bin/python -I -B tools/redproof.py --name auth/    # names containing it
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
    GREEN       the test passed with the code broken. Two readings, needing
                different fixes: the test is asleep, or what was mutated is no
                longer load-bearing because something later covers it. If the
                second, mark the entry `superseded=` with the reason rather
                than deleting it.
    SUPERSEDED  GREEN, on an entry marked superseded: a real answer.
    REMOVED     the code the entry mutates was deleted, and the entry says so.
    RED?        it failed, but not on `expect`, or ran out of time, or was
                killed: proves nothing.
    ERROR       the harness itself failed while judging it: a defect in the
                harness, named against the entry, and the run goes on.
    SPEC        the entry cannot be judged: its text is missing or ambiguous,
                the mutation is a no-op, or the test fails unmutated, which
                includes a baseline whose worker died before it finished.

POSIX only: each run is a process group of its own, waited for with
os.wait4 and killed with os.killpg.

Exit status 0 only when every selected entry is PROVEN, SUPERSEDED or
REMOVED, and no run wrote to a tracked file or went unchecked for one; 1
otherwise, and when the copy does not import its own schwaby or the suite
cannot be collected; 2 when --name matches nothing, or the arguments are
wrong.

Every run judges every selected entry. Verdicts are not kept from one run to
the next: the whole set takes about a minute and a half on 20 cores, and the
machinery that kept them, keyed on everything a run read, produced every high
finding in two review rounds, each a way a verdict stayed kept after
something it depended on had changed.

SHARDS. `--shard K/N` judges one slice of N, so N machines can share one
run: the selected entries (after --name) are divided by a hash of each name,
and the entries in slice K run. Every entry is in exactly one slice, so the
slices of one run never overlap and never miss one, and an entry stays in its
slice from run to run. An empty slice is not an error: it has nothing to run
and exits 0.

THE WORKING TREE IS NEVER TOUCHED. The run snapshots the repository's own files
(tracked, plus untracked and not ignored) into a temporary directory, reads the
entries from the snapshot, and gives every test run a fresh copy of it.
Mutations and comparisons are in bytes. The tests run under
tools/redproof_audit.py, which puts the copy first on sys.path; before judging
anything the run proves that `import schwaby` in a copy resolves inside it,
because an import from anywhere else makes every verdict a false GREEN.

PARALLEL. Every usable core with REDPROOF_JOBS_ALL=1, which a job that holds
a machine to itself sets; two at a time otherwise, since a run on a machine
someone is using should stay modest. No more than memory holds, beyond the
one run that always goes ahead: the pool is min(cores, (available memory -
FLOOR) / the largest run measured so far), at least one, printed with its
reason. The first baseline runs alone to give that
measurement. `--jobs` overrides the core count. Entries run side by side
whatever their tests are: each login-flow test takes a free callback port of
its own, so two no longer take each other's server for their own.

TRACKED WRITES. A test that writes to a tracked file fails the run, by
name: the copy is compared with the snapshot after each run, and the audit
hook records the run's writes, by path and by the file each touched, so a
file written and put back before the run ended is caught too, under its own
name, through a link to it, or through a descriptor. A run whose writes the
hook could not all place, or that left no record of them, fails the run the
same way. What the hook
cannot see is a write by a process the test started, unless it is left in
place.
'''

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CASES = 'tools/redproof_cases.py'
AUDIT = 'tools/redproof_audit.py'

#: The lowest a run may leave available, in bytes
FLOOR = 2 << 30
#: The bound on one test run, in seconds, before it is RED? by name
RUN_LIMIT = 600
#: The bound on a run's leftover processes going, once it has exited
REAP_LIMIT = 30
#: The bounds on the steps before any entry runs
GIT_LIMIT = 60
PREPARE_LIMIT = 300

#: Files that may hold a secret, which an untracked file named like one is
#: left out of every copy for
SECRET_PATH = re.compile(
    r'(^|/)(\.env[^/]*|\.netrc|\.pypirc|\.git-credentials|[^/]*token[^/]*'
    r'|[^/]*secret[^/]*|[^/]*credential[^/]*|[^/]*password[^/]*'
    r'|[^/]*\.(pem|key|p12|pfx)|id_[a-z0-9]+)$|(^|/)\.(config|ssh|gnupg)/',
    re.IGNORECASE)

# The process groups this process started and has not reaped. Changed only
# on the main thread, where stop() also runs, so it takes no lock: one held
# there when a signal landed deadlocked the handler against its own thread.
_live = set()
_stopping = threading.Event()
# Set in a worker: tells the parent each group it starts and reaps, so the
# parent can kill what a worker that died left running. That worker's time
# limit died with it, and a login-flow test left running keeps its port.
_report = None


def _track(pid):
    _live.add(pid)
    if _report:
        _report(('started', pid))


def _untrack(pid):
    _live.discard(pid)
    if _report:
        _report(('reaped', pid))


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


# A failure's header: underscores, its title (the test, and a failing
# subTest's label or params), underscores. pytest pads a long title with one.
_HEADER = re.compile(r'^_+ (?P<title>.+) _+$')
# A line that starts the next part of the report, ending a failure's section.
_RULE = re.compile(r'^={3,}')
# A failure's captured stdout, stderr or log: what the test printed, which
# can start a line with `E ` like anything else it chooses.
_CAPTURED = re.compile(r'^-{3,} Captured ')


def failure_sections(output):
    '''[(title, what it says)] for each failure pytest reports: the header
    naming the test (and the subtest, if one failed) and the `E` lines of its
    traceback, less the explanation assertion rewriting adds. Not the source
    lines pytest echoes, not node ids, not a test's captured output, not a
    rewritten assert's quoted expression: any of those can contain an
    entry's `expect` whatever actually failed, which made a failure on the
    wrong assertion read as PROVEN. `E` lines outside any section count,
    untitled.'''
    sections = [['', []]]
    current = sections[0]
    for line in output.splitlines():
        m = _HEADER.match(line)
        if m:
            current = [m.group('title'), []]
            sections.append(current)
        elif _RULE.match(line):
            current = sections[0]
        elif _CAPTURED.match(line):
            current = ['', []]      # read, and counted nowhere
        elif line.startswith('E '):
            current[1].append(line[2:])
    return [(title, '\n'.join(without_explanation(said)))
            for title, said in sections if said]


def without_explanation(lines):
    '''A failure's E lines without the explanation pytest's assertion
    rewriting adds: the `assert <expression>` line and the more-indented
    lines under it. That explanation quotes the failing expression from the
    source, so an expect written as a literal in some other assert matched
    whatever that assert's failure was. A plain `assert` with no message
    therefore says nothing an entry can match; its message, when it has one,
    is the line before.'''
    kept = []
    skipping_below = None
    for line in lines:
        text = line.lstrip()
        indent = len(line) - len(text)
        if skipping_below is not None and (not text or indent > skipping_below):
            continue
        skipping_below = None
        if text.startswith('assert ') or text.startswith(
                'AssertionError: assert '):
            skipping_below = indent
            continue
        kept.append(line)
    return kept


def failed_on(case, output):
    '''Whether a failure says the entry's expect, in the subtest it names.
    A subtest's label is a qualifier, never the match: it is text the test
    chose, so matching it proved an entry whose test had no assertion left.'''
    return any(case.expect in said
               and (not case.subtest or case.subtest in title)
               for title, said in failure_sections(output))


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
    not copied: nothing in the repository can depend on a file git does not
    have, and a copy is one more place for a secret to be read from.'''
    return [f for f in files if f not in tracked and SECRET_PATH.search(f)]


def copy_tree(files, src, dest):
    for f in files:
        os.makedirs(os.path.dirname(os.path.join(dest, f)), exist_ok=True)
        # With its mode: a copy lost the executable bit, and a test that
        # checks a hook script is executable failed in every one.
        shutil.copy(os.path.join(src, f), os.path.join(dest, f))


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
        self.content = content

    def copy(self, dest):
        copy_tree(self.files, self.path, dest)


# --------------------------------------------------------------- running tests

def kill_group(pgid):
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def reap(pgid):
    '''Kill what the run left behind and wait until it is gone: a callback
    server outliving its test holds its port, and anything it was doing goes
    on into the next run's time.'''
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


def run_tests(workdir, copy, nodes, audit_script, record,
              limit=RUN_LIMIT):
    env = {
        'PATH': os.environ.get('PATH', '/usr/bin:/bin'),
        'HOME': os.path.join(workdir, 'home'),
        'TMPDIR': os.path.join(workdir, 'tmp'),
        'LANG': 'C.UTF-8',
    }
    os.makedirs(env['HOME'], exist_ok=True)
    os.makedirs(env['TMPDIR'], exist_ok=True)
    env['REDPROOF_AUDIT'] = record
    # --tb=short: pytest's long form prints each frame's arguments through
    # repr, and a test object whose repr or type name raises (several here
    # exist to) then crashed pytest itself, with an INTERNALERROR and no
    # failure reported. The short form prints the same E lines.
    # -B, not PYTHONDONTWRITEBYTECODE: -I ignores every PYTHON* variable.
    argv = [sys.executable, '-I', '-B', audit_script, '-q', '--tb=short',
            '-p', 'no:cacheprovider'] + list(nodes)
    log = os.path.join(workdir, 'output.log')
    started = time.monotonic()
    timed_out = threading.Event()
    with open(log, 'wb') as out:
        proc = subprocess.Popen(argv, cwd=copy, env=env, stdin=subprocess.DEVNULL,
                                stdout=out, stderr=subprocess.STDOUT,
                                start_new_session=True)
    _track(proc.pid)

    def expire():
        timed_out.set()
        kill_group(proc.pid)

    timer = threading.Timer(limit, expire)
    timer.start()
    try:
        _, status, usage = os.wait4(proc.pid, 0)
    finally:
        timer.cancel()
        # Gone before anything forks: a process with threads is unsafe to
        # fork, and the worker pool is forked from this one.
        timer.join()
    proc.returncode = os.waitstatus_to_exitcode(status)
    seconds = time.monotonic() - started
    try:
        reap(proc.pid)
    finally:
        _untrack(proc.pid)
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


# ------------------------------------------------------------ tracked writes

def under(path, root):
    return path == root or path.startswith(root.rstrip('/') + '/')


def tracked_inodes(copy, tracked):
    '''{(device, inode): path} for the copy's tracked files, as the run is
    about to find them.'''
    inodes = {}
    for rel in tracked:
        try:
            st = os.stat(os.path.join(copy, rel))
        except OSError:
            continue
        inodes[(st.st_dev, st.st_ino)] = rel
    return inodes


def tracked_writes_in(record, copy, tracked, inodes=None):
    '''The tracked files the audit saw a run write, whether or not the run
    put them back: by their path in the copy, and by the file itself, so a
    write through a link to one or through a descriptor counts too.'''
    found = set()
    for root in {os.path.abspath(copy), os.path.realpath(copy)}:
        for path in record.get('written', ()):
            if under(path, root):
                rel = os.path.relpath(path, root).replace(os.sep, '/')
                if rel in tracked:
                    found.add(rel)
    for dev, ino in record.get('inodes', ()):
        rel = (inodes or {}).get((dev, ino))
        if rel is not None:
            found.add(rel)
    return sorted(found)


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
        self.largest = 0
        self.lowest_available = available_memory()
        self.tracked_writes = {}
        # Runs whose writes could not all be checked, and why.
        self.unchecked = {}
        self.timing = threading.local()

    def workdir(self):
        # mkdtemp, not a counter: each worker process has its own copy of a
        # counter, and two of them made the same directory.
        return tempfile.mkdtemp(prefix='run', dir=self.tmp)

    def note_memory(self, result):
        self.largest = max(self.largest, result.peak)
        avail = available_memory()
        if avail is not None and (self.lowest_available is None
                                  or avail < self.lowest_available):
            self.lowest_available = avail

    def run(self, nodes, mutation=None):
        '''Runs nodes in a fresh copy, with the mutation (path, bytes) if any.
        Returns (Result, the tracked files it wrote, why its writes could not
        all be checked or None).'''
        started = time.monotonic()
        work = self.workdir()
        copy = os.path.join(work, 'copy')
        self.snapshot.copy(copy)
        if mutation:
            with open(os.path.join(copy, mutation[0]), 'wb') as f:
                f.write(mutation[1])
        record_path = os.path.join(work, 'record.json')
        inodes = tracked_inodes(copy, self.snapshot.tracked)
        copied = time.monotonic()
        if _stopping.is_set():
            raise SystemExit(1)
        result = run_tests(work, copy, nodes, self.audit, record_path)
        self.note_memory(result)
        changed = set(self.changed_tracked(copy, mutation))
        unchecked = None
        if os.path.exists(record_path):
            with open(record_path, 'rb') as f:
                record = json.loads(f.read())
            changed.update(tracked_writes_in(
                record, copy, self.snapshot.tracked, inodes))
            if record.get('unresolved'):
                unchecked = 'the audit could not place %d of its writes (%s)' % (
                    len(record['unresolved']),
                    ', '.join(sorted(set(record['unresolved']))))
        else:
            # Killed, or a test that left with os._exit: what it wrote and
            # put back is unknown.
            unchecked = 'it left no record of its writes'
        shutil.rmtree(work, ignore_errors=True)
        # Where the time went, for the entry's line: the copy, and pytest
        # itself; the rest is the harness's.
        self.timing.copy = copied - started
        self.timing.pytest = result.seconds
        return result, sorted(changed), unchecked

    def changed_tracked(self, copy, mutation):
        '''Tracked files whose bytes differ after the run. A write that was
        restored before the run ended is caught by the audit instead
        (tracked_writes_in).'''
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
    '''Runs nodes unmutated; a tracked file it wrote, restored or not, is
    noted against it.'''
    result, changed, unchecked = run.run(list(nodes))
    name = 'the baseline of ' + ' '.join(nodes)
    if changed:
        run.tracked_writes[name] = changed
    if unchecked:
        run.unchecked[name] = unchecked
    return result


def judge(run, case, baseline):
    '''(verdict, detail).'''
    original = run.snapshot.content.get(case.path)
    if original is None:
        return 'SPEC', ('%s does not exist. If the code was deleted on '
                        'purpose, mark the entry removed= with the reason.'
                        % case.path)
    mutated, why = mutate(case, original)
    if mutated is None:
        return 'SPEC', why
    if baseline.code != 0:
        return 'SPEC', 'test does not pass before the mutation%s' % (
            ' (%s)' % baseline.killed if baseline.killed else '')
    result, changed, unchecked = run.run(tests_of(case), (case.path, mutated))
    if changed:
        run.tracked_writes[case.name] = changed
    if unchecked:
        run.unchecked[case.name] = unchecked
    if result.code is None:
        return 'RED?', result.killed
    if result.code == 0:
        if case.superseded:
            return 'SUPERSEDED', case.superseded
        return 'GREEN', ('the mutation changed nothing the test could see. '
                         'Either the test is asleep, or what was mutated is '
                         'no longer load-bearing; if the second, mark the '
                         'entry superseded= rather than deleting it.')
    if not failed_on(case, result.output):
        said = [line for _, text in failure_sections(result.output)
                for line in text.splitlines() if line.strip()]
        return 'RED?', ('failed, but not on %r%s: got %s' % (
            case.expect, ' in subtest %r' % case.subtest if case.subtest
            else '', said[0].strip() if said else '?'))
    return 'PROVEN', ''


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--name', action='append', default=[],
                        help='only entries whose name contains this '
                        '(repeatable)')
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
    so the answer does not depend on which entries are selected.'''
    k, n = shard
    return int(sha(name.encode('utf-8')), 16) % n == k - 1


def shard_line(shard, in_slice, total):
    '''The line a scheduler reads to see how a run was divided: this slice's
    selected entries and the whole selection's, before slicing.'''
    return 'redproof: shard %d/%d: %d of %d entries' % (
        shard[0], shard[1], in_slice, total)


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

    removed = [c for c in selected if c.removed]
    live = [c for c in selected if not c.removed]
    # 'to run' is the count a scheduler reads off this line.
    print('redproof: %d selected, %d removed, %d to run%s' % (
        len(selected), len(removed), len(live),
        ' (shard %d/%d)' % args.shard if args.shard else ''), flush=True)
    if args.shard:
        print(shard_line(args.shard, len(selected),
                         len(select(cases, args.name, None))), flush=True)

    verdicts = {}
    for c in removed:
        verdicts[c.name] = ('REMOVED', c.removed, 0.0)
    if live:
        verdicts.update(run_entries(run, args, live))

    for c in removed:
        print('REMOVED     %s  (0 s)' % c.name)
    return report(run, selected, verdicts, started)


def report(run, selected, verdicts, started):
    '''Prints the run's summary and returns its exit status.'''
    accounted = ('PROVEN', 'SUPERSEDED', 'REMOVED')
    bad = [c.name for c in selected if verdicts[c.name][0] not in accounted]
    for name, files in sorted(run.tracked_writes.items()):
        print('redproof: %s wrote to tracked files: %s' % (
            name, ', '.join(files)))
    for name, why in sorted(run.unchecked.items()):
        print('redproof: %s could not be checked for tracked writes: %s' % (
            name, why))
    writes = len(set(run.tracked_writes) | set(run.unchecked))
    print('redproof: %d accounted for, %d not%s%s, %d s' % (
        len(selected) - len(bad), len(bad),
        (': ' + '; '.join('%s (%s)' % (n, verdicts[n][0]) for n in bad))
        if bad else '',
        '; %d run%s wrote to tracked files or could not be checked' % (
            writes, '' if writes == 1 else 's') if writes else '',
        time.monotonic() - started), flush=True)
    return 1 if bad or writes else 0


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


def run_entries(run, args, entries):
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
    if where.returncode != 0 or not under(imported, os.path.realpath(copy)):
        print('redproof: a copy imports schwaby from %r, not from the copy: '
              'every verdict would be a false GREEN, so none is judged\n%s'
              % (imported, where.stderr))
        raise SystemExit(1)

    # A suite that cannot be collected fails every baseline, each for the
    # same reason: say it once.
    out = subprocess.run(
        [sys.executable, '-I', '-B', run.audit, '--collect-only', '-q',
         '-p', 'no:cacheprovider', 'tests/'], cwd=copy,
        env={'PATH': os.environ.get('PATH', ''), 'HOME': work,
             'LANG': 'C.UTF-8'},
        capture_output=True, text=True, timeout=PREPARE_LIMIT)
    if out.returncode != 0:
        print('redproof: collecting the suite failed, so nothing is judged'
              '\n%s%s' % (out.stdout[-2000:], out.stderr[-2000:]))
        raise SystemExit(1)
    shutil.rmtree(work, ignore_errors=True)

    # Baselines: every distinct test, unmutated. The first alone, to measure
    # what a run costs before deciding how many fit.
    distinct = []
    for c in entries:
        t = tuple(tests_of(c))
        if t not in distinct:
            distinct.append(t)
    total = len(distinct) + len(entries)
    done = [0]
    done_lock = threading.Lock()

    def progress(verdict, label, detail, seconds, breakdown=''):
        with done_lock:
            done[0] += 1
            n = done[0]
        print('[%d/%d] %-10s %s  (%.1f s%s)%s' % (
            n, total, verdict, label, seconds, breakdown,
            ('\n           ' + detail) if detail else ''), flush=True)

    baselines = {}

    def merge(task_result):
        _merge(run, task_result)

    def baseline_done(task_result):
        t, result = task_result[:2]
        merge(task_result)
        baselines[t] = result
        if result.code == 0:
            progress('baseline', ' '.join(t), '', result.seconds)
        else:
            progress('BASELINE', ' '.join(t), result.killed or (
                'fails unmutated: %s' % last_lines(result.output)),
                result.seconds)

    _job.update(run=run, cases={c.name: c for c in entries})
    baseline_done(_baseline_task(distinct[0]))
    size, reason = pool_size(args, run.largest)
    print('redproof: %d at once: %s' % (size, reason), flush=True)

    for task_result in parallel(_baseline_task, distinct[1:], size,
                                _baseline_failed):
        baseline_done(task_result)
    size, reason = pool_size(args, run.largest)
    print('redproof: %d at once for the entries: %s' % (size, reason),
          flush=True)

    verdicts = {}
    _job.update(baselines=baselines)

    for task_result in parallel(_entry_task, [c.name for c in entries], size,
                                _entry_failed):
        name, verdict, detail, seconds, breakdown = task_result[:5]
        merge(task_result)
        verdicts[name] = (verdict, detail, seconds)
        progress(verdict, name, detail, seconds, breakdown)

    avail = run.lowest_available
    print('redproof: memory: largest run %s, lowest available %s' % (
        gib(run.largest), gib(avail) if avail is not None else 'unknown'),
        flush=True)
    return verdicts


def split(run, seconds):
    '''`: copy a, pytest c, harness d` for the run this task just
    made, or '' when it made none (an entry judged without running).'''
    t = run.timing
    if not hasattr(t, 'pytest'):
        return ''
    own = max(0.0, seconds - t.copy - t.pytest)
    text = ': copy %.1f, pytest %.1f, harness %.1f' % (t.copy, t.pytest, own)
    del t.copy, t.pytest
    return text


# What the worker processes inherit when they are forked: the run, its
# entries and, for the entries' phase, the baselines. Forked rather than
# spawned, so nothing large is pickled on the way in; only a task's result
# comes back.
_job = {}


def _merge(run, task_result):
    '''What a worker learned that the run as a whole keeps.'''
    writes, unchecked, largest, lowest = task_result[-4:]
    run.tracked_writes.update(writes)
    run.unchecked.update(unchecked)
    run.largest = max(run.largest, largest)
    if lowest is not None and (run.lowest_available is None
                               or lowest < run.lowest_available):
        run.lowest_available = lowest


def _worker_result(run):
    '''What every task hands back besides its own result: the tracked
    writes it found, the runs it could not check for them, and the memory
    it saw.'''
    writes, unchecked = dict(run.tracked_writes), dict(run.unchecked)
    run.tracked_writes.clear()
    run.unchecked.clear()
    return writes, unchecked, run.largest, run.lowest_available


def _baseline_task(t):
    run = _job['run']
    # A worker is forked with whatever the parent's last run left here.
    run.timing.__dict__.clear()
    try:
        result = run_baseline(run, t)
    except Exception as e:
        result = Result(None, '', 0.0, 0, 'the harness failed running it: '
                        '%s: %s' % (type(e).__name__, e))
    return (t, result) + _worker_result(run)


def _entry_task(name):
    run = _job['run']
    c = _job['cases'][name]
    t = tuple(tests_of(c))
    # A worker is forked with whatever the parent's last run left here, and
    # an entry judged without running printed the first baseline's split.
    run.timing.__dict__.clear()
    started = time.monotonic()
    try:
        verdict, detail = judge(run, c, _job['baselines'][t])
    except Exception as e:
        # A defect in the harness, reported against the entry it hit: one
        # used to end the whole run, an hour in, with nothing judged after.
        verdict, detail = 'ERROR', 'the harness failed judging it: ' \
            '%s: %s' % (type(e).__name__, e)
    seconds = time.monotonic() - started
    return (name, verdict, detail, seconds,
            split(run, seconds)) + _worker_result(run)


def _baseline_failed(t, why):
    return (t, Result(None, '', 0.0, 0, why), {}, {}, 0, None)


def _entry_failed(name, why):
    return (name, 'ERROR', why, 0.0, '', {}, {}, 0, None)


#: How long a worker has to stop once told to, before it is killed
WORKER_STOP_LIMIT = 10
#: The longest one task may take, seen from the parent: its one run and that
#: run's reap, and five minutes for copying and the rest. Past it, the worker
#: is killed and the item fails.
TASK_LIMIT = RUN_LIMIT + REAP_LIMIT + 300


def parallel(fn, items, size, failed):
    '''fn over items in `size` worker processes, yielding each result as
    it arrives, and failed(item, why) for an item whose worker died, raised
    or overran TASK_LIMIT. Processes, not threads: measured while the harness
    still classified every run's reads, twenty threads queued on the
    interpreter lock spent about fourteen seconds an entry waiting where
    pytest took half of one. That work is gone since; processes still keep
    each task's state its own.

    A pool of its own, not multiprocessing's or concurrent.futures': a Pool
    waits forever on a dead worker and hangs in terminate() after an idle
    one dies; the executor joins a broken pool's workers with no bound on
    3.12 and later, and a worker that catches its own SystemExit goes back
    for more work. Here each worker is forked from this single-threaded
    process and talks to it over two pipes; a worker that dies closes its
    pipe, which fails its one item, kills the groups it reported, and puts a
    new worker in its place. Every wait here has a bound.'''
    if not items:
        return
    import selectors
    pending = list(range(len(items)))[::-1]
    workers = []
    selector = selectors.DefaultSelector()

    def give(w):
        index = pending.pop()
        if not w.send(index):
            # Gone since its last result: the item waits for the worker
            # that takes its place.
            pending.append(index)

    def start():
        w = _Worker(fn, items)
        workers.append(w)
        selector.register(w.results, selectors.EVENT_READ, w)
        if pending:
            give(w)

    outstanding = len(items)
    try:
        for _ in range(min(size, len(items))):
            start()
        while outstanding:
            for key, _ in selector.select(timeout=1.0):
                w = key.data
                frames = w.frames()
                if frames is None:
                    # Gone: what it left running dies with it.
                    w.reap_groups()
                    selector.unregister(w.results)
                    workers.remove(w)
                    status = w.close()
                    if w.item is not None:
                        outstanding -= 1
                        yield failed(items[w.item], (
                            'its worker process ran past %d s and was killed'
                            % TASK_LIMIT) if w.overdue else (
                            'its worker process stopped (%s)' % status))
                    if pending:
                        start()
                    continue
                for frame in frames:
                    if w.take(frame):
                        continue
                    kind, index, value = frame
                    w.item = None
                    outstanding -= 1
                    yield value if kind == 'result' else failed(
                        items[index], 'the harness failed: %s' % value)
                    if pending:
                        give(w)
            now = time.monotonic()
            for w in workers:
                if w.item is not None and now - w.since > TASK_LIMIT:
                    w.overdue = True
                    w.signal(signal.SIGKILL)
    finally:
        # Idle workers stop when their task pipe closes; busy ones, on a
        # signal or a run ending early, are told to and then killed. Each
        # worker's cleanup on its own: one that fails, or a signal landing
        # in it, must not leave the rest running.
        failures = []
        for w in workers:
            try:
                os.close(w.tasks)
                w.tasks = None
                if w.item is not None:
                    w.signal(signal.SIGTERM)
            except BaseException as e:
                failures.append(e)
        deadline = time.monotonic() + WORKER_STOP_LIMIT
        for w in workers:
            try:
                if _wait_gone(w.pid, deadline) is False:
                    w.signal(signal.SIGKILL)
                    _wait_gone(w.pid, time.monotonic() + WORKER_STOP_LIMIT)
                # What it reported after the parent stopped reading: a group
                # it started just before it stopped is otherwise never killed.
                w.drain()
            except BaseException as e:
                failures.append(e)
                # Whatever stopped the wait, the worker does not outlive it,
                # and what it reported last is still read.
                w.signal(signal.SIGKILL)
                try:
                    w.drain()
                except BaseException as e:
                    failures.append(e)
            try:
                w.reap_groups()
                os.close(w.results)
            except BaseException as e:
                failures.append(e)
        selector.close()
        if failures:
            raise failures[0]


class _Worker:
    '''One forked worker: tasks go to it as 8-byte item indexes, and frames
    come back as a 4-byte length and a pickle.'''

    def __init__(self, fn, items):
        results_r, results_w = os.pipe()
        tasks_r, tasks_w = os.pipe()
        pid = os.fork()
        if pid == 0:
            try:
                os.close(results_r)
                os.close(tasks_w)
                _worker_main(fn, items, tasks_r, results_w)
            finally:
                os._exit(1)
        os.close(results_w)
        os.close(tasks_r)
        self.pid, self.results, self.tasks = pid, results_r, tasks_w
        self.buffer = b''
        self.groups = set()
        self.item = self.since = None
        self.overdue = False

    def send(self, index):
        '''Whether the worker took the item: one that died after its last
        result has a task pipe nobody reads.'''
        import struct
        try:
            os.write(self.tasks, struct.pack('>q', index))
        except OSError:
            return False
        self.item, self.since = index, time.monotonic()
        return True

    def take(self, frame):
        '''Whether frame was a group it started or reaped, now noted: one
        place for both, so the parent kills what is running and never a
        group already gone, whose number may be another's by then.'''
        if frame[0] == 'started':
            self.groups.add(frame[1])
        elif frame[0] == 'reaped':
            self.groups.discard(frame[1])
        else:
            return False
        return True

    def frames(self):
        '''The whole frames read so far, or None when the worker is gone.'''
        import pickle
        import struct
        data = os.read(self.results, 1 << 16)
        if not data:
            return None
        self.buffer += data
        out = []
        while len(self.buffer) >= 4:
            n, = struct.unpack('>I', self.buffer[:4])
            if len(self.buffer) < 4 + n:
                break
            out.append(pickle.loads(self.buffer[4:4 + n]))
            self.buffer = self.buffer[4 + n:]
        return out

    def drain(self):
        '''Reads the groups the worker reported up to its end. Only once
        it is gone, so the pipe ends; and bounded, in case it is not.'''
        deadline = time.monotonic() + WORKER_STOP_LIMIT
        os.set_blocking(self.results, False)
        while time.monotonic() < deadline:
            try:
                frames = self.frames()
            except BlockingIOError:
                return
            if frames is None:
                return
            for frame in frames:
                self.take(frame)

    def signal(self, sig):
        try:
            os.kill(self.pid, sig)
        except ProcessLookupError:
            pass

    def reap_groups(self):
        '''Kills what it reported running and waits for it to go: a
        callback server left behind holds its port, and runs on.'''
        for pgid in sorted(self.groups):
            try:
                reap(pgid)
            except RuntimeError:
                pass    # outlived REAP_LIMIT; killed, and nothing more to do
        self.groups.clear()

    def close(self):
        '''Reaps the worker, whose pipe has closed, and says how it ended.'''
        os.close(self.results)
        self.results = None
        if self.tasks is not None:
            os.close(self.tasks)
            self.tasks = None
        # Its pipe closes only as it exits, so this is a moment's wait.
        status = _wait_gone(self.pid, time.monotonic() + WORKER_STOP_LIMIT)
        if status is None or status is False:
            return 'its exit was not seen'
        code = os.waitstatus_to_exitcode(status)
        return 'killed by signal %d' % -code if code < 0 else 'exit %d' % code


def _wait_gone(pid, deadline):
    '''Its wait status once it has exited, None if it is not ours to wait
    for, or False at the deadline.'''
    while True:
        try:
            done, status = os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            return None
        if done:
            return status
        if time.monotonic() > deadline:
            return False
        time.sleep(0.02)


def _worker_main(fn, items, tasks, results):
    import pickle
    import struct
    global _report

    def send(message):
        data = pickle.dumps(message)
        data = struct.pack('>I', len(data)) + data
        while data:
            data = data[os.write(results, data):]

    def stopped(signum, frame):
        # Kills its own groups and leaves: no exception, which task code or
        # cleanup could catch and carry on from. The parent reaps what it
        # reported as well, but not a group started a moment before its
        # report, nor any after its own cleanup was cut short.
        for pid in list(_live):
            kill_group(pid)
        os._exit(128 + signum)

    for s in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(s, stopped)
    _report = send
    while True:
        data = b''
        while len(data) < 8:
            more = os.read(tasks, 8 - len(data))
            if not more:
                os._exit(0)
            data += more
        index, = struct.unpack('>q', data)
        try:
            message = ('result', index, fn(items[index]))
            pickle.dumps(message)
        except BaseException as e:
            message = ('failed', index, '%s: %s' % (type(e).__name__, e))
        send(message)


def last_lines(output, n=3):
    lines = [line for line in output.splitlines() if line.strip()]
    return ' | '.join(lines[-n:])


def stop(signum, frame):
    # Once: a second signal while the first one's cleanup runs would cut
    # that cleanup short. Every wait in it has a bound.
    if _stopping.is_set():
        return
    _stopping.set()
    for pid in list(_live):
        kill_group(pid)
    raise SystemExit(128 + signum)


if __name__ == '__main__':
    for s in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(s, stop)
    sys.exit(main(sys.argv[1:]))
