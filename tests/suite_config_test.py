'''The suite's own configuration: how many workers `-n auto` starts, the
limit every test runs under, the Pythons CI runs, and the publish workflow's
check that main holds the release commit.'''

import importlib.util
import os
import subprocess
import tempfile
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

    def test_a_runq_jobs_cores_bound_the_workers(self):
        # A light runq job's unit has a CPU quota process_cpu_count() does
        # not show; RUNQ_CORES says it.
        for value, expected in (('4', 4), (' 4 ', 4),
                                # Above the usable count it does not raise it.
                                ('16', 7),
                                ('', 7), ('four', 7), ('4.5', 7), ('0', 7),
                                ('-2', 7)):
            with self.subTest(RUNQ_CORES=value):
                self.assertEqual(expected, self.workers(fake_os(
                    environ={'RUNQ_CORES': value},
                    process_cpu_count=lambda: 7)))

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
            self.assertIsNone(
                re.search(r'uses:\s*["\']?actions/setup-python', text),
                '%s installs a Python the pin does not choose' % workflow)
            # Every uv setup takes the pinned build, and checks it got it.
            uv = text.count('uses: astral-sh/setup-uv@')
            self.assertEqual(
                uv, text.count('python-version: ${{ steps.pin.outputs.version }}'),
                '%s sets up a Python other than the pinned one' % workflow)
            self.assertEqual(uv, text.count('The interpreter is the pinned build'),
                             '%s does not check its interpreter' % workflow)


@unittest.skipIf(os.name == 'nt', 'the step runs under bash on Linux')
class ReleaseCommitOnMainTest(unittest.TestCase):

    STEP = 'The release commit is on main'

    def script(self):
        # The step's own run block, as the workflow runs it.
        with open(os.path.join(ROOT, '.github', 'workflows', 'publish.yml'),
                  encoding='utf-8') as f:
            lines = f.read().splitlines()
        at = lines.index('      - name: %s' % self.STEP)
        while lines[at].strip() != 'run: |':
            at += 1
        body = []
        for line in lines[at + 1:]:
            if line.strip() and not line.startswith(' ' * 10):
                break
            body.append(line[10:])
        return '\n'.join(body) + '\n'

    def git(self, cwd, *args):
        return subprocess.run(['git', *args], cwd=cwd, env=self.env,
                              capture_output=True, text=True,
                              check=True).stdout.strip()

    def commit(self, cwd, name):
        with open(os.path.join(cwd, name), 'w') as f:
            f.write(name)
        self.git(cwd, 'add', name)
        self.git(cwd, 'commit', '-q', '-m', name)
        return self.git(cwd, 'rev-parse', 'HEAD')

    def test_a_release_commit_main_does_not_hold_is_refused(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith('GIT_')}
        self.env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM='1')
        origin = os.path.join(tmp.name, 'origin')
        os.mkdir(origin)
        self.git(origin, 'init', '-q', '-b', 'main')
        self.git(origin, 'config', 'user.name', 'test')
        self.git(origin, 'config', 'user.email', 'test@example.invalid')
        tagged = self.commit(origin, 'tagged')
        # Main has moved on since, as it does between a tag and its release.
        self.commit(origin, 'later')
        work = os.path.join(tmp.name, 'work')
        self.git(tmp.name, 'clone', '-q', origin, work)
        self.git(work, 'config', 'user.name', 'test')
        self.git(work, 'config', 'user.email', 'test@example.invalid')
        unmerged = self.commit(work, 'unmerged')
        script = self.script()
        for sha, code in ((tagged, 0), (unmerged, 1)):
            with self.subTest(on_main=not code):
                run = subprocess.run(
                    ['bash', '-e', '-o', 'pipefail', '-c', script], cwd=work,
                    env=dict(self.env, GITHUB_SHA=sha),
                    capture_output=True, text=True, timeout=60)
                self.assertEqual(code, run.returncode,
                                 run.stdout + run.stderr)
                if code:
                    self.assertIn('%s is not on main' % sha, run.stdout)
