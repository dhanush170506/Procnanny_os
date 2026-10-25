"""Safe, opt-in process priority adjustment based on rolling CPU averages."""

from __future__ import annotations

import logging
import math
import os
import sys
import threading
from collections import deque
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

import psutil

from .config import ProcNannyConfig
from .logger import ProcessLogger
from .monitor import ProcessInfo

_logger = logging.getLogger(__name__)
_MAX_RECENT_DECISIONS = 1000
_PROTECTED_NAMES = {
    "init",
    "systemd",
    "kthreadd",
    "ksoftirqd",
    "migration",
    "rcu_sched",
    "rcu_bh",
    "watchdog",
    "watchdogd",
    "kswapd",
    "kcompactd",
}
_PROTECTED_PREFIXES = ("kworker", "rcu_", "watchdog/", "migration/", "irq/")
_WINDOWS_PROTECTED_NAMES = {
    "system",
    "registry",
    "smss.exe",
    "csrss.exe",
    "wininit.exe",
    "services.exe",
    "lsass.exe",
    "svchost.exe",
    "winlogon.exe",
}
_WINDOWS_PROTECTED_PIDS = {4}
_WINDOWS_PRIORITY_CLASSES = {
    "HIGH_PRIORITY_CLASS": psutil.HIGH_PRIORITY_CLASS,
    "ABOVE_NORMAL_PRIORITY_CLASS": psutil.ABOVE_NORMAL_PRIORITY_CLASS,
    "NORMAL_PRIORITY_CLASS": psutil.NORMAL_PRIORITY_CLASS,
    "BELOW_NORMAL_PRIORITY_CLASS": psutil.BELOW_NORMAL_PRIORITY_CLASS,
    "IDLE_PRIORITY_CLASS": psutil.IDLE_PRIORITY_CLASS,
    "REALTIME_PRIORITY_CLASS": psutil.REALTIME_PRIORITY_CLASS,
}
_WINDOWS_DEMOTION_ORDER = (
    psutil.HIGH_PRIORITY_CLASS,
    psutil.ABOVE_NORMAL_PRIORITY_CLASS,
    psutil.NORMAL_PRIORITY_CLASS,
    psutil.BELOW_NORMAL_PRIORITY_CLASS,
    psutil.IDLE_PRIORITY_CLASS,
)


@dataclass(slots=True)
class _ProcessState:
    create_time: float
    original_priority: int
    projected_priority: int
    high_seconds: float = 0
    recovery_seconds: float = 0
    cooldown_seconds: float = 0
    failed_actions: set[tuple[str, int, int]] = field(default_factory=set)


@dataclass(frozen=True, slots=True)
class PriorityDecision:
    """Explain one policy decision separately from any OS priority action."""

    decision_id: str
    timestamp: str
    process_name: str
    pid: int
    create_time: float | None
    cpu_percent: float | None
    average_cpu_percent: float | None
    threshold_percent: float
    current_priority: dict[str, object] | None
    decision: str
    proposed_priority: dict[str, object] | None
    explanation: str
    eligible: bool
    eligibility_reason: str | None
    action: str | None
    action_status: Literal["not_executed", "proposed", "applied", "failed"]
    execution_error: str | None = None


class PriorityAdjuster:
    """Evaluate process observations and safely execute explicitly enabled actions."""

    def __init__(self, config: ProcNannyConfig, process_logger: ProcessLogger) -> None:
        self.config = config
        self.process_logger = process_logger
        self._states: dict[int, _ProcessState] = {}
        self._disabled_high_seconds: dict[int, tuple[float, float]] = {}
        self._decisions: deque[PriorityDecision] = deque(
            maxlen=_MAX_RECENT_DECISIONS
        )
        self._decisions_lock = threading.Lock()
        self._execution_lock = threading.RLock()
        self._issued_decisions: dict[str, dict[str, object]] = {}
        self._execution_results: dict[str, PriorityDecision] = {}

    @property
    def supported(self) -> bool:
        return sys.platform.startswith("linux") or sys.platform == "win32"

    def get_status(self) -> dict[str, object]:
        return {
            "enabled": self.config.priority_adjustment_enabled,
            "dry_run": self.config.priority_adjustment_dry_run,
            "supported": self.supported,
            "platform": sys.platform,
            "high_duration_seconds": (
                self.config.priority_adjustment_high_duration_seconds
            ),
            "recovery_duration_seconds": (
                self.config.priority_adjustment_recovery_duration_seconds
            ),
            "cooldown_seconds": self.config.priority_adjustment_cooldown_seconds,
            "step": self.config.priority_adjustment_step,
            "min_nice_value": self.config.min_nice_value,
            "max_nice_value": self.config.max_nice_value,
        }

    def update_config(self, config: ProcNannyConfig) -> None:
        """Switch policy settings without racing an in-flight priority action."""
        with self._execution_lock:
            previous = self.config
            self.config = config
            if (
                previous.cpu_high_threshold_percent
                != config.cpu_high_threshold_percent
                or previous.cpu_low_threshold_percent
                != config.cpu_low_threshold_percent
                or previous.priority_adjustment_high_duration_seconds
                != config.priority_adjustment_high_duration_seconds
                or previous.priority_adjustment_recovery_duration_seconds
                != config.priority_adjustment_recovery_duration_seconds
            ):
                for state in self._states.values():
                    state.high_seconds = 0
                    state.recovery_seconds = 0
                self._disabled_high_seconds.clear()

    def evaluate(
        self,
        process: ProcessInfo,
        average_cpu_percent: float | None = None,
    ) -> PriorityDecision:
        with self._execution_lock:
            return self._evaluate(process, average_cpu_percent)

    def _evaluate(
        self,
        process: ProcessInfo,
        average_cpu_percent: float | None = None,
    ) -> PriorityDecision:
        """Evaluate one observation using the shared rolling CPU history."""
        average = (
            process.cpu_percent
            if average_cpu_percent is None
            else average_cpu_percent
        )
        timestamp = process.observed_at or datetime.now(UTC).isoformat()
        threshold = self.config.cpu_high_threshold_percent
        current = self._priority_representation(process.nice_value)

        def decision(
            name: str,
            explanation: str,
            *,
            eligible: bool,
            eligibility_reason: str | None = None,
            proposed: int | None = None,
            action: str | None = None,
            action_status: Literal[
                "not_executed", "proposed", "applied", "failed"
            ] = "not_executed",
        ) -> PriorityDecision:
            result = PriorityDecision(
                decision_id=str(uuid4()),
                timestamp=timestamp,
                process_name=process.name,
                pid=process.pid,
                create_time=(
                    process.create_time
                    if self._valid_create_time(process.create_time)
                    else None
                ),
                cpu_percent=(
                    process.cpu_percent
                    if self._valid_cpu(process.cpu_percent)
                    else None
                ),
                average_cpu_percent=average if self._valid_cpu(average) else None,
                threshold_percent=threshold,
                current_priority=current,
                decision=name,
                proposed_priority=(
                    self._priority_representation(proposed)
                    if proposed is not None
                    else None
                ),
                explanation=explanation,
                eligible=eligible,
                eligibility_reason=eligibility_reason,
                action=action,
                action_status=action_status,
            )
            with self._decisions_lock:
                if len(self._decisions) == self._decisions.maxlen:
                    expired = self._decisions[0]
                    self._issued_decisions.pop(expired.decision_id, None)
                    self._execution_results.pop(expired.decision_id, None)
                self._decisions.append(result)
                self._issued_decisions[result.decision_id] = asdict(result)
            return result

        if (
            not self._valid_cpu(process.cpu_percent)
            or not self._valid_cpu(average)
            or not self._valid_create_time(process.create_time)
        ):
            return decision(
                "invalid_observation",
                "CPU usage or process identity is missing or invalid.",
                eligible=False,
                eligibility_reason="invalid_observation",
            )

        exclusion = self._exclusion_reason(process)
        if exclusion is not None:
            return decision(
                "excluded",
                f"Process is protected: {exclusion}.",
                eligible=False,
                eligibility_reason=exclusion,
            )

        if not self.config.priority_adjustment_enabled:
            if average < threshold:
                self._disabled_high_seconds.pop(process.pid, None)
                return decision(
                    "below_threshold",
                    "CPU is below the configured high threshold; no demotion is justified.",
                    eligible=True,
                )
            process_create_time = float(process.create_time)
            previous = self._disabled_high_seconds.get(process.pid)
            high_seconds = (
                previous[1]
                if previous is not None
                and math.isclose(
                    previous[0], process_create_time, rel_tol=0, abs_tol=1e-6
                )
                else 0.0
            ) + self.config.poll_interval_seconds
            self._disabled_high_seconds[process.pid] = (
                process_create_time,
                high_seconds,
            )
            if high_seconds < self.config.priority_adjustment_high_duration_seconds:
                return decision(
                    "waiting_for_sustained_cpu",
                    f"CPU must remain above threshold for "
                    f"{self.config.priority_adjustment_high_duration_seconds:g} "
                    f"seconds; observed {high_seconds:g}. Priority adjustment "
                    "is disabled, so no proposal will be made.",
                    eligible=True,
                )
            return decision(
                "adjustment_disabled",
                f"CPU remained above threshold for {high_seconds:g} seconds, "
                "meeting the sustained-usage duration; priority adjustment is "
                "disabled, so no proposal was made.",
                eligible=True,
            )

        if not self._valid_priority(process.nice_value):
            if average < threshold:
                return decision(
                    "below_threshold",
                    "CPU is below the configured high threshold; no demotion is justified.",
                    eligible=True,
                )
            return decision(
                "priority_unavailable",
                f"Current priority is unavailable or invalid for {self._priority_scheme()}.",
                eligible=False,
                eligibility_reason="priority_unavailable",
            )

        assert process.create_time is not None
        assert process.nice_value is not None
        state = self._states.get(process.pid)
        if state is None or state.create_time != process.create_time:
            state = _ProcessState(
                process.create_time,
                process.nice_value,
                process.nice_value,
            )
            self._states[process.pid] = state
        current_priority = (
            state.projected_priority
            if self.config.priority_adjustment_dry_run
            else process.nice_value
        )
        state.cooldown_seconds = max(
            0, state.cooldown_seconds - self.config.poll_interval_seconds
        )

        if average >= self.config.cpu_high_threshold_percent:
            state.high_seconds += self.config.poll_interval_seconds
            state.recovery_seconds = 0
        elif average < self.config.cpu_low_threshold_percent:
            state.recovery_seconds += self.config.poll_interval_seconds
            state.high_seconds = 0
        else:
            state.high_seconds = 0
            state.recovery_seconds = 0
            return decision(
                "hysteresis_hold",
                "CPU is between the high and recovery thresholds; policy timers were reset.",
                eligible=True,
            )

        if state.cooldown_seconds > 0:
            return decision(
                "cooldown",
                f"An earlier proposal/action is cooling down for {state.cooldown_seconds:g} more seconds.",
                eligible=True,
            )

        if average < self.config.cpu_high_threshold_percent:
            if (
                state.recovery_seconds
                >= self.config.priority_adjustment_recovery_duration_seconds
                and current_priority != state.original_priority
            ):
                proposed = self._restore_priority(
                    current_priority, state.original_priority
                )
                if proposed is not None:
                    if (
                        "priority_restore",
                        current_priority,
                        proposed,
                    ) in state.failed_actions:
                        return decision(
                            "action_blocked",
                            "The same restoration step previously failed for this process instance; it will not be retried automatically.",
                            eligible=True,
                        )
                    return self._propose(
                        proposed,
                        "priority_restore",
                        decision,
                        "Sustained CPU below the recovery threshold supports one priority restoration step.",
                    )
            return decision(
                "below_threshold",
                "CPU is below the high threshold; no demotion is justified.",
                eligible=True,
            )

        if (
            state.high_seconds
            < self.config.priority_adjustment_high_duration_seconds
        ):
            return decision(
                "waiting_for_sustained_cpu",
                f"CPU must remain above threshold for {self.config.priority_adjustment_high_duration_seconds:g} seconds; observed {state.high_seconds:g}.",
                eligible=True,
            )

        proposed = self._demote_priority(current_priority)
        if proposed is None:
            return decision(
                "priority_limit",
                "Current priority is already at the lowest supported adjustment level.",
                eligible=True,
            )
        if ("priority_demote", current_priority, proposed) in state.failed_actions:
            return decision(
                "action_blocked",
                "The same demotion step previously failed for this process instance; it will not be retried automatically.",
                eligible=True,
            )
        return self._propose(
            proposed,
            "priority_demote",
            decision,
            "Sustained rolling-average CPU above the configured threshold supports one demotion step.",
        )

    def get_recent_decisions(self, limit: int = 50) -> list[dict[str, object]]:
        """Return the newest in-memory decisions as JSON-ready records."""
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= _MAX_RECENT_DECISIONS
        ):
            raise ValueError(
                f"Decision limit must be between 1 and {_MAX_RECENT_DECISIONS}."
            )
        with self._decisions_lock:
            decisions = list(self._decisions)[-limit:]
        return [asdict(item) for item in decisions]

    def _propose(
        self,
        proposed: int,
        action: str,
        make_decision: Callable[..., PriorityDecision],
        explanation: str,
    ) -> PriorityDecision:
        if not self._valid_priority(proposed):
            return make_decision(
                "invalid_proposal",
                f"Proposed priority {proposed} is outside supported {self._priority_scheme()} values.",
                eligible=True,
                eligibility_reason="invalid_proposal",
            )

        return make_decision(
            "proposal",
            explanation,
            eligible=True,
            proposed=proposed,
            action=action,
            action_status="not_executed",
        )

    @staticmethod
    def _valid_cpu(value: object) -> bool:
        return (
            not isinstance(value, bool)
            and isinstance(value, (int, float))
            and math.isfinite(value)
            and value >= 0
        )

    @staticmethod
    def _valid_create_time(value: object) -> bool:
        return (
            not isinstance(value, bool)
            and isinstance(value, (int, float))
            and math.isfinite(value)
        )

    def _priority_scheme(self) -> str:
        platform_name = str(sys.platform)
        if platform_name == "win32":
            return "windows_priority_class"
        if platform_name.startswith(("linux", "darwin", "freebsd")):
            return "unix_nice"
        return "unsupported_priority"

    def _priority_representation(self, value: int | None) -> dict[str, object] | None:
        if value is None:
            return None
        if self._priority_scheme() == "windows_priority_class":
            label = next(
                (name for name, class_value in _WINDOWS_PRIORITY_CLASSES.items()
                 if class_value == value),
                "UNKNOWN_PRIORITY_CLASS",
            )
        else:
            label = f"nice {value}"
        return {"scheme": self._priority_scheme(), "value": value, "label": label}

    def _valid_priority(self, value: int | None) -> bool:
        if isinstance(value, bool) or not isinstance(value, int):
            return False
        if self._priority_scheme() == "windows_priority_class":
            return value in _WINDOWS_DEMOTION_ORDER or value == psutil.REALTIME_PRIORITY_CLASS
        if self._priority_scheme() == "unix_nice":
            return -20 <= value <= 19
        return False

    def _demote_priority(self, value: int) -> int | None:
        if self._priority_scheme() == "windows_priority_class":
            if value not in _WINDOWS_DEMOTION_ORDER:
                return None
            index = _WINDOWS_DEMOTION_ORDER.index(value)
            return (
                _WINDOWS_DEMOTION_ORDER[index + 1]
                if index + 1 < len(_WINDOWS_DEMOTION_ORDER)
                else None
            )
        proposed = min(self.config.max_nice_value, value + self.config.priority_adjustment_step)
        return proposed if proposed > value and proposed >= self.config.min_nice_value else None

    def _restore_priority(self, current: int, original: int) -> int | None:
        if self._priority_scheme() == "windows_priority_class":
            current_index = _WINDOWS_DEMOTION_ORDER.index(current)
            original_index = _WINDOWS_DEMOTION_ORDER.index(original)
            if current_index > original_index:
                return _WINDOWS_DEMOTION_ORDER[current_index - 1]
            return None
        proposed = max(original, current - self.config.priority_adjustment_step)
        return proposed if proposed < current else None

    def retain_pids(self, active_pids: set[int]) -> None:
        """Forget priority baselines for processes absent from a complete scan."""
        for pid in self._states.keys() - active_pids:
            del self._states[pid]
        for pid in self._disabled_high_seconds.keys() - active_pids:
            del self._disabled_high_seconds[pid]

    def _exclusion_reason(self, process: ProcessInfo) -> str | None:
        name = process.name.strip().casefold()
        excluded = {item.casefold() for item in self.config.excluded_processes}
        if self._priority_scheme() == "windows_priority_class" and (
            process.pid in _WINDOWS_PROTECTED_PIDS
            or name in _WINDOWS_PROTECTED_NAMES
        ):
            return "windows_system_process"
        if process.pid <= 1:
            return "system_pid"
        if process.pid == os.getpid():
            return "procnanny_process"
        if process.user_id == 0:
            return "root_owned_process"
        if process.status == psutil.STATUS_ZOMBIE:
            return "zombie_process"
        if (
            self._priority_scheme() == "windows_priority_class"
            and process.nice_value == psutil.REALTIME_PRIORITY_CLASS
        ):
            return "realtime_priority_process"
        if name in excluded:
            return "configured_protected_process"
        if name in _PROTECTED_NAMES or name.startswith(_PROTECTED_PREFIXES):
            return "system_process"
        return None

    def _is_protected(self, process: ProcessInfo) -> bool:
        return self._exclusion_reason(process) is not None

    def execute(
        self,
        process: ProcessInfo,
        decision: PriorityDecision,
    ) -> PriorityDecision:
        """Execute one evaluator-issued proposal once, with fresh identity checks."""
        with self._execution_lock:
            cached = self._execution_results.get(decision.decision_id)
            if cached is not None:
                return cached

            issued = self._issued_decisions.get(decision.decision_id)
            if issued is None or issued != asdict(decision):
                return replace(
                    decision,
                    decision="invalid_proposal",
                    action_status="failed",
                    execution_error="Decision was not issued by this evaluator.",
                )
            if (
                decision.decision != "proposal"
                or not decision.eligible
                or decision.action not in {"priority_demote", "priority_restore"}
                or decision.proposed_priority is None
                or decision.create_time is None
            ):
                status = (
                    "excluded"
                    if decision.decision == "excluded"
                    else "skipped"
                )
                self.process_logger.log_priority_event(
                    pid=decision.pid,
                    process_name=decision.process_name,
                    create_time=decision.create_time,
                    cpu_percent=decision.cpu_percent,
                    average_cpu_percent=decision.average_cpu_percent,
                    previous_priority=self._value(decision.current_priority),
                    proposed_priority=self._value(decision.proposed_priority),
                    resulting_priority=None,
                    action="priority_evaluation",
                    status=status,
                    reason=decision.explanation,
                    dry_run=self.config.priority_adjustment_dry_run,
                    error=decision.eligibility_reason,
                )
                return decision
            if not self.config.priority_adjustment_enabled:
                return self._complete_execution(
                    process,
                    decision,
                    previous_priority=self._value(decision.current_priority),
                    requested_priority=self._value(decision.proposed_priority),
                    status="not_executed",
                    error="Priority adjustment is disabled by configuration.",
                    dry_run=self.config.priority_adjustment_dry_run,
                )
            if not self.supported:
                return self._complete_execution(
                    process,
                    decision,
                    previous_priority=self._value(decision.current_priority),
                    requested_priority=self._value(decision.proposed_priority),
                    status="failed",
                    error="Priority adjustment is not supported on this platform.",
                    dry_run=self.config.priority_adjustment_dry_run,
                )

            invalid_error = self._validate_execution_proposal(process, decision)
            if invalid_error is not None:
                return self._complete_execution(
                    process,
                    decision,
                    previous_priority=self._value(decision.current_priority),
                    requested_priority=self._value(decision.proposed_priority),
                    status="failed",
                    error=invalid_error,
                    dry_run=self.config.priority_adjustment_dry_run,
                )

            assert decision.create_time is not None
            assert decision.action is not None
            assert decision.proposed_priority is not None
            requested = self._value(decision.proposed_priority)
            assert requested is not None
            state = self._states.get(process.pid)
            if state is None or not math.isclose(
                state.create_time, decision.create_time, rel_tol=0, abs_tol=1e-6
            ):
                return self._complete_execution(
                    process,
                    decision,
                    previous_priority=self._value(decision.current_priority),
                    requested_priority=requested,
                    status="failed",
                    error="Process policy state is missing or belongs to a different process instance.",
                    dry_run=self.config.priority_adjustment_dry_run,
                )

            fresh_priority: int | None = None
            actual_priority: int | None = None

            def fail_execution(exc: Exception, reason: str | None = None) -> PriorityDecision:
                return self._complete_execution(
                    process,
                    decision,
                    previous_priority=(
                        fresh_priority
                        if fresh_priority is not None
                        else self._value(decision.current_priority)
                    ),
                    requested_priority=requested,
                    status="failed",
                    error=reason or f"{type(exc).__name__}: {exc}",
                    dry_run=self.config.priority_adjustment_dry_run,
                    state=state,
                    resulting_priority=actual_priority,
                )

            try:
                target = psutil.Process(process.pid)
                target_create_time = target.create_time()
                if not math.isclose(
                    target_create_time,
                    decision.create_time,
                    rel_tol=0,
                    abs_tol=1e-6,
                ):
                    raise ValueError("PID identity changed before priority execution.")
                target_name = target.name()
                if target_name.strip().casefold() != decision.process_name.strip().casefold():
                    raise ValueError("Process name changed before priority execution.")
                target_status = target.status()
                if target_status == psutil.STATUS_ZOMBIE:
                    raise ValueError("Process became a zombie before priority execution.")
                if not psutil.WINDOWS and target.uids().real == 0:
                    raise ValueError("Root-owned processes are protected.")

                fresh_priority = target.nice()
                if not self._valid_priority(fresh_priority):
                    raise ValueError("Current process priority is unavailable or invalid.")
                if self._exclusion_reason(process) is not None:
                    raise ValueError(
                        f"Process is protected: {self._exclusion_reason(process)}."
                    )
                if not self.config.priority_adjustment_dry_run:
                    expected = self._value(decision.current_priority)
                    if fresh_priority != expected:
                        raise ValueError(
                            "Current process priority changed after evaluation."
                        )

                baseline = (
                    state.projected_priority
                    if self.config.priority_adjustment_dry_run
                    else fresh_priority
                )
                valid_next = (
                    self._demote_priority(baseline)
                    if decision.action == "priority_demote"
                    else self._restore_priority(baseline, state.original_priority)
                )
                if valid_next != requested:
                    raise ValueError(
                        "Proposed priority no longer matches the validated policy step."
                    )

                if self.config.priority_adjustment_dry_run:
                    return self._complete_execution(
                        process,
                        decision,
                        previous_priority=baseline,
                        requested_priority=requested,
                        status="proposed",
                        error=None,
                        dry_run=True,
                        state=state,
                    )

                target.nice(requested)
                actual_priority = target.nice()
                if actual_priority != requested:
                    raise ValueError(
                        f"Priority verification failed: requested {requested}, observed {actual_priority}."
                    )
            except psutil.ZombieProcess as exc:
                return fail_execution(
                    exc,
                    f"ZombieProcess: process became a zombie before priority execution: {exc}",
                )
            except psutil.AccessDenied as exc:
                return fail_execution(
                    exc,
                    f"AccessDenied: operating system denied priority access: {exc}",
                )
            except psutil.NoSuchProcess as exc:
                return fail_execution(
                    exc,
                    f"NoSuchProcess: process terminated before priority execution: {exc}",
                )
            except NotImplementedError as exc:
                return fail_execution(
                    exc,
                    f"NotImplementedError: priority adjustment is unsupported: {exc}",
                )
            except (psutil.Error, OSError, AttributeError, ValueError) as exc:
                return fail_execution(exc)

            return self._complete_execution(
                process,
                decision,
                previous_priority=fresh_priority,
                requested_priority=requested,
                status="applied",
                error=None,
                dry_run=False,
                state=state,
                resulting_priority=actual_priority,
            )

    @staticmethod
    def _value(priority: dict[str, object] | None) -> int | None:
        if priority is None:
            return None
        value = priority.get("value")
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    def _validate_execution_proposal(
        self,
        process: ProcessInfo,
        decision: PriorityDecision,
    ) -> str | None:
        if (
            process.pid != decision.pid
            or process.name.casefold() != decision.process_name.casefold()
            or process.create_time is None
            or not math.isclose(
                process.create_time,
                decision.create_time or 0,
                rel_tol=0,
                abs_tol=1e-6,
            )
        ):
            return "Proposal does not match the sampled process identity."
        if self._exclusion_reason(process) is not None:
            return f"Process is protected: {self._exclusion_reason(process)}."

        scheme = self._priority_scheme()
        if (
            decision.current_priority is None
            or decision.current_priority.get("scheme") != scheme
            or decision.proposed_priority is None
            or decision.proposed_priority.get("scheme") != scheme
        ):
            return "Priority proposal uses an unsupported platform representation."
        proposed = self._value(decision.proposed_priority)
        if proposed is None or not self._valid_priority(proposed):
            return "Proposed priority is outside the supported platform range."
        if decision.action not in {"priority_demote", "priority_restore"}:
            return "Proposal contains an unsupported priority action."
        return None

    def _complete_execution(
        self,
        process: ProcessInfo,
        decision: PriorityDecision,
        *,
        previous_priority: int | None,
        requested_priority: int | None,
        status: Literal["not_executed", "proposed", "applied", "failed"],
        error: str | None,
        dry_run: bool,
        state: _ProcessState | None = None,
        resulting_priority: int | None = None,
    ) -> PriorityDecision:
        execution_error = error
        if (
            status != "not_executed"
            and previous_priority is not None
            and requested_priority is not None
            and decision.action is not None
        ):
            reason = (
                "sustained_high_cpu"
                if decision.action == "priority_demote"
                else "sustained_low_cpu"
            )
            logged = self.process_logger.log_priority_action(
                pid=decision.pid,
                process_name=decision.process_name,
                create_time=decision.create_time or 0,
                previous_priority=previous_priority,
                requested_priority=requested_priority,
                action=decision.action,
                reason=reason,
                success=(
                    None
                    if status in {"proposed", "not_executed"}
                    else status == "applied"
                ),
                dry_run=dry_run,
                error=error,
                resulting_priority=resulting_priority,
                cpu_percent=decision.cpu_percent,
                average_cpu_percent=decision.average_cpu_percent,
                status=(
                    "dry_run" if status == "proposed"
                    else "skipped" if status == "not_executed"
                    else status
                ),
            )
            if not logged:
                execution_error = (
                    f"{execution_error}; " if execution_error else ""
                ) + "Priority action could not be written to the structured log."

        if (
            status == "failed"
            and state is not None
            and decision.action is not None
            and previous_priority is not None
            and requested_priority is not None
        ):
            state.failed_actions.add(
                (decision.action, previous_priority, requested_priority)
            )
        if status == "failed":
            _logger.warning(
                "Priority action %s failed for PID %s (%s): %s",
                decision.action,
                process.pid,
                process.name,
                execution_error,
            )
        if state is not None:
            if status == "proposed" and requested_priority is not None:
                state.projected_priority = requested_priority
            if status in {"proposed", "applied", "failed"}:
                state.cooldown_seconds = self.config.priority_adjustment_cooldown_seconds
            if status == "applied":
                state.projected_priority = resulting_priority or requested_priority or state.projected_priority
                state.high_seconds = 0
                state.recovery_seconds = 0

        result = replace(
            decision,
            action_status=status,
            execution_error=execution_error,
        )
        with self._decisions_lock:
            self._execution_results[decision.decision_id] = result
            self._decisions = deque(
                (result if item.decision_id == decision.decision_id else item
                 for item in self._decisions),
                maxlen=_MAX_RECENT_DECISIONS,
            )
        return result
