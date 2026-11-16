#!/usr/bin/env python3
"""Test policy: answers 'a'*ACTION_SIZE to every state, optionally after POL_STEP_MS."""
import os, signal, sys, time
signal.signal(signal.SIGINT, signal.SIG_DFL)

A = int(os.environ.get("ACTION_SIZE", 4))
step_ms = int(os.environ.get("POL_STEP_MS", 0))
for _ in sys.stdin:
    time.sleep(step_ms / 1000)
    sys.stdout.write("a" * A + "\n")
    sys.stdout.flush()
