"""Read-only process monitoring using psutil."""

from __future__ import annotations

import logging
import math
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import psutil

from .config import ProcNannyConfig, load_config

logger = logging.getLogger(__name__)
_PROCESS_ACCESS_ERRORS = (
    psutil.Error,
    OSError,
    AttributeError,
    NotImplementedError,
)
_WINDOWS_PRIORITY_LABELS = {
    psutil.IDLE_PRIORITY_CLASS: "IDLE_PRIORITY_CLASS",
    psutil.BELOW_NORMAL_PRIORITY_CLASS: "BELOW_NORMAL_PRIORITY_CLASS",
    psutil.NORMAL_PRIORITY_CLASS: "NORMAL_PRIORITY_CLASS",
    psutil.ABOVE_NORMAL_PRIORITY_CLASS: "ABOVE_NORMAL_PRIORITY_CLASS",
    psutil.HIGH_PRIORITY_CLASS: "HIGH_PRIORITY_CLASS",
    psutil.REALTIME_PRIORITY_CLASS: "REALTIME_PRIORITY_CLASS",
}


def _process_attributes() -> list[str]:
    attributes = ["pid", "name", "create_time", "status"]
    if not psutil.WINDOWS:
        attributes.append("uids")
    return attributes


def _priority_scheme() -> str:
    return "windows_priority_class" if psutil.WINDOWS else "unix_nice"


def _priority_label(value: int) -> str | None:
    if psutil.WINDOWS:
        return _WINDOWS_PRIORITY_LABELS.get(value)
    return None


@dataclass(frozen=True, slots=True)
class ProcessInfo:
    """A process observation with optional platform-dependent metadata."""

    pid: int
    name: str
    cpu_percent: float
    nice_value: int | None
    create_time: float | None = None
    user_id: int | None = None
    status: str | None = None
    observed_at: str | None = None
    priority_scheme: str = field(default_factory=_priority_scheme)
    priority_label: str | None = None
    priority_unavailable_reason: str | None = None


class ProcessMonitor:
    """Collect visible process metrics without changing process priorities."""

    def __init__(
        self,
        config: ProcNannyConfig | None = None,
        excluded_processes: Collection[str] | None = None,
    ) -> None:
        self.config = config if config is not None else load_config()
        configured_exclusions = self.config.excluded_processes
        additional_exclusions = (
            excluded_processes if excluded_processes is not None else ()
        )
        if isinstance(additional_exclusions, str) or any(
            not isinstance(name, str) or not name.strip()
            for name in additional_exclusions
        ):
            raise ValueError("Excluded process names must be non-empty strings.")
        self._additional_exclusions = {
            name.strip().casefold() for name in additional_exclusions
        }
        self._excluded_processes = {
            name.strip().casefold()
            for name in configured_exclusions
        }
        self._excluded_processes.update(self._additional_exclusions)
        self._samplers: dict[tuple[int, float], psutil.Process] = {}
        self.last_scan_complete = False
        self.last_scan_observed_pids: set[int] = set()

    def get_processes(self) -> list[ProcessInfo]:
        """Return readable process metrics, priming newly seen CPU counters first."""
        processes: list[ProcessInfo] = []
        active_keys: set[tuple[int, float]] = set()
        observed_pids: set[int] = set()
        scan_complete = True

        try:
            process_iterator = psutil.process_iter(
                attrs=_process_attributes(),
                ad_value=None,
            )
            for process in process_iterator:
                sampler_key: tuple[int, float] | None = None
                try:
                    pid_candidate = process.pid
                    if (
                        isinstance(pid_candidate, int)
                        and not isinstance(pid_candidate, bool)
                        and pid_candidate > 0
                    ):
                        observed_pids.add(pid_candidate)
                    info: Any = process.info
                    if not isinstance(info, Mapping):
                        continue
                    pid = info.get("pid")
                    name = info.get("name")
                    create_time = info.get("create_time")
                    status = info.get("status")
                    uids = info.get("uids")
                    user_id = getattr(uids, "real", None)
                    if (
                        isinstance(pid, bool)
                        or not isinstance(pid, int)
                        or pid <= 0
                        or not isinstance(name, str)
                        or not name.strip()
                        or isinstance(create_time, bool)
                        or not isinstance(create_time, (int, float))
                        or not math.isfinite(create_time)
                    ):
                        continue
                    if status == psutil.STATUS_ZOMBIE:
                        continue
                    observed_pids.add(pid)
                    if name.strip().casefold() in self._excluded_processes:
                        continue

                    key = (pid, float(create_time))
                    sampler_key = key
                    active_keys.add(key)
                    sampler = self._samplers.get(key)
                    if sampler is None:
                        sampler = process
                        self._samplers[key] = sampler
                        sampler.cpu_percent(interval=None)
                        continue

                    cpu_percent = sampler.cpu_percent(interval=None)
                    if (
                        isinstance(cpu_percent, bool)
                        or not isinstance(cpu_percent, (int, float))
                        or not math.isfinite(cpu_percent)
                        or cpu_percent < 0
                    ):
                        continue

                    nice_value: int | None = None
                    priority_unavailable_reason: str | None = None
                    try:
                        sampled_priority = sampler.nice()
                    except psutil.AccessDenied as exc:
                        priority_unavailable_reason = "AccessDenied"
                        logger.debug(
                            "Could not read priority for process %s (pid=%s): "
                            "AccessDenied.",
                            name,
                            pid,
                            exc_info=exc,
                        )
                    except psutil.ZombieProcess as exc:
                        priority_unavailable_reason = "ZombieProcess"
                        logger.debug(
                            "Could not read priority for process %s (pid=%s): "
                            "ZombieProcess.",
                            name,
                            pid,
                            exc_info=exc,
                        )
                    except psutil.NoSuchProcess as exc:
                        priority_unavailable_reason = "NoSuchProcess"
                        logger.debug(
                            "Could not read priority for process %s (pid=%s): "
                            "NoSuchProcess.",
                            name,
                            pid,
                            exc_info=exc,
                        )
                    except _PROCESS_ACCESS_ERRORS as exc:
                        priority_unavailable_reason = type(exc).__name__
                        logger.debug(
                            "Could not read priority for process %s (pid=%s): %s.",
                            name,
                            pid,
                            priority_unavailable_reason,
                            exc_info=exc,
                        )
                    else:
                        if (
                            isinstance(sampled_priority, int)
                            and not isinstance(sampled_priority, bool)
                        ):
                            nice_value = sampled_priority
                        else:
                            priority_unavailable_reason = "InvalidPriorityValue"
                            logger.warning(
                                "psutil returned an invalid priority value for "
                                "process %s (pid=%s): %r",
                                name,
                                pid,
                                sampled_priority,
                            )

                    processes.append(
                        ProcessInfo(
                            pid=pid,
                            name=name.strip(),
                            cpu_percent=float(cpu_percent),
                            nice_value=nice_value,
                            priority_scheme=_priority_scheme(),
                            priority_label=(
                                _priority_label(nice_value)
                                if nice_value is not None
                                else None
                            ),
                            priority_unavailable_reason=priority_unavailable_reason,
                            create_time=float(create_time),
                            user_id=user_id if isinstance(user_id, int) else None,
                            status=status if isinstance(status, str) else None,
                            observed_at=datetime.now(UTC).isoformat(),
                        )
                    )
                except psutil.NoSuchProcess:
                    if sampler_key is not None:
                        active_keys.discard(sampler_key)
                        self._samplers.pop(sampler_key, None)
                    logger.debug("Skipping a process that is no longer accessible.")
                except _PROCESS_ACCESS_ERRORS:
                    logger.debug("Skipping a process that is no longer accessible.")
        except _PROCESS_ACCESS_ERRORS as exc:
            scan_complete = False
            logger.warning("Could not complete process scan: %s", exc)

        if scan_complete:
            self._samplers = {
                key: sampler
                for key, sampler in self._samplers.items()
                if key in active_keys
            }
        self.last_scan_complete = scan_complete
        self.last_scan_observed_pids = observed_pids
        return processes

    def update_config(self, config: ProcNannyConfig) -> None:
        """Apply a newly validated configuration to subsequent scans."""
        self.config = config
        self._excluded_processes = {
            name.strip().casefold() for name in config.excluded_processes
        } | self._additional_exclusions
