'''tools/repeat.py's reading of pytest's summary: a flake is counted by the
test it names, so a reading that loses the name loses the measurement.'''

import importlib.util
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location(
    'repeat', os.path.join(ROOT, 'tools', 'repeat.py'))
repeat = importlib.util.module_from_spec(spec)
spec.loader.exec_module(repeat)


class FailuresTest(unittest.TestCase):

    def test_names_every_failed_and_erroring_test_once(self):
        output = '\n'.join([
            '..F.E',
            '=========================== short test summary info ===',
            'FAILED tests/auth_test.py::T::test_a - AssertionError: boom',
            'ERROR tests/auth_test.py::T::test_b - RuntimeError',
            'FAILED tests/auth_test.py::T::test_a - AssertionError: again',
            '1 failed, 1 error, 3 passed in 1.23s',
        ])
        self.assertEqual(
            ['tests/auth_test.py::T::test_a', 'tests/auth_test.py::T::test_b'],
            repeat.failures(output), 'a failed test was not named')

    def test_a_passing_run_names_nothing(self):
        # The positive case above is what makes this one mean anything.
        self.assertEqual([], repeat.failures('.....\n5 passed in 0.10s\n'))
