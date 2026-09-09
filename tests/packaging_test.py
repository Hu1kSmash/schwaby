'''Tests for setup.py's metadata.

setup.py is not imported by anything else in the suite, so a syntax error or a
mistake in the dependency lists is invisible until someone runs a build or a
`pip install`. That happened: an edit to this file left it unparseable while
the whole suite stayed green.

These assertions are about relationships between the lists rather than their
contents, so adding a package does not require editing a test.
'''

import ast
import collections
import contextlib
import importlib
import inspect
import os
import re
import tempfile
import unittest
from unittest.mock import patch

import setuptools

from .utils import no_duplicates


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))


def cls_own_body(func):
    """Every node inside `func` except those inside a nested function.

    `ast.walk` has no notion of scope, so a test whose local helper returns a
    value looks identical to a test that returns one itself.
    """
    nested = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)
    stack, out = list(func.body), []
    while stack:
        node = stack.pop()
        # Skipping the node itself, not just its children. The first version
        # dropped nested `def`s from the *stack* but still appended the one it
        # was looking at and then walked its body, so a local helper's return
        # was reported as the test's own.
        if isinstance(node, nested):
            continue
        out.append(node)
        stack.extend(ast.iter_child_nodes(node))
    return out


def display_path(path):
    '''Path relative to the repository, or absolute if it is not under one.

    os.path.relpath raises on Windows when the two paths are on different
    drives, and the control fixtures in this file live in a temp directory --
    D: on a GitHub runner, where the checkout is C:. Linux and macOS have a
    single root, so this only ever failed on the platform the matrix runs
    least often: pushes are Linux-only, and the full matrix runs on tags.
    '''
    try:
        return os.path.relpath(path, REPO_ROOT)
    except ValueError:
        return path
SETUP_PY = os.path.join(REPO_ROOT, 'setup.py')


@contextlib.contextmanager
def in_repo_root():
    '''setup.py opens README.md and schwaby/version.py by relative path, the
    way pip runs it. Every other test here is independent of the working
    directory and this one has to be too, so run it where it expects to be
    rather than from wherever pytest was invoked.'''
    previous = os.getcwd()
    os.chdir(REPO_ROOT)
    try:
        yield
    finally:
        os.chdir(previous)


def setup_kwargs():
    '''Runs setup.py with setuptools.setup captured rather than executed.'''
    captured = {}

    with in_repo_root():
        with open(SETUP_PY) as f:
            source = f.read()

        with patch.object(setuptools, 'setup', captured.update):
            exec(compile(source, SETUP_PY, 'exec'),
                 {'__name__': '__main__', 'setuptools': setuptools})

    return captured


def package_name(requirement):
    '''"websockets>=14.0" -> "websockets". Applied to both sides of every
    comparison below: normalising only one side made
    test_no_extra_is_in_install_requires pass for exactly the case it exists to
    catch, since a pinned duplicate in an extra would not match a bare name.'''
    for separator in ('>', '<', '=', '!', '~', '[', ';', ' '):
        requirement = requirement.split(separator)[0]
    return requirement.strip()


class SetupPyTest(unittest.TestCase):

    def setUp(self):
        self.kwargs = setup_kwargs()

    @no_duplicates
    def test_setup_py_parses_and_calls_setup(self):
        self.assertIn('name', self.kwargs)
        self.assertEqual('schwaby', self.kwargs['name'])

    @no_duplicates
    def test_the_distribution_and_the_package_have_the_same_name(self):
        # They used to differ: the distribution was `schwaby` and the package
        # was `schwab`, the same directory `schwab-py` ships. That made the
        # two impossible to install alongside each other -- whichever went in
        # second overwrote the other's files -- and needed a runtime check on
        # every import to say so.
        #
        # Naming them the same thing removed all of it. Asserted because
        # reverting to a `schwab` package would reintroduce the collision
        # rather than restore compatibility, and would do it quietly.
        # Not `schwaby.__name__`, which is 'schwaby' by construction the
        # moment the import above succeeds and would hold no matter what
        # setup.py said. The two sides that can actually disagree are the
        # distribution name and the shipped package list.
        self.assertEqual('schwaby', self.kwargs['name'])
        self.assertIn(self.kwargs['name'], self.kwargs['packages'])
        self.assertNotIn('schwab', self.kwargs['packages'])

    @no_duplicates
    def test_the_version_matches_the_package(self):
        from schwaby.version import version
        self.assertEqual(version, self.kwargs['version'])

    @no_duplicates
    def test_install_requires_is_the_agreed_set(self):
        # Anything here lands on every machine running this library, so it
        # should be a deliberate decision rather than a merge artifact. The
        # login packages moved back in 3.0.0 after the extras split saved
        # twelve packages for nobody and cost three silent failure modes.
        #
        # cryptography is here because the callback server runs with
        # ssl_context='adhoc' and werkzeug builds that certificate with it.
        # Nothing in flask's or werkzeug's metadata says so -- it arrives via
        # authlib today, which is a coincidence, not a guarantee.
        names = sorted(package_name(r)
                       for r in self.kwargs['install_requires'])
        self.assertEqual(
                ['authlib', 'cryptography', 'flask', 'httpx2', 'multiprocess',
                 'psutil', 'websockets'], names)

    @no_duplicates
    def test_dev_is_the_only_extra(self):
        # No `login` or `codegen` shim. They were kept empty through 3.0.0's
        # development so an old pin would not warn, then removed: measured,
        # that defended four hours of PyPI history, and pip treats an unknown
        # extra as a warning rather than an error.
        self.assertEqual(['dev'], sorted(self.kwargs['extras_require']))

    # The two checks below are vacuous against setup.py as it stands, because
    # `dev` is the only extra and both loops skip it: a subset test and a
    # disjointness test over nothing hold whatever the other side contains.
    # They would pass on a `dev` list emptied by a bad merge, which is the
    # exact failure the first one exists to catch.
    #
    # So each is a function tested twice -- once against setup.py, and once
    # against a fixture that violates it. The second is the positive control.
    # Without it the assertions look like protection and are not, which is
    # worse than not having them. They are kept rather than deleted because the
    # extras are still declared and an extra with contents is one edit away.

    @staticmethod
    def extras_missing_from_dev(extras):
        dev = set(package_name(r) for r in extras.get('dev', ()))
        return sorted(name for name, packages in extras.items()
                      if name != 'dev'
                      and not set(package_name(r) for r in packages) <= dev)

    @staticmethod
    def extras_duplicating_install_requires(install_requires, extras):
        hard = set(package_name(r) for r in install_requires)
        return sorted(name for name, packages in extras.items()
                      if name != 'dev'
                      and set(package_name(r) for r in packages) & hard)

    @no_duplicates
    def test_dev_covers_every_extra(self):
        # The suite really starts flask servers through multiprocess, so a
        # package in an extra but missing from dev is a green local run against
        # a stale virtualenv and a red CI.
        self.assertEqual(
                [], self.extras_missing_from_dev(self.kwargs['extras_require']))

    @no_duplicates
    def test_dev_coverage_check_catches_a_gap(self):
        self.assertEqual(
                ['login'],
                self.extras_missing_from_dev({'login': ['flask'],
                                              'codegen': [],
                                              'dev': ['pytest']}))

    @no_duplicates
    def test_no_extra_is_in_install_requires(self):
        # An extra which repeats a hard dependency is a package that can never
        # be absent, advertised as optional.
        self.assertEqual(
                [],
                self.extras_duplicating_install_requires(
                    self.kwargs['install_requires'],
                    self.kwargs['extras_require']))

    @no_duplicates
    def test_duplicate_check_catches_a_duplicate(self):
        self.assertEqual(
                ['login'],
                self.extras_duplicating_install_requires(
                    ['flask', 'authlib>=1.8'],
                    {'login': ['flask'], 'codegen': [], 'dev': ['flask']}))


class ShippedFilesTest(unittest.TestCase):
    """What ends up in the wheel.

    `find_packages()` with no arguments matched `tests` as well as `schwab`, so
    every release through 2.6.0 shipped a top-level `tests` package into users'
    site-packages -- colliding file-for-file with anything else that ships one,
    and answering `import tests` from outside a project root.
    """

    EXPECTED_PACKAGES = ['schwaby', 'schwaby.client', 'schwaby.contrib',
                         'schwaby.orders']

    @no_duplicates
    def test_only_schwaby_packages_are_shipped(self):
        with in_repo_root():
            found = setuptools.find_packages(include=['schwaby', 'schwaby.*'])
        self.assertEqual(self.EXPECTED_PACKAGES, sorted(found))

    @no_duplicates
    def test_setup_py_ships_that_package_list(self):
        # The assertion above is about find_packages. This one is about what
        # setup.py actually passes, which is the thing that ends up in a wheel.
        self.assertEqual(self.EXPECTED_PACKAGES,
                         sorted(setup_kwargs()['packages']))


class LinkTest(unittest.TestCase):
    """Every URL a reader can follow, from shipped code and from the docs.

    Links rot in two ways and both have happened here.

    A whole host dies. `auth.py` raised a ValueError pointing at
    `schwab-py.readthedocs.io` --- the *original* project's docs, so a user who
    got a callback URL wrong was sent to read about a different codebase.
    `orders/generic.py` pointed at `developer.schwabmeritrade.com`, from the TD
    Ameritrade era, which does not resolve. `getting-started.rst` sent a new
    reader to `beta-developer.schwab.com` to register their app --- the first
    click in the whole onboarding flow, at a hostname that does not resolve.

    All three were found one at a time, and the first guard against them was a
    denylist of the hosts already caught, walking only `schwab/` and `bin/`.
    That is a record, not a check: it could not catch the next one, and the
    next one was in `docs/`, which is where the clickable links actually are.
    So this is an allowlist, over every file a reader's link can come from.

    Or the host survives and the target moves. Five of our own links are
    `github.com/.../blob/main/<path>` deep links, one of them an anchor into a
    section title. Renaming the file or retitling the section leaves the host
    unchanged, so a host check stays green while the link 404s or lands
    mid-page. Those are resolved against the working tree instead.
    """

    ALLOWED_HOSTS = frozenset((
        'schwaby.readthedocs.io',           # our published documentation
        'github.com',                       # ours, plus httpx2's changelog
        'img.shields.io',                   # README badges
        'api.schwabapi.com',                # the API itself
        'developer.schwab.com',             # Schwab's own portal
        'www.schwab.com',
        'docs.python.org',
        'pypi.org',
        'pandas.pydata.org',
        'websockets.readthedocs.io',        # dependencies' own docs
        'requests-oauthlib.readthedocs.io',
        'virtualenv.pypa.io',
        'www.sphinx-doc.org',               # a comment in docs/conf.py
        'www.investopedia.com',             # order-type explanations
        'optionstradingiq.com',
        'www.cboe.com',
        'www.sec.gov',
        'www.businessinsider.com',
    ))

    # Hostnames that appear only inside example code, where they stand for a
    # value the reader replaces. They are not links and nobody clicks them.
    # RFC 2606 reserves example.com for documentation, and loopback is the
    # callback server rather than a link. `callback.com` used to be here: a
    # real registrable domain standing in for one the reader supplies, which is
    # someone else's host to send a reader to.
    EXAMPLE_HOSTS = frozenset(('127.0.0.1', 'localhost'))
    EXAMPLE_SUFFIXES = ('.example.com', '.example.org', '.example.net')

    OURS = 'https://github.com/Hu1kSmash/schwaby/blob/main/'

    # `\s*` after the slashes is load-bearing. RST wraps long links, and
    # `getting-started.rst` had `<https://\nvirtualenv.pypa.io/...>` -- so a
    # host anchored straight to `//` never saw it. The tell was that
    # virtualenv.pypa.io was in the allowlist and no scanned file produced it.
    URL = re.compile(r'https?://\s*([^/\s\'"`)>,]+)')
    OUR_LINK = re.compile(
            r'https://github\.com/Hu1kSmash/schwaby/blob/main/'
            r'([A-Za-z0-9_./-]+?)(?:#([a-z0-9-]+))?(?=[\s\'"`)>,]|$)')

    # `'…schwaby/' + 'blob/main/…'` is one URL to a reader and two string
    # literals to a regex. Both ValueError links in this library are written
    # that way, so the deep-link check silently covered neither -- which a
    # red-proof caught only because pointing one at a missing file changed
    # nothing. Joining adjacent literals first can merge two unrelated strings
    # into something URL-shaped, but that direction fails loudly; the other one
    # is a guard that quietly does nothing.
    ADJACENT_LITERALS = re.compile(r'[\'"]\s*\+?\s*[\'"]')

    @classmethod
    def read_joined(cls, path):
        with open(path, encoding='utf-8') as f:
            contents = f.read()
        if path.endswith('.py'):
            contents = cls.ADJACENT_LITERALS.sub('', contents)
        return contents

    @staticmethod
    def linkable_files():
        """Everything a reader's link can come out of: shipped code and docs.

        The first version of this walked `schwab/` and `bin/` only, which left
        the docs tree -- the place links are actually written -- uncovered, and
        a dead onboarding hostname sat there through it. `bin/` is gone as of
        3.0.0; the roots are asserted to exist so a removed one fails loudly
        rather than quietly reducing what is checked.
        """
        found = []
        for directory in ('schwaby', 'docs'):
            root = os.path.join(REPO_ROOT, directory)
            # os.walk on a path that does not exist yields nothing and raises
            # nothing, so a directory that is renamed or removed silently
            # shrinks what this check covers. `bin/` went that way in 3.0.0.
            assert os.path.isdir(root), 'walk root is gone: %s' % directory
            for dirpath, _, filenames in os.walk(root):
                if '__pycache__' in dirpath or '_build' in dirpath:
                    continue
                for name in filenames:
                    if name.endswith(('.py', '.rst')):
                        found.append(os.path.join(dirpath, name))
        for name in sorted(os.listdir(REPO_ROOT)):
            if name.endswith(('.rst', '.md')):
                found.append(os.path.join(REPO_ROOT, name))
        # setup.py carries the Documentation URL PyPI shows in its sidebar,
        # which is as reader-facing as anything in the docs and was outside
        # this walk until a red-proof pointed it at a nonexistent page and
        # nothing failed.
        found.append(os.path.join(REPO_ROOT, 'setup.py'))
        return found

    @classmethod
    def disallowed_hosts_in(cls, files):
        """Returns (path, host) for every URL host outside the allowlist.

        `files` is a parameter rather than a lookup so the positive control can
        run this same collection code over a fixture. A control that
        re-implements the predicate against a string literal proves only that
        the literal matches; it says nothing about whether the walk feeding the
        real assertion found anything at all.
        """
        offenders = []
        for path in files:
            contents = cls.read_joined(path)
            for host in cls.URL.findall(contents):
                bare = host.split(':')[0]
                if (bare in cls.EXAMPLE_HOSTS
                        or bare == 'example.com'
                        or bare.endswith(cls.EXAMPLE_SUFFIXES)):
                    continue
                if host not in cls.ALLOWED_HOSTS:
                    offenders.append((display_path(path), host))
        return sorted(set(offenders))

    @staticmethod
    def rst_titles(path):
        """The section titles in an .rst file.

        A title is a line with a punctuation underline of at least its own
        length beneath it. Overlined titles fall out of this too: the title is
        still the line above its underline.
        """
        lines = open(path, encoding='utf-8').read().split('\n')
        titles = []
        for i, line in enumerate(lines[:-1]):
            title, under = line.strip(), lines[i + 1].strip()
            if (title and len(under) >= len(title) and len(under) > 2
                    and len(set(under)) == 1
                    and under[0] in '=-~+^"\'`#*_:.'):
                titles.append(title)
        return titles

    @classmethod
    def rst_anchors(cls, path):
        """GitHub's slugs: drop non-word characters, spaces become hyphens."""
        out = set()
        for title in cls.rst_titles(path):
            slug = re.sub(r'[^\w\s-]', '', title.lower())
            out.add(re.sub(r'[\s_]+', '-', slug).strip('-'))
        return out

    @classmethod
    def sphinx_anchors(cls, path):
        """Sphinx's slugs, which are NOT GitHub's.

        Sphinx replaces every non-alphanumeric run with a single hyphen;
        GitHub deletes the punctuation instead. So the heading
        "Browser Warnings About Invalid/Self-Signed Certificates" is
        ...invalid-self-signed... on Read the Docs and ...invalidself-signed...
        on GitHub. One slugifier for both renderers reported a live anchor as
        missing, which is how this was found.
        """
        out = set()
        for title in cls.rst_titles(path):
            out.add(re.sub(r'[^a-z0-9]+', '-', title.lower()).strip('-'))
        # Explicit `.. _label:` targets become anchors too.
        for label in re.findall(r'^\.\. _([\w-]+):', 
                                open(path, encoding='utf-8').read(), re.M):
            out.add(label.replace('_', '-'))
        return out

    @classmethod
    def broken_own_links_in(cls, files):
        """Returns (path, url, why) for our own deep links that do not resolve.

        A host check cannot see these: rename docs/auth.rst or retitle a
        section and the host is still github.com.
        """
        broken = []
        for path in files:
            contents = cls.read_joined(path)
            for target, fragment in cls.OUR_LINK.findall(contents):
                where = display_path(path)
                absolute = os.path.join(REPO_ROOT, target)
                if not os.path.exists(absolute):
                    broken.append((where, target, 'no such file'))
                elif fragment and target.endswith('.rst'):
                    if fragment not in cls.rst_anchors(absolute):
                        broken.append(
                                (where, target + '#' + fragment,
                                 'no section with that anchor'))
        return sorted(set(broken))

    @no_duplicates
    def test_no_disallowed_url_hosts(self):
        files = self.linkable_files()

        # Positive control for the walk. assertEqual([], offenders) holds just
        # as well when linkable_files() found nothing -- a mistyped root, or a
        # future directory that is not named in it.
        self.assertGreater(len(files), 20)
        for expected in (os.path.join(REPO_ROOT, 'schwaby', 'auth.py'),
                         os.path.join(REPO_ROOT, 'docs', 'getting-started.rst'),
                         os.path.join(REPO_ROOT, 'README.md')):
            self.assertIn(expected, files)

        self.assertEqual([], self.disallowed_hosts_in(files))

    @no_duplicates
    def test_the_host_check_catches_a_foreign_host(self):
        # Through the same collection code, not a re-implementation of the
        # predicate. All three hosts really were in this tree.
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'shipped.py')
            with open(path, 'w') as f:
                f.write('See https://schwab-py.readthedocs.io/en/latest/ and\n'
                        'https://developer.schwabmeritrade.com/orders and\n'
                        'https://github.com/Hu1kSmash/schwaby and\n'
                        'https://127.0.0.1:8182\n')

            wrapped = os.path.join(tmp, 'wrapped.rst')
            with open(wrapped, 'w') as f:
                # The shape that slipped past: RST breaking a long link
                # immediately after the slashes.
                f.write('`the console <https://\n'
                        'beta-developer.schwab.com/dashboard>`__\n')

            offenders = self.disallowed_hosts_in([path, wrapped])

        self.assertEqual(['beta-developer.schwab.com',
                          'developer.schwabmeritrade.com',
                          'schwab-py.readthedocs.io'],
                         sorted(host for _, host in offenders))

    @no_duplicates
    def test_our_own_deep_links_resolve(self):
        files = self.linkable_files()
        self.assertGreater(len(files), 20)

        # Positive control: these assertions are about a list that is empty
        # when everything is fine, so prove the links were actually found.
        # auth.py carries the ValueError's anchored link.
        found = collections.Counter()
        for path in files:
            for target, _ in self.OUR_LINK.findall(self.read_joined(path)):
                found[target] += 1
        self.assertGreater(sum(found.values()), 3)

        # Named rather than counted. order-templates is written as
        # concatenated string literals in orders/generic.py, which is the case
        # that went unseen until the joining above; a count alone would have
        # stayed green. auth.rst used to be here too and moved to Read the
        # Docs, which the check below covers instead.
        self.assertIn('docs/order-templates.rst', found)

        self.assertEqual([], self.broken_own_links_in(files))

    # Read the Docs links cannot be resolved against the working tree the way
    # a blob link can -- the page does not exist until Sphinx builds it. But
    # the anchor is derived from a section title in docs/*.rst, so retitling
    # that section is exactly the rot this catches, without a network call.
    RTD_LINK = re.compile(
            r'https://schwaby\.readthedocs\.io/en/stable/'
            r'([a-z0-9-]+)\.html(?:#([a-z0-9-]+))?')

    @classmethod
    def broken_rtd_anchors_in(cls, files):
        broken = []
        for path in files:
            contents = cls.read_joined(path)
            for page, anchor in cls.RTD_LINK.findall(contents):
                where = display_path(path)
                source = os.path.join(REPO_ROOT, 'docs', page + '.rst')
                if not os.path.exists(source):
                    broken.append((where, page, 'no docs/%s.rst' % page))
                elif anchor and anchor not in cls.sphinx_anchors(source):
                    broken.append((where, page + '#' + anchor,
                                   'no section with that anchor'))
        return sorted(set(broken))

    @no_duplicates
    def test_our_readthedocs_links_match_a_real_section(self):
        files = self.linkable_files()

        # Positive control: these assertions are about an empty list, so prove
        # the links were found. auth.py alone carries four.
        found = sum(len(self.RTD_LINK.findall(self.read_joined(p)))
                    for p in files)
        self.assertGreater(found, 3)

        self.assertEqual([], self.broken_rtd_anchors_in(files))

    @no_duplicates
    def test_the_rtd_check_catches_a_retitled_section(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'shipped.py')
            with open(path, 'w') as f:
                f.write('https://schwaby.readthedocs.io/en/stable/'
                        'auth.html#callback-url-requirements\n'
                        'https://schwaby.readthedocs.io/en/stable/'
                        'auth.html#no-such-section\n'
                        'https://schwaby.readthedocs.io/en/stable/gone.html\n')

            broken = self.broken_rtd_anchors_in([path])

        self.assertEqual(
                [('auth#no-such-section', 'no section with that anchor'),
                 ('gone', 'no docs/gone.rst')],
                [(t, why) for _, t, why in broken])

    @no_duplicates
    def test_the_link_check_catches_a_moved_target_and_a_retitled_section(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'shipped.py')
            with open(path, 'w') as f:
                f.write(self.OURS + 'docs/auth.rst#callback-url-requirements\n'
                        + self.OURS + 'docs/auth.rst#no-such-section\n'
                        + self.OURS + 'docs/deleted-file.rst\n')

            broken = self.broken_own_links_in([path])

        self.assertEqual(
                [('docs/auth.rst#no-such-section',
                  'no section with that anchor'),
                 ('docs/deleted-file.rst', 'no such file')],
                [(target, why) for _, target, why in broken])

    @no_duplicates
    def test_the_anchor_the_callback_error_points_at_exists(self):
        # Named on its own because it is the one a user reaches while already
        # in trouble: client_from_login_flow refused their callback URL.
        anchors = self.rst_anchors(os.path.join(REPO_ROOT, 'docs', 'auth.rst'))
        self.assertIn('callback-url-requirements', anchors)


class DocsConfigTest(unittest.TestCase):
    """`docs/conf.py`'s project name, which titles every rendered page.

    It said `schwab-py` from the rename until 3.0.0 -- so every page title,
    browser tab and sidebar named the original project while the text on the
    page said to install `schwaby`. Nothing catches a name in a config file:
    the docs build does not care, and the link checks look at URLs.
    """

    @no_duplicates
    def test_sphinx_project_matches_the_distribution_name(self):
        namespace = {'__file__': os.path.join(REPO_ROOT, 'docs', 'conf.py')}
        with in_repo_root():
            with open(namespace['__file__']) as f:
                exec(compile(f.read(), 'conf.py', 'exec'), namespace)

        self.assertEqual(setup_kwargs()['name'], namespace['project'])

    @no_duplicates
    def test_the_author_credit_is_deliberately_not_checked(self):
        # `author` and `copyright` still name Alex Golec, which is correct and
        # must not be swept along by the assertion above. Stated as a test so
        # that changing it is a deliberate act rather than a tidy-up.
        namespace = {'__file__': os.path.join(REPO_ROOT, 'docs', 'conf.py')}
        with in_repo_root():
            with open(namespace['__file__']) as f:
                exec(compile(f.read(), 'conf.py', 'exec'), namespace)

        self.assertIn('Alex Golec', namespace['author'])
        self.assertIn('Alex Golec', namespace['copyright'])


class ScreenerVocabularyTest(unittest.TestCase):
    """The REST enum and the streaming prose describe the same vocabulary.

    `Client.Movers.Index` and the prefix list in the screener section of
    `docs/streaming.rst` are the same set of identifiers -- Schwab uses them
    for the REST `symbol_id` and as the first component of a streaming
    subscription key. They drifted: the enum said `$SPX` and the prose said
    `$SPX.X`, inherited from the pre-Schwab API, for a long time with nothing
    to notice.

    That mattered more than a typo. Over REST `$SPX.X` is an HTTP 400, which
    is at least loud. On the stream Schwab replies `code: 0, "SUBS command
    succeeded"` and then sends nothing, forever -- measured 2026-09-08 -- so a
    reader following the prose got a success acknowledgement and silence, with
    no error anywhere to explain it.

    **The tokens are compared exactly, against the bullet list alone.** The
    first version of this test asked `value in section` and passed with the
    bug reinstated, because `'$SPX' in '$SPX.X'` is true and because the
    section also contains a paragraph naming the wrong spelling on purpose. A
    substring test against prose that discusses its own counter-example cannot
    fail.
    """

    def prefixes(self):
        """The identifiers named in the screener section's bullet list."""
        with in_repo_root():
            with open('docs/streaming.rst', encoding='utf-8') as f:
                doc = f.read()

        start = doc.index('(PREFIX)_(SORTFIELD)_(FREQUENCY)')
        end = doc.index('and sortField is:', start)

        found = set()
        for line in doc[start:end].split('\n'):
            line = line.strip()
            if not line.startswith('*') or ':' not in line:
                continue
            for token in line.split(':', 1)[1].split(','):
                token = token.strip()
                if token:
                    found.add(token)
        return found

    @no_duplicates
    def test_the_bullet_list_is_exactly_the_enum(self):
        from schwaby.client import Client

        documented = self.prefixes()
        known = {m.value for m in Client.Movers.Index}

        self.assertEqual(
                set(), known - documented,
                'Movers.Index values missing from the screener bullet list')
        self.assertEqual(
                set(), documented - known,
                'the screener bullet list names prefixes that are not '
                'Movers.Index values -- `$SPX.X` was exactly this, and Schwab '
                'accepts it on the stream without ever delivering a frame')

    @no_duplicates
    def test_the_comparison_is_exact_not_substring(self):
        # The positive control this test needed and did not have. `$SPX.X`
        # contains `$SPX`, so a membership test against the raw text passes
        # with the defect present. Prove the comparison rejects it.
        documented = self.prefixes()
        self.assertIn('$SPX', documented)
        self.assertNotIn('$SPX.X', documented)


class SplitLinkTest(unittest.TestCase):
    """No hyperlink target is broken across a line.

    RST allows a link target to wrap, and sphinx renders it correctly by
    joining the lines. Everything that reads the source instead sees a
    truncated URL, and that is most things: `LinkTest` below, a grep, a
    reviewer skimming a diff.

    Five links across four pages had wrapped, and the halves left behind were
    plausible enough to pass unnoticed --- `https://www.sec.gov/`,
    `https://www.investopedia.com/articles/active-trading/032614/`,
    `https://www.pandas.pydata.org/...api/`. `LinkTest` had been checking
    those, reporting the host was fine, and never seeing the article.

    Catching it needs the raw text rather than the parsed document, because
    by the time sphinx has parsed it the defect is gone.
    """

    @no_duplicates
    def test_no_link_target_wraps_across_a_line(self):
        import pathlib
        import re

        with in_repo_root():
            offenders = []
            for path in sorted(pathlib.Path('docs').glob('*.rst')):
                text = path.read_text(encoding='utf-8')
                for m in re.finditer(r'<(https?://[^>]*)>', text, re.S):
                    if '\n' in m.group(1):
                        line = text[:m.start()].count('\n') + 1
                        offenders.append('%s:%d %r' % (
                                path, line, m.group(1)[:60]))

        self.assertEqual(
                [], offenders,
                'link targets split across a line -- sphinx joins them, but '
                'anything reading the source sees only the first half: %s'
                % offenders)


class DocReferenceTest(unittest.TestCase):
    """Every name the documentation points at, resolved against the code.

    `streaming.rst` told readers to call `Client.search_instruments()` and gave
    a worked example using it. No such method exists -- it is `get_instruments`
    -- so anyone copying the example got an AttributeError. The reference was
    inherited and nothing noticed, because `sphinx -W` does not resolve a
    `:meth:` target to a real attribute: an unresolvable one renders as plain
    text and the build succeeds.

    Two forms have to be handled, and only handling the first is how this was
    missed the first time it was checked by hand:

        :meth:`schwaby.client.Client.get_quote`
        :meth:`Client.get_quote() <schwaby.client.Client.get_quote>`

    The second puts the real target inside the angle brackets, so a pattern
    that reads the visible label sees `Client.get_quote()` -- which does not
    start with `schwaby`, gets skipped as somebody else's name, and the broken
    target behind it is never looked at.
    """

    ROLE = re.compile(r':(?:func|meth|class|attr|obj|data|exc):`([^`]+)`')
    AUTODOC = re.compile(
            r'\.\.\s+auto(?:function|class|method|attribute|data|exception)::'
            r'\s+([\w.:]+)')

    # Bare class names the docs use as shorthand, and where they live.
    SHORTHAND = {
        'Client': 'schwaby.client',
        'AsyncClient': 'schwaby.client',
        'StreamClient': 'schwaby.streaming',
        'OrderBuilder': 'schwaby.orders.generic',
    }

    @staticmethod
    def doc_files():
        found = [os.path.join(REPO_ROOT, 'docs', n)
                 for n in sorted(os.listdir(os.path.join(REPO_ROOT, 'docs')))
                 if n.endswith('.rst')]
        found.append(os.path.join(REPO_ROOT, 'README.md'))
        return found

    @classmethod
    def references_in(cls, files):
        """Returns {dotted name: {'file:line', ...}} for every code reference."""
        refs = {}
        for path in files:
            with open(path, encoding='utf-8') as f:
                contents = f.read()
            for pattern, take_target in ((cls.ROLE, True),
                                         (cls.AUTODOC, False)):
                for m in pattern.finditer(contents):
                    body = m.group(1)
                    if take_target:
                        inner = re.search(r'<([^>]+)>', body)
                        body = inner.group(1) if inner else body
                    name = body.strip().lstrip('~').replace('()', '')
                    name = name.replace('::', '.')
                    line = contents[:m.start()].count('\n') + 1
                    refs.setdefault(name, set()).add(
                            '%s:%d' % (os.path.basename(path), line))
        return refs

    @classmethod
    def unresolvable(cls, refs):
        broken = []
        for name, where in sorted(refs.items()):
            parts = name.split('.')
            if parts[0] in cls.SHORTHAND:
                obj = importlib.import_module(cls.SHORTHAND[parts[0]])
                rest = parts
            elif parts[0] == 'schwaby':
                obj, rest = None, None
                for i in range(len(parts), 0, -1):
                    try:
                        obj = importlib.import_module('.'.join(parts[:i]))
                    except ImportError:
                        continue
                    rest = parts[i:]
                    break
                if obj is None:
                    broken.append((name, sorted(where)))
                    continue
            else:
                # Not one of ours -- a python.org type, or prose in a role.
                continue
            for attr in rest:
                if not hasattr(obj, attr):
                    broken.append((name, sorted(where)))
                    break
                obj = getattr(obj, attr)
        return broken

    @no_duplicates
    def test_every_documented_name_resolves(self):
        files = self.doc_files()

        # Positive control for the collection, twice over: an empty file list
        # and a pattern that matches nothing both produce an empty broken list.
        self.assertGreater(len(files), 8)
        refs = self.references_in(files)
        self.assertGreater(len(refs), 150)
        self.assertIn('schwaby.client.Client.get_quote', refs)

        broken = self.unresolvable(refs)
        self.assertEqual([], broken)

    @no_duplicates
    def test_the_check_reads_the_target_not_the_label(self):
        # The form that hid `search_instruments`: the visible label is not the
        # target, and a checker reading the label skips it as foreign.
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'doc.rst')
            with open(path, 'w') as f:
                f.write(
                    'Use :meth:`Client.search_instruments() '
                    '<schwaby.client.Client.search_instruments>` for this.\n'
                    'And :meth:`schwaby.client.Client.get_quote` for that.\n'
                    '.. autoclass:: schwaby.streaming::StreamClient.NoSuchEnum\n')

            refs = self.references_in([path])
            broken = self.unresolvable(refs)

        self.assertEqual(
                ['schwaby.client.Client.search_instruments',
                 'schwaby.streaming.StreamClient.NoSuchEnum'],
                sorted(name for name, _ in broken))


class DocExampleTest(unittest.TestCase):
    """Keyword arguments in documentation examples, against real signatures.

    `client.rst` showed placing an order with
    `easy_client(..., webdriver_func=make_webdriver)` and a selenium import.
    There is no `webdriver_func` parameter and selenium is not a dependency and
    appears nowhere in the library -- it is left over from the TD Ameritrade
    era, when the login flow drove a real browser. A reader following that
    example got a TypeError before reaching the part they came for.

    `DocReferenceTest` cannot see this: the reference role `:func:`easy_client``
    resolves perfectly well. What is wrong is the call written underneath it.
    This walks the python code blocks instead and checks that every keyword
    passed to a `schwaby` callable is one that callable accepts.

    Deliberately narrow. It resolves a call only when the name is one the
    documentation imported from `schwaby`, and it skips a callable that takes
    `**kwargs`. It is not a type checker; it catches the argument that used to
    exist and does not any more, which is the one that rots.
    """

    CODE_BLOCK = re.compile(r'^([ \t]*)\.\.\s+code-block::\s*python\s*$')
    DIRECTIVE_OPTION = re.compile(r'^[ \t]+:[a-zA-Z-]+:.*$')

    @staticmethod
    def dedent(block):
        lines = block.split('\n')
        indents = [len(l) - len(l.lstrip()) for l in lines if l.strip()]
        if not indents:
            return ''
        margin = min(indents)
        return '\n'.join(l[margin:] if l.strip() else '' for l in lines)

    @classmethod
    def code_blocks_in(cls, files):
        blocks = []
        for path in files:
            with open(path, encoding='utf-8') as f:
                contents = f.read()
            if path.endswith('.py'):
                # A file under examples/ is one big block. It is checked the
                # same way as a doc snippet because it rots the same way and
                # had nothing watching it: the one example here passed
                # `account_id=` to StreamClient for years after that parameter
                # stopped meaning anything.
                blocks.append((display_path(path), 1, contents))
                continue
            extract = (cls.fenced_blocks if path.endswith('.md')
                       else cls.python_blocks)
            blocks.extend((os.path.basename(path), line, code)
                          for line, code in extract(contents))
        return blocks

    FENCE = re.compile(r'^```python\s*\n(.*?)^```', re.S | re.M)

    @classmethod
    def fenced_blocks(cls, contents):
        """Yields (line, source) for every ```python fence in a markdown file.

        `doc_files()` has always included README.md and nothing has ever looked
        inside it: the extractor understood the reStructuredText directive
        only, so seven blocks in the file PyPI renders as the project's front
        page were silently contributing nothing to any of these checks.
        """
        for m in cls.FENCE.finditer(contents):
            yield contents[:m.start()].count('\n') + 1, m.group(1)

    @classmethod
    def python_blocks(cls, contents):
        """Yields (line, source) for every ``code-block:: python`` in an rst file.

        A directive's body is every line indented further than the directive
        itself, blank lines included; it ends at the first non-blank line that
        is not. Written that way rather than as one regex because the regex
        this replaces ran to the first *unindented* line, so a block nested
        inside an admonition swallowed the paragraph after it -- which dedents
        to a shallower margin than the code and makes the whole thing an
        `unexpected indent` SyntaxError. `bad_keywords_in` then skipped it,
        silently. Three of the forty-four blocks it found were being dropped
        that way and seven more were never extracted at all.
        """
        lines = contents.split('\n')
        i = 0
        while i < len(lines):
            m = cls.CODE_BLOCK.match(lines[i])
            if not m:
                i += 1
                continue
            base, j, body = len(m.group(1)), i + 1, []
            # A directive may carry options -- `:caption:`,
            # `:emphasize-lines:` -- on the lines right after it, indented
            # like the body. They are not Python, and appending them makes the
            # block a SyntaxError, so an ordinary docs edit would fail the
            # parse check. Nothing here uses one today, which is why it needs
            # handling now rather than the first time somebody adds one.
            while j < len(lines) and cls.DIRECTIVE_OPTION.match(lines[j]):
                j += 1
            while j < len(lines):
                stripped = lines[j].strip()
                if not stripped:
                    body.append('')
                elif len(lines[j]) - len(lines[j].lstrip()) > base:
                    body.append(lines[j])
                else:
                    break
                j += 1
            yield i + 1, cls.dedent('\n'.join(body))
            i = j

    @staticmethod
    def example_files():
        root = os.path.join(REPO_ROOT, 'examples')
        assert os.path.isdir(root), 'examples/ is gone'
        found = []
        for dirpath, _, filenames in os.walk(root):
            if '__pycache__' in dirpath:
                continue
            found.extend(os.path.join(dirpath, n)
                         for n in filenames if n.endswith('.py'))
        return found

    @staticmethod
    def resolve_callable(name, imported):
        """Resolve a called name to a schwaby callable, or None if not ours."""
        root = name.split('.')[0]
        if root not in imported:
            return None
        obj = imported[root]
        for attr in name.split('.')[1:]:
            obj = getattr(obj, attr, None)
            if obj is None:
                return None
        return obj if callable(obj) else None

    @classmethod
    def bad_keywords_in(cls, blocks):
        """Returns (where, call, keyword) for each keyword the callee rejects."""
        problems = []
        for where, line, code in blocks:
            try:
                tree = ast.parse(code)
            except SyntaxError:
                # Fragments and shell-ish snippets are not our business.
                continue

            imported = {}
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and \
                        (node.module or '').startswith('schwaby'):
                    for alias in node.names:
                        try:
                            mod = importlib.import_module(node.module)
                        except ImportError:                # pragma: no cover
                            continue
                        target = getattr(mod, alias.name, None)
                        if target is not None:
                            imported[alias.asname or alias.name] = target
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name.startswith('schwaby'):
                            try:
                                imported[alias.asname or alias.name] = \
                                        importlib.import_module(alias.name)
                            except ImportError:            # pragma: no cover
                                pass

            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                try:
                    name = ast.unparse(node.func)
                except Exception:                          # pragma: no cover
                    continue
                target = cls.resolve_callable(name, imported)
                if target is None:
                    continue
                try:
                    sig = inspect.signature(target)
                except (ValueError, TypeError):             # pragma: no cover
                    continue
                if any(p.kind is inspect.Parameter.VAR_KEYWORD
                       for p in sig.parameters.values()):
                    continue
                accepted = set(sig.parameters)
                for kw in node.keywords:
                    if kw.arg is not None and kw.arg not in accepted:
                        problems.append(
                                ('%s:%d' % (where, line), name, kw.arg))
        return sorted(set(problems))

    # The one block that names a module this project does not depend on, and
    # names it deliberately: `docs/client.rst` shows `import httpx` as the
    # mistake to avoid, because authlib resolves `httpx2` and the two share no
    # exception hierarchy. Keyed by the module, not by file and line, so
    # editing around it does not need this list updated.
    IMPORTS_NOT_INSTALLED = {'httpx': 'client.rst names it as the wrong module'}

    #: Order templates are called positionally in the documentation, and
    #: `bad_keywords_in` only inspects keyword arguments -- so the shape this
    #: release actually broke, `equity_buy_market('AAPL', '10')`, is invisible
    #: to every other check here.
    ORDER_TEMPLATE_MODULES = ('schwaby.orders.equities', 'schwaby.orders.options')

    @classmethod
    def order_templates(cls):
        templates = {}
        for name in cls.ORDER_TEMPLATE_MODULES:
            module = importlib.import_module(name)
            for attr in dir(module):
                obj = getattr(module, attr)
                if callable(obj) and not attr.startswith('_') \
                        and getattr(obj, '__module__', None) == name:
                    templates[attr] = obj
        return templates

    @no_duplicates
    def test_every_documented_order_template_call_still_builds(self):
        """Documented template calls, executed rather than inspected.

        Building an order touches nothing outside the process, so the calls
        can simply be run -- and running them is the only way to catch a
        positional argument of the wrong type. `equity_buy_market('AAPL',
        '10')` raises now, and every existing check here would have let a
        documentation example doing that go on shipping.

        No release number here on purpose. `RELEASING.md` step 3 exists
        because prose naming a version goes stale silently and the release may
        land under a different number than the one written -- and its greps
        cover `README.md`, `docs/` and `schwaby/`, not `tests/`, so nothing
        here would have caught it.

        Only calls whose arguments are all literals are run. A call written
        with names or an ellipsis is prose about the shape rather than
        something a reader can copy, and inventing values for it would test
        the invention.
        """
        templates = self.order_templates()
        self.assertGreater(len(templates), 5, 'no templates found to check')

        blocks = self.code_blocks_in(
                DocReferenceTest.doc_files() + self.example_files())

        checked, failures = [], []
        for where, line, code in blocks:
            try:
                tree = ast.parse(code)
            except SyntaxError:                            # pragma: no cover
                continue
            source_lines = code.split('\n')
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                name = getattr(node.func, 'id', None)
                if name not in templates or node.keywords:
                    continue
                try:
                    args = [ast.literal_eval(a) for a in node.args]
                except ValueError:
                    continue                 # a name, not something to run

                # `equity_buy_limit(...)` is prose about the shape rather
                # than a call, and `...` is a literal, so literal_eval is
                # happy with it and the filter above is not enough.
                if any(a is Ellipsis for a in args):
                    continue

                # A block may deliberately show what does *not* work.
                # Marked in the documentation the reader can see, rather
                # than by an allowlist of file and line here, which would
                # go stale on the next edit above it.
                #
                # Every line the call spans is searched, not just the one it
                # starts on: the marker naturally goes on the line carrying
                # the offending argument, which for a wrapped call is not the
                # first one.
                span = source_lines[node.lineno - 1:node.end_lineno]
                marked = any('raises' in l.partition('#')[2] for l in span)

                where_line = '%s:%d %s' % (where, line, name)
                checked.append(where_line)

                # Marked calls are run too, and required to raise. Skipping
                # them instead would let the documentation claim something
                # raises after it had stopped raising -- and would quietly
                # drop a working call whose comment happens to contain the
                # word for another reason.
                try:
                    templates[name](*args).build()
                except Exception as e:
                    if not marked:
                        failures.append('%s -> %s: %s'
                                        % (where_line, type(e).__name__, e))
                else:
                    if marked:
                        failures.append(
                                '%s is marked `# raises` and did not'
                                % where_line)

        self.assertEqual([], failures)

        # Positive control. The assertion above is satisfied by finding
        # nothing at all, which is precisely what a broken extractor produces.
        self.assertGreater(len(checked), 4,
                           'expected to have executed several documented '
                           'template calls, found {}'.format(checked))

    @no_duplicates
    def test_the_template_call_check_would_catch_a_string_quantity(self):
        # The control for the control: prove the thing being executed is
        # actually rejected, so the test above is not green because building
        # never fails.
        templates = self.order_templates()
        with self.assertRaises(ValueError):
            templates['equity_buy_market']('AAPL', '10').build()
        templates['equity_buy_market']('AAPL', 10).build()

    @no_duplicates
    def test_no_collected_test_returns_a_value(self):
        """A helper named `test_*` is collected, called, and passes.

        It tests nothing, reports green, and raises the count -- which is how
        `test_files()` slipped in as a static helper on this very class. The
        `filterwarnings` rule in `setup.cfg` turns unittest's warning about it
        into a failure, but that warning only exists from CPython 3.11, and CI
        runs 3.10 as well: on that leg the trap is still silent.

        So this asks the question structurally instead, which no interpreter
        version can opt out of. Read from source, since a decorated test does
        not always expose a usable `__code__` at runtime.
        """
        offenders = []
        for path in self.python_files_under_tests():
            with open(path, encoding='utf-8') as f:
                tree = ast.parse(f.read())
            for node in ast.walk(tree):
                if not isinstance(node, ast.ClassDef):
                    continue
                for item in node.body:
                    if not isinstance(item, (ast.FunctionDef,
                                             ast.AsyncFunctionDef)):
                        continue
                    if not item.name.startswith('test'):
                        continue
                    # Only this function's own body. `ast.walk` descends
                    # into nested `def`s, and a test defining a local helper
                    # that returns something -- a `side_effect`, typically --
                    # is not what this is about. `test_running_on_collab_
                    # environment` is exactly that shape and was the first
                    # thing this flagged.
                    if any(isinstance(inner, ast.Return)
                           and inner.value is not None
                           for inner in cls_own_body(item)):
                        offenders.append(
                                '%s: %s.%s returns a value'
                                % (display_path(path), node.name, item.name))
        self.assertEqual([], offenders)

    @no_duplicates
    def test_no_test_module_defines_a_class_name_twice(self):
        """A shadowed test class takes its tests with it, silently.

        `tests/orders/generic_test.py` had two classes called
        `OrderBuilderExamplesTest`. The second shadowed the first, so two of
        its tests never ran -- and one of them asserted a contract the library
        had already stopped honouring, so it would have failed had anything
        executed it. `no_duplicates` cannot catch this: it keys on
        `__qualname__`, and duplicate classes have the same one.

        Read from the source rather than from the imported module, because by
        import time the first definition is already gone.
        """
        offenders = []
        for path in self.python_files_under_tests():
            with open(path, encoding='utf-8') as f:
                tree = ast.parse(f.read())
            seen = collections.Counter(
                    n.name for n in tree.body if isinstance(n, ast.ClassDef))
            offenders.extend(
                    '%s: %s defined %d times' % (display_path(path), name, n)
                    for name, n in sorted(seen.items()) if n > 1)
        self.assertEqual([], offenders)

    @staticmethod
    def python_files_under_tests():
        # Not `test_files`. pytest collects anything named `test_*`, so a
        # helper spelled that way is picked up, called, and passes -- a test
        # that tests nothing, inflating the count and reporting green.
        root = os.path.join(REPO_ROOT, 'tests')
        found = []
        for dirpath, _, filenames in os.walk(root):
            if '__pycache__' in dirpath:
                continue
            found.extend(os.path.join(dirpath, n)
                         for n in filenames if n.endswith('.py'))
        return found

    @no_duplicates
    def test_every_documentation_code_block_parses(self):
        """No block may be skipped for being unparseable.

        `bad_keywords_in` swallows a SyntaxError and moves on, which is right
        for a genuine fragment and wrong for a block the extractor mangled.
        Three of these were being dropped that way -- a code block nested
        inside an admonition ran on into the paragraph after it, which dedents
        to a shallower margin than the code and makes the whole thing an
        `unexpected indent`. Nothing counted them, so the check reported a
        clean run over a smaller set than it claimed.
        """
        blocks = self.code_blocks_in(
                DocReferenceTest.doc_files() + self.example_files())
        self.assertGreaterEqual(len(blocks), 54)

        unparsed = []
        for where, line, code in blocks:
            if not code.strip():
                continue
            try:
                ast.parse(code)
            except SyntaxError as e:
                unparsed.append('%s:%d: %s' % (where, line, e))
        self.assertEqual([], unparsed)

    @no_duplicates
    def test_every_import_in_the_documentation_resolves(self):
        """Every module a documented example imports has to exist.

        `bad_keywords_in` cannot answer this. It resolves a call only when the
        name was imported from `schwaby`, so a block still importing `schwab`
        is skipped rather than failed -- which is how 3.0.3 shipped three
        examples whose wrong import concealed a wrong argument, and it is
        exactly what a package rename leaves behind. This one imports what the
        documentation says to import and fails if it is not there.
        """
        blocks = self.code_blocks_in(
                DocReferenceTest.doc_files() + self.example_files())
        missing, seen = [], set()
        for where, line, code in blocks:
            try:
                tree = ast.parse(code)
            except SyntaxError:                            # pragma: no cover
                continue                # test_every_..._parses owns this case
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module \
                        and not node.level:
                    names = [node.module]
                for name in names:
                    seen.add(name)
                    if name in self.IMPORTS_NOT_INSTALLED:
                        continue
                    try:
                        importlib.import_module(name)
                    except ImportError as e:
                        missing.append('%s:%d: import %s -- %s'
                                       % (where, line, name, e))
        self.assertEqual([], missing)

        # Positive control. The assertion above passes just as well over an
        # empty set, and an extractor that silently stopped finding blocks is
        # the failure this whole class exists to notice.
        self.assertIn('schwaby.auth', seen)
        self.assertIn('schwaby.streaming', seen)
        self.assertIn('schwaby.orders.equities', seen)

    @no_duplicates
    def test_the_documentation_imports_nothing_by_the_old_package_name(self):
        """`import schwab` in a snippet would resolve for anyone who also has
        `schwab-py` installed, and quietly demonstrate the wrong library."""
        blocks = self.code_blocks_in(
                DocReferenceTest.doc_files() + self.example_files())
        offenders = [
                '%s:%d' % (where, line)
                for where, line, code in blocks
                if re.search(r'(?<![\w.-])schwab(?![y\w-])', code)]
        self.assertEqual([], offenders)

    @no_duplicates
    def test_the_examples_directory_is_covered(self):
        # Named separately so that examples/ going unchecked is a failure
        # rather than a silently smaller number.
        files = self.example_files()
        self.assertGreater(len(files), 0)
        blocks = self.code_blocks_in(files)
        self.assertEqual(len(files), len(blocks))
        self.assertEqual([], self.bad_keywords_in(blocks))

    @no_duplicates
    def test_no_example_passes_an_argument_that_does_not_exist(self):
        blocks = DocReferenceTest.doc_files() + self.example_files()
        blocks = self.code_blocks_in(blocks)

        # Positive control for the collection: an empty block list makes the
        # assertion below hold for the wrong reason. Also prove a real call is
        # being resolved and checked, not merely parsed.
        self.assertGreater(len(blocks), 20)
        resolved = sum(
                1 for _, _, code in blocks
                if 'easy_client(' in code or 'client_from_login_flow(' in code)
        self.assertGreater(resolved, 1)

        self.assertEqual([], self.bad_keywords_in(blocks))

    @no_duplicates
    def test_the_check_catches_the_argument_that_was_removed(self):
        # webdriver_func, verbatim from the example this was written for.
        code = ('from schwaby.auth import easy_client\n'
                'c = easy_client(\n'
                "        token_path='/path/to/token.json',\n"
                "        api_key='api-key',\n"
                "        app_secret='app-secret',\n"
                "        callback_url='https://callback.example.com',\n"
                '        webdriver_func=make_webdriver)\n')
        problems = self.bad_keywords_in([('doc.rst', 1, code)])
        self.assertEqual([('doc.rst:1', 'easy_client', 'webdriver_func')],
                         problems)

    @no_duplicates
    def test_a_correct_call_is_not_flagged(self):
        # The same call, as it is actually written now. Without this the test
        # above passes for a checker that flags everything.
        code = ('from schwaby.auth import easy_client\n'
                'c = easy_client(\n'
                "        api_key='api-key',\n"
                "        app_secret='app-secret',\n"
                "        callback_url='https://127.0.0.1:8182',\n"
                "        token_path='/path/to/token.json')\n")
        self.assertEqual([], self.bad_keywords_in([('doc.rst', 1, code)]))


class LongDescriptionTest(unittest.TestCase):
    """The README as PyPI will render it.

    Until 3.0.0 the README was reStructuredText, and `twine check --strict` was
    a real gate on it: rst rejects malformed markup, which is how a title
    underline one character short -- enough to publish a release with a blank
    description -- was caught before it shipped.

    Markdown is far more permissive. Measured against `readme_renderer`, it
    rejects an empty document and a whitespace-only one and accepts everything
    else, including unclosed HTML tags and broken link syntax. So `twine check`
    alone no longer covers the failure that actually happened, and this asserts
    the property directly instead: the description renders, it is substantial,
    and it contains what the page has to carry.
    """

    @staticmethod
    def rendered():
        import readme_renderer.markdown
        with in_repo_root():
            with open('README.md', encoding='utf-8') as f:
                text = f.read()
        return readme_renderer.markdown.render(text)

    @no_duplicates
    def test_setup_py_declares_the_matching_content_type(self):
        # A markdown README declared as text/x-rst renders on PyPI as a wall of
        # unformatted text, and nothing fails to tell you.
        self.assertEqual('text/markdown',
                         setup_kwargs()['long_description_content_type'])

    @no_duplicates
    def test_the_readme_renders_to_a_real_page(self):
        html = self.rendered()
        self.assertIsNotNone(html)

        # Substantial, not merely non-empty: the failure being guarded is a
        # description that publishes as blank or near-blank.
        self.assertGreater(len(html), 4000)

        # Single tokens only. A code block comes back syntax-highlighted, so
        # `pip install schwaby` renders as `pip<span class="w"> </span>install`
        # -- asserting a multi-word literal against the HTML checks the
        # highlighter, not the README. An identifier survives because the
        # highlighter wraps it rather than splitting it, which is why
        # `easy_client` is here despite appearing only inside code. Anything
        # with a space in it goes against the raw file instead; see
        # `test_the_install_command_is_in_the_readme`, where an assertion
        # written against this HTML went green for exactly this reason.
        for required in ('schwaby', 'schwab-py', 'easy_client'):
            self.assertIn(required, html)

        # Structure, not just length: a description that lost its headings is
        # not a page even if it is long.
        self.assertGreater(html.count('<h2'), 5)
        self.assertIn('<table>', html)

    @no_duplicates
    def test_the_install_command_is_in_the_readme(self):
        # Against the source, where it is one string. The rendered form is
        # broken up by the syntax highlighter.
        with in_repo_root():
            with open('README.md', encoding='utf-8') as f:
                text = f.read()
        self.assertIn('pip install schwaby', text)
        self.assertNotIn('pip install schwab-py', text)

        # The README used to tell readers to uninstall `schwab-py` first,
        # because both projects shipped a package called `schwab` and
        # installing one over the other destroyed the install. The package is
        # now `schwaby` too, so they coexist and that instruction is wrong ---
        # it would strand anyone who still imports `schwab`.
        #
        # Against the source for the same reason as the assertions above, and
        # this one learned it the hard way: written against the rendered HTML
        # it went GREEN under mutation, because the highlighter splits
        # `pip uninstall -y schwab-py` with `<span>`s and the substring is not
        # there to find. The README naming `schwab-py` at all is checked in
        # `test_the_readme_renders_to_a_real_page`; what is checked here is
        # that the old instruction has not come back.
        self.assertNotIn('uninstall schwab-py', text)
        self.assertNotIn('uninstall -y schwab-py', text)

    @no_duplicates
    def test_setup_py_names_an_encoding_when_it_reads_a_file(self):
        # test_setup_py_ships_what_was_rendered catches this only where the
        # locale is not UTF-8, which on Linux it is -- so that test passed here
        # for a year and failed on every Windows runner, and Windows only runs
        # on tags. This one is locale-independent: it reads the source.
        #
        # Without an encoding Python uses the locale's preferred one, so a
        # build on Windows decoded the README as a codepage and put replacement
        # characters where the em dashes are. A wheel built on Linux is fine
        # and one built on Windows is quietly corrupt.
        with in_repo_root():
            with open('setup.py', encoding='utf-8') as f:
                source = f.read()

        # Parsed rather than grepped. `open\(([^)]*)\)` stops at the first
        # `)`, so `open(os.path.join(a, b), encoding='utf-8')` reads as
        # `os.path.join(a, b` -- no `encoding=` in it, and the guard fails on
        # correct code.
        #
        # A denylist rather than an allowlist, because the failure being
        # guarded is a file read *added* later, and an allowlist of two
        # spellings passes anything it has not heard of -- `Path(x).open()`
        # and `Path(x).read_text()` both read a file and both take an
        # `encoding`. The only names excluded are the two that would fail
        # this test with a message blaming the wrong thing: `os.open` takes
        # no `encoding` at all, and `webbrowser.open` opens nothing on disk.
        READS = ('open', 'read_text', 'write_text')
        NOT_FILE_IO = ('os', 'webbrowser', 'sys', 'subprocess')

        def is_open(node):
            if not isinstance(node, ast.Call):
                return False
            if isinstance(node.func, ast.Name):
                return node.func.id in READS
            if not isinstance(node.func, ast.Attribute):
                return False
            if node.func.attr not in READS:
                return False
            value = node.func.value
            return not (isinstance(value, ast.Name)
                        and value.id in NOT_FILE_IO)

        opens = [n for n in ast.walk(ast.parse(source)) if is_open(n)]
        self.assertGreater(len(opens), 1)       # it does read files

        # How many positional arguments a call must have before one of them
        # IS the encoding. The builtin and `io.open` take
        # `(file, mode, buffering, encoding)`, but `Path.open` takes
        # `(mode, buffering, encoding)` -- no `file`, because it is the
        # receiver -- so the same name needs different numbers depending on
        # how it was reached. `Path.read_text(encoding, errors)` and
        # `Path.write_text(data, encoding, errors)` are shorter still.
        #
        # Reporting a call as missing an encoding it did pass is the false
        # failure this test already had once, in the regex it replaced.
        BUILTIN_POSITION = {'open': 4}
        METHOD_POSITION = {'open': 3, 'read_text': 1, 'write_text': 2}

        for call in opens:
            if isinstance(call.func, ast.Name):
                name, table = call.func.id, BUILTIN_POSITION
            elif getattr(call.func.value, 'id', None) == 'io':
                name, table = call.func.attr, BUILTIN_POSITION
            else:
                name, table = call.func.attr, METHOD_POSITION

            named = 'encoding' in [kw.arg for kw in call.keywords]
            # A spelling with no entry -- a bare `read_text` from
            # `from pathlib import Path` shadowing, say -- has no known
            # signature, so nothing can be concluded from its positionals.
            # `table[name]` there would raise `KeyError` and blame the wrong
            # thing, which is what this whole matcher exists to avoid.
            wanted = table.get(name)
            positional = wanted is not None and len(call.args) >= wanted
            self.assertTrue(
                    named or positional,
                    'setup.py reads a file without an encoding, at line %d'
                    % call.lineno)

    @no_duplicates
    def test_setup_py_ships_what_was_rendered(self):
        # The assertions above are about README.md on disk. This one is about
        # the string setup.py actually puts in the metadata, which is the thing
        # that reaches PyPI.
        self.assertEqual(self.rendered() is not None, True)
        with in_repo_root():
            with open('README.md', encoding='utf-8') as f:
                self.assertEqual(f.read(), setup_kwargs()['long_description'])
