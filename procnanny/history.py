"""Rolling per-process CPU usage history."""

from __future__ import annotations

import math
from collections import deque

from .config import ProcNannyConfig, load_config


class ProcessHistory:
    """Store a bounded CPU sample window for each active process ID."""

    def __init__(self, config: ProcNannyConfig | None = None) -> None:
        self.config = config if config is not None else load_config()
        self._max_samples = max(
            1,
            math.ceil(
                self.config.history_window_seconds / self.config.poll_interval_seconds
            ),
        )
        self._samples: dict[int, deque[float]] = {}
        self._identities: dict[int, float] = {}

    def add_sample(
        self,
        pid: int,
        cpu_percent: float,
        create_time: float | None = None,
    ) -> float:
        """Record a CPU percentage and return the updated average."""
        if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
            raise ValueError("PID must be a positive integer.")
        if (
            isinstance(cpu_percent, bool)
            or not isinstance(cpu_percent, (int, float))
            or not math.isfinite(cpu_percent)
            or cpu_percent < 0
        ):
            raise ValueError("CPU percentage must be a finite, non-negative number.")
        if create_time is not None and (
            isinstance(create_time, bool)
            or not isinstance(create_time, (int, float))
            or not math.isfinite(create_time)
        ):
            raise ValueError("Process creation time must be a finite number.")

        if create_time is not None:
            if self._identities.get(pid) != create_time:
                self.remove(pid)
            self._identities[pid] = float(create_time)
        samples = self._samples.setdefault(pid, deque(maxlen=self._max_samples))
        samples.append(float(cpu_percent))
        return sum(samples) / len(samples)

    def get_average(self, pid: int) -> float | None:
        """Return the current average for a PID, or None if it has no samples."""
        samples = self._samples.get(pid)
        if not samples:
            return None
        return sum(samples) / len(samples)

    def update_config(self, config: ProcNannyConfig) -> None:
        """Use the new rolling-window size while preserving recent samples."""
        self.config = config
        self._max_samples = max(
            1,
            math.ceil(
                config.history_window_seconds / config.poll_interval_seconds
            ),
        )
        self._samples = {
            pid: deque(samples, maxlen=self._max_samples)
            for pid, samples in self._samples.items()
        }

    def remove(self, pid: int) -> None:
        """Discard history for a process that is no longer being monitored."""
        self._samples.pop(pid, None)
        self._identities.pop(pid, None)

    def retain_pids(self, active_pids: set[int]) -> None:
        """Discard history for PIDs absent from the latest completed scan."""
        for pid in self._samples.keys() - active_pids:
            self.remove(pid)

    def __len__(self) -> int:
        """Return the number of PIDs with retained CPU samples."""
        return len(self._samples)
