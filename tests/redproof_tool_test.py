'''The red-proof harness's own decisions: what a mutation does, what keys a
kept verdict, when one holds, and how many runs fit. A harness that keeps a
verdict it should not reports a guard as working after the guard broke, and
nothing else would ever notice.'''

import argparse
import datetime
import importlib.util
import json
import os
import tempfile
import unittest
from unittest.mock import patch

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
                (record(opened=['/home/u/.env']), 'secret')):
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
        self.assertTrue(redproof.holds(keyed, self.s, self.mutated))
        # The same file with a different mutation is a different read.
        self.assertFalse(redproof.holds(
            keyed, self.s, ('schwaby/x.py', b'a = 3\n')))
        grown = snapshot({**self.s.content, 'schwaby/new.py': b''})
        self.assertFalse(redproof.holds(keyed, grown, self.mutated))


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
        # The pinned build gets past the pin, and stops at the missing lock:
        # the positive control that the pin is what refused the other.
        why = redproof.environment_matches_lock(
            snapshot({'.python-versions': ('%s\n' % running).encode()}))
        self.assertNotIn('pinned', why)
        self.assertIn('requirements.lock', why)


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
