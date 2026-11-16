#!/usr/bin/env python3
"""Configurable test env. Behaviour is chosen with environment variables:
  ENV_STEP_MS   sleep this long before every state it sends (default 0)
  ENV_CRASH_AT  exit(1) instead of sending the N-th state (default: never)
  ENV_CRASH_ONLY only crash in the test with this name
  ENV_IGNORE_INT=1  ignore SIGINT
State/name sizes come from STATE_SIZE (default 4)."""
import os, signal, sys, time

S = int(os.environ.get("STATE_SIZE", 4))
step_ms = int(os.environ.get("ENV_STEP_MS", 0))
crash_at = int(os.environ.get("ENV_CRASH_AT", -1))
if os.environ.get("ENV_CRASH_ONLY") not in (None, sys.argv[1]):
    crash_at = -1  # crash only in the test with this name
signal.signal(signal.SIGINT, signal.SIG_DFL)
if os.environ.get("ENV_IGNORE_INT") == "1":
    signal.signal(signal.SIGINT, signal.SIG_IGN)

state = sys.argv[1][0]
n = 0
while True:
    if n == crash_at:
        sys.exit(1)
    time.sleep(step_ms / 1000)
    sys.stdout.write(state + "Z" * (S - 1) + "\n")
    sys.stdout.flush()
    if state == "T":
        break
    if not sys.stdin.readline():
        sys.exit(1)
    state = chr(ord(state) + 1)
    n += 1
