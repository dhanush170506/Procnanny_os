"""Local FastAPI interface for ProcNanny monitoring and priority status."""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import psutil
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, StrictBool, StrictFloat, StrictInt

from .config import (
    DEFAULT_CONFIG_PATH,
    ProcNannyConfig,
    ProcNannyConfigError,
    ProcNannyConfigPersistenceError,
    load_config,
    save_config,
)
from .history import ProcessHistory
from .logger import ProcessLogger
from .main import run_monitor
from .monitor import ProcessInfo, ProcessMonitor
from .priority import PriorityAdjuster

_logger = logging.getLogger(__name__)

PriorityActionStatus = Literal[
    "proposed",
    "dry_run",
    "applied",
    "failed",
    "skipped",
    "excluded",
]


class PriorityActionRecord(BaseModel):
    """Validated persisted priority decision/action event."""

    event_id: str
    timestamp: str
    process_name: str
    pid: int
    create_time: float | None = None
    cpu_percent: float | None = None
    average_cpu_percent: float | None = None
    previous_priority: int | None = None
    proposed_priority: int | None = None
    requested_priority: int | None = None
    resulting_priority: int | None = None
    nice_value: int | None = None
    action: str
    status: PriorityActionStatus
    reason: str
    success: bool | None = None
    dry_run: bool
    error: str | None = None


class PriorityActionsResponse(BaseModel):
    """Paginated priority-action history payload."""

    source: Literal["live"] = "live"
    enabled: bool
    dry_run: bool
    limit: int
    offset: int
    actions: list[PriorityActionRecord]


class SettingsUpdate(BaseModel):
    """Settings accepted from the local configuration page."""

    model_config = {"extra": "forbid"}

    high_cpu_threshold_percent: StrictFloat
    low_cpu_threshold_percent: StrictFloat
    poll_interval_seconds: StrictFloat
    history_window_seconds: StrictFloat
    excluded_processes: list[str]
    priority_adjustment_enabled: StrictBool
    priority_adjustment_dry_run: StrictBool
    cooldown_seconds: StrictFloat
    high_duration_seconds: StrictFloat
    recovery_duration_seconds: StrictFloat
    step: StrictInt
    confirm_live_adjustment: StrictBool = False


class MonitoringService:
    """Own the lifecycle and latest published snapshot of the monitor thread."""

    def __init__(
        self,
        config: ProcNannyConfig,
        log_path: str | Path,
        config_path: str | Path | None = None,
    ) -> None:
        self.config = config
        self.log_path = Path(log_path)
        self.config_path = Path(config_path) if config_path is not None else None
        self.process_logger = ProcessLogger(self.log_path)
        self.priority_adjuster = PriorityAdjuster(config, self.process_logger)
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._stop_event: threading.Event | None = None
        self._state = "stopped"
        self._last_error: str | None = None
        self._last_scan_complete: bool | None = None
        self._system_cpu_primed = False
        self._history: ProcessHistory | None = None
        self._snapshot: dict[str, Any] = {
            "source": "live",
            "updated_at": None,
            "stale": False,
            "system_cpu_percent": None,
            "processes": [],
        }

    def get_status(self) -> dict[str, object]:
        with self._lock:
            return {
                "state": self._state,
                "monitoring": self._state in {"starting", "running", "stopping"},
                "poll_interval_seconds": self.config.poll_interval_seconds,
                "history_window_seconds": self.config.history_window_seconds,
                "high_cpu_threshold_percent": self.config.cpu_high_threshold_percent,
                "low_cpu_threshold_percent": self.config.cpu_low_threshold_percent,
                "excluded_processes": list(self.config.excluded_processes),
                "last_scan_complete": self._last_scan_complete,
                "last_updated": self._snapshot["updated_at"],
                "last_error": self._last_error,
                "priority_adjustment_enabled": (
                    self.config.priority_adjustment_enabled
                ),
                "priority_adjustment": self.priority_adjuster.get_status(),
                "settings_persistent": self.config_path is not None,
            }

    def get_snapshot(self) -> dict[str, object]:
        with self._lock:
            return {
                **self._snapshot,
                "processes": list(self._snapshot["processes"]),
            }

    def update_config(self, config: ProcNannyConfig) -> dict[str, object]:
        """Persist settings before making them active in the monitor pipeline."""
        with self._lock:
            if self.config_path is None:
                raise RuntimeError(
                    "Persistent settings are unavailable for an in-memory configuration."
                )
            updated_config = save_config(config, self.config_path)
            self.config = updated_config
            self.priority_adjuster.update_config(updated_config)
            return self.get_status()

    def start(self) -> dict[str, object]:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return self.get_status()

            self._stop_event = threading.Event()
            self._state = "starting"
            self._last_error = None
            self._last_scan_complete = None
            self._system_cpu_primed = False
            self._snapshot = {
                "source": "live",
                "updated_at": None,
                "stale": False,
                "system_cpu_percent": None,
                "processes": [],
            }
            self._thread = threading.Thread(
                target=self._run,
                args=(self._stop_event,),
                name="procnanny-monitor",
                daemon=True,
            )
            self._thread.start()
            return self.get_status()

    def stop(self) -> dict[str, object]:
        with self._lock:
            thread = self._thread
            stop_event = self._stop_event
            if thread is None or not thread.is_alive():
                if self._state != "error":
                    self._state = "stopped"
                return self.get_status()
            self._state = "stopping"
            if stop_event is not None:
                stop_event.set()

        thread.join(timeout=max(2.0, self.config.poll_interval_seconds * 2))
        return self.get_status()

    def _run(self, stop_event: threading.Event) -> None:
        with self._lock:
            self._state = "running"
            history = ProcessHistory(self.config)
            self._history = history
        try:
            run_monitor(
                self.config,
                monitor=ProcessMonitor(self.config),
                history=history,
                process_logger=self.process_logger,
                stop_event=stop_event,
                log_path=self.log_path,
                on_snapshot=self._publish_snapshot,
                priority_adjuster=self.priority_adjuster,
                config_provider=lambda: self.config,
            )
        except Exception as exc:
            _logger.exception("ProcNanny monitor thread stopped unexpectedly.")
            with self._lock:
                self._state = "error"
                self._last_error = f"Monitoring stopped unexpectedly: {exc}"
                if self._snapshot["updated_at"] is not None:
                    self._snapshot["stale"] = True
        else:
            with self._lock:
                self._state = "stopped"
                if self._snapshot["updated_at"] is not None:
                    self._snapshot["stale"] = True

    def _publish_snapshot(
        self,
        processes: list[ProcessInfo],
        scan_complete: bool,
    ) -> None:
        with self._lock:
            self._last_scan_complete = scan_complete
            if not scan_complete:
                self._snapshot["stale"] = True
                self._last_error = (
                    "Process scan was incomplete; retaining last good sample."
                )
                return

            try:
                cpu_sample = psutil.cpu_percent(interval=None)
            except (psutil.Error, OSError):
                _logger.exception("Could not sample system CPU usage.")
                system_cpu_percent = None
                self._last_error = "System CPU usage is temporarily unavailable."
            else:
                if self._system_cpu_primed:
                    system_cpu_percent = cpu_sample
                    self._last_error = None
                else:
                    self._system_cpu_primed = True
                    system_cpu_percent = None

            process_records = []
            for process in processes:
                process_records.append(
                    {
                        "pid": process.pid,
                        "name": process.name,
                        "create_time": process.create_time,
                        "status": process.status,
                        "observed_at": process.observed_at,
                        "cpu_percent": process.cpu_percent,
                        "average_cpu_percent": (
                            self._history.get_average(process.pid)
                            if self._history is not None
                            else None
                        ),
                        "nice_value": process.nice_value,
                        "priority_value": process.nice_value,
                        "priority_scheme": process.priority_scheme,
                        "priority_label": process.priority_label,
                        "priority_unavailable_reason": (
                            process.priority_unavailable_reason
                        ),
                    }
                )
            self._snapshot = {
                "source": "live",
                "updated_at": datetime.now(UTC).isoformat(),
                "stale": False,
                "system_cpu_percent": system_cpu_percent,
                "processes": process_records,
            }


def create_app(
    config: ProcNannyConfig | None = None,
    log_path: str | Path | None = None,
    config_path: str | Path | None = None,
) -> FastAPI:
    """Create the API app; monitoring remains stopped until explicitly started."""
    persistent_config_path = (
        Path(config_path)
        if config_path is not None
        else DEFAULT_CONFIG_PATH if config is None else None
    )
    settings = (
        config
        if config is not None
        else load_config(persistent_config_path or DEFAULT_CONFIG_PATH)
    )
    process_log_path = (
        log_path
        if log_path is not None
        else os.environ.get("PROCNANNY_LOG_PATH", "procnanny.log")
    )
    service = MonitoringService(settings, process_log_path, persistent_config_path)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None, None]:
        yield
        service.stop()

    app = FastAPI(
        title="ProcNanny API",
        description=(
            "Local process monitoring API with opt-in, configurable priority "
            "adjustment. Priority changes are disabled by default."
        ),
        version="0.1.0",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://172.16.160.134:5173",
            "http://localhost:5174",
            "http://127.0.0.1:5174",
        ],
        allow_methods=["GET", "POST", "PUT"],
        allow_headers=["Content-Type"],
    )
    app.state.monitoring_service = service

    @app.get("/api/status")
    def get_status() -> dict[str, object]:
        return service.get_status()

    @app.get("/api/settings")
    def get_settings() -> dict[str, object]:
        return service.get_status()

    @app.put("/api/settings")
    def update_settings(payload: SettingsUpdate) -> dict[str, object]:
        current = service.config
        enabling_live_adjustment = (
            payload.priority_adjustment_enabled
            and not payload.priority_adjustment_dry_run
            and (
                not current.priority_adjustment_enabled
                or current.priority_adjustment_dry_run
            )
        )
        if enabling_live_adjustment and not payload.confirm_live_adjustment:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Explicit confirmation is required before enabling live "
                    "priority adjustment."
                ),
            )

        candidate = replace(
            current,
            cpu_high_threshold_percent=payload.high_cpu_threshold_percent,
            cpu_low_threshold_percent=payload.low_cpu_threshold_percent,
            poll_interval_seconds=payload.poll_interval_seconds,
            history_window_seconds=payload.history_window_seconds,
            excluded_processes=tuple(payload.excluded_processes),
            priority_adjustment_enabled=payload.priority_adjustment_enabled,
            priority_adjustment_dry_run=payload.priority_adjustment_dry_run,
            priority_adjustment_cooldown_seconds=payload.cooldown_seconds,
            priority_adjustment_high_duration_seconds=payload.high_duration_seconds,
            priority_adjustment_recovery_duration_seconds=(
                payload.recovery_duration_seconds
            ),
            priority_adjustment_step=payload.step,
        )
        try:
            return service.update_config(candidate)
        except ProcNannyConfigPersistenceError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except ProcNannyConfigError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/processes")
    def get_processes() -> dict[str, object]:
        return service.get_snapshot()

    @app.get("/api/logs")
    def get_logs(limit: int = Query(default=50, ge=1, le=200)) -> dict[str, object]:
        try:
            logs = service.process_logger.read_recent(
                limit,
                exclude_action_prefix="priority_",
            )
        except OSError as exc:
            _logger.exception("Could not read ProcNanny log records.")
            raise HTTPException(
                status_code=503,
                detail="ProcNanny log records are temporarily unavailable.",
            ) from exc
        return {"source": "live", "logs": logs}

    @app.get("/api/priority/actions")
    def get_priority_actions(
        limit: int = Query(default=20, ge=1, le=200),
        offset: int = Query(default=0, ge=0, le=10_000),
        status: PriorityActionStatus | None = None,
        process_name: str | None = Query(default=None, min_length=1, max_length=256),
        pid: int | None = Query(default=None, ge=1),
    ) -> PriorityActionsResponse:
        try:
            actions = service.process_logger.read_priority_actions(
                limit=limit,
                offset=offset,
                status=status,
                process_name=process_name,
                pid=pid,
            )
        except OSError as exc:
            _logger.exception("Could not read ProcNanny priority action records.")
            raise HTTPException(
                status_code=503,
                detail="ProcNanny priority action records are temporarily unavailable.",
            ) from exc
        return PriorityActionsResponse(
            enabled=service.config.priority_adjustment_enabled,
            dry_run=service.config.priority_adjustment_dry_run,
            limit=limit,
            offset=offset,
            actions=actions,
        )

    @app.get("/api/priority/evaluations")
    def get_priority_evaluations(
        limit: int = Query(default=50, ge=1, le=1000),
    ) -> dict[str, object]:
        return {
            "source": "live",
            "enabled": service.config.priority_adjustment_enabled,
            "dry_run": service.config.priority_adjustment_dry_run,
            "evaluations": service.priority_adjuster.get_recent_decisions(limit),
        }

    @app.post("/api/monitor/start")
    def start_monitoring() -> dict[str, object]:
        return service.start()

    @app.post("/api/monitor/stop")
    def stop_monitoring() -> dict[str, object]:
        return service.stop()

    return app


app = create_app()
