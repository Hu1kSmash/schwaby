'''The suite's own configuration: how many workers `-n auto` starts, and the
limit every test runs under.'''

import importlib.util
import os
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_conftest():
    spec = importlib.util.spec_from_file_location(
        'conftest_under_test', os.path.join(ROOT, 'tests', 'conftest.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fake_os(environ=None, **calls):
    # Only what the hook may consult; a missing attribute is a Python
    # without it.
    return types.SimpleNamespace(environ=environ or {}, **calls)


class AutoWorkersTest(unittest.TestCase):

    def setUp(self):
        self.conftest = load_conftest()

    def workers(self, os_module):
        self.conftest.os = os_module
        return self.conftest.pytest_xdist_auto_num_workers(None)

    def test_auto_is_the_usable_core_count_not_the_machines(self):
        self.assertEqual(7, self.workers(fake_os(
            process_cpu_count=lambda: 7, sched_getaffinity=lambda pid: {0},
            cpu_count=lambda: 99)),
            '-n auto did not start one worker per usable core')

    def test_before_3_13_the_affinity_mask_decides(self):
        self.assertEqual(3, self.workers(fake_os(
            sched_getaffinity=lambda pid: {0, 1, 2}, cpu_count=lambda: 99)),
            '-n auto ignored the affinity mask')

    def test_the_environment_override_still_wins(self):
        self.assertIsNone(self.workers(fake_os(
            environ={'PYTEST_XDIST_AUTO_NUM_WORKERS': '3'},
            process_cpu_count=lambda: 7)),
            'the hook answered over PYTEST_XDIST_AUTO_NUM_WORKERS')


def test_every_test_runs_under_a_time_limit(pytestconfig):
    # Asked of the running pytest rather than read from setup.cfg: the limit
    # exists only if the plugin that reads it is loaded.
    assert pytestconfig.pluginmanager.hasplugin('timeout'), (
        'pytest-timeout is not loaded, so no test has a time limit')
    limit = float(pytestconfig.getini('timeout') or 0)
    # Under the login flow's five-minute callback timeout, so a test hung on
    # a lost port fails by name before that timeout says something vaguer.
    assert 0 < limit < 300, 'the per-test time limit is %r' % limit


class PythonPinsTest(unittest.TestCase):
    '''.python-versions pins one exact release of every supported Python, so
    every machine that runs the suite runs the same builds.'''

    def read(self, *path):
        with open(os.path.join(ROOT, *path), encoding='utf-8') as f:
            return f.read()

    def pins(self):
        return self.read('.python-versions').split()

    def supported(self):
        import re
        return sorted(set(re.findall(
            r"'Programming Language :: Python :: (3\.\d+)'",
            self.read('setup.py'))), key=lambda v: tuple(map(int, v.split('.'))))

    def test_every_supported_python_is_pinned_exactly_once(self):
        import re
        pins = self.pins()
        self.assertEqual([], [p for p in pins
                              if not re.fullmatch(r'\d+\.\d+\.\d+', p)],
                         'a pin is not an exact release')
        minors = [p.rsplit('.', 1)[0] for p in pins]
        self.assertEqual(self.supported(), minors,
                         'the pinned Pythons are not the supported ones')
        self.assertIn("python_requires='>=%s'" % minors[0],
                      self.read('setup.py'))

    def test_ci_tests_the_pinned_pythons(self):
        import re
        minors = [p.rsplit('.', 1)[0] for p in self.pins()]
        for workflow in ('python.yml', 'publish.yml', 'fresh.yml'):
            text = self.read('.github', 'workflows', workflow)
            matrices = re.findall(r'python: \[([^\]]*)\]', text)
            self.assertTrue(matrices, workflow)
            for matrix in matrices:
                versions = re.findall(r'"([0-9.]+)"', matrix)
                self.assertLessEqual(set(versions), set(minors),
                                     '%s tests an unpinned Python' % workflow)
            self.assertEqual(
                minors, re.findall(r'"([0-9.]+)"', matrices[0]),
                '%s does not test every pinned Python' % workflow)
            self.assertNotIn('uses: actions/setup-python', text,
                             '%s installs a Python the pin does not '
                             'choose' % workflow)
