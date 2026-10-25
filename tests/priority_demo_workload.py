"""Bounded CPU workload for manually demonstrating ProcNanny priority policy."""

from __future__ import annotations

import os
import time
from datetime import datetime

import psutil

BASELINE_DELAY_SECONDS = 20
CPU_PHASE_SECONDS = 45
IDLE_PHASE_SECONDS = 60
BUSY_SLICE_SECONDS = 0.8
PERIOD_SECONDS = 1.0


def run_workload(process: psutil.Process) -> None:
    print(
        f"PID={process.pid}; waiting {BASELINE_DELAY_SECONDS}s "
        "for monitoring startup.",
        flush=True,
    )
    time.sleep(BASELINE_DELAY_SECONDS)
    original_priority = process.nice()
    create_time = process.create_time()
    print(
        f"started_at={datetime.now().astimezone().isoformat(timespec='seconds')} "
        f"PID={process.pid} create_time={create_time} "
        f"original_priority={original_priority} process_name={process.name()}",
        flush=True,
    )
    print(
        f"CPU phase: {CPU_PHASE_SECONDS}s at approximately "
        f"{BUSY_SLICE_SECONDS / PERIOD_SECONDS:.0%} duty cycle.",
        flush=True,
    )

    cpu_end = time.monotonic() + CPU_PHASE_SECONDS
    while time.monotonic() < cpu_end:
        period_start = time.monotonic()
        period_end = min(period_start + PERIOD_SECONDS, cpu_end)
        busy_end = min(period_start + BUSY_SLICE_SECONDS, period_end)
        while time.monotonic() < busy_end:
            pass
        remaining = period_end - time.monotonic()
        if remaining > 0:
            time.sleep(remaining)

    print(
        f"cpu_phase_completed_at={datetime.now().astimezone().isoformat(timespec='seconds')}; "
        f"remaining idle for {IDLE_PHASE_SECONDS}s "
        "to allow recovery.",
        flush=True,
    )
    time.sleep(IDLE_PHASE_SECONDS)
    print(
        f"exited_at={datetime.now().astimezone().isoformat(timespec='seconds')} "
        "Workload exiting normally.",
        flush=True,
    )


def main() -> int:
    process = psutil.Process(os.getpid())
    process.nice(psutil.NORMAL_PRIORITY_CLASS)
    baseline = process.nice()
    if baseline != psutil.NORMAL_PRIORITY_CLASS:
        raise RuntimeError(
            f"Could not establish a normal-priority baseline for PID {process.pid}; "
            f"observed priority class {baseline}."
        )
    print(
        f"test_pid={process.pid} baseline_priority={baseline} "
        f"baseline_label=NORMAL_PRIORITY_CLASS",
        flush=True,
    )
    run_workload(process)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
