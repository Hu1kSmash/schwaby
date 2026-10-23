import os

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
