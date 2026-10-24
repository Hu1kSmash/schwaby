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
file, so moving code does not read as adding a warning, and a renamed file is
compared with its old path at HEAD. A syntax error is never subtracted: a
file that does not parse, or that parses and does not compile, fails, whatever
HEAD had.
'''

import collections
import os
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
        # Some messages name a line ("redefinition of unused 'x' from line
        # 20"): without it a warning moved by an edit above it is the same
        # warning, not a new one.
        self.messages.append(re.sub(r'\bline \d+', 'line N',
                                    message.message % message.message_args))


def warnings_in(source, filename):
    '''The pyflakes messages for source, without line numbers.'''
    collect = _Collect()
    api.check(source, filename, collect)
    return collections.Counter(collect.messages)


def git(*args, check=True):
    return subprocess.run(['git', *args], capture_output=True, check=check)


def blob(spec):
    '''The bytes of a blob, or None if there is none. Bytes, not text: a
    stray byte that is not UTF-8 makes a file Python refuses to import, and
    decoding it here hid that.'''
    out = git('show', spec, check=False)
    return out.stdout if out.returncode == 0 else None


def staged_python():
    '''(path, its path at HEAD) for each staged Python file. A renamed file
    is compared with what it was, which is not a new file with every warning
    new; a symlink is not code.'''
    out = git('diff', '--cached', '--raw', '-z', '-M', '--diff-filter=ACMRT')
    fields = out.stdout.split(b'\0')
    staged = []
    i = 0
    while i < len(fields) and fields[i].startswith(b':'):
        # :old-mode new-mode old-sha new-sha status, then one path, or two
        # for a rename or a copy
        meta = fields[i].decode('ascii').split()
        if meta[4][0] in 'RC':
            before, path = os.fsdecode(fields[i + 1]), os.fsdecode(fields[i + 2])
            i += 3
        else:
            before = path = os.fsdecode(fields[i + 1])
            i += 2
        if path.endswith('.py') and meta[1] != '120000':
            staged.append((path, before))
    return staged


def compile_error(source, path):
    '''The error CPython raises compiling source, or None. pyflakes only
    parses, and some code parses and still cannot be compiled: `await`
    outside an async function, `nonlocal` at module level, a value returned
    from an async generator.'''
    try:
        compile(source, path, 'exec', dont_inherit=True)
    except (SyntaxError, ValueError) as e:
        return 'syntax error: %s' % (getattr(e, 'msg', None) or e)
    return None


def new_warnings(path, before_path):
    staged = blob(':' + path) or b''
    now = warnings_in(staged, path)
    if not any(m.startswith(('syntax error:', 'error:')) for m in now):
        error = compile_error(staged, path)
        if error:
            now[error] += 1
    before_text = blob('HEAD:' + before_path)
    before = (warnings_in(before_text, before_path)
              if before_text is not None else collections.Counter())
    new = now - before
    # A file that does not parse fails whatever HEAD had: that it did not
    # parse before either is no reason to commit it. On 3.10 a NUL byte is
    # reported as an `error:`, not a syntax error.
    for message in now:
        if message.startswith(('syntax error:', 'error:')):
            new[message] = now[message]
    return sorted(new.elements())


def main():
    if api is None:
        print('light check: pyflakes is not installed for %s; '
              "`pip install -e '.[dev]'` in your virtualenv installs it"
              % sys.executable)
        return 1
    failed = False
    for path, before in staged_python():
        for message in new_warnings(path, before):
            print('light check: %s: %s' % (path, message))
            failed = True
    check = git('diff', '--cached', '--check', check=False)
    if check.returncode != 0:
        sys.stdout.write(re.sub(r'(?m)^(?=.)', 'light check: ',
                                check.stdout.decode('utf-8', 'replace')))
        failed = True
    if failed:
        print('light check: failed, so nothing was committed')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
