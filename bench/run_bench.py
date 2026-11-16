#!/usr/bin/env python3
"""Throughput sweeps for the evaluator.

Usage:
    python3 bench/run_bench.py [sweep ...] [--build DIR] [--reps N] [--quick]

Sweeps: cpu, pol, envs, ab, busy (default: all). Each writes bench/results/<sweep>.csv.

Method (see TODO.md "Design decisions"):
  * fake env/policy that only sleep (or burn CPU in the `busy` sweep), so the
    workload is known exactly;
  * one limit varies, the other two are set high enough never to bind;
  * a fixed set of tests, much larger than the number of slots (ramp-up/down);
  * 1 warmup run per config (discarded, decided up front) + N measured runs;
  * runs are strictly sequential: two benchmarks at once would compete for the
    same cores and pollute each other.

Each row also stores the throughput predicted by a simple bottleneck model:
    predicted = min(envs / T_episode, cpu / T_cpu_held, pol / T_pol_held)
where T_x_held is how long one test holds that semaphore in total.
"""
import argparse
import csv
import os
import platform
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "bench/results")

# Workload of one test (episode). Kept constant across sweeps.
WORK = dict(steps=5, pol_step_ms=10, env_step_ms=40, env_init_ms=0, pol_init_ms=0)
N_TESTS = 128


def predicted_throughput(pol, cpu, envs, late=False, work=WORK):
    """Tests/s if the binding semaphore were the only limit (ignores overhead)."""
    s, p, e = work["steps"], work["pol_step_ms"] / 1e3, work["env_step_ms"] / 1e3
    t_episode = work["env_init_ms"] / 1e3 + s * (p + e)
    t_cpu = work["env_init_ms"] / 1e3 + s * (p + e)   # cpu is held for both halves of a step
    t_pol = s * (p + e) if late else s * p              # early release: policy half only
    return min(envs / t_episode, cpu / t_cpu, pol / t_pol)


def run_once(evaluator, pol, cpu, envs, work, n_tests):
    build = os.path.dirname(os.path.dirname(evaluator))
    args = [evaluator, f"{build}/bench/fake_policy", f"{build}/bench/fake_env",
            str(pol), str(cpu), str(envs)]
    args += [f"{k}={v}" for k, v in work.items() if k != "mode"]
    if work.get("mode"):
        args.append(f"mode={work['mode']}")
    stdin = "".join("t%03d\n" % (i % 1000) for i in range(n_tests)).encode()

    start = time.perf_counter()  # monotonic, high resolution: right clock for intervals
    p = subprocess.run(args, input=stdin, capture_output=True, timeout=600)
    seconds = time.perf_counter() - start

    lines = p.stdout.decode().splitlines()
    if p.returncode != 0 or len(lines) != n_tests or not all(l.split()[1].startswith("T") for l in lines):
        raise RuntimeError(f"bad run {args}: exit={p.returncode}, {len(lines)} lines")
    return seconds


def sweep(name, configs, reps, build, work=WORK, n_tests=N_TESTS):
    """configs: list of (variant, pol, cpu, envs)."""
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, f"{name}.csv")
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["sweep", "variant", "pol", "cpu", "envs", "rep", "tests",
                    "seconds", "throughput", "predicted"])
        for variant, pol, cpu, envs in configs:
            exe = f"{build}/solution/" + ("evaluator_late" if variant == "late" else "evaluator")
            run_once(exe, pol, cpu, envs, work, n_tests)  # warmup, discarded
            pred = (predicted_throughput(pol, cpu, envs, variant == "late", work)
                    if work.get("mode") != "busy" else "")
            for rep in range(reps):
                secs = run_once(exe, pol, cpu, envs, work, n_tests)
                w.writerow([name, variant, pol, cpu, envs, rep, n_tests,
                            f"{secs:.4f}", f"{n_tests / secs:.3f}", pred])
                f.flush()
            print(f"  {name:5} {variant:5} pol={pol:<3} cpu={cpu:<3} envs={envs:<3} "
                  f"last={n_tests / secs:7.2f} tests/s  model={pred}", flush=True)
    print(f"wrote {path}")


LEVELS = [1, 2, 4, 8, 16]

SWEEPS = {
    # vary one limit, the other two high enough never to bind
    "cpu":  lambda: [("early", 16, c, 32) for c in LEVELS],
    "pol":  lambda: [("early", p, 32, 32) for p in LEVELS],
    "envs": lambda: [("early", 64, 64, e) for e in LEVELS + [32]],
    # A/B: early vs late sem_pol release, policy pool scarce, CPU plentiful
    "ab":   lambda: [(v, p, 16, 32) for v in ("early", "late") for p in LEVELS],
    # busy mode: real CPU work, should plateau around the number of cores
    "busy": lambda: [("early", 16, c, 16) for c in [1, 2, 4, 8]],
}

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("sweeps", nargs="*", default=list(SWEEPS))
    ap.add_argument("--build", default=os.path.join(ROOT, "build"))
    ap.add_argument("--reps", type=int, default=5)
    a = ap.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)
    with open(os.path.join(OUT_DIR, "machine.txt"), "w") as f:
        f.write(f"{platform.platform()}\ncores (os.cpu_count): {os.cpu_count()}\n"
                f"python {platform.python_version()}\nworkload: {WORK}, tests={N_TESTS}\n")

    for name in a.sweeps:
        if name == "busy":  # CPU-bound runs are slow on few cores: fewer tests
            sweep(name, SWEEPS[name](), a.reps, a.build,
                  work=dict(WORK, mode="busy"), n_tests=64)
        else:
            sweep(name, SWEEPS[name](), a.reps, a.build)
