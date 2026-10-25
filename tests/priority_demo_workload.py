"""Bounded CPU workload for manually demonstrating ProcNanny priority policy."""

from __future__ import annotations

import os
import time

import psutil

CPU_PHASE_SECONDS = 18
IDLE_PHASE_SECONDS = 22
BUSY_SLICE_SECONDS = 0.5
PERIOD_SECONDS = 1.0


def main() -> None:
    process = psutil.Process(os.getpid())
    original_priority = process.nice()
    print(
        f"PID={process.pid} original_priority={original_priority} "
        f"process_name={process.name()}",
        flush=True,
    )
    print(
        f"CPU phase: {CPU_PHASE_SECONDS}s at approximately "
        f"{BUSY_SLICE_SECONDS / PERIOD_SECONDS:.0%} duty cycle.",
        flush=True,
    )

    cpu_end = time.monotonic() + CPU_PHASE_SECONDS
    while time.monotonic() < cpu_end:
        period_end = min(time.monotonic() + PERIOD_SECONDS, cpu_end)
        busy_end = min(time.monotonic() + BUSY_SLICE_SECONDS, period_end)
        while time.monotonic() < busy_end:
            pass
        remaining = period_end - time.monotonic()
        if remaining > 0:
            time.sleep(remaining)

    print(
        f"CPU phase complete; remaining idle for {IDLE_PHASE_SECONDS}s "
        "to allow recovery.",
        flush=True,
    )
    time.sleep(IDLE_PHASE_SECONDS)
    print("Workload exiting normally.", flush=True)


if __name__ == "__main__":
    main()
