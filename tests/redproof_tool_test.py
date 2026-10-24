'''The red-proof harness's own decisions: what a mutation does, what keys a
kept verdict, when one holds, and how many runs fit. A harness that keeps a
verdict it should not reports a guard as working after the guard broke, and
nothing else would ever notice.'''

import argparse
import datetime
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
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
    s.index(content)
    s.tracked = set(content)
    return s


def record(opened=(), written=(), listed=(), probed=(), spawned=(),
           connected=(), unresolved=()):
    return {'opened': {**{p: False for p in opened},
                       **{p: True for p in written}},
            'listed': list(listed), 'probed': list(probed),
            'spawned': list(spawned), 'connected': list(connected),
            'unresolved': list(unresolved)}


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


class ClassifierTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = os.path.join(self.tmp.name, 'run1')
        self.copy = os.path.join(self.work, 'copy')
        os.makedirs(self.copy)
        self.c = redproof.Classifier(self.copy, [self.work, self.tmp.name])

    def test_names_a_copy_path_relative_to_the_copy(self):
        self.assertEqual(('copy', 'schwaby/auth.py'), self.c.classify(
            os.path.join(self.copy, 'schwaby', 'auth.py')))
        self.assertEqual(('copy', ''), self.c.classify(self.copy))

    def test_a_path_that_cannot_be_resolved_is_named_so(self):
        # A test probed a path with a NUL in it; realpath raised, and the
        # whole red-proof ended there.
        self.assertEqual(('unresolved',), self.c.classify('/tmp/x\0y'))

    def test_the_runs_own_files_and_the_interpreter_key_nothing(self):
        self.assertEqual(('own',), self.c.classify(
            os.path.join(self.work, 'tmp', 'pytest-1', 'x')))
        self.assertEqual(('interpreter',), self.c.classify(
            os.path.join(self.c.interpreter[0], 'x.py')))

    def test_a_secret_holding_file_is_named_as_one(self):
        for path in ('/home/u/.env', '/home/u/token.json',
                     '/home/u/.config/gh/hosts.yml', '/srv/server.key'):
            self.assertEqual(('secret',), self.c.classify(path), path)
        self.assertEqual(('outside', '/etc/hosts'),
                         self.c.classify('/etc/hosts'))

    def test_directories_above_a_root_are_above_and_others_are_not(self):
        self.assertTrue(self.c.above(self.tmp.name))
        self.assertTrue(self.c.above('/'))
        self.assertFalse(self.c.above('/etc/hosts'))
        self.assertFalse(self.c.above(os.path.join(self.copy, 'schwaby')))


class ReadsTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.copy = os.path.join(self.tmp.name, 'run1', 'copy')
        os.makedirs(self.copy)
        self.c = redproof.Classifier(self.copy, [self.tmp.name])
        self.s = snapshot({'schwaby/x.py': b'a = 1\n',
                           'schwaby/__init__.py': b''})
        self.mutated = ('schwaby/x.py', b'a = 2\n')

    def path(self, rel):
        return os.path.join(self.copy, rel)

    def reads(self, rec):
        return redproof.reads_from(rec, self.c, self.s, self.mutated,
                                   self.s.tracked)

    def test_keys_a_copy_file_by_the_bytes_the_run_found(self):
        keyed, why, _ = self.reads(record(opened=[self.path('schwaby/x.py')]))
        self.assertIsNone(why)
        self.assertEqual(
            {'copy:schwaby/x.py': ('read', 'file:' + redproof.sha(b'a = 2\n'))},
            keyed)

    def test_keys_a_listing_by_names_and_a_probe_by_kind(self):
        keyed, why, _ = self.reads(record(
            listed=[self.path('schwaby')],
            probed=[self.path('schwaby/missing.py'),
                    os.path.dirname(self.tmp.name)]))
        self.assertIsNone(why)
        self.assertEqual({'copy:schwaby', 'copy:schwaby/missing.py'},
                         set(keyed))
        self.assertEqual(('probe', 'absent'), keyed['copy:schwaby/missing.py'])

    def test_a_run_that_started_a_process_or_read_a_secret_is_not_kept(self):
        for rec, reason in (
                (record(spawned=['subprocess.Popen']), 'started a process'),
                (record(connected=["('8.8.8.8', 53)"]), 'beyond loopback'),
                (record(unresolved=['open']), 'could not work out'),
                (record(opened=['/proc/self/status']), 'read /proc'),
                (record(opened=['/home/u/.env']), 'secret'),
                (record(probed=['/tmp/x\0y']), 'cannot be resolved')):
            keyed, why, _ = self.reads(rec)
            self.assertIsNone(keyed, reason)
            self.assertIn(reason, why)

    def test_a_secret_is_not_digested_alongside_other_reads(self):
        keyed, why, _ = self.reads(record(
            opened=['/home/u/token.json', self.path('schwaby/x.py')]))
        self.assertIsNone(keyed)
        self.assertIn('secret', why)

    def test_a_write_to_a_tracked_file_is_reported(self):
        _, why, writes = self.reads(record(
            written=[self.path('schwaby/__init__.py')]))
        self.assertEqual(['schwaby/__init__.py'], writes)
        self.assertIn('wrote', why)

    def test_a_record_holds_until_what_it_read_changes(self):
        keyed, _, _ = self.reads(record(
            opened=[self.path('schwaby/x.py')],
            listed=[self.path('schwaby')]))
        kept = {'mutated': keyed, 'baseline': {}}
        self.assertTrue(redproof.holds(kept, self.s, self.mutated))
        # The same file with a different mutation is a different read.
        self.assertFalse(redproof.holds(
            kept, self.s, ('schwaby/x.py', b'a = 3\n')))
        grown = snapshot({**self.s.content, 'schwaby/new.py': b''})
        self.assertFalse(redproof.holds(kept, grown, self.mutated))

    def test_what_only_the_baseline_read_keys_the_record_too(self):
        # The mutated run failed before reading the fixture; the passing
        # baseline read it. Change the fixture so the test fails unmutated,
        # and the record must stop holding.
        s = snapshot({**self.s.content, 'tests/data.txt': b'1\n'})
        mutated, _, _ = self.reads(record(opened=[self.path('schwaby/x.py')]))
        baseline, why, _ = redproof.reads_from(
            record(opened=[self.path('schwaby/x.py'),
                           self.path('tests/data.txt')]),
            self.c, s, None, s.tracked)
        self.assertIsNone(why)
        kept = {'mutated': mutated, 'baseline': baseline}
        self.assertTrue(redproof.holds(kept, s, self.mutated))
        broken = snapshot({**s.content, 'tests/data.txt': b'2\n'})
        self.assertFalse(redproof.holds(kept, broken, self.mutated),
                         'a record held after a file only the baseline read '
                         'had changed')

    def test_a_secret_outside_the_copy_never_holds(self):
        self.assertFalse(redproof.holds(
            {'mutated': {'/home/u/.env': ('read', 'file:x')}, 'baseline': {}},
            self.s, self.mutated))


class FakeRun:
    '''Stands in for redproof.Run in judge(): a snapshot, and run() handing
    back a canned result.'''

    def __init__(self, content, result, audit=None, classifier=None):
        self.snapshot = snapshot(content)
        self.result, self.audit, self.classifier = result, audit, classifier
        self.tracked_writes = {}
        self.ran = []

    def run(self, nodes, mutation=None, record=False):
        self.ran.append((nodes, mutation))
        return self.result, self.audit, self.classifier, []


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
        verdict, detail, _ = self.judge(self.ECHOED)
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
        verdict, detail, _ = self.judge(self.ECHOED,
                                        baseline=result(1, 'FAILED'))
        self.assertEqual('SPEC', verdict, 'an entry was judged on a test that '
                         'fails unmutated')
        self.assertIn('does not pass before the mutation', detail)

    def test_a_run_with_no_exit_status_proves_nothing(self):
        # Killed after printing the very failure the entry expects: still no
        # verdict, because a run that did not finish proves nothing.
        verdict, detail, _ = self.judge('E   AssertionError: expected',
                                        code=None)
        self.assertEqual('RED?', verdict, 'a timed-out run was judged')

    def test_a_pass_is_green_unless_superseded(self):
        self.assertEqual('GREEN', self.judge('1 passed', code=0)[0])
        self.assertEqual('SUPERSEDED', self.judge(
            '1 passed', code=0, superseded='covered elsewhere')[0])

    def test_a_record_needs_both_runs_to_be_keepable(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        copy = os.path.join(tmp.name, 'run1', 'copy')
        os.makedirs(copy)
        c = case()
        audit = record(opened=[os.path.join(copy, 'schwaby', 'x.py')])
        run = FakeRun(self.CONTENT, result(1, 'E   expected'), audit,
                      redproof.Classifier(copy, [tmp.name]))
        self.assertIsNone(redproof.judge(run, c, self.PASSED, None)[2],
                          'kept without the baseline being keepable')
        kept = redproof.judge(run, c, self.PASSED, {})[2]
        self.assertEqual({'mutated', 'baseline'}, set(kept))

    def test_a_restored_write_to_a_tracked_file_is_reported(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        copy = os.path.join(tmp.name, 'run1', 'copy')
        os.makedirs(copy)
        audit = record(written=[os.path.join(copy, 'schwaby', 'x.py')])
        run = FakeRun(self.CONTENT, result(1, 'E   expected'), audit,
                      redproof.Classifier(copy, [tmp.name]))
        redproof.judge(run, case(), self.PASSED, {})
        self.assertEqual({'x/y': ['schwaby/x.py']}, run.tracked_writes,
                         'a write the run restored went unreported')


class BaselineTest(unittest.TestCase):

    def test_a_baselines_restored_write_is_reported(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        copy = os.path.join(tmp.name, 'run1', 'copy')
        os.makedirs(copy)
        audit = record(written=[os.path.join(copy, 'schwaby', 'x.py')])
        run = FakeRun(JudgeTest.CONTENT, result(0, '1 passed'), audit,
                      redproof.Classifier(copy, [tmp.name]))
        _, reads = redproof.run_baseline(run, ['tests/x_test.py'])
        self.assertEqual(
            {'the baseline of tests/x_test.py': ['schwaby/x.py']},
            run.tracked_writes, 'a baseline that wrote a tracked file and '
            'restored it went unreported')
        self.assertIsNone(reads)


class SnapshotTest(unittest.TestCase):

    def test_secret_directories_at_the_root_are_left_out(self):
        files = ['.config/gh/hosts.yml', '.ssh/known_hosts',
                 '.gnupg/pubring.kbx', 'docs/config.rst']
        self.assertEqual(files[:3], redproof.secret_untracked(files, set()),
                         'a secret directory at the root was copied')

    def test_untracked_secret_names_are_left_out(self):
        files = ['README.md', '.env.local', 'docs/api-credentials.rst',
                 'notes/token-notes.md', 'tests/secret_logging_test.py']
        tracked = {'README.md', 'tests/secret_logging_test.py'}
        self.assertEqual(
            ['.env.local', 'docs/api-credentials.rst', 'notes/token-notes.md'],
            redproof.secret_untracked(files, tracked),
            'an untracked secret-named file would be copied and digested')


class KeyTest(unittest.TestCase):

    def content(self, **changes):
        import platform
        content = {'tools/redproof.py': b'harness', redproof.AUDIT: b'audit',
                   'requirements.lock': b'a==1\n',
                   '.python-versions': (platform.python_version() + '\n')
                   .encode()}
        content.update(changes)
        return content

    def test_the_key_names_the_lock_the_harness_and_the_pinned_build(self):
        import platform
        key = redproof.entry_key(case(), snapshot(self.content()))
        major, minor, patch = platform.python_version().split('.')[:3]
        bumped = '%s.%s.%d\n' % (major, minor, int(patch) + 1)
        for what, changes in (
                ('lock', {'requirements.lock': b'a==2\n'}),
                ('harness', {'tools/redproof.py': b'harness, edited'}),
                ('audit hook', {redproof.AUDIT: b'audit, edited'}),
                ('pin', {'.python-versions': bumped.encode()})):
            self.assertNotEqual(
                key, redproof.entry_key(case(), snapshot(self.content(
                    **changes))), 'the key ignores the %s' % what)
        # Nothing installs the standard library, so no RECORD holds it.
        from unittest import mock
        with mock.patch.object(redproof, 'stdlib_digest',
                               return_value='edited'):
            self.assertNotEqual(key, redproof.entry_key(case(), snapshot(
                self.content())), 'the key ignores the standard library')
        # The positive control: nothing changed, the same key.
        self.assertEqual(key, redproof.entry_key(case(), snapshot(
            self.content())))

    def test_a_set_other_than_the_lock_keeps_nothing(self):
        import platform
        why = redproof.environment_matches_lock(snapshot({
            '.python-versions': (platform.python_version() + '\n').encode(),
            'requirements.lock': b'no-such-package-anywhere==1.0\n'}))
        if sys.version_info >= (3, 11):
            self.assertIn('differ from requirements.lock', why or '',
                          'an environment unlike the lock was accepted')


class InstalledFilesTest(unittest.TestCase):

    class File:
        def __init__(self, rel, mode, value):
            self.rel, self.name = rel, rel.rsplit('/', 1)[-1]
            self.hash = types.SimpleNamespace(mode=mode, value=value) \
                if mode else None

        def __str__(self):
            return self.rel

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = tmp.name

    def dist(self, name, files, mode='sha256'):
        '''files: (rel, bytes on disk or None for absent, bytes recorded or
        None for no hash).'''
        import base64
        import hashlib
        recorded = []
        for rel, data, recorded_data in files:
            if data is not None:
                with open(os.path.join(self.root, rel), 'wb') as f:
                    f.write(data)
            value = None if recorded_data is None else \
                base64.urlsafe_b64encode(hashlib.new(
                    mode, recorded_data).digest()).rstrip(b'=').decode()
            recorded.append(self.File(rel, recorded_data is not None and mode,
                                      value))
        return types.SimpleNamespace(
            metadata={'Name': name}, files=recorded,
            locate_file=lambda f: os.path.join(self.root, f.rel))

    def differ(self, *dists):
        return redproof.installed_files_differ(dists, roots=[self.root])

    def test_a_file_edited_at_the_same_version_is_caught(self):
        intact = self.dist('intact', [('a.py', b'x', b'x')])
        self.assertIsNone(self.differ(intact))
        edited = self.dist('edited', [('b.py', b'y = 2', b'y = 1')])
        why = self.differ(intact, edited)
        self.assertEqual('edited: b.py differs from its RECORD', why,
                         'an installed file edited in place was not noticed')

    def test_pip_is_held_to_its_record(self):
        # Neither pip nor schwaby is skipped: schwaby's editable finder and
        # .pth run at every interpreter start.
        self.assertIsNone(self.differ(self.dist('pip', [('c.py', b'z', b'z')])))
        self.assertEqual('pip: d.py differs from its RECORD', self.differ(
            self.dist('pip', [('d.py', b'z = 2', b'z = 1')])),
            'an edited pip file was not checked')

    def test_a_missing_file_is_caught(self):
        self.assertEqual('gone: e.py is missing', self.differ(
            self.dist('gone', [('e.py', None, b'x')])),
            'an installed file that is gone was not noticed')

    def test_a_distribution_with_no_record_keeps_nothing(self):
        bare = types.SimpleNamespace(metadata={'Name': 'bare'}, files=None)
        self.assertEqual('bare has no RECORD, so its files cannot be checked',
                         self.differ(bare), 'a distribution with no RECORD '
                         'was taken as intact')

    def test_only_record_and_pyc_may_go_unhashed(self):
        self.assertIsNone(self.differ(self.dist('ok', [
            ('f.py', b'x', b'x'), ('RECORD', b'', None),
            ('g.cpython-314.pyc', b'', None)])))
        self.assertEqual('unhashed: h.py has no hash in its RECORD',
                         self.differ(self.dist('unhashed', [
                             ('h.py', b'x', None)])),
                         'a source file listed without a hash was not '
                         'refused')

    def test_a_hash_it_does_not_check_keeps_nothing(self):
        self.assertIsNone(self.differ(self.dist(
            'strong', [('i.py', b'x', b'x')], mode='sha512')))
        self.assertEqual('weak: j.py is hashed with sha1, which is not '
                         'checked', self.differ(self.dist(
                             'weak', [('j.py', b'x', b'x')], mode='sha1')),
                         'a file hashed with an unchecked algorithm was '
                         'taken as intact')

    def test_a_file_no_record_lists_is_caught(self):
        owned = self.dist('owned', [('k.py', b'x', b'x')])
        self.assertIsNone(self.differ(owned))
        with open(os.path.join(self.root, 'zz_stray.pth'), 'w') as f:
            f.write('import os\n')
        self.assertIn('zz_stray.pth is in ', self.differ(owned) or '',
                      'a file no RECORD lists went unnoticed')
        # Except compiled bytecode, which nothing hashes.
        os.remove(os.path.join(self.root, 'zz_stray.pth'))
        os.mkdir(os.path.join(self.root, '__pycache__'))
        open(os.path.join(self.root, '__pycache__', 'k.pyc'), 'wb').close()
        self.assertIsNone(self.differ(owned))

    @unittest.skipIf(sys.version_info < (3, 11), 'kept verdicts need 3.11')
    def test_the_gate_holds_installed_files_to_their_records(self):
        # The gate itself, not only the function: the real pin and lock,
        # as installed, reach the check.
        import platform
        from importlib import metadata
        from unittest import mock
        installed = {redproof.normalize(d.metadata['Name']): d.version
                     for d in metadata.distributions()
                     if d.metadata['Name']}
        for own in ('pip', 'schwaby'):
            installed.pop(own, None)
        pinned = snapshot({'.python-versions': (
            platform.python_version() + '\n').encode()})
        with mock.patch.object(redproof, 'lock_pins', return_value=installed):
            with mock.patch.object(redproof, 'installed_files_differ',
                                   return_value='x: y.py differs'):
                why = redproof.environment_matches_lock(pinned)
            self.assertEqual('an installed file is not what was installed: '
                             'x: y.py differs', why,
                             'the gate does not check installed files')
            # The positive control: the same gate, this environment as it
            # is, passes the check.
            with mock.patch.object(redproof, 'installed_files_differ',
                                   return_value=None):
                self.assertIsNone(redproof.environment_matches_lock(pinned))


class GroupLockTest(unittest.TestCase):

    def test_entries_sharing_an_xdist_group_share_a_lock(self):
        run = types.SimpleNamespace(group_locks={}, groups={
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


class LockTest(unittest.TestCase):

    def test_only_the_pins_whose_marker_holds_here_count(self):
        s = snapshot({'requirements.lock': (
            "# a comment\n"
            "a==2 ; python_version >= '3.0'\n"
            "a==1 ; python_version < '3.0'\n"
            "B_c==3\n"
            "    # via a\n").encode()})
        self.assertEqual({'a': '2', 'b-c': '3'}, redproof.lock_pins(s),
                         'a pin for another Python was counted')


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


class PinTest(unittest.TestCase):

    def test_only_the_pinned_build_keeps_verdicts(self):
        import platform
        running = platform.python_version()
        major, minor, patch = running.split('.')[:3]
        other = '%s.%s.%d' % (major, minor, int(patch) + 1)
        why = redproof.environment_matches_lock(
            snapshot({'.python-versions': ('3.9.1\n%s\n' % other).encode()}))
        self.assertIn('is not the pinned %s' % other, why or '',
                      'a build other than the pinned one was accepted')
        # The pinned build gets past the pin and is stopped by a later check
        # (no lock here; on 3.10, pathlib): the positive control that the pin
        # is what refused the other.
        why = redproof.environment_matches_lock(
            snapshot({'.python-versions': ('%s\n' % running).encode()}))
        self.assertIsNotNone(why)
        self.assertNotIn('pinned', why)


class StoreTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, 'cache', 'redproof.json')

    def at(self, days_ago):
        return (datetime.datetime.now(datetime.timezone.utc)
                - datetime.timedelta(days=days_ago)).isoformat()

    def test_prunes_by_last_use_and_skips_tombstones(self):
        store = redproof.Store(self.path)
        store.put('k:1', {'at': self.at(61), 'reads': {}})
        store.put('k:2', {'at': self.at(1), 'reads': {}})
        store.put('k:3', {'at': self.at(1), 'gone': True})
        store.prune()
        self.assertEqual({'k:2', 'k:3'}, set(store.records))
        self.assertEqual({'k:2'}, set(store.under('k')))
        self.assertEqual({'k:1'}, store.removed)

    def test_a_pruned_record_is_gone_from_the_file(self):
        store = redproof.Store(self.path)
        store.put('k:old', {'at': self.at(61), 'reads': {}})
        store.put('k:new', {'at': self.at(1), 'reads': {}})
        store.save()
        again = redproof.Store(self.path)
        again.load()
        again.prune()
        again.save()
        with open(self.path, 'rb') as f:
            self.assertEqual({'k:new'}, set(json.loads(f.read())['records']),
                             'a pruned record was saved again')

    def test_a_renewal_does_not_undo_a_tombstone(self):
        first, second = redproof.Store(self.path), redproof.Store(self.path)
        first.load()
        second.load()
        first.put('k:1', {'gone': True, 'at': self.at(1)})
        first.save()
        # Saved later, by a run that only found the record still holding.
        second.put('k:1', {'at': self.at(0), 'proved': self.at(5),
                           'reads': {}})
        second.save()
        third = redproof.Store(self.path)
        third.load()
        self.assertEqual({}, third.under('k'),
                         'a renewal brought back a retired record')
        # A proof made after the tombstone does bring it back.
        third.put('k:1', {'at': self.at(0), 'proved': self.at(0),
                          'reads': {}})
        third.save()
        fourth = redproof.Store(self.path)
        fourth.load()
        self.assertEqual({'k:1'}, set(fourth.under('k')))

    def test_a_tombstone_retires_a_record_already_saved(self):
        # The ordinary case: the file holds the record when the run that
        # disproved it saves.
        seed = redproof.Store(self.path)
        seed.put('k:1', {'at': self.at(2), 'proved': self.at(2), 'reads': {}})
        seed.save()
        disprover = redproof.Store(self.path)
        disprover.load()
        disprover.put('k:1', {'gone': True, 'at': self.at(0)})
        disprover.save()
        after = redproof.Store(self.path)
        after.load()
        self.assertEqual({}, after.under('k'),
                         'a tombstone did not retire the record it named')

    def test_a_record_missing_a_runs_reads_does_not_hold(self):
        s = snapshot({'a.py': b''})
        self.assertFalse(redproof.holds({'mutated': {}}, s, None))
        self.assertTrue(redproof.holds({'mutated': {}, 'baseline': {}}, s,
                                       None))

    def test_a_record_renewed_elsewhere_survives_a_prune(self):
        old = {'at': self.at(61), 'reads': {}}
        seed = redproof.Store(self.path)
        seed.put('k:1', old)
        seed.save()
        pruner = redproof.Store(self.path)
        pruner.load()
        renewer = redproof.Store(self.path)
        renewer.load()
        renewer.put('k:1', dict(old, at=self.at(0)))
        renewer.save()
        pruner.prune()
        pruner.save()
        with open(self.path, 'rb') as f:
            self.assertIn('k:1', json.loads(f.read())['records'],
                          'a prune dropped a record another run had just used')

    def test_saving_merges_onto_what_another_run_saved(self):
        first, second = redproof.Store(self.path), redproof.Store(self.path)
        first.load()
        second.load()
        first.put('a:1', {'at': self.at(0), 'reads': {}})
        first.save()
        second.put('b:1', {'at': self.at(0), 'reads': {}})
        second.save()
        with open(self.path, 'rb') as f:
            records = json.loads(f.read())['records']
        self.assertEqual({'a:1', 'b:1'}, set(records))


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
