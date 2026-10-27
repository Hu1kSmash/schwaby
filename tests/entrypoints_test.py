'''Each tool run the way it is used: as a script, under ``python -I`` so
neither the working directory nor PYTHONPATH is on the path, from a working
directory outside the checkout. A tool's own tests import it by path, which
proves its functions and not that the file starts: a tool that works only
with the repository root on the path passes every one of them.'''

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, 'tools')


class EntrypointTest(unittest.TestCase):

    # Each tool with a main, and the test that runs it as a script.
    RUN_BY = {
        'light_check.py': 'tests/light_check_test.py, through both hooks',
        'redproof.py': 'test_redproof_runs_as_a_script',
        'redproof_audit.py': 'test_redproof_audit_runs_as_a_script',
        'repeat.py': 'test_repeat_runs_as_a_script',
    }

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.cwd = tmp.name

    def run_tool(self, path, *args, env=None):
        return subprocess.run(
            [sys.executable, '-I', '-B', path, *args], cwd=self.cwd, env=env,
            capture_output=True, text=True, timeout=120)

    def test_every_tool_with_a_main_is_run_as_a_script(self):
        mains = []
        for name in sorted(os.listdir(TOOLS)):
            if name.endswith('.py'):
                with open(os.path.join(TOOLS, name), encoding='utf-8') as f:
                    if re.search(r'^if __name__ == .__main__.:', f.read(),
                                 re.M):
                        mains.append(name)
        self.assertEqual(sorted(self.RUN_BY), mains,
                         'a tool with a main that no test runs as a script, '
                         'or one listed here that has none')
        for name, by in self.RUN_BY.items():
            if by.startswith('test_'):
                self.assertTrue(hasattr(self, by), '%s: no %s' % (name, by))

    def test_redproof_runs_as_a_script(self):
        # From a checkout of its own, since it snapshots the git checkout it
        # sits in: a red-proof's copy of this one is not a checkout. Past its
        # imports, the snapshot and the cases file, to the first thing it
        # can refuse without running an entry.
        checkout = os.path.join(self.cwd, 'checkout')
        os.makedirs(os.path.join(checkout, 'tools'))
        for name in ('redproof.py', 'redproof_cases.py'):
            shutil.copy2(os.path.join(TOOLS, name),
                         os.path.join(checkout, 'tools', name))
        # Nothing from a surrounding git process: run inside a hook, git
        # exports GIT_INDEX_FILE and friends.
        env = {k: v for k, v in os.environ.items()
               if not k.startswith('GIT_')}
        env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM='1')
        for args in (['init', '-q'], ['add', 'tools']):
            subprocess.run(['git', *args], cwd=checkout, env=env,
                           capture_output=True, check=True)
        run = self.run_tool(os.path.join(checkout, 'tools', 'redproof.py'),
                            '--name', 'no-entry-is-called-this', env=env)
        self.assertEqual(2, run.returncode, run.stdout + run.stderr)
        self.assertIn("no entry name contains 'no-entry-is-called-this'",
                      run.stdout, run.stderr)

    def test_redproof_audit_runs_as_a_script(self):
        # Run from the root of a copy, as the harness runs it: the copy's
        # package is the one it reports.
        package = os.path.join(self.cwd, 'schwaby')
        os.mkdir(package)
        open(os.path.join(package, '__init__.py'), 'w').close()
        run = self.run_tool(os.path.join(TOOLS, 'redproof_audit.py'),
                            '--where')
        self.assertEqual(0, run.returncode, run.stdout + run.stderr)
        self.assertEqual(
            os.path.realpath(os.path.join(package, '__init__.py')),
            run.stdout.strip())

    @unittest.skipIf(os.name == 'nt', 'repeat.py runs on POSIX only')
    def test_repeat_runs_as_a_script(self):
        test = os.path.join(self.cwd, 'test_one.py')
        with open(test, 'w') as f:
            f.write('def test_one():\n    pass\n')
        run = self.run_tool(os.path.join(TOOLS, 'repeat.py'),
                            '--runs', '1', test)
        self.assertEqual(0, run.returncode, run.stdout + run.stderr)
        self.assertIn('[1/1] passed', run.stdout)
        self.assertIn('repeat: 0 of 1 runs failed', run.stdout)
