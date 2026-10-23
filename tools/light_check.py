'''The light check every commit runs: what can be judged in a second, on what
is about to be committed.

    python tools/light_check.py            # the staged changes against HEAD

Run by tools/hooks/pre-commit and tools/hooks/pre-merge-commit; `git config
core.hooksPath tools/hooks` turns them on in a clone. The full suite and the
red-proof are the heavy checks, run before a merge rather than on every
commit.

It checks the index, not the working tree, because the index is what the
commit will contain:

 * every staged Python file parses, and gains no pyflakes warning it did not
   have at HEAD. The library has warnings it is built around -- the package
   __init__ files import names to re-export them -- so requiring none would
   fail every commit; requiring no new one catches the unused import or
   undefined name a change introduces;
 * `git diff --cached --check`: no whitespace errors or conflict markers.

A warning is compared by its text without its line number, as a count per
file, so moving code does not read as adding a warning.
'''

import collections
import re
import subprocess
import sys

try:
    from pyflakes import api
except ImportError:
    api = None


class _Collect:
    '''A pyflakes reporter that keeps the messages.'''

    def __init__(self):
        self.messages = []

    def unexpectedError(self, filename, msg):
        self.messages.append('error: %s' % msg)

    def syntaxError(self, filename, msg, lineno, offset, text):
        self.messages.append('syntax error: %s' % msg)

    def flake(self, message):
        self.messages.append(message.message % message.message_args)


def warnings_in(source, filename):
    '''The pyflakes messages for source, without line numbers.'''
    collect = _Collect()
    api.check(source, filename, collect)
    return collections.Counter(collect.messages)


def git(*args, check=True):
    return subprocess.run(['git', *args], capture_output=True, check=check)


def blob(spec):
    '''The text of a blob, or None if there is none.'''
    out = git('show', spec, check=False)
    return out.stdout.decode('utf-8', 'replace') if out.returncode == 0 else None


def staged_python():
    out = git('diff', '--cached', '--name-only', '-z', '--diff-filter=ACMR')
    return [f for f in out.stdout.decode('utf-8').split('\0')
            if f.endswith('.py')]


def new_warnings(path):
    now = warnings_in(blob(':' + path) or '', path)
    before_text = blob('HEAD:' + path)
    before = (warnings_in(before_text, path) if before_text is not None
              else collections.Counter())
    return sorted((now - before).elements())


def main():
    if api is None:
        print('light check: pyflakes is not installed; '
              "`pip install -e '.[dev]'` installs it")
        return 1
    failed = False
    for path in staged_python():
        for message in new_warnings(path):
            print('light check: %s: %s' % (path, message))
            failed = True
    check = git('diff', '--cached', '--check', check=False)
    if check.returncode != 0:
        sys.stdout.write(re.sub(r'(?m)^', 'light check: ',
                                check.stdout.decode('utf-8', 'replace')))
        failed = True
    if failed:
        print('light check: failed, so nothing was committed')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
