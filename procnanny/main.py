"""ProcNanny process monitoring and optional priority-adjustment loop."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path

import psutil

from .config import ProcNannyConfig, ProcNannyConfigError, load_config
from .history import ProcessHistory
from .logger import ProcessLogger
from .monitor import ProcessInfo, ProcessMonitor
from .priority import PriorityAdjuster

_logger = logging.getLogger(__name__)
_PROCESS_SCAN_ERRORS = (psutil.Error, OSError)


def run_monitor(
    config: ProcNannyConfig | None = None,
    *,
    monitor: ProcessMonitor | None = None,
    history: ProcessHistory | None = None,
    process_logger: ProcessLogger | None = None,
    stop_event: threading.Event | None = None,
    log_path: str | Path = "procnanny.log",
    on_snapshot: Callable[[list[ProcessInfo], bool], None] | None = None,
    priority_adjuster: PriorityAdjuster | None = None,
    config_provider: Callable[[], ProcNannyConfig] | None = None,
) -> None:
    """Poll, track, and log process CPU usage until stopped or interrupted."""
    settings = config if config is not None else load_config()
    process_monitor = monitor if monitor is not None else ProcessMonitor(settings)
    process_history = history if history is not None else ProcessHistory(settings)
    record_logger = (
        process_logger if process_logger is not None else ProcessLogger(log_path)
    )
    process_priority_adjuster = (
        priority_adjuster
        if priority_adjuster is not None
        else PriorityAdjuster(settings, record_logger)
    )
    shutdown_event = stop_event if stop_event is not None else threading.Event()

    _logger.info("ProcNanny monitoring started.")
    try:
        while not shutdown_event.is_set():
            if config_provider is not None:
                current_settings = config_provider()
                if current_settings is not settings:
                    settings = current_settings
                    process_monitor.update_config(settings)
                    process_history.update_config(settings)
                    process_priority_adjuster.update_config(settings)

            scan_complete = True
            try:
                processes = process_monitor.get_processes()
            except _PROCESS_SCAN_ERRORS:
                _logger.exception("Process scan failed; continuing to the next poll.")
                processes = []
                scan_complete = False
            else:
                scan_complete = getattr(process_monitor, "last_scan_complete", True)

            active_pids: set[int] = set()
            for process in processes:
                try:
                    average = process_history.add_sample(
                        process.pid, process.cpu_percent, process.create_time
                    )
                except ValueError:
                    _logger.exception(
                        "Skipping invalid CPU sample for PID %s.", process.pid
                    )
                    continue

                active_pids.add(process.pid)
                record_logger.log_process(process, average)
                decision = process_priority_adjuster.evaluate(process, average)
                process_priority_adjuster.execute(process, decision)

            if scan_complete:
                observed_pids = getattr(
                    process_monitor, "last_scan_observed_pids", None
                )
                if isinstance(observed_pids, set):
                    active_pids.update(observed_pids)
                process_history.retain_pids(active_pids)
                process_priority_adjuster.retain_pids(active_pids)
            if on_snapshot is not None:
                on_snapshot(processes, scan_complete)
            if shutdown_event.wait(settings.poll_interval_seconds):
                break
    except KeyboardInterrupt:
        _logger.info("ProcNanny stopped by user.")
    finally:
        try:
            record_logger.close()
        except OSError:
            _logger.exception("Could not close the ProcNanny process logger.")
        _logger.info("ProcNanny monitoring stopped.")


def main() -> int:
    """Load settings and run ProcNanny; report startup errors to stderr."""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    try:
        run_monitor()
    except ProcNannyConfigError as exc:
        _logger.error("Cannot start ProcNanny: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
