#!/usr/bin/env python3
"""Black-box tests for the evaluator.

Usage:
    python3 tests/run_tests.py [path/to/evaluator]      (default: build/solution/evaluator)

Each test runs the evaluator in its own process group (like a shell job), so a
"Ctrl+C" can be simulated by sending SIGINT to the whole group. Every test has a
timeout; a test that does not finish in time is reported as HANG, and the whole
process group is killed so nothing is left behind.
"""
import os
import signal
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVALUATOR = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "build/solution/evaluator")
ENV = os.path.join(ROOT, "tests/fixtures/env.py")
POLICY = os.path.join(ROOT, "tests/fixtures/policy.py")


def run(limits, tests, env_vars=None, sigint_after=None, timeout=10):
    """Run the evaluator. Returns (status, exit_code, stdout_lines, seconds).

    status is "ok" if it exited by itself, "hang" if it had to be killed.
    sigint_after: seconds after start to send SIGINT to the whole process group.
    """
    env = dict(os.environ, **(env_vars or {}))
    args = [EVALUATOR, POLICY, ENV] + [str(x) for x in limits]
    start = time.monotonic()
    p = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL, env=env, start_new_session=True)
    p.stdin.write("".join(t + "\n" for t in tests).encode())
    p.stdin.close()
    if sigint_after is not None:
        time.sleep(sigint_after)
        os.killpg(p.pid, signal.SIGINT)
    try:
        p.wait(timeout=timeout)
        status = "ok"
    except subprocess.TimeoutExpired:
        status = "hang"
        os.killpg(p.pid, signal.SIGKILL)
        p.wait()
    out = p.stdout.read().decode().splitlines()
    try:  # anything still alive in the group (e.g. orphaned grandchildren)?
        os.killpg(p.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    return status, p.returncode, out, time.monotonic() - start


# ---------------------------------------------------------------- tests

def test_order_and_results():
    """Episodes of different length finish out of order; output must follow input order."""
    tests = [c + "x%02d" % i for i, c in enumerate("ASDRBQEPCO" * 3)]
    status, code, out, _ = run([4, 4, 8], tests, {"ENV_STEP_MS": "10"})
    expected = [t + " TZZZ" for t in tests]
    if status != "ok":
        return False, "hang"
    if out != expected:
        return False, f"wrong output, first lines: {out[:3]}"
    return code == 0, f"exit={code}"


def test_ctrl_c():
    """Ctrl+C (SIGINT to the whole group) mid-run: should exit quickly with code 2."""
    tests = ["Aaa%d" % (i % 10) for i in range(40)]
    status, code, out, secs = run([2, 2, 4], tests, {"ENV_STEP_MS": "50"}, sigint_after=0.5)
    if status != "ok":
        return False, "hang after SIGINT"
    return code == 2, f"exit={code}, {secs:.1f}s, {len(out)} results printed"


def test_sigint_main_only():
    """SIGINT to the main process only (kill -INT <pid>): should stop and exit with 2."""
    tests = ["Aaa%d" % (i % 10) for i in range(40)]
    env = dict(os.environ, ENV_STEP_MS="50")
    p = subprocess.Popen([EVALUATOR, POLICY, ENV, "2", "2", "4"], stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env,
                         start_new_session=True)
    p.stdin.write("".join(t + "\n" for t in tests).encode())
    p.stdin.close()
    time.sleep(0.5)
    os.kill(p.pid, signal.SIGINT)
    try:
        p.wait(timeout=10)
    except subprocess.TimeoutExpired:
        os.killpg(p.pid, signal.SIGKILL)
        p.wait()
        return False, "hang"
    return p.returncode == 2, f"exit={p.returncode}"


def test_ctrl_c_env_ignores_sigint():
    """Ctrl+C when env ignores SIGINT (a real simulator might): must still terminate."""
    tests = ["Aaa%d" % (i % 10) for i in range(40)]
    status, code, _, secs = run([2, 2, 4], tests,
                                {"ENV_STEP_MS": "50", "ENV_IGNORE_INT": "1"}, sigint_after=0.5)
    if status != "ok":
        return False, "hang after SIGINT"
    return code == 2, f"exit={code}, {secs:.1f}s"


def test_env_crash():
    """Env crashes mid-episode in every test. The evaluator must not hang
    (exact output for crashed tests is up to the spec)."""
    tests = ["Aaa%d" % i for i in range(5)]
    status, code, out, _ = run([1, 1, 2], tests, {"ENV_CRASH_AT": "2"}, timeout=5)
    if status != "ok":
        return False, "hang"
    return True, f"exit={code}, {len(out)} lines printed"


def test_one_env_crash_keeps_others():
    """Only test Aaa1 crashes. Results of the other tests must still be printed."""
    tests = ["Aaa%d" % i for i in range(5)]
    status, code, out, _ = run([1, 1, 10], tests,
                               {"ENV_CRASH_AT": "2", "ENV_CRASH_ONLY": "Aaa1"}, timeout=5)
    if status != "ok":
        return False, "hang"
    got = [line.split()[0] for line in out]
    missing = [t for t in tests if t != "Aaa1" and t not in got]
    return not missing, f"exit={code}, printed {got}, missing {missing}"


TESTS = [test_order_and_results, test_ctrl_c, test_sigint_main_only,
         test_ctrl_c_env_ignores_sigint, test_env_crash, test_one_env_crash_keeps_others]

if __name__ == "__main__":
    if not os.access(EVALUATOR, os.X_OK):
        sys.exit(f"evaluator not found at {EVALUATOR}; build it first (see README)")
    failed = 0
    for t in TESTS:
        ok, info = t()
        failed += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {t.__name__:34} {info}")
    sys.exit(1 if failed else 0)
