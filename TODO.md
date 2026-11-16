# TODO

Working list for turning the course project into a benchmarked, sanitizer-clean harness.
Line numbers refer to `solution/main.cpp` at commit `d2ce7f7`.

## Proposed order

0. **Orientation.** Read README.md, then run the course test suite and note which tests fail and how. *(The suite is not in this repo. Add it under `tests/` or keep it aside.)*
1. **Easy bugs**: items 2.1, 2.2, 2.4, 2.6, 2.7, 2.8. These are small, local fixes.
2. **Sanitizers (task 3)**: ASan and UBSan first. They help confirm the fixes above and catch anything new.
3. **Hard bugs**: 2.3 (cleanup hang) and 2.9 (fd leaks). These are likely related.
4. **Benchmark (task 1)**: fake env/policy, the sweep, plots, the bottleneck analysis.
5. **A/B on `sem_pol` release (task 1e)**.

The reason for this order: you can't trust a benchmark on a harness that can leak a semaphore slot and hang.

---

## 1. Benchmark (main goal)

- [x] **a)** Fake `env` and `policy` (C or Python) that follow the protocol but only `sleep` a configurable time per step, e.g. `--step-ms`. Episode length should also be configurable. **No stderr logging.** The demo programs log every step, which would dominate the measurement.
- [x] **b)** Sweep `argv[3]`, `argv[4]` and `argv[5]` over a fixed test set. Measure wall time and compute throughput (tests/s). Repeat each config N≥5 times and report spread (stddev or min/max), not just the mean.
- [x] **c)** matplotlib: throughput vs each limit, with error bars.
- [x] **d)** Answer two questions. Which pool is the bottleneck? Where does throughput plateau?
  Rough prediction to check: per step, `sem_cpu` is held for policy time + env time, and `sem_pol` only for policy time. When `pol_limit ≥ cpu_limit`, `cpu_limit` should be the binding constraint.
- [x] **e)** A/B test of `sem_pol` release timing.
  ⚠ **The current code already releases `sem_pol` early** (line 245, right after reading the action and before the env step). The variant you still need to write is the **late** one, which releases at the end of the iteration next to `sem_cpu`.
  The difference should show up when the policy pool is scarcer than the CPU pool and env time is a large share of a step.

### Design decisions so far (learning sessions 18.10 and 16.11)

- Delays live in the fake env/policy, never in the harness. Knobs are passed via `extra_args` (both programs receive them): `env_init_ms`, `pol_init_ms`, `env_step_ms`, `pol_step_ms`, `steps`.
- Start with `sleep`-based delays, which measure the scheduling logic (semaphores act as tokens). Add a busy-loop variant later, which measures harness + hardware and should plateau near the core count.
- Remember that policy startup is **not** a free gap: `start_subprocess` returns immediately, so policy load time is paid inside the first step, under `sem_pol` + `sem_cpu`.
- Report the **median + min/max** of each config. Never drop outliers by eye. Discard only warmup runs, and decide that before looking at the results.
- One-factor-at-a-time: when sweeping one limit, set the others so they never bind (`pol ≥ max swept cpu`; `envs ≈ 2× max swept cpu`, to cover the gaps when a worker is alive but holds no CPU).
- Number of tests must be much larger than the number of slots (ramp-up/ramp-down). Starting point: 128 tests.
- Budget for the cpu sweep: 10 steps × (10 ms + 40 ms), 128 tests, cpu ∈ {1,2,4,8,16}, 5 repeats ≈ 124 s × 5 ≈ 10 min. `cpu=1` alone is half of that.
- Long sleeps hide the harness's own overhead. Near-zero sleeps measure exactly that overhead (a separate experiment).

Results and answers: see `bench/RESULTS.md`.

## 2. Bugs

- [ ] **2.1 `read_exactly`** (l. 415–423): when `r < 0 && errno != EINTR` there is no `return -1`, so `total_read += r` adds −1. Confirmed.
- [ ] **2.2 `shm_init`** (l. 379): `mmap` result is not checked against `MAP_FAILED`. Confirmed.
- [ ] **2.3 `cleanup_processes`**: you remembered the `waitpid` as commented out ("in tests sometimes crushes"). **In the pushed code it is active** (l. 493, 497). Is there an unpushed local version? Diagnose the hang either way.
  Leads, not answers:
  - What happens when an env or policy *ignores or handles* SIGINT?
  - Who else holds the write end of env's stdin? See 2.9.
  - What does `waitpid` do if the worker itself gets SIGINT while waiting?
- [ ] **2.4 `next_ticket`** (l. 43, 394): unused, remove. Confirmed.
- [ ] **2.5 `extra_args = &argv[6]`** (l. 76–80). **Correction: this is not a bug.** The C standard and POSIX guarantee `argv[argc] == NULL`, so at `argc == 6` it points at that terminator, which is exactly what `extra_args_to_argv` expects. The real problem is that argument validation is an `assert`, which disappears under `NDEBUG`. Replace it with a proper usage check.
- [ ] **2.6 `assert(len == NAME_SIZE)`** (l. 126): malformed input kills the run, and under `NDEBUG` it is silently accepted. Also, a line longer than `NAME_SIZE + 1` is split by `fgets` into two "tests". Confirmed.
- [ ] **2.7 Unchecked syscalls**: `sem_init` ×3 (l. 390–392) is confirmed. Note that **there is no `fcntl` call anywhere** in the code; only `<fcntl.h>` is included. `sem_post` and `close` in the error paths are unchecked too.

### Found while writing the README

### Confirmed by `tests/run_tests.py` (16.11)

- Ctrl+C (SIGINT to the whole group), SIGINT to main only, and Ctrl+C with an env that ignores SIGINT: all **pass** (exit 2, no hang). The Ctrl+C problem you remembered does not reproduce on `d2ce7f7`.
- `test_env_crash` **hangs** (see 2.8). `ps` shows main in `futex_do_wait` (blocked on `sem_env`) and the printer in `anon_pipe_read`. With `max_active_envs ≥ #tests` it does not hang, which confirms the slot leak.
- [x] FIXED: every worker now sends exactly one `Result` (with a `failed` flag), from the same place as the `sem_env` post. Failed tests are reported on stderr, and stdout keeps its format. Original issue: `test_one_env_crash_keeps_others` **failed**: if test *k* crashes, no `Result` with `test_num = k` is ever sent, so the printer's `next_to_print` stops at *k*. Every later result is silently dropped, and the exit code is still 0. Fix idea: always send a Result, with an error marker if needed (what does the spec say should be printed?).
- Workers are never reaped before main exits. They show up as `<defunct>` until init adopts and reaps them. This is harmless in a normal shell but untidy.

- [x] **2.8 `sem_env` slot leak on worker error paths.** FIXED: the slot is now posted in the worker process right after `worker()` returns, at the same level where `main` took it. Only the first error path (l. 190–193) and the success path post `sem_env`. Every other `return 1` in `worker()` leaks a slot. After `max_active_envs` failed episodes, `main` blocks forever in `safe_sem_wait(&sem_env)`. This could explain "some tests hang".
- [ ] **2.9 File descriptors leak into children** (no `O_CLOEXEC` or `pipe2`):
  - env and policy inherit `fd_to_writer`. A stuck env or policy keeps the printer, and therefore `main`, from ever finishing.
  - policy is started *after* env, so it inherits `env_in` and `env_out`. Env does not see EOF on stdin until the policy also exits.
- [ ] **2.10 Limits are not validated**: `atoi` with no checks. A value of `0` for any limit means a guaranteed deadlock.
- [ ] **2.11 Minor issues**:
  - `#include <asm-generic/errno-base.h>` is non-portable; `<cerrno>` is enough.
  - `struct Global {…} Global;` also defines an unused global *variable* named `Global`.
  - `shm_cleanup()` is never called.
  - `main` reaps workers only with `WNOHANG`, and relies on the printer EOF to wait for them (documented in README).

## 3. Sanitizers

- [ ] Build variants with `-fsanitize=address,undefined` and with `-fsanitize=thread` (separately; TSan cannot be combined with ASan), plus `-g -fno-omit-frame-pointer`. Add a CMake option, e.g. `-DSANITIZE=address`.
- [ ] Learn to read the reports: error type, the access stack, the allocation or "previous access" stack, and the `SUMMARY:` line.
- [ ] ⚠ **Expectation to set before you start: TSan is unlikely to find anything here.** TSan tracks races between *threads of one process*. This harness has no threads; it uses `fork`, and the shared state lives in a `MAP_SHARED` page that several *processes* touch. TSan does not see accesses across processes. Being able to explain this, and how you would look for cross-process races instead (lock-order reasoning, `strace -f`, stress runs with randomized sleeps), is a stronger interview answer than "TSan found nothing".

## Open questions

- Where is the course test suite, and which tests failed?
- Is there a local version newer than `d2ce7f7` (see 2.3)?
- The `main.c` / `CMakeLists.txt` with `plant.h` (workers, stations, tasks) are from a different project. Was that intentional?
