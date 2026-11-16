# Benchmark results

Machine: cloud container, 2 cores (`results/machine.txt`). Raw data: `results/*.csv`. Table: `results/summary.md`.
Reproduce with `python3 bench/run_bench.py && python3 bench/plot.py` (about 25 min).

## Setup

* **Workload.** The fake `env` and `policy` (`bench/fake_*.cpp`) follow the real protocol, but each step only sleeps:
  10 ms policy + 40 ms env, with 5 steps per episode. That gives 0.25 s per test. There are 128 tests per run.
* **Method.** One limit is swept and the other two are set so they never bind.
  Each configuration gets 1 warmup run (discarded, decided up front) and 5 measured runs.
  Points on the plots are medians, with min–max whiskers. Runs are strictly sequential.
  Every run is checked for correctness (128 results, all terminal).
* **Model.** A test holds `sem_cpu` for its whole episode and `sem_pol` only for the policy part of each step.
  Throughput is therefore bounded by `min(envs / T_episode, cpu / T_cpu, pol / T_pol)`, where `T_x` is how long one test holds that semaphore.
  The dashed lines on the plots show this bound.
* **Noise.** The coefficient of variation is ≤ 1.3% in every configuration, so the min–max whiskers are barely visible.

## Findings

**1. The model predicts the measurements.** At low limits the harness reaches 95–99% of the model. Each semaphore behaves exactly as designed.

**2. Which pool is the bottleneck?** It depends on the workload, and the model tells you in advance.
With this workload a test holds the CPU slot for 0.25 s but the policy slot for only 0.05 s. One policy slot is therefore worth five CPU slots.
In the `pol` sweep, throughput grows linearly up to `pol ≈ 6.4`. Beyond that point the `cpu`/`envs` bound (32 / 0.25 s = 128 tests/s) takes over, so `pol=8` and `pol=16` measure the same (~111 tests/s).
**Where the plateau starts is set by whichever other limit binds next. How high it reaches is eroded by overhead.**
(The x-axes are log₂, so linear scaling looks like an upward curve.)

**3. The harness's own overhead appears at high throughput.** Efficiency drops from 98% to 87% as throughput approaches 110 tests/s.
The cost is per test (a `fork` of the worker plus two `execv`) and per run (start-up, plus ramp-up of 32 workers). On a run lasting about 1 s it is significant.
Check: the same config with 128 / 512 / 2048 tests gives 111.5 / 119.9 / 122.8 tests/s, which is 87% / 94% / 96% of the model.
A longer run spreads the fixed cost, so the plateau rises toward the model but does not move.

**4. A/B: releasing `sem_pol` early was the right decision, but only where policy is the bottleneck** (`ab.png`).
With `cpu=16` and `envs=32`:

| pol | A: early release | B: late release | gain |
|---|---|---|---|
| 1 | 19.3 | 4.0 | 4.9× |
| 2 | 38.1 | 7.9 | 4.8× |
| 4 | 59.9 | 15.6 | 3.8× |
| 8 | 60.3 | 30.7 | 2.0× |
| 16 | 58.9 | 58.7 | 1.0× |

The upper bound on the gain is `(t_pol + t_env) / t_pol`, which is 5× for this workload: late release makes a worker hold the policy slot through the env step too.
The gain vanishes once `pol` stops being the bottleneck (here at `pol=16`, where `cpu` binds in both variants).
For workloads where the env is cheap compared to the policy, the gain would be close to 1×.

**5. Sleep vs real CPU work** (`busy.png`). With `mode=busy` (the fakes burn CPU time instead of sleeping), throughput stops at `cpu = 2`, which is the core count.
Raising `max_concurrent_calls` beyond the number of cores does nothing for CPU-bound work, while the sleep workload keeps scaling.
Sleep-based numbers measure the scheduling logic. Busy-based numbers measure what this hardware can do.

## Caveats

* These are 2-core cloud numbers. Rerun on your own machine before quoting them.
* The measurements were taken before the `sem_env` and failed-result fixes (`d25aa19` / next commit). Those fixes only touch episode end and error paths, so they should not change throughput; a rerun is still cheap.
* `-DCMAKE_BUILD_TYPE=Release` fails because clang-tidy rejects `-march=native` on this toolchain. RelWithDebInfo (-O2) was used.
