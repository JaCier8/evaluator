# evaluator

A multi-process harness, written in C++ on top of POSIX, for evaluating reinforcement-learning policies.
Each test pairs an **environment** (`env`) with a **policy** (`policy`). The two are external programs that talk to the harness over pipes.
The harness runs many episodes in parallel and enforces three separate concurrency limits with process-shared POSIX semaphores.
Results are printed in **input order**, regardless of which episode finishes first.

Written for the *Concurrent Programming* course (Programowanie Współbieżne) at MIMUW.

---

## Repository layout

```
.
├── CMakeLists.txt        # top-level build: flags, STATE/ACTION/NAME_SIZE defines
├── common/
│   ├── err.h / err.c     # error helpers: ASSERT_SYS_OK, ASSERT_ZERO, syserr(), fatal()
├── demo/
│   ├── env.c             # toy environment: advances state[0] one letter per step until 'T'
│   └── policy.c          # toy policy: ignores the state, always answers "aaaa"
└── solution/
    └── main.cpp          # the evaluator itself (everything interesting lives here)
```

## Building

Requirements: CMake ≥ 3.24, a C17 compiler, a C++23 compiler, and `clang-tidy`. The build runs `clang-tidy` on the C sources, so it fails if `clang-tidy` is missing.

```bash
cmake -S . -B build                   # add -DCMAKE_BUILD_TYPE=Release for -O3 -march=native
cmake --build build
```

This produces `build/solution/evaluator`, `build/demo/demo_env` and `build/demo/demo_policy`.

### Message sizes

`STATE_SIZE`, `ACTION_SIZE` and `NAME_SIZE` are compile-time constants passed as `-D` defines. Each defaults to **4**.
You override them with **environment variables at configure time**. They are not CMake cache options.

```bash
STATE_SIZE=8 ACTION_SIZE=2 NAME_SIZE=6 cmake -S . -B build-8-2-6
```

The evaluator, `env` and `policy` must all be built with the same values. Otherwise the fixed-length protocol desynchronises.

All targets compile with `-Wall -Wextra -Werror`.

## Usage

```
evaluator <policy_path> <env_path> <max_concurrent_policy_calls> <max_concurrent_calls> <max_active_envs> [extra_args...]
```

| arg | name | meaning |
|---|---|---|
| `argv[1]` | `policy_path` | executable to run as the policy |
| `argv[2]` | `env_path` | executable to run as the environment |
| `argv[3]` | `max_concurrent_policy_calls` | at most this many policies may be *computing* at once (think: GPU slots) |
| `argv[4]` | `max_concurrent_calls` | at most this many env **or** policy steps may be in flight at once (think: CPU slots) |
| `argv[5]` | `max_active_envs` | at most this many episodes (worker + env + policy) may exist at once |
| `argv[6..]` | `extra_args` | appended verbatim to the argv of **every** env and policy |

**Input (stdin):** one test name per line. Each name must be exactly `NAME_SIZE` characters long.
**Output (stdout):** one line per test, `<test_name> <final_state>`, in the same order as the input.
**Exit code:** `0` on normal EOF and `2` after a `SIGINT` (Ctrl+C).

Example with the demo programs and the default sizes of 4:

```bash
$ printf 'Aaaa\nCbbb\nMccc\nRddd\n' | ./build/solution/evaluator \
      ./build/demo/demo_policy ./build/demo/demo_env 2 3 2 2>/dev/null
Aaaa TZZZ
Cbbb TZZZ
Mccc TZZZ
Rddd TZZZ
```

The demo programs log every step to stderr. Drop the `2>/dev/null` to see the full conversation.

## The env / policy protocol

The harness launches both programs with `fork` + `execv`. Their stdin and stdout are redirected to pipes with `dup2`.

| program | `argv[1]` | then |
|---|---|---|
| env | the test name | `extra_args...` |
| policy | a unique policy id (0, 1, 2, … in start order) | `extra_args...` |

All messages are **fixed-length lines**:

* **state** is `STATE_SIZE` bytes followed by `'\n'`, for `STATE_SIZE + 1` bytes in total. Env writes it and policy reads it.
* **action** is `ACTION_SIZE` bytes followed by `'\n'`, for `ACTION_SIZE + 1` bytes in total. Policy writes it and env reads it.

One episode runs like this:

```
env  ──state──▶ harness ──state──▶ policy
env ◀──action── harness ◀──action── policy
          … repeat until the state starts with 'T' …
```

A state whose first byte is `'T'` is **terminal**. It becomes the test's result, and the episode ends.
The harness never interprets the payload; it only relays bytes and checks the first one.

## Architecture

### Process tree

```
evaluator (main)                 reads stdin, admission control, forks one worker per test
├── result printer               restores input order, the only process that writes stdout
├── worker #0                    runs one episode
│   ├── env    (execv env_path)
│   └── policy (execv policy_path)
├── worker #1
│   ├── env
│   └── policy
└── …                            at most max_active_envs workers at a time
```

Every process is created with `fork()`. No threads are involved, so all coordination goes through **process-shared** primitives:

* a `struct Global` placed in an **anonymous shared mapping** (`mmap(MAP_SHARED | MAP_ANONYMOUS)`), created in `main` *before* any fork so every child inherits the same physical page;
* inside it, three POSIX semaphores initialised with `pshared = 1`, plus a few C11 atomics;
* pipes, for both data (env/policy I/O) and results (worker → printer).

### Concurrency limits

| semaphore | limit | who takes it | held during |
|---|---|---|---|
| `sem_env` | `max_active_envs` | **main**, before forking a worker | the whole episode; the worker posts it after it finishes |
| `sem_pol` | `max_concurrent_policy_calls` | worker | sending the state to the policy and reading the action back |
| `sem_cpu` | `max_concurrent_calls` | worker | starting env and reading its first state; then, every step, from the policy call **through** the env call |

**Lock ordering.** Inside a step a worker always acquires `sem_pol` *before* `sem_cpu`, and that fixed order rules out deadlock between the two.
`sem_env` sits outside both, because it is taken by `main` before the worker exists.

**Admission control.** `main` blocks on `sem_env` *before* it forks. When all episode slots are busy, `main` stops reading stdin and forks nothing new, so the process count stays bounded. This is how the harness smooths load instead of forking everything at once.

### One episode, step by step (`worker()`)

1. Take a policy id from `global->next_policy_id` (atomic fetch-add) and build argv vectors for env and policy.
2. **`wait(sem_cpu)`**, start env, and read the initial state. **`post(sem_cpu)`**.
3. Start the policy. No semaphore is held here.
4. Loop while `state[0] != 'T'`:
   1. **`wait(sem_pol)`**, then **`wait(sem_cpu)`**.
   2. Write the state to the policy and read the action back.
   3. **`post(sem_pol)`**. The policy slot is released *early*, before the env step.
   4. Write the action to env and read the new state.
   5. **`post(sem_cpu)`**.
5. Send a `struct Result {test_num, test_name, test_result}` to the printer pipe.
6. Close the pipes, `kill(SIGINT)` and `waitpid` both children, and **`post(sem_env)`**.

### Ordered output (`result_printer()`)

The printer is forked once, before any worker. Every worker inherits the write end of its pipe (`fd_to_writer`).

* Each test gets a sequence number (`test_num`) from a **local counter in `main`**, assigned in input order.
* The printer reads `Result` records as they arrive, stores them in a `std::map<int, Result>`, and prints while `next_to_print` is present. That is the classic reorder buffer.
* A `Result` is smaller than `PIPE_BUF`, so each `write` to the pipe is atomic. Records from different workers never interleave.

**Shutdown without an explicit join.** After stdin hits EOF, `main` closes its own copy of `fd_to_writer` and then `waitpid`s the printer only.
The printer sees EOF only when *every* process holding the write end has exited. That includes all workers, and also their env/policy children (see TODO.md). In practice, "wait for the printer" therefore means "wait for everything".
Workers themselves are reaped opportunistically with `waitpid(-1, …, WNOHANG)`.

### SIGINT handling

* `handle_sigint` only sets `global->shutdown = true`.
* `sigaction` is installed **without `SA_RESTART`**, so a blocked `read`, `write`, `sem_wait` or `fgets` returns with `EINTR`. Every wrapper (`read_exactly`, `write_exactly`, `safe_sem_wait`) checks `shutdown` and bails out with `-1`.
* `fork` keeps signal handlers, so workers and the printer inherit `handle_sigint`. `execv` resets caught signals to their default action, so env and policy get the **default** SIGINT action (terminate) unless they install their own.
* Ctrl+C in a terminal sends SIGINT to the whole **foreground process group**. Every process in the tree receives it directly, not only `main`.
* `main` returns `2` when `shutdown` was set.

## Function reference (`solution/main.cpp`)

The code is plain procedural C++ with no classes: global configuration plus free functions.

| function | role |
|---|---|
| `main` | parses argv, installs SIGINT handler, `shm_init`, starts printer, then loop: reap zombies → `fgets` a test name → `wait(sem_env)` → fork `worker` |
| `worker(test_num, test_name)` | runs one full episode (see above) and reports the result to the printer |
| `result_printer()` | forks the printer process and returns its pid; the parent keeps `fd_to_writer` |
| `start_subprocess(path, argv, &to_child, &from_child)` | two pipes + fork + `dup2` onto stdin/stdout + `execv`; returns child pid and the parent's ends of both pipes |
| `extra_args_to_argv(exec_path, arg1)` | builds a NULL-terminated `{exec_path, arg1, extra_args..., NULL}` vector for `execv` |
| `read_exactly` / `write_exactly` | loop until exactly `size` bytes are transferred; handle short I/O, EINTR, EOF and EPIPE |
| `safe_sem_wait(sem)` | `sem_wait` that retries on EINTR and gives up (re-posting if it already acquired) when `shutdown` is set |
| `cleanup_processes(env_pid, pol_pid)` | `kill(SIGINT)` then blocking `waitpid` on the policy, then the env |
| `safe_close_pipes(a, b, c, d)` | closes whichever of the four fds are `!= -1` |
| `shm_init()` / `shm_cleanup()` | create the shared `Global` and its semaphores / destroy them (`shm_cleanup` is currently never called) |
| `handle_sigint` | sets the shutdown flag |

### Shared state: `struct Global`

| field | type | used for |
|---|---|---|
| `sem_env`, `sem_cpu`, `sem_pol` | `sem_t` (pshared) | the three limits above |
| `next_policy_id` | `atomic_int` | unique ids handed to policies across workers |
| `shutdown` | `atomic_bool` | set by the SIGINT handler and read everywhere |
| `next_ticket` | `atomic_int` | **unused** (ordering uses the local counter in `main`) |
.
