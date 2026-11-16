// Shared helpers for the fake env/policy used in benchmarks.
//
// Both programs receive the evaluator's extra_args, so every knob is passed as
// key=value and each program picks the keys it cares about:
//   steps=N          episode length in policy calls          (env, default 10)
//   env_init_ms=X    env start-up time                       (env, default 0)
//   env_step_ms=X    env work per step                       (env, default 0)
//   pol_init_ms=X    policy start-up time (e.g. loading weights) (policy, default 0)
//   pol_step_ms=X    policy work per step                    (policy, default 0)
//   mode=sleep|busy  sleep: the process sleeps (no CPU used)
//                    busy:  the process burns X ms of *CPU time* (needs a real core)
#pragma once
#include <cerrno>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <string>
#include <unistd.h>

struct Knobs {
    long steps = 10;
    double env_init_ms = 0, env_step_ms = 0, pol_init_ms = 0, pol_step_ms = 0;
    bool busy = false;
};

inline Knobs parse_knobs(int argc, char* argv[])
{
    Knobs k;
    for (int i = 2; i < argc; i++) {
        std::string a = argv[i];
        auto eq = a.find('=');
        if (eq == std::string::npos) continue;
        std::string key = a.substr(0, eq), val = a.substr(eq + 1);
        if (key == "steps") k.steps = std::atol(val.c_str());
        else if (key == "env_init_ms") k.env_init_ms = std::atof(val.c_str());
        else if (key == "env_step_ms") k.env_step_ms = std::atof(val.c_str());
        else if (key == "pol_init_ms") k.pol_init_ms = std::atof(val.c_str());
        else if (key == "pol_step_ms") k.pol_step_ms = std::atof(val.c_str());
        else if (key == "mode") k.busy = (val == "busy");
    }
    return k;
}

inline double now_ms(clockid_t clock)
{
    timespec ts;
    clock_gettime(clock, &ts);
    return ts.tv_sec * 1e3 + ts.tv_nsec / 1e6;
}

// Spend `ms` milliseconds either sleeping or burning CPU time.
inline void work(double ms, bool busy)
{
    if (ms <= 0) return;
    if (busy) {
        // CPU-time clock: only advances while we actually run on a core, so
        // under contention this takes longer than `ms` of wall time.
        double end = now_ms(CLOCK_PROCESS_CPUTIME_ID) + ms;
        volatile unsigned long sink = 0;
        while (now_ms(CLOCK_PROCESS_CPUTIME_ID) < end)
            for (int i = 0; i < 1000; i++) sink = sink + i;
    } else {
        timespec ts;
        ts.tv_sec = static_cast<time_t>(ms / 1000);
        ts.tv_nsec = static_cast<long>((ms - ts.tv_sec * 1000) * 1e6);
        while (nanosleep(&ts, &ts) == -1 && errno == EINTR) {}
    }
}

// Read/write exactly n bytes; return false on EOF or error.
inline bool read_n(int fd, char* buf, size_t n)
{
    size_t got = 0;
    while (got < n) {
        ssize_t r = read(fd, buf + got, n - got);
        if (r < 0 && errno == EINTR) continue;
        if (r <= 0) return false;
        got += r;
    }
    return true;
}

inline bool write_n(int fd, const char* buf, size_t n)
{
    size_t put = 0;
    while (put < n) {
        ssize_t w = write(fd, buf + put, n - put);
        if (w < 0 && errno == EINTR) continue;
        if (w <= 0) return false;
        put += w;
    }
    return true;
}
