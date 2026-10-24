'''The red-proof harness's own decisions: what a mutation does, which failure
counts, which writes to tracked files are caught, how many runs fit, and what
its worker processes do when one dies. A harness that calls a guard PROVEN
when it is not reports it working after it broke, and nothing else would
ever notice.'''

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import patch

# The harness is POSIX only (process groups, os.wait4, fcntl), and these tests
# read paths the way it does, with '/'.
if os.name == 'nt':
    raise unittest.SkipTest('the red-proof harness runs on POSIX only')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load(name):
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(ROOT, 'tools', name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


redproof = load('redproof')
cases = load('redproof_cases')


def case(old='a = 1', new='a = 2', **kwargs):
    return cases.Case('x/y', 'schwaby/x.py', old, new,
                      'tests/x_test.py::T::test_x', 'expected', **kwargs)


def snapshot(content):
    s = redproof.Snapshot.__new__(redproof.Snapshot)
    s.content = content
    s.tracked = set(content)
    return s


class MutateTest(unittest.TestCase):

    def test_replaces_the_one_occurrence_in_bytes(self):
        original = 'x = "é"\na = 1\n'.encode('utf-8')
        mutated, why = redproof.mutate(case(), original)
        self.assertIsNone(why)
        self.assertEqual('x = "é"\na = 2\n'.encode('utf-8'), mutated)

    def test_applies_the_second_pair_too(self):
        mutated, why = redproof.mutate(
            case(also=('b = 1', 'b = 3')), b'a = 1\nb = 1\n')
        self.assertIsNone(why)
        self.assertEqual(b'a = 2\nb = 3\n', mutated)

    def test_refuses_a_no_op(self):
        mutated, why = redproof.mutate(case(new='a = 1'), b'a = 1\n')
        self.assertIsNone(mutated, 'a no-op mutation was applied')
        self.assertIn('identical', why)

    def test_refuses_text_that_is_missing_or_repeated(self):
        for original, count in ((b'b = 1\n', 0), (b'a = 1\na = 1\n', 2)):
            mutated, why = redproof.mutate(case(), original)
            self.assertIsNone(mutated, 'text appearing %d times was mutated'
                              % count)
            self.assertIn('appears %d times' % count, why)


class FakeRun:
    '''Stands in for redproof.Run in judge(): a snapshot, and run() handing
    back a canned result and the tracked files it wrote.'''

    def __init__(self, content, result, changed=(), unchecked=None):
        self.snapshot = snapshot(content)
        self.result, self.changed = result, list(changed)
        self.unchecked_why = unchecked
        self.tracked_writes, self.unchecked = {}, {}
        self.ran = []

    def run(self, nodes, mutation=None):
        self.ran.append((nodes, mutation))
        return self.result, self.changed, self.unchecked_why


def result(code, output='', killed=None):
    return redproof.Result(code, output, 0.1, 1 << 20, killed)


class JudgeTest(unittest.TestCase):

    CONTENT = {'schwaby/x.py': b'a = 1\n'}
    PASSED = result(0, '1 passed')

    # What pytest -q prints for a test whose first assertion failed, with the
    # entry's expect text in the source it echoes and in nothing it says.
    ECHOED = '\n'.join([
        '    def test_x(self):',
        ">       self.assertIn('identical', why)",
        "        self.assertIsNone(mutated, 'expected')",
        "E       AssertionError: 'identical' not found in 'other'",
        'FAILED tests/expected_test.py::T::test_x - AssertionError: '
        "'identical' not found",
        '1 failed in 0.01s'])

    def judge(self, output, expect='expected', baseline=PASSED, code=1,
              **kwargs):
        c = case(**kwargs)
        c.expect = expect
        run = FakeRun(self.CONTENT, result(code, output))
        return redproof.judge(run, c, baseline)

    def test_expect_must_be_in_what_the_failure_says(self):
        verdict, detail = self.judge(self.ECHOED)
        self.assertEqual('RED?', verdict,
                         'a failure on another assertion was PROVEN because '
                         'the expect text was in the echoed source')
        said = self.ECHOED.replace("'identical' not found in",
                                   "'expected' not found in")
        self.assertEqual('PROVEN', self.judge(said)[0])

    SAME = "E       AssertionError: '7521' unexpectedly found in 'xxx'"

    @staticmethod
    def section(title, *said):
        return '\n'.join(['_ %s _' % title, '    def test_x(self):', *said])

    def test_a_subtest_qualifier_picks_the_failing_subtest(self):
        # Every subtest fails with the same message; only the subtest pytest
        # names in each header says which helper it was.
        both = '\n'.join([
            self.section("T.test_x (helper='_safe_name', offset=1)", self.SAME),
            self.section("T.test_x (helper='_safe_str', offset=1)", self.SAME),
            '=== short test summary info ===', '1 failed'])
        self.assertEqual('PROVEN', self.judge(
            both, expect='unexpectedly found in',
            subtest="helper='_safe_str'")[0])
        other = '\n'.join([
            self.section("T.test_x (helper='_safe_name', offset=1)", self.SAME),
            '=== short test summary info ===', '1 failed'])
        self.assertEqual('RED?', self.judge(
            other, expect='unexpectedly found in',
            subtest="helper='_safe_str'")[0],
            'a failure in another subtest was PROVEN')

    def test_a_subtest_label_is_never_the_match(self):
        # The test's own subtest keyword, and a failure that is a crash, not
        # the assertion: this was PROVEN with every assertion deleted.
        crashed = '\n'.join([
            self.section('T.test_x (protocol=0)',
                         'E   RecursionError: maximum recursion depth exceeded'),
            "SUBFAILED(protocol=0) tests/redact_test.py::T::test_x",
            '1 failed'])
        self.assertEqual('RED?', self.judge(crashed, expect='protocol=')[0],
                         'an entry matched on a label the test chose')

    def test_a_rewritten_asserts_quoted_expression_is_not_what_failed(self):
        # What pytest prints for `assert r != 0 or 'expected' == ''`: the
        # expression, quoted from the source, with the literal in it.
        bare = '\n'.join([
            self.section('test_other',
                         "E       AssertionError: assert (0 != 0 or "
                         "'expected' == ''",
                         'E         ',
                         "E         + expected)"),
            '1 failed'])
        self.assertEqual('RED?', self.judge(bare)[0],
                         "an assert's quoted expression was taken for what "
                         'failed')
        # The positive control: an assert's own message still counts, and
        # the explanation under it does not.
        with_message = '\n'.join([
            self.section('test_real', 'E       AssertionError: expected',
                         'E       assert 3 == 4'),
            '1 failed'])
        self.assertEqual('PROVEN', self.judge(with_message)[0])
        other = '\n'.join([
            self.section('test_other', 'E       AssertionError: the count',
                         "E       assert (0 == 1 and 'expected' != '')"),
            '1 failed'])
        self.assertEqual('RED?', self.judge(other)[0])

    def test_a_subtest_failure_message_counts(self):
        out = '\n'.join([self.section('T.test_x (i=1)',
                                       'E   AssertionError: expected 3'),
                          '1 failed'])
        self.assertEqual('PROVEN', self.judge(out)[0])

    def test_captured_output_and_the_summary_are_not_what_failed(self):
        # A captured log line and the short summary both echo text a test can
        # choose; only the failure's own E lines say what failed.
        out = '\n'.join([
            self.section('T.test_x', 'E   AssertionError: something else'),
            '----- Captured stdout call -----',
            'E   AssertionError: expected, printed by the test itself',
            '----- Captured log call -----',
            'ERROR    schwaby.streaming:streaming.py:812 expected',
            '=== short test summary info ===',
            'FAILED tests/x_test.py::T::test_x - AssertionError: expected',
            '1 failed'])
        self.assertEqual('RED?', self.judge(out)[0],
                         'captured output was taken for what failed')

    def test_a_baseline_that_fails_is_not_judged(self):
        verdict, detail = self.judge(self.ECHOED,
                                        baseline=result(1, 'FAILED'))
        self.assertEqual('SPEC', verdict, 'an entry was judged on a test that '
                         'fails unmutated')
        self.assertIn('does not pass before the mutation', detail)

    def test_a_run_with_no_exit_status_proves_nothing(self):
        # Killed after printing the very failure the entry expects: still no
        # verdict, because a run that did not finish proves nothing.
        verdict, detail = self.judge('E   AssertionError: expected',
                                        code=None)
        self.assertEqual('RED?', verdict, 'a timed-out run was judged')

    def test_a_pass_is_green_unless_superseded(self):
        self.assertEqual('GREEN', self.judge('1 passed', code=0)[0])
        self.assertEqual('SUPERSEDED', self.judge(
            '1 passed', code=0, superseded='covered elsewhere')[0])

    def test_a_run_that_could_not_be_checked_is_reported(self):
        run = FakeRun(self.CONTENT, result(1, 'E   expected'),
                      unchecked='it left no record of its writes')
        redproof.judge(run, case(), self.PASSED)
        self.assertEqual({'x/y': 'it left no record of its writes'},
                         run.unchecked, 'a run whose writes went unchecked '
                         'was not reported')

    def test_a_tracked_write_is_reported_against_the_entry(self):
        run = FakeRun(self.CONTENT, result(1, 'E   expected'),
                      changed=['schwaby/x.py'])
        self.assertEqual('PROVEN', redproof.judge(run, case(), self.PASSED)[0])
        self.assertEqual({'x/y': ['schwaby/x.py']}, run.tracked_writes,
                         'a tracked write went unreported')


class TrackedWritesTest(unittest.TestCase):

    def test_a_write_the_run_put_back_is_found(self):
        # The comparison after the run cannot see it: only the record can.
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        copy = os.path.join(tmp.name, 'run1', 'copy')
        os.makedirs(copy)
        record = {'written': [os.path.join(copy, 'schwaby', 'x.py'),
                              os.path.join(copy, 'untracked.log'),
                              os.path.join(tmp.name, 'run1', 'home', 'x.py')],
                  'unresolved': []}
        self.assertEqual(['schwaby/x.py'], redproof.tracked_writes_in(
            record, copy, {'schwaby/x.py'}),
            'a write the run restored went unreported')

    def test_a_write_to_the_file_under_any_name_is_found(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        copy = os.path.join(tmp.name, 'copy')
        os.makedirs(os.path.join(copy, 'schwaby'))
        open(os.path.join(copy, 'schwaby', 'x.py'), 'w').close()
        inodes = redproof.tracked_inodes(copy, {'schwaby/x.py'})
        st = os.stat(os.path.join(copy, 'schwaby', 'x.py'))
        record = {'written': [os.path.join(tmp.name, 'elsewhere.py')],
                  'inodes': [[st.st_dev, st.st_ino]]}
        self.assertEqual(['schwaby/x.py'], redproof.tracked_writes_in(
            record, copy, {'schwaby/x.py'}, inodes),
            'a write to a tracked file under another name went unreported')

    def test_a_write_through_a_linked_path_is_found(self):
        # A copy under a linked temporary directory is written under either
        # spelling.
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        real = os.path.join(tmp.name, 'real')
        os.makedirs(os.path.join(real, 'copy'))
        os.symlink(real, os.path.join(tmp.name, 'link'))
        copy = os.path.join(tmp.name, 'link', 'copy')
        record = {'written': [os.path.join(real, 'copy', 'schwaby', 'x.py')]}
        self.assertEqual(['schwaby/x.py'], redproof.tracked_writes_in(
            record, copy, {'schwaby/x.py'}),
            'a write by the copy\'s real path went unreported')


class AuditTest(unittest.TestCase):
    '''The audit hook itself, under a real pytest: the one thing that sees
    a tracked file written and put back before the run ended.'''

    TEST = '''
import os

def test_writes_and_puts_back():
    with open('tracked.py', 'rb') as f:
        old = f.read()
    with open('tracked.py', 'wb') as f:
        f.write(b'changed')
    with open('tracked.py', 'wb') as f:
        f.write(old)
    os.rename('moved.py', 'moved2.py')
    os.rename('moved2.py', 'moved.py')
    with open('only_read.py') as f:
        f.read()
    # One path per kind of write, so each kind is seen on its own.
    os.remove('removed.py')
    os.chmod('chmodded.py', 0o644)
    os.chown('chowned.py', os.getuid(), os.getgid())
    os.utime('touched.py')
    os.truncate('truncated.py', 0)
    fd = os.open('sub', os.O_RDONLY)
    try:
        os.close(os.open('inner.py', os.O_WRONLY, dir_fd=fd))
        # Made in sub, not here: nothing may be recorded at ./decoy.py.
        os.close(os.open('decoy.py', os.O_WRONLY | os.O_CREAT, dir_fd=fd))
    finally:
        os.close(fd)
    # Through other names for a file, and through a descriptor opened only
    # for reading: the file itself is what was written.
    os.symlink('sym_target.py', 'sym_alias.py')
    with open('sym_alias.py', 'w') as f:
        f.write('through a symlink')
    os.link('hard_target.py', 'hard_alias.py')
    with open('hard_alias.py', 'w') as f:
        f.write('through a hard link')
    fd = os.open('fd_target.py', os.O_RDONLY)
    try:
        os.fchmod(fd, 0o600)
    finally:
        os.close(fd)
    # What is not a write: a directory opened to read it, a file with no
    # name, a hard link made and removed again, a link's own times, and an
    # open that could only fail.
    os.close(os.open('sub', os.O_RDONLY))
    os.link('kept_a.py', 'kept_a_link.py')
    os.remove('kept_a_link.py')
    os.symlink('kept_b.py', 'kept_b_link.py')
    os.utime('kept_b_link.py', ns=(0, 0), follow_symlinks=False)
    try:
        open('kept_c.py', 'x')
    except FileExistsError:
        pass
    if hasattr(os, 'O_TMPFILE'):
        os.close(os.open('.', os.O_TMPFILE | os.O_WRONLY))
'''

    KINDS = (('removed.py', 'removal'), ('chmodded.py', 'chmod'),
             ('chowned.py', 'chown'), ('touched.py', 'utime'),
             ('truncated.py', 'truncation'),
             ('sub/inner.py', 'write relative to a directory descriptor'))
    IDENTITIES = (('sym_target.py', 'write through a symlink'),
                  ('hard_target.py', 'write through a hard link'),
                  ('fd_target.py', 'change through a read-only descriptor'))

    def test_a_write_put_back_is_recorded_and_a_read_is_not(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = os.path.realpath(tmp.name)
        os.makedirs(os.path.join(root, 'sub'))
        for name, data in (('test_x.py', self.TEST), ('tracked.py', 'a = 1\n'),
                           ('moved.py', ''), ('only_read.py', ''),
                           ('removed.py', ''), ('chmodded.py', ''),
                           ('chowned.py', ''), ('touched.py', ''),
                           ('truncated.py', 'x'), ('sub/inner.py', ''),
                           ('sym_target.py', ''), ('hard_target.py', ''),
                           ('fd_target.py', ''), ('kept_a.py', ''),
                           ('kept_b.py', ''), ('kept_c.py', '')):
            with open(os.path.join(root, name), 'w') as f:
                f.write(data)
        out = os.path.join(root, 'record.json')
        env = dict(os.environ, REDPROOF_AUDIT=out)
        run = subprocess.run(
            [sys.executable, '-I', '-B',
             os.path.join(ROOT, redproof.AUDIT), '-q', '-p',
             'no:cacheprovider', 'test_x.py'], cwd=root, env=env,
            capture_output=True, text=True, timeout=120)
        self.assertEqual(0, run.returncode, run.stdout + run.stderr)
        with open(out) as f:
            record = json.load(f)
        written = set(record['written'])
        inodes = {tuple(i) for i in record['inodes']}
        for name in ('tracked.py', 'moved.py', 'moved2.py'):
            self.assertIn(os.path.join(root, name), written,
                          'a write the test put back was not recorded '
                          '(%s)' % name)
        for name, kind in self.KINDS:
            self.assertIn(os.path.join(root, name), written,
                          'a %s was not recorded' % kind)
        for name, kind in self.IDENTITIES:
            st = os.stat(os.path.join(root, name))
            self.assertIn((st.st_dev, st.st_ino), inodes,
                          'a %s was not recorded' % kind)
        self.assertNotIn(os.path.join(root, 'decoy.py'), written,
                         'a write was recorded where it was not made')
        for name, what in (
                ('kept_a.py', 'a removed link was taken for its file'),
                ('kept_b.py', "a change to a link itself was taken for its "
                 "file's"),
                ('kept_c.py', 'an open that could only fail was taken for a '
                 'write')):
            st = os.stat(os.path.join(root, name))
            self.assertNotIn((st.st_dev, st.st_ino), inodes, what)
        self.assertNotIn(os.path.join(root, 'kept_c.py'), written,
                         'an open that could only fail was taken for a write')
        self.assertNotIn(os.path.join(root, 'sub'), written,
                         'a directory opened to read was recorded as a write')
        if hasattr(os, 'O_TMPFILE'):
            self.assertNotIn(root, written,
                             'a file with no name was recorded as a write')
        # The positive control: what was only read is not a write.
        self.assertNotIn(os.path.join(root, 'only_read.py'), written,
                         'a read was recorded as a write')


class RunRecordTest(unittest.TestCase):

    def test_a_write_the_record_names_is_one_the_run_made(self):
        # Run.run merges what the hook saw with the comparison after the
        # run, which cannot see a file put back.
        import threading
        from unittest import mock
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        content = {'schwaby/x.py': b'a = 1\n'}
        snap = snapshot(content)
        snap.copy = lambda dest: [
            os.makedirs(os.path.join(dest, 'schwaby'), exist_ok=True),
            open(os.path.join(dest, 'schwaby', 'x.py'), 'wb').write(b'a = 1\n')]
        run = redproof.Run.__new__(redproof.Run)
        run.tmp, run.snapshot, run.audit = tmp.name, snap, 'audit.py'
        run.locks_dir, run.groups = tmp.name, {}
        run.largest, run.lowest_available = 0, None
        run.timing = threading.local()

        def run_tests(work, copy, nodes, audit, record, **kwargs):
            with open(record, 'w') as f:
                json.dump({'written': [os.path.join(copy, 'schwaby', 'x.py')],
                           'unresolved': []}, f)
            return result(1, 'E   expected')

        def by_identity(work, copy, nodes, audit, record, **kwargs):
            # Written under another name: only the file's identity says so.
            st = os.stat(os.path.join(copy, 'schwaby', 'x.py'))
            with open(record, 'w') as f:
                json.dump({'written': [], 'inodes': [[st.st_dev, st.st_ino]],
                           'unresolved': []}, f)
            return result(1, 'E   expected')

        with mock.patch.object(redproof, 'run_tests', by_identity):
            self.assertEqual(['schwaby/x.py'], run.run(['tests/x_test.py'])[1],
                             'a write the hook knew by identity was dropped')

        with mock.patch.object(redproof, 'run_tests', run_tests):
            _, changed, unchecked = run.run(['tests/x_test.py'])
        self.assertEqual(['schwaby/x.py'], changed,
                         'a write the hook recorded was dropped')
        self.assertIsNone(unchecked)

        # A run that left no record, and one whose writes the hook could
        # not all place: neither is a run with no writes.
        def no_record(work, copy, nodes, audit, record, **kwargs):
            return result(1, 'E   expected')

        def unresolved(work, copy, nodes, audit, record, **kwargs):
            with open(record, 'w') as f:
                json.dump({'written': [], 'unresolved': ['os.rename']}, f)
            return result(1, 'E   expected')

        for fake, why in ((no_record, 'it left no record of its writes'),
                          (unresolved, 'the audit could not place 1 of its '
                           'writes (os.rename)')):
            with self.subTest(fake.__name__):
                with mock.patch.object(redproof, 'run_tests', fake):
                    self.assertEqual(why, run.run(['tests/x_test.py'])[2],
                                     'a run whose writes went unchecked '
                                     'passed as having none')


class ReportTest(unittest.TestCase):

    def test_a_run_that_could_not_be_checked_fails_the_whole_run(self):
        import contextlib
        import io
        run = types.SimpleNamespace(tracked_writes={}, unchecked={})
        selected = [case()]
        verdicts = {'x/y': ('PROVEN', '', 0.1)}
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(0, redproof.report(run, selected, verdicts, 0))
        run.unchecked['x/y'] = 'it left no record of its writes'
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = redproof.report(run, selected, verdicts, 0)
        self.assertEqual(1, code, 'a run whose writes went unchecked passed')
        self.assertIn('x/y could not be checked for tracked writes: it left '
                      'no record of its writes', out.getvalue(),
                      'an unchecked run was not named')

    def test_what_a_worker_found_reaches_the_run(self):
        worker = types.SimpleNamespace(
            tracked_writes={'a': ['x.py']}, unchecked={'b': 'why'},
            largest=5, lowest_available=None)
        handed = redproof._worker_result(worker)
        self.assertEqual(({}, {}), (worker.tracked_writes, worker.unchecked))
        parent = types.SimpleNamespace(tracked_writes={}, unchecked={},
                                       largest=0, lowest_available=None)
        redproof._merge(parent, ('result',) + handed)
        self.assertEqual({'b': 'why'}, parent.unchecked,
                         'a worker\'s unchecked run did not reach the run')
        self.assertEqual({'a': ['x.py']}, parent.tracked_writes)


class BaselineTest(unittest.TestCase):

    def test_a_baselines_unchecked_run_is_reported(self):
        run = FakeRun(JudgeTest.CONTENT, result(0, '1 passed'),
                      unchecked='it left no record of its writes')
        redproof.run_baseline(run, ['tests/x_test.py'])
        self.assertEqual(
            {'the baseline of tests/x_test.py':
             'it left no record of its writes'}, run.unchecked,
            'a baseline whose writes went unchecked was not reported')

    def test_a_baselines_tracked_write_is_reported(self):
        run = FakeRun(JudgeTest.CONTENT, result(0, '1 passed'),
                      changed=['schwaby/x.py'])
        self.assertEqual(0, redproof.run_baseline(
            run, ['tests/x_test.py']).code)
        self.assertEqual(
            {'the baseline of tests/x_test.py': ['schwaby/x.py']},
            run.tracked_writes, 'a baseline that wrote a tracked file went '
            'unreported')


class SnapshotTest(unittest.TestCase):

    def test_secret_directories_at_the_root_are_left_out(self):
        files = ['.config/gh/hosts.yml', '.ssh/known_hosts',
                 '.gnupg/pubring.kbx', 'docs/config.rst']
        self.assertEqual(files[:3], redproof.secret_untracked(files, set()),
                         'a secret directory at the root was copied')

    def test_a_copy_keeps_each_files_mode(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        src, dest = os.path.join(tmp.name, 's'), os.path.join(tmp.name, 'd')
        os.makedirs(os.path.join(src, 'tools'))
        hook = os.path.join(src, 'tools', 'hook')
        open(hook, 'w').close()
        os.chmod(hook, 0o755)
        redproof.copy_tree(['tools/hook'], src, dest)
        self.assertEqual(0o755, os.stat(os.path.join(dest, 'tools', 'hook'))
                         .st_mode & 0o777, 'a copy lost a file\'s mode')

    def test_untracked_secret_names_are_left_out(self):
        files = ['README.md', '.env.local', 'docs/api-credentials.rst',
                 'notes/token-notes.md', 'tests/secret_logging_test.py']
        tracked = {'README.md', 'tests/secret_logging_test.py'}
        self.assertEqual(
            ['.env.local', 'docs/api-credentials.rst', 'notes/token-notes.md'],
            redproof.secret_untracked(files, tracked),
            'an untracked secret-named file would be copied')


@unittest.skipUnless(os.path.isdir('/proc'), 'the scenarios find processes '
                     'through /proc; without it every check of one passes '
                     'for the wrong reason')
class WorkerTest(unittest.TestCase):
    '''The worker processes: a dead or stopped one is reported, not waited
    for, what it left running is killed, and the rest of the work goes on.'''

    SCRIPT = '''
import importlib.util, os, signal, subprocess, sys, time
spec = importlib.util.spec_from_file_location('redproof', sys.argv[1])
rp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rp)
for s in (signal.SIGTERM, signal.SIGINT):
    signal.signal(s, rp.stop)

def group(name):
    # A pytest, as run_tests starts one and tells the parent about it.
    p = subprocess.Popen(['sleep', '120'], start_new_session=True,
                         stdout=subprocess.DEVNULL)
    rp._track(p.pid)
    print(name, p.pid, flush=True)
    return p

def siblings():
    out = []
    for d in os.listdir('/proc'):
        if not d.isdigit():
            continue
        try:
            with open('/proc/%s/stat' % d) as f:
                ppid = int(f.read().rsplit(')', 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            continue
        if ppid == os.getppid() and int(d) != os.getpid():
            out.append(int(d))
    return out

def task(item):
    if item == 'dies':
        group('orphan')
        time.sleep(0.5)
        os._exit(1)
    if item == 'stopped':
        group('stopped-orphan')
        os.kill(os.getpid(), signal.SIGTERM)
        time.sleep(30)
    if item == 'slow':
        # Its own group must outlive a sibling's death.
        p = group('slow-group')
        time.sleep(2)
        alive = p.poll() is None
        rp.kill_group(p.pid)
        p.wait()
        rp._untrack(p.pid)
        return ('done', item, alive)
    if item == 'kills-idle':
        time.sleep(0.5)
        for pid in siblings():
            os.kill(pid, signal.SIGKILL)
    if item == 'raises':
        raise SystemExit(143)
    if item == 'big':
        # Larger than one read of the pipe: its frame arrives in pieces.
        return ('big', 'x' * 300000)
    if item == 'reaps-then-dies':
        p = group('reaped-group')
        rp.kill_group(p.pid)
        p.wait()
        rp._untrack(p.pid)
        os._exit(1)
    if item == 'dies-after':
        # Gone a moment after its result, while the parent is still busy
        # with that result: the next item meets a pipe nobody reads.
        signal.setitimer(signal.ITIMER_REAL, 0.05)
    if item == 'unpicklable':
        return lambda: None
    if item == 'hangs':
        time.sleep(20)
    if item in ('busy', 'wedged', 'wedged2', 'own'):
        if item in ('wedged', 'wedged2'):
            # Stuck: told to stop, it starts more tests instead, which it
            # reports after the parent has stopped reading.
            late = 'late-group' if item == 'wedged' else 'late2-group'
            signal.signal(signal.SIGTERM, lambda *a: group(late))
        group(item + '-group')
        open(MARK + item, 'w').close()
        time.sleep(30)
    if item.startswith('quick-'):
        # Done only once the busy one has started its tests.
        deadline = time.monotonic() + 10
        while not os.path.exists(MARK + item[6:]) and time.monotonic() < deadline:
            time.sleep(0.01)
    return ('done', item)

MARK = os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), 'mark-')

failed = lambda item, why: ('failed', item, why)
# Every group the parent kills, so a reaped one killed again shows.
killed = []
real_kill_group = rp.kill_group
rp.kill_group = lambda pgid: (killed.append(pgid), real_kill_group(pgid))
for items, size in ((['a', 'b'], 2), (['dies', 'slow'], 2),
                    (['stopped', 'after'], 1), (['kills-idle', 'x'], 3),
                    (['raises', 'a'], 1), (['unpicklable'], 1),
                    (['reaps-then-dies'], 1)):
    got = sorted(rp.parallel(task, items, size, failed))
    print(items[0], got, flush=True)
print('killed', killed, flush=True)
got = [(r[0], len(r[1])) for r in rp.parallel(task, ['big'], 1, failed)]
print('big', got, flush=True)
rp.TASK_LIMIT = 1
print('hangs', sorted(rp.parallel(task, ['hangs'], 1, failed)), flush=True)
rp.WORKER_STOP_LIMIT = 1
for first in ('busy', 'wedged'):
    # The run ends early, as on a signal, with a worker still busy.
    started = time.monotonic()
    for result in rp.parallel(task, [first, 'quick-' + first], 2, failed):
        break
    print('abandoned-' + first, round(time.monotonic() - started, 1),
          flush=True)
# As if the parent's cleanup were cut short before it reaped: a stopped
# worker's own kill is then all there is.
real_reap = rp.reap
rp.reap = lambda pgid: None
for result in rp.parallel(task, ['own', 'quick-own'], 2, failed):
    break
rp.reap = real_reap
# A wait cut short, as by a signal landing in it: the worker is killed, and
# what it reported last is still read and its tests killed.
real_wait_gone = rp._wait_gone
cut = [False]
def cut_short(pid, deadline):
    if not cut[0]:
        cut[0] = True
        time.sleep(0.3)
        raise RuntimeError('a wait cut short')
    return real_wait_gone(pid, deadline)
rp._wait_gone = cut_short
try:
    for result in rp.parallel(task, ['wedged2', 'quick-wedged2'], 2, failed):
        break
except RuntimeError:
    pass
rp._wait_gone = real_wait_gone
# A cleanup that fails says so, not silently.
real_wait_gone = rp._wait_gone
def broken(pid, deadline):
    raise ValueError('cleanup broke')
rp._wait_gone = broken
pool = rp.parallel(task, ['a', 'b'], 2, failed)
next(pool)
try:
    pool.close()
    print('cleanup-error swallowed', flush=True)
except ValueError:
    print('cleanup-error raised', flush=True)
rp._wait_gone = real_wait_gone
# That pool never waited for its workers: reap them here, or they are this
# script's zombies until it exits, and then systemd's, which the check for
# a worker outliving its pool raced.
while True:
    try:
        os.waitpid(-1, 0)
    except ChildProcessError:
        break
got = []
for result in rp.parallel(task, ['dies-after', 'b', 'c'], 1, failed):
    got.append(result)
    time.sleep(0.3)
print('dies-after', sorted(got), flush=True)
'''

    def run_script(self):
        tmp = self.tmp()
        script = os.path.join(tmp, 'workers.py')
        with open(script, 'w') as f:
            f.write(self.SCRIPT)
        # To a file, not a pipe: a pipe is held open by whatever the script
        # leaves running, and reading it would wait on that, not the pool.
        with open(os.path.join(tmp, 'out'), 'w') as out, \
                open(os.path.join(tmp, 'err'), 'w') as err:
            proc = subprocess.Popen(
                [sys.executable, '-I', script, os.path.join(
                    os.path.dirname(__file__), '..', 'tools', 'redproof.py')],
                stdout=out, stderr=err, start_new_session=True)
        def kill_what_it_started():
            # Each test group runs in a session of its own, so killing the
            # script's group does not reach them; the script names each as it
            # starts it.
            with open(os.path.join(tmp, 'out')) as f:
                for line in f.read().splitlines():
                    name, _, rest = line.partition(' ')
                    if name.endswith(('-group', 'orphan')) and rest.isdigit():
                        redproof.kill_group(int(rest))

        try:
            proc.wait(timeout=40)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, 9)
            proc.wait()
            kill_what_it_started()
            self.stderr = ''
            self.fail('the pool waited on a dead or overdue worker')
        # A worker the script never waited for is reaped by init once the
        # script exits, which takes a moment: allow it one.
        deadline = time.monotonic() + 2
        while True:
            try:
                os.killpg(proc.pid, 0)
            except ProcessLookupError:
                break
            if time.monotonic() > deadline:
                os.killpg(proc.pid, 9)
                kill_what_it_started()
                self.stderr = ''
                self.fail('a worker outlived its pool')
            time.sleep(0.05)
        with open(os.path.join(tmp, 'err')) as f:
            self.stderr = f.read()
        self.returncode = proc.returncode
        with open(os.path.join(tmp, 'out')) as f:
            return dict(line.split(' ', 1) for line in f.read().splitlines())

    def say(self, message):
        # The script's own error, when it crashed, is the first thing a
        # reader of any failure here needs.
        return message + ('\nthe scenario script said:\n' + self.stderr[-2000:]
                          if self.stderr.strip() else '')

    def test_a_dead_worker_is_reported_and_its_tests_killed(self):
        lines = self.run_script()
        # Whatever the script started is killed however the test ends: an
        # assertion failing first left them running for two minutes.
        for name in ('orphan', 'stopped-orphan', 'busy-group', 'wedged-group',
                     'late-group', 'own-group', 'reaped-group', 'slow-group',
                     'wedged2-group', 'late2-group'):
            if lines.get(name, '').strip().isdigit():
                self.addCleanup(redproof.kill_group, int(lines[name]))
        # The positive control: workers that live return what they made.
        self.assertEqual("[('done', 'a'), ('done', 'b')]", lines.get('a'))
        self.assertEqual("[('done', 'slow', True), ('failed', 'dies', 'its "
                         "worker process stopped (exit 1)')]",
                         lines.get('dies'), 'a dead worker was not reported, '
                         'or took a sibling\'s tests with it')
        self.assertEqual("[('done', 'after'), ('failed', 'stopped', 'its "
                         "worker process stopped (exit 143)')]",
                         lines.get('stopped'), 'the work after a stopped '
                         'worker did not go on')
        self.assertEqual("[('done', 'kills-idle'), ('done', 'x')]",
                         lines.get('kills-idle'), 'an idle worker\'s death '
                         'lost work')
        self.assertEqual("[('done', 'a'), ('failed', 'raises', 'the harness "
                         "failed: SystemExit: 143')]", lines.get('raises'),
                         'a task that raised ended its worker\'s work')
        self.assertRegex(lines.get('unpicklable', ''),
                         r"\('failed', 'unpicklable', .the harness failed: ",
                         'a result that cannot '
                         'cross to the parent was not reported as such')
        killed = lines.get('killed', '').replace('[', ' ').replace(
            ']', ' ').replace(',', ' ').split()
        # The positive control: the spy saw the dead worker's group killed,
        # so its silence about the reaped one means something.
        self.assertIn(lines.get('orphan'), killed, self.say(
            'the kill spy saw nothing, so it cannot say what was not killed'))
        self.assertIn('reaped-group', lines, self.say('the scenario never '
                      'reaped a group'))
        self.assertNotIn(lines['reaped-group'], killed,
                         self.say('a reaped group was killed again'))
        self.assertEqual("[('big', 300000)]", lines.get('big'),
                         'a result larger than one read was lost')
        self.assertEqual("[('failed', 'hangs', 'its worker process ran past "
                         "1 s and was killed')]", lines.get('hangs'),
                         'a task past its bound was not stopped')
        # Told to stop, a busy worker stops at once; one that cannot is
        # killed at the bound, 1 s here.
        self.assertLess(float(lines.get('abandoned-busy', 99)), 0.9,
                        'an abandoned pool\'s busy worker was not told to '
                        'stop')
        # Left alive, a stuck worker keeps its killed tests as zombies, so
        # reaping them waits out REAP_LIMIT: 30 s, not about 1.
        self.assertLess(float(lines.get('abandoned-wedged', 99)), 5,
                        self.say('a stuck worker was not killed at the bound'))
        self.assertEqual('raised', lines.get('cleanup-error'),
                         self.say('a failing cleanup was swallowed'))
        self.assertEqual("[('done', 'b'), ('done', 'c'), ('done', "
                         "'dies-after')]", lines.get('dies-after'),
                         'a worker that died after its result lost the run')
        for name in ('orphan', 'stopped-orphan', 'busy-group',
                     'wedged-group', 'late-group', 'own-group', 'late2-group'):
            self.assertIn(name, lines, self.say('the scenario never started '
                          '%s' % name))
            pid = int(lines[name])
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and self.alive(pid):
                time.sleep(0.1)
            if self.alive(pid):
                os.kill(pid, 9)
                self.fail('a dead worker\'s tests were left running (%s)'
                          % name)
        self.assertEqual(0, self.returncode, 'the scenario script failed: '
                         + self.stderr[-2000:])

    def test_a_second_signal_does_not_cut_the_first_ones_cleanup_short(self):
        import threading
        from unittest import mock
        with mock.patch.object(redproof, '_stopping', threading.Event()), \
                mock.patch.object(redproof, '_live', set()):
            with self.assertRaises(SystemExit):
                redproof.stop(15, None)
            try:
                second = redproof.stop(15, None)
            except SystemExit:
                second = 'raised'
            self.assertIsNone(second, 'a second signal raised into the '
                              'first one\'s cleanup')

    def tmp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return tmp.name

    @staticmethod
    def alive(pid):
        try:
            with open('/proc/%d/stat' % pid) as f:
                return f.read().rsplit(')', 1)[1].split()[0] != 'Z'
        except OSError:
            return False

    def test_an_entry_judged_without_running_has_no_split(self):
        # A worker is forked with whatever the parent's first baseline left
        # in the timing, and printed it for an entry that never ran.
        import threading
        from unittest import mock
        run = types.SimpleNamespace(
            snapshot=types.SimpleNamespace(content={}), timing=threading.local(),
            tracked_writes={}, unchecked={}, largest=0, lowest_available=None)
        run.timing.copy, run.timing.lock, run.timing.pytest = 0.1, 0.0, 0.5
        entry = case()
        with mock.patch.object(redproof, '_job', {
                'run': run, 'cases': {entry.name: entry},
                'baselines': {tuple(redproof.tests_of(entry)): None},
                }):
            result = redproof._entry_task(entry.name)
        self.assertEqual('SPEC', result[1])
        self.assertEqual('', result[4], 'an entry that never ran printed '
                         'another run\'s time split')


class GroupLockTest(unittest.TestCase):

    def test_entries_sharing_an_xdist_group_share_a_lock(self):
        run = types.SimpleNamespace(groups={
            'tests/a_test.py::Flow::test_one': 'callback-port',
            'tests/a_test.py::Flow::test_two': 'callback-port'})
        whole_file = redproof.Run.locks_for(run, ['tests/a_test.py'])
        one_test = redproof.Run.locks_for(
            run, ['tests/a_test.py::Flow::test_two'])
        self.assertEqual(['callback-port'], whole_file, 'a test in the port '
                         'group ran without its lock')
        self.assertEqual(whole_file, one_test)
        self.assertEqual([], redproof.Run.locks_for(
            run, ['tests/a_test.py::Other::test_one']))

    def test_a_group_lock_holds_across_processes(self):
        # The entries run in worker processes, so a thread lock would hold
        # nothing: two workers would bind port 6969 at once.
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        run = types.SimpleNamespace(locks_dir=tmp.name)
        held = redproof.Run.hold_groups(run, ['callback-port'])
        self.addCleanup(lambda: [f.close() for f in held])
        probe = subprocess.run([sys.executable, '-c', '''
import fcntl, os, sys
f = open(os.path.join(sys.argv[1], sys.argv[2]), 'w')
try:
    fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    print('taken')
except BlockingIOError:
    print('held')
''', tmp.name, os.path.basename(held[0].name)], capture_output=True, text=True)
        self.assertEqual('held', probe.stdout.strip(),
                         'another process could take a held group lock')


class PortLockTest(unittest.TestCase):
    '''A port group's lock is held by the test process itself, not only by
    the worker: a worker that died released it while its callback server
    could still hold the port.'''

    def test_the_lock_reaches_the_test(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        work, copy = os.path.join(tmp.name, 'work'), os.path.join(tmp.name, 'c')
        os.makedirs(work)
        os.makedirs(copy)
        script = os.path.join(tmp.name, 'fake_audit.py')
        # The test process checks the descriptor it was handed is the lock
        # file itself, not merely that some descriptor has that number.
        with open(script, 'w') as f:
            f.write('import os, sys\n'
                    'fd, path = int(sys.argv[-2]), sys.argv[-1]\n'
                    'if os.fstat(fd).st_ino == os.stat(path).st_ino:\n'
                    '    open(os.path.join(os.environ["HOME"], "held"), "w")\n')
        run = types.SimpleNamespace(locks_dir=tmp.name)
        held = redproof.Run.hold_groups(run, ['callback-port'])
        self.addCleanup(lambda: [f.close() for f in held])
        fd = held[0].fileno()
        redproof.run_tests(work, copy, [str(fd), held[0].name], script,
                           os.path.join(work, 'record.json'), keep_open=[fd])
        self.assertTrue(os.path.exists(os.path.join(work, 'home', 'held')),
                        'the port lock did not reach the test')

    def test_a_run_hands_its_locks_to_the_test(self):
        import threading
        from unittest import mock
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        snap = snapshot({})
        snap.copy = lambda dest: os.makedirs(dest)
        run = redproof.Run.__new__(redproof.Run)
        run.tmp, run.snapshot, run.audit = tmp.name, snap, 'audit.py'
        run.locks_dir = tmp.name
        run.groups = {'tests/x_test.py::T::test_x': 'callback-port'}
        run.largest, run.lowest_available = 0, None
        run.timing = threading.local()
        seen = []

        def run_tests(work, copy, nodes, audit, record, keep_open=()):
            # Each descriptor handed over is a lock another process cannot
            # take while the test runs.
            for fd in keep_open:
                probe = subprocess.run([sys.executable, '-c', '''
import fcntl, os, sys
with open(sys.argv[1], 'w') as f:
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        print('taken')
    except BlockingIOError:
        print('held')
''', os.readlink('/proc/self/fd/%d' % fd)], capture_output=True, text=True)
                seen.append(probe.stdout.strip())
            return result(0, '1 passed')

        with mock.patch.object(redproof, 'run_tests', run_tests):
            run.run(['tests/x_test.py'])
        self.assertEqual(['held'], seen, 'a run kept its port lock from the '
                         'test')


class ChangedTrackedTest(unittest.TestCase):

    def test_a_tracked_file_left_changed_is_named(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for name, data in (('a.py', b'one\n'), ('b.py', b'two\n')):
            with open(os.path.join(tmp.name, name), 'wb') as f:
                f.write(data)
        s = snapshot({'a.py': b'one\n', 'b.py': b'TWO\n'})
        run = types.SimpleNamespace(snapshot=s)
        self.assertEqual(['b.py'], redproof.Run.changed_tracked(
            run, tmp.name, None), 'a changed tracked file was not named')
        # The mutated file is expected to differ, by exactly its mutation.
        self.assertEqual([], redproof.Run.changed_tracked(
            run, tmp.name, ('b.py', b'two\n')))


class ShardTest(unittest.TestCase):

    def cases(self):
        return [cases.Case('area/entry-%d' % i, 'schwaby/x.py', 'a', 'b',
                           'tests/x_test.py', 'e') for i in range(200)]

    def test_the_slices_of_a_run_cover_every_entry_once(self):
        everything = self.cases()
        for n in (1, 2, 3, 7):
            seen = []
            for k in range(1, n + 1):
                seen += [c.name for c in redproof.select(everything, [], (k, n))]
            self.assertEqual(sorted(c.name for c in everything), sorted(seen),
                             'the slices of %d missed or repeated an entry' % n)

    def test_an_entry_keeps_its_slice_whatever_else_is_selected(self):
        everything = self.cases()
        for name in [c.name for c in everything]:
            # --name matches by substring, so entry-1 also selects entry-1x:
            # the narrowing is what varies here, and the answer must not.
            narrow = redproof.select(everything, [name], (2, 3))
            among = redproof.select(everything, [], (2, 3))
            self.assertEqual(
                name in [c.name for c in among],
                name in [c.name for c in narrow],
                'which slice %s is in depends on the others' % name)

    def test_the_shard_line_names_the_slice_and_the_whole(self):
        everything = self.cases()
        mine = redproof.select(everything, [], (2, 3))
        self.assertEqual(
            'redproof: shard 2/3: %d of 200 entries' % len(mine),
            redproof.shard_line((2, 3), len(mine), len(everything)),
            'the shard line misstates the slice or the whole')

    def test_an_empty_slice_is_not_a_failed_name(self):
        # One entry, three slices: two of them hold nothing.
        one = self.cases()[:1]
        found = [redproof.select(one, ['area/entry-0'], (k, 3))
                 for k in (1, 2, 3)]
        self.assertNotIn(None, found, 'an empty slice was reported as a name '
                         'matching nothing')
        self.assertEqual([0, 0, 1], sorted(len(f) for f in found))
        self.assertIsNone(redproof.select(one, ['nothing-like-it'], (1, 3)))


class PoolSizeTest(unittest.TestCase):

    def args(self, jobs=None):
        return argparse.Namespace(jobs=jobs)

    def test_two_at_a_time_unless_told_to_use_every_core(self):
        with patch.dict(os.environ, {}, clear=True), \
                patch.object(redproof, 'available_memory', lambda: None):
            self.assertEqual(2, redproof.pool_size(self.args(), 0)[0])
        with patch.dict(os.environ, {'REDPROOF_JOBS_ALL': '1'}), \
                patch.object(redproof, 'usable_cpus', lambda: 20), \
                patch.object(redproof, 'available_memory', lambda: None):
            size, reason = redproof.pool_size(self.args(), 0)
        self.assertEqual(20, size)
        self.assertIn('REDPROOF_JOBS_ALL=1', reason)

    def test_memory_bounds_the_pool_and_says_so(self):
        with patch.dict(os.environ, {'REDPROOF_JOBS_ALL': '1'}), \
                patch.object(redproof, 'usable_cpus', lambda: 20), \
                patch.object(redproof, 'available_memory',
                             lambda: redproof.FLOOR + (6 << 30)):
            size, reason = redproof.pool_size(self.args(), 1 << 30)
        self.assertEqual(6, size)
        self.assertIn('memory holds 6', reason)


class CasesTest(unittest.TestCase):

    def test_every_entry_has_a_unique_name_and_an_expectation(self):
        names = [c.name for c in cases.CASES]
        self.assertGreater(len(names), 600)
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual([], [c.name for c in cases.CASES if not c.expect])
