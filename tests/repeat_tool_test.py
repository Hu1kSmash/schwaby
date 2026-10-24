'''tools/repeat.py counts a flake by the test it names, so a run that loses
or invents a name loses the measurement. These drive real pytest runs
through it.'''

import importlib.util
import os
import tempfile
import textwrap
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location(
    'repeat', os.path.join(ROOT, 'tools', 'repeat.py'))
repeat = importlib.util.module_from_spec(spec)
spec.loader.exec_module(repeat)

# Every way a test can fail that the summary lines made ambiguous: a subTest
# with a positional label and with a ')' in its value, a parametrize id with
# ' - ' in it, and a passing test whose own output looks like a failure.
TESTS = textwrap.dedent('''\
    import logging
    import unittest

    import pytest


    class T(unittest.TestCase):

        def test_positional(self):
            with self.subTest('custom msg'):
                self.assertEqual(1, 2)

        def test_kw(self):
            with self.subTest(v='a)b'):
                self.assertEqual(1, 2)

        def test_prints_like_a_failure(self):
            logging.getLogger('x').error('FAILED to connect - ERROR thing')
            print('FAILED tests/nothing_test.py::nothing - not a test')


    @pytest.mark.parametrize('p', ['a - b'])
    def test_param(p):
        assert False


    def test_passes():
        pass
''')


@unittest.skipIf(os.name == 'nt', 'repeat.py runs on POSIX only')
class RunOnceTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = os.path.join(self.tmp.name, 'work')
        os.makedirs(self.work)
        with open(os.path.join(self.work, repeat.PLUGIN_NAME + '.py'),
                  'w') as f:
            f.write(repeat.PLUGIN)

    def run_file(self, name, text):
        path = os.path.join(self.tmp.name, name)
        with open(path, 'w') as f:
            f.write(text)
        return repeat.run_once([path], self.work)

    def test_every_failing_test_is_named_once_and_nothing_else(self):
        ok, failed, _, _, why = self.run_file('x_test.py', TESTS)
        self.assertEqual(
            ['T::test_kw', 'T::test_positional', 'test_param[a - b]'],
            sorted(f.split('x_test.py::')[1] for f in failed),
            'a failing test was not named, or a passing one was')
        self.assertFalse(ok)
        self.assertIsNone(why)

    def test_a_collection_error_is_named(self):
        ok, failed, _, _, _ = self.run_file('broken_test.py', 'def f(:\n')
        self.assertFalse(ok)
        self.assertEqual(1, len(failed), 'a collection error was not named')
        self.assertTrue(failed[0].endswith('broken_test.py'), failed)

    def test_a_passing_run_names_nothing(self):
        # The positive cases above are what make this one mean anything.
        ok, failed, _, _, why = self.run_file('ok_test.py',
                                              'def test_ok():\n    pass\n')
        self.assertEqual((True, [], None), (ok, failed, why))
