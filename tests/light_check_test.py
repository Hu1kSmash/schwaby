'''tools/light_check.py, against a throwaway repository: a commit that adds a
warning is refused, and one that only moves code or keeps an old warning is
not.'''

import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHECK = os.path.join(ROOT, 'tools', 'light_check.py')
HOOKS = os.path.join(ROOT, 'tools', 'hooks')


class LightCheckTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = self.tmp.name
        # Nothing from a surrounding git process: run inside a hook, git
        # exports GIT_INDEX_FILE and friends, and they would point these
        # commands at the outer repository.
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith('GIT_')}
        self.env.update(GIT_CONFIG_GLOBAL=os.devnull,
                        GIT_CONFIG_NOSYSTEM='1')
        self.git('init', '-q')
        self.git('config', 'user.name', 'test')
        self.git('config', 'user.email', 'test@example.invalid')
        self.write('mod.py', 'import os\n\n\ndef f():\n    return 1\n')
        self.git('add', 'mod.py')
        self.git('commit', '-q', '-m', 'start')

    def git(self, *args):
        return subprocess.run(['git', *args], cwd=self.repo, env=self.env,
                              capture_output=True, check=True)

    def write(self, name, text):
        with open(os.path.join(self.repo, name), 'w') as f:
            f.write(text)

    def check(self):
        out = subprocess.run([sys.executable, CHECK], cwd=self.repo,
                             env=self.env, capture_output=True, text=True)
        return out.returncode, out.stdout

    def stage(self, name, text):
        self.write(name, text)
        self.git('add', name)

    def test_a_new_warning_is_refused_and_named(self):
        self.stage('mod.py', 'import os\nimport sys\n\n\ndef f():\n'
                   '    return 1\n')
        code, out = self.check()
        self.assertEqual(1, code, 'a new warning was let through: ' + out)
        self.assertIn("mod.py: 'sys' imported but unused", out)
        self.assertNotIn("'os'", out, 'a warning HEAD already had was blamed')

    def test_a_warning_moved_or_kept_is_not_new(self):
        self.stage('mod.py', '\n\nimport os\n\n\ndef f():\n    return 2\n')
        code, out = self.check()
        self.assertEqual(0, code, 'an old warning was blamed on this commit: '
                         + out)

    def test_a_new_file_is_checked_whole(self):
        self.stage('new.py', 'def g():\n    return undefined_name\n')
        code, out = self.check()
        self.assertEqual(1, code, 'a new file was not checked: ' + out)
        self.assertIn("new.py: undefined name 'undefined_name'", out)

    def test_a_file_that_does_not_parse_is_refused(self):
        self.stage('mod.py', 'def f(:\n')
        code, out = self.check()
        self.assertEqual(1, code, 'a file that does not parse was let '
                         'through: ' + out)
        self.assertIn('mod.py: syntax error', out)

    def test_whitespace_errors_are_refused(self):
        self.stage('notes.txt', 'trailing   \n')
        code, out = self.check()
        self.assertEqual(1, code, 'a whitespace error was let through: ' + out)
        self.assertIn('trailing whitespace', out)

    def test_the_index_is_checked_not_the_working_tree(self):
        self.stage('mod.py', 'import os\nimport sys\n\n\ndef f():\n'
                   '    return 1\n')
        # Fixed on disk but not staged: the commit would still carry it.
        self.write('mod.py', 'import os\n\n\ndef f():\n    return 1\n')
        code, out = self.check()
        self.assertEqual(1, code, 'the working tree was checked instead of '
                         'the index: ' + out)

    def test_both_hooks_run_the_check(self):
        for hook in ('pre-commit', 'pre-merge-commit'):
            with open(os.path.join(HOOKS, hook)) as f:
                self.assertIn('tools/light_check.py', f.read(), hook)
            self.assertTrue(os.access(os.path.join(HOOKS, hook), os.X_OK),
                            '%s is not executable' % hook)
