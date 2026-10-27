'''tools/light_check.py, against a throwaway repository: a commit that adds a
warning is refused, and one that only moves code or keeps an old warning is
not.'''

import os
import shlex
import shutil
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
        # LF on every platform: written in text mode on Windows, each line
        # ends in CRLF, which `git diff --check` reports as trailing
        # whitespace.
        with open(os.path.join(self.repo, name), 'w', newline='\n') as f:
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
        self.assertIn("'sys' imported but unused", out)

    def test_a_renamed_file_keeps_its_old_warnings(self):
        self.git('mv', 'mod.py', 'renamed.py')
        code, out = self.check()
        self.assertEqual(0, code, 'a rename was blamed for the warnings the '
                         'file already had: ' + out)
        # The positive control: a rename that adds one is still caught.
        self.write('renamed.py', 'import os\nimport sys\n\n\ndef f():\n'
                   '    return 1\n')
        self.git('add', 'renamed.py')
        code, out = self.check()
        self.assertEqual(1, code, out)
        self.assertIn("'sys' imported but unused", out)

    def test_bytes_that_are_not_utf8_do_not_parse(self):
        with open(os.path.join(self.repo, 'mod.py'), 'wb') as f:
            f.write(b'x = "caf\xe9"\n')
        self.git('add', 'mod.py')
        code, out = self.check()
        self.assertEqual(1, code, 'a file Python cannot decode was let '
                         'through: ' + out)
        self.assertIn('syntax error', out)

    def test_code_that_parses_but_does_not_compile_is_refused(self):
        # pyflakes parses; the compiler refuses this, and so would import.
        self.stage('mod.py', 'import os\n\n\ndef f():\n    await os.g()\n')
        code, out = self.check()
        self.assertEqual(1, code, 'a file Python cannot compile was let '
                         'through: ' + out)
        self.assertIn("'await' outside async function", out)

    def test_a_syntax_error_already_at_head_still_fails(self):
        self.write('broken.py', 'def f(:\n')
        self.git('add', 'broken.py')
        self.git('commit', '-q', '--no-verify', '-m', 'broken')
        self.stage('broken.py', 'def f(:\n\n# edited\n')
        code, out = self.check()
        self.assertEqual(1, code, 'a file that still does not parse was let '
                         'through: ' + out)
        self.assertIn('broken.py: syntax error', out)

    def test_a_warning_that_names_a_line_is_the_same_when_moved(self):
        self.stage('dup.py', 'import os\nimport os\n')
        self.git('commit', '-q', '--no-verify', '-m', 'dup')
        self.stage('dup.py', '# a line above it\nimport os\nimport os\n')
        code, out = self.check()
        self.assertEqual(0, code, 'a warning naming a line was blamed on the '
                         'edit that moved it: ' + out)

    @unittest.skipIf(os.name == 'nt', 'a symlink on Windows needs a '
                     'privilege and core.symlinks, which these repos turn off')
    def test_a_symlink_replaced_by_a_file_is_checked(self):
        os.symlink('mod.py', os.path.join(self.repo, 'link.py'))
        self.git('add', 'link.py')
        self.git('commit', '-q', '--no-verify', '-m', 'link')
        os.remove(os.path.join(self.repo, 'link.py'))
        self.stage('link.py', 'import sys\n')
        code, out = self.check()
        self.assertEqual(1, code, 'a file that was a symlink went unchecked: '
                         + out)
        self.assertIn("'sys' imported but unused", out)

    @unittest.skipIf(os.name == 'nt', 'a symlink on Windows needs a '
                     'privilege and core.symlinks, which these repos turn off')
    def test_a_symlink_is_not_checked_as_code(self):
        os.symlink('mod.py', os.path.join(self.repo, 'link.py'))
        self.git('add', 'link.py')
        code, out = self.check()
        self.assertEqual(0, code, 'a symlink was checked as code: ' + out)

    def test_a_warning_only_in_the_working_tree_is_not_the_commits(self):
        # The other direction from the test above: staged clean, dirty on
        # disk. The commit carries the clean one.
        self.stage('mod.py', 'import os\n\n\ndef f():\n    return 2\n')
        self.write('mod.py', 'import os\nimport sys\n\n\ndef f():\n'
                   '    return 2\n')
        code, out = self.check()
        self.assertEqual(0, code, 'the working tree was checked instead of '
                         'the index: ' + out)

    def test_both_hooks_run_the_check(self):
        for hook in ('pre-commit', 'pre-merge-commit'):
            with open(os.path.join(HOOKS, hook)) as f:
                self.assertIn('tools/light_check.py', f.read(), hook)
            self.assertTrue(os.access(os.path.join(HOOKS, hook), os.X_OK),
                            '%s is not executable' % hook)

    def hook_checkout(self):
        # The hooks as git runs them, from a checkout's tools/.
        os.makedirs(os.path.join(self.repo, 'tools', 'hooks'))
        for name in ['light_check.py'] + ['hooks/' + h
                                          for h in os.listdir(HOOKS)]:
            shutil.copy2(os.path.join(ROOT, 'tools', name),
                         os.path.join(self.repo, 'tools', name))
        # Empty, so a hook that misses every stand-in fails by name.
        self.ran = os.path.join(self.repo, '.git', 'interpreters')
        open(self.ran, 'w').close()

    def stand_in(self, where, label):
        # The interpreter running this test, which has pyflakes, under the
        # name a hook looks for, noting each time it is the one that ran: a
        # hook that missed it would find another python3, which may have
        # pyflakes too and pass the same way.
        os.makedirs(os.path.join(where, 'bin'), exist_ok=True)
        python = os.path.join(where, 'bin', 'python')
        with open(python, 'w') as f:
            f.write('#!/bin/sh\necho %s >> %s\nexec %s "$@"\n' % (
                label, shlex.quote(self.ran), shlex.quote(sys.executable)))
        os.chmod(python, 0o755)

    def last_ran(self):
        with open(self.ran) as f:
            ran = f.read().split()
        return ran[-1] if ran else None

    @unittest.skipIf(os.name == 'nt', 'the hooks are sh scripts, and a test '
                     'cannot tell which interpreter Git for Windows finds')
    def test_each_hook_takes_the_interpreter_it_names(self):
        # The last one present wins: venv, then .venv at the checkout's root,
        # then the active virtualenv.
        self.hook_checkout()
        active = tempfile.TemporaryDirectory()
        self.addCleanup(active.cleanup)
        env = dict(self.env)
        env.pop('VIRTUAL_ENV', None)
        for hook in sorted(os.listdir(HOOKS)):
            for name in ('venv', '.venv'):
                shutil.rmtree(os.path.join(self.repo, name),
                              ignore_errors=True)
            steps = (('venv', os.path.join(self.repo, 'venv'), None),
                     ('.venv', os.path.join(self.repo, '.venv'), None),
                     ('active', active.name, active.name))
            for label, where, venv in steps:
                with self.subTest(hook=hook, interpreter=label):
                    self.stand_in(where, label)
                    if venv:
                        env['VIRTUAL_ENV'] = venv
                    out = subprocess.run(
                        [os.path.join(self.repo, 'tools', 'hooks', hook)],
                        cwd=self.repo, env=env, capture_output=True,
                        text=True)
                    self.assertEqual(0, out.returncode,
                                     out.stdout + out.stderr)
                    self.assertEqual(label, self.last_ran(),
                                     '%s ran another interpreter' % hook)
            env.pop('VIRTUAL_ENV')

    @unittest.skipIf(os.name == 'nt', 'the hooks are sh scripts, and a test '
                     'cannot tell which interpreter Git for Windows finds')
    def test_a_commit_or_merge_through_the_hooks_is_refused_and_named(self):
        # Through git itself: the check's answer is the commit's or the
        # merge's, refusing one that adds a warning and letting a clean one
        # through.
        self.hook_checkout()
        venv = tempfile.TemporaryDirectory()
        self.addCleanup(venv.cleanup)
        self.stand_in(venv.name, 'active')
        self.env['VIRTUAL_ENV'] = venv.name

        # Two side branches, one adding a warning, committed before the
        # hooks are on.
        for branch, text in (('side', 'import sys\n'),
                             ('clean', 'def g():\n    return 4\n')):
            self.git('checkout', '-q', '-b', branch)
            self.stage(branch + '.py', text)
            self.git('commit', '-q', '-m', branch)
            self.git('checkout', '-q', '-')
        self.stage('other.py', 'def h():\n    return 3\n')
        self.git('commit', '-q', '-m', 'other')
        self.git('config', 'core.hooksPath', 'tools/hooks')
        head = self.git('rev-parse', 'HEAD').stdout

        def run(*args):
            out = subprocess.run(['git', *args], cwd=self.repo, env=self.env,
                                 capture_output=True, text=True)
            return out.returncode, out.stdout + out.stderr

        self.stage('mod.py', 'import os\nimport sys\n\n\ndef f():\n'
                   '    return 1\n')
        code, out = run('commit', '-q', '-m', 'warning')
        self.assertNotEqual(0, code, 'pre-commit let a warning through: '
                            + out)
        self.assertIn("mod.py: 'sys' imported but unused", out)
        self.assertEqual(head, self.git('rev-parse', 'HEAD').stdout)
        self.assertEqual('active', self.last_ran())

        # A clean commit goes through: the refusal above was the check's,
        # not a hook that refuses everything.
        self.stage('mod.py', 'import os\n\n\ndef f():\n    return 2\n')
        code, out = run('commit', '-q', '-m', 'clean')
        self.assertEqual(0, code, 'pre-commit refused a clean commit: ' + out)
        head = self.git('rev-parse', 'HEAD').stdout

        code, out = run('merge', '-q', '--no-ff', '-m', 'merge', 'side')
        self.assertNotEqual(0, code, 'pre-merge-commit let a warning '
                            'through: ' + out)
        self.assertIn("side.py: 'sys' imported but unused", out)
        self.assertEqual(head, self.git('rev-parse', 'HEAD').stdout)
        open(self.ran, 'w').close()
        self.git('merge', '--abort')

        # And a clean merge goes through, by way of the same hook.
        code, out = run('merge', '-q', '--no-ff', '-m', 'merge', 'clean')
        self.assertEqual(0, code, 'pre-merge-commit refused a clean merge: '
                         + out)
        self.assertEqual('active', self.last_ran())
