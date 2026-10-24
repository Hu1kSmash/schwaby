import functools
import os
import urllib.parse

import pytest


def usable_cpus():
    '''The cores this process may run on: its affinity mask, not the
    machine's count, and logical cores rather than physical ones.'''
    if hasattr(os, 'process_cpu_count'):
        return os.process_cpu_count() or 1
    if hasattr(os, 'sched_getaffinity'):
        return len(os.sched_getaffinity(0))
    return os.cpu_count() or 1


# `-n auto` asks xdist how many workers to start, and xdist answers with
# psutil's PHYSICAL core count when psutil is installed -- which it always is
# here, since the login flow needs it. On a machine with two threads per core
# that is half of what the suite can use, and it ignores an affinity mask, so
# a run confined to four cores of a large machine starts too many. The
# PYTEST_XDIST_AUTO_NUM_WORKERS override still wins, as it does in xdist.
@pytest.hookimpl(optionalhook=True)
def pytest_xdist_auto_num_workers(config):
    if os.environ.get('PYTEST_XDIST_AUTO_NUM_WORKERS'):
        return None
    return usable_cpus()


# A fixed callback port made two runs of the suite on one machine fail each
# other, and nothing in one run can see it coming back. The source scan in
# packaging_test finds a port written into a URL; this finds one however it
# was written, by checking the port each login flow is actually given.
@pytest.fixture(autouse=True)
def login_flows_take_a_port_a_helper_handed_out(monkeypatch):
    # Imported here: suite_config_test loads this file on its own, outside
    # the package, where a relative import at the top fails.
    from schwaby import auth
    from .utils import HANDED_OUT_PORTS
    real = auth.client_from_login_flow

    @functools.wraps(real)
    def checked(api_key, app_secret, callback_url, *args, **kwargs):
        try:
            parsed = urllib.parse.urlparse(callback_url)
            port = parsed.port if parsed.hostname == '127.0.0.1' else None
        except (TypeError, ValueError):
            # Left for the flow to refuse, as it would without this.
            port = None
        if port is not None and port not in HANDED_OUT_PORTS:
            pytest.fail('a login flow was given port {}, which neither '
                        'free_callback_port nor bound_callback_socket handed '
                        'out: a port written down is one two runs of the '
                        'suite share'.format(port))
        return real(api_key, app_secret, callback_url, *args, **kwargs)

    monkeypatch.setattr(auth, 'client_from_login_flow', checked)
