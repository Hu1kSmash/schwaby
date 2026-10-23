'''Runs the same tests many times and reports which failed, and how often.

    python -I -B tools/repeat.py --runs 40 tests/auth_test.py::ClientFromLoginFlowTest
    python -I -B tools/repeat.py --runs 40 --load tests/auth_test.py

A test that fails one run in forty is a defect, not bad luck: something in it
or under it depends on timing or on what else the machine is doing. This is
how such a failure is measured, and how a fix for one is shown to work: the
same count before and after, on the same tests, under the same load.

Each run is a fresh `python -m pytest` in one process, from the repository
root, with the interpreter this runs under. The runs are one after another,
never side by side: the login flow's tests bind port 6969, and two copies of
them fail each other, which would be a measurement of this tool.

--load keeps every usable core busy for the whole measurement with spinning
processes, one per core, started before the first run and stopped after the
last. A failure that appears only under load is still a failure; that is the
condition a busy machine creates on its own.

Every run is bounded (RUN_LIMIT) and a run that passes it is counted, by
name, as having run out of time. Exit status 0 only when every run passed.
'''

import argparse
import os
import re
import signal
import subprocess
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUN_LIMIT = 900

_FAILED = re.compile(r'^(?:FAILED|ERROR) (\S+)', re.MULTILINE)


def usable_cpus():
    if hasattr(os, 'process_cpu_count'):
        return os.process_cpu_count() or 1
    if hasattr(os, 'sched_getaffinity'):
        return len(os.sched_getaffinity(0))
    return os.cpu_count() or 1


def failures(output):
    '''The node ids pytest's short summary names as failed or erroring.'''
    return sorted(set(m.split(' - ')[0] for m in _FAILED.findall(output)))


def run_once(nodes):
    '''(passed, failed node ids, seconds, peak bytes, why it has no status)'''
    argv = [sys.executable, '-m', 'pytest', '-q', '-rfE',
            '-p', 'no:cacheprovider'] + nodes
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    started = time.monotonic()
    proc = subprocess.Popen(argv, cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            start_new_session=True)
    expired = threading.Event()

    def expire():
        expired.set()
        os.killpg(proc.pid, signal.SIGKILL)

    timer = threading.Timer(RUN_LIMIT, expire)
    timer.start()
    try:
        output = proc.stdout.read().decode('utf-8', 'replace')
        _, status, usage = os.wait4(proc.pid, 0)
    finally:
        timer.cancel()
    proc.returncode = os.waitstatus_to_exitcode(status)
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    seconds = time.monotonic() - started
    peak = usage.ru_maxrss * 1024
    if expired.is_set():
        return False, nodes, seconds, peak, 'ran past %d s' % RUN_LIMIT
    if proc.returncode < 0:
        return False, nodes, seconds, peak, 'killed by %s' % (
            signal.Signals(-proc.returncode).name)
    failed = failures(output)
    if proc.returncode != 0 and not failed:
        # Failed without naming a test: a collection error, a crash. Counted,
        # and the end of the output says what it was.
        tail = ' | '.join(line for line in output.splitlines()[-3:] if line)
        return False, [], seconds, peak, 'exit %d: %s' % (proc.returncode,
                                                          tail)
    return proc.returncode == 0, failed, seconds, peak, None


def start_load(n):
    return [subprocess.Popen([sys.executable, '-c', 'while True: pass'],
                             stdin=subprocess.DEVNULL, start_new_session=True)
            for _ in range(n)]


def stop_load(procs):
    for p in procs:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        p.wait()


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--runs', type=int, default=20)
    parser.add_argument('--load', action='store_true',
                        help='keep every usable core busy throughout')
    parser.add_argument('nodes', nargs='+', help='pytest node ids or paths')
    args = parser.parse_args(argv)

    load = []
    if args.load:
        load = start_load(usable_cpus())
    print('repeat: %d runs of %s on Python %s, %s' % (
        args.runs, ' '.join(args.nodes), sys.version.split()[0],
        '%d cores kept busy' % len(load) if load else 'no added load'),
        flush=True)

    def stop(signum, frame):
        stop_load(load)
        raise SystemExit(128 + signum)

    for s in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(s, stop)

    counts = {}
    failed_runs = 0
    peak = 0
    try:
        for i in range(1, args.runs + 1):
            ok, failed, seconds, run_peak, why = run_once(args.nodes)
            peak = max(peak, run_peak)
            if ok:
                print('[%d/%d] passed  (%.1f s)' % (i, args.runs, seconds),
                      flush=True)
                continue
            failed_runs += 1
            for node in failed or ['(no test named)']:
                counts[node] = counts.get(node, 0) + 1
            print('[%d/%d] FAILED  (%.1f s) %s%s' % (
                i, args.runs, seconds, ', '.join(failed),
                ('  [%s]' % why) if why else ''), flush=True)
    finally:
        stop_load(load)

    print('repeat: largest run %d MiB' % (peak >> 20))
    print('repeat: %d of %d runs failed%s' % (
        failed_runs, args.runs, ''.join(
            '\n  %3d  %s' % (n, node)
            for node, n in sorted(counts.items(), key=lambda kv: -kv[1]))),
        flush=True)
    return 1 if failed_runs else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
