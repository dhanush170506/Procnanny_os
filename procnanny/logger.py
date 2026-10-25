"""Structured JSON-lines logging for observed process metrics."""

from __future__ import annotations

import json
import logging
import math
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO, cast
from uuid import NAMESPACE_URL, uuid4, uuid5

from .monitor import ProcessInfo

_logger = logging.getLogger(__name__)
_WRITE_LOCK = threading.Lock()


class ProcessLogger:
    """Append timestamped process observations to a JSON-lines log."""

    def __init__(self, path: str | Path = "procnanny.log") -> None:
        self.path = Path(path)

    def close(self) -> None:
        """Close the logger interface; records are opened and closed per write."""

    def read_recent(
        self,
        limit: int = 50,
        *,
        action_prefix: str | None = None,
        exclude_action_prefix: str | None = None,
    ) -> list[dict[str, object]]:
        """Read the newest valid structured records from the JSON-lines log."""
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("Log limit must be a positive integer.")
        if not self.path.exists():
            return []

        newest_records: list[dict[str, object]] = []
        with self.path.open("rb") as log_file:
            log_file.seek(0, 2)
            position = log_file.tell()
            remainder = b""
            while position > 0 and len(newest_records) < limit:
                chunk_size = min(4096, position)
                position -= chunk_size
                log_file.seek(position)
                chunk = log_file.read(chunk_size)
                parts = (chunk + remainder).split(b"\n")
                remainder = parts[0]
                for line in reversed(parts[1:]):
                    record = self._decode_record(line)
                    if record is not None and (
                        action_prefix is None
                        or (
                            isinstance(record.get("action"), str)
                            and record["action"].startswith(action_prefix)
                        )
                    ) and not (
                        exclude_action_prefix is not None
                        and isinstance(record.get("action"), str)
                        and record["action"].startswith(exclude_action_prefix)
                    ):
                        newest_records.append(record)
                    if len(newest_records) == limit:
                        break
            if position == 0 and remainder.strip() and len(newest_records) < limit:
                record = self._decode_record(remainder)
                if record is not None and (
                    action_prefix is None
                    or (
                        isinstance(record.get("action"), str)
                        and record["action"].startswith(action_prefix)
                    )
                ) and not (
                    exclude_action_prefix is not None
                    and isinstance(record.get("action"), str)
                    and record["action"].startswith(exclude_action_prefix)
                ):
                    newest_records.append(record)
        return list(reversed(newest_records))

    def read_priority_actions(
        self,
        *,
        limit: int = 20,
        offset: int = 0,
        status: str | None = None,
        process_name: str | None = None,
        pid: int | None = None,
    ) -> list[dict[str, object]]:
        """Read newest priority events first, applying filters before pagination."""
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            raise ValueError("Priority action limit must be between 1 and 200.")
        if isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset <= 10_000:
            raise ValueError("Priority action offset must be between 0 and 10000.")
        if not self.path.exists():
            return []

        newest_records: list[dict[str, object]] = []
        matched_count = 0
        with self.path.open("rb") as log_file:
            log_file.seek(0, 2)
            position = log_file.tell()
            remainder = b""
            while position > 0 and len(newest_records) < limit:
                chunk_size = min(4096, position)
                position -= chunk_size
                log_file.seek(position)
                parts = (log_file.read(chunk_size) + remainder).split(b"\n")
                remainder = parts[0]
                for line in reversed(parts[1:]):
                    record = self._decode_record(line)
                    normalized = (
                        self._normalize_priority_record(record)
                        if record is not None
                        else None
                    )
                    if normalized is None or not self._matches_priority_filters(
                        normalized, status, process_name, pid
                    ):
                        continue
                    if matched_count < offset:
                        matched_count += 1
                        continue
                    newest_records.append(normalized)
                    if len(newest_records) == limit:
                        break
            if (
                position == 0
                and remainder.strip()
                and len(newest_records) < limit
            ):
                record = self._decode_record(remainder)
                normalized = (
                    self._normalize_priority_record(record)
                    if record is not None
                    else None
                )
                if normalized is not None and self._matches_priority_filters(
                    normalized, status, process_name, pid
                ):
                    if matched_count < offset:
                        matched_count += 1
                    else:
                        newest_records.append(normalized)
        return newest_records

    @staticmethod
    def _matches_priority_filters(
        record: dict[str, object],
        status: str | None,
        process_name: str | None,
        pid: int | None,
    ) -> bool:
        action = record.get("action")
        if not isinstance(action, str) or not action.startswith("priority_"):
            return False
        if status is not None and ProcessLogger._priority_status(record) != status:
            return False
        if pid is not None and record.get("pid") != pid:
            return False
        if process_name is not None:
            name = record.get("process_name")
            if not isinstance(name, str) or process_name.casefold() not in name.casefold():
                return False
        return True

    @staticmethod
    def _priority_status(record: dict[str, object]) -> str:
        status = record.get("status")
        if isinstance(status, str):
            return status
        if record.get("success") is True:
            return "applied"
        if record.get("success") is False:
            return "failed"
        if record.get("dry_run") is True:
            return "dry_run"
        return "skipped"

    @staticmethod
    def _normalize_priority_record(
        record: dict[str, object],
    ) -> dict[str, object] | None:
        if (
            not isinstance(record.get("timestamp"), str)
            or not record["timestamp"]
            or not isinstance(record.get("process_name"), str)
            or not record["process_name"].strip()
            or not isinstance(record.get("pid"), int)
            or isinstance(record.get("pid"), bool)
            or not isinstance(record.get("action"), str)
            or not record["action"].startswith("priority_")
        ):
            _logger.warning("Skipping an incomplete ProcNanny priority record.")
            return None
        normalized = dict(record)
        event_id = normalized.get("event_id")
        if not isinstance(event_id, str) or not event_id:
            normalized["event_id"] = str(
                uuid5(NAMESPACE_URL, json.dumps(record, sort_keys=True, default=str))
            )
        status = ProcessLogger._priority_status(record)
        if status not in {
            "proposed",
            "dry_run",
            "applied",
            "failed",
            "skipped",
            "excluded",
        }:
            _logger.warning("Skipping a ProcNanny priority record with invalid status.")
            return None
        normalized["status"] = status
        normalized.setdefault("proposed_priority", record.get("requested_priority"))
        normalized.setdefault(
            "resulting_priority",
            record.get("nice_value") if status == "applied" else None,
        )
        normalized.setdefault("cpu_percent", None)
        normalized.setdefault("average_cpu_percent", None)
        normalized.setdefault("error", None)
        normalized.setdefault("reason", "")
        normalized.setdefault("dry_run", False)
        normalized.setdefault("success", None)
        if (
            not isinstance(normalized["dry_run"], bool)
            or not isinstance(normalized["reason"], str)
            or (
                normalized["error"] is not None
                and not isinstance(normalized["error"], str)
            )
            or (
                normalized["success"] is not None
                and not isinstance(normalized["success"], bool)
            )
        ):
            _logger.warning("Skipping a malformed ProcNanny priority record.")
            return None
        for field in (
            "create_time",
            "cpu_percent",
            "average_cpu_percent",
            "previous_priority",
            "proposed_priority",
            "requested_priority",
            "resulting_priority",
            "nice_value",
        ):
            value = normalized.get(field)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, (int, float))
            ):
                _logger.warning("Skipping a malformed ProcNanny priority record.")
                return None
            if isinstance(value, float) and not math.isfinite(value):
                _logger.warning("Skipping a malformed ProcNanny priority record.")
                return None
        return normalized

    @staticmethod
    def _decode_record(line: bytes) -> dict[str, object] | None:
        if not line.strip():
            return None
        try:
            record = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            _logger.warning("Skipping a malformed ProcNanny log record.")
            return None
        if isinstance(record, dict):
            return cast(dict[str, object], record)
        _logger.warning("Skipping a non-object ProcNanny log record.")
        return None

    def log_process(
        self,
        process: ProcessInfo,
        average_cpu_percent: float,
        action: str = "monitor",
        reason: str = "periodic_sample",
    ) -> bool:
        """Write a process record, returning False and reporting I/O failures."""
        record = {
            "timestamp": process.observed_at or datetime.now(UTC).isoformat(),
            "pid": process.pid,
            "process_name": process.name,
            "cpu_percent": process.cpu_percent,
            "average_cpu_percent": average_cpu_percent,
            "nice_value": process.nice_value,
            "status": process.status,
            "action": action,
            "reason": reason,
        }
        try:
            with _WRITE_LOCK, self.path.open("a", encoding="utf-8") as log_file:
                self._write_record(log_file, record)
        except OSError:
            _logger.exception(
                "Could not write ProcNanny log record to '%s'.", self.path
            )
            return False
        return True

    def log_priority_action(
        self,
        *,
        pid: int,
        process_name: str,
        create_time: float,
        previous_priority: int,
        requested_priority: int,
        action: str,
        reason: str,
        success: bool | None,
        dry_run: bool,
        error: str | None = None,
        resulting_priority: int | None = None,
        cpu_percent: float | None = None,
        average_cpu_percent: float | None = None,
        status: str | None = None,
    ) -> bool:
        """Write a structured record for a proposed or attempted priority change."""
        event_status = status or (
            "applied" if success is True
            else "failed" if success is False
            else "dry_run" if dry_run
            else "skipped"
        )
        return self.log_priority_event(
            pid=pid,
            process_name=process_name,
            create_time=create_time,
            cpu_percent=cpu_percent,
            average_cpu_percent=average_cpu_percent,
            previous_priority=previous_priority,
            proposed_priority=requested_priority,
            resulting_priority=resulting_priority,
            action=action,
            status=event_status,
            reason=reason,
            dry_run=dry_run,
            error=error,
        )

    def log_priority_event(
        self,
        *,
        pid: int,
        process_name: str,
        create_time: float | None,
        cpu_percent: float | None,
        average_cpu_percent: float | None,
        previous_priority: int | None,
        proposed_priority: int | None,
        resulting_priority: int | None,
        action: str,
        status: str,
        reason: str,
        dry_run: bool,
        error: str | None = None,
    ) -> bool:
        """Persist one structured priority decision/action event."""
        record = {
            "event_id": str(uuid4()),
            "timestamp": datetime.now(UTC).isoformat(),
            "pid": pid,
            "process_name": process_name,
            "create_time": create_time,
            "cpu_percent": cpu_percent,
            "average_cpu_percent": average_cpu_percent,
            "previous_priority": previous_priority,
            "proposed_priority": proposed_priority,
            "requested_priority": proposed_priority,
            "resulting_priority": resulting_priority,
            "nice_value": (
                resulting_priority
                if resulting_priority is not None
                else proposed_priority
                if status == "applied"
                else previous_priority
            ),
            "action": action,
            "status": status,
            "reason": reason,
            "success": (
                True if status == "applied"
                else False if status == "failed"
                else None
            ),
            "dry_run": dry_run,
            "error": error,
        }
        try:
            with _WRITE_LOCK, self.path.open("a", encoding="utf-8") as log_file:
                self._write_record(log_file, record)
        except OSError:
            _logger.exception(
                "Could not write ProcNanny priority record to '%s'.", self.path
            )
            return False
        return True

    @staticmethod
    def _write_record(log_file: TextIO, record: dict[str, object]) -> None:
        log_file.write(json.dumps(record, separators=(",", ":")) + "\n")
