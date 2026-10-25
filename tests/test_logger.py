"""Tests for structured process log records."""

import json
import logging
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from procnanny.logger import ProcessLogger
from procnanny.monitor import ProcessInfo


class ProcessLoggerTests(unittest.TestCase):
    def log_priority_event(
        self,
        logger: ProcessLogger,
        pid: int,
        status: str,
    ) -> bool:
        return logger.log_priority_event(
            pid=pid,
            process_name=f"worker-{pid}",
            create_time=1000.0 + pid,
            cpu_percent=81.5,
            average_cpu_percent=77.25,
            previous_priority=0,
            proposed_priority=1,
            resulting_priority=1 if status == "applied" else None,
            action="priority_demote",
            status=status,
            reason="sustained_high_cpu",
            dry_run=status == "dry_run",
            error="denied" if status == "failed" else None,
        )

    def test_writes_structured_timestamped_record(self) -> None:
        process = ProcessInfo(42, "worker", 28.5, 7)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "procnanny.log"

            result = ProcessLogger(path).log_process(process, 26.25)

            self.assertTrue(result)
            record = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(record["pid"], 42)
        self.assertEqual(record["process_name"], "worker")
        self.assertEqual(record["cpu_percent"], 28.5)
        self.assertEqual(record["average_cpu_percent"], 26.25)
        self.assertEqual(record["nice_value"], 7)
        self.assertEqual(record["action"], "monitor")
        self.assertEqual(record["reason"], "periodic_sample")
        self.assertTrue(record["timestamp"].endswith("+00:00"))

    def test_preserves_observation_time_and_process_status(self) -> None:
        process = ProcessInfo(
            42,
            "worker",
            28.5,
            None,
            status="sleeping",
            observed_at="2026-10-24T12:00:00+00:00",
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "procnanny.log"

            self.assertTrue(ProcessLogger(path).log_process(process, 26.25))
            record = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(record["timestamp"], process.observed_at)
        self.assertEqual(record["status"], "sleeping")
        self.assertIsNone(record["nice_value"])

    def test_reports_io_errors_and_returns_false(self) -> None:
        process = ProcessInfo(42, "worker", 28.5, 7)
        process_logger = ProcessLogger("unwritable.log")

        with (
            patch.object(Path, "open", side_effect=OSError("disk unavailable")),
            self.assertLogs("procnanny.logger", level=logging.ERROR),
        ):
            result = process_logger.log_process(process, 28.5)

        self.assertFalse(result)

    def test_close_is_safe_without_a_persistent_file_handle(self) -> None:
        process_logger = ProcessLogger("unused.log")

        process_logger.close()

    def test_reads_only_the_newest_valid_log_records_in_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "procnanny.log"
            process_logger = ProcessLogger(path)
            for pid in (1, 2, 3):
                process_logger.log_process(ProcessInfo(pid, "worker", 1.0, 0), 1.0)
            with path.open("a", encoding="utf-8") as log_file:
                log_file.write("not-json\n")

            records = process_logger.read_recent(limit=2)

        self.assertEqual([record["pid"] for record in records], [2, 3])

    def test_priority_event_has_id_status_and_measurements_and_survives_reload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "procnanny.log"
            logger = ProcessLogger(path)
            self.log_priority_event(logger, 42, "dry_run")

            record = ProcessLogger(path).read_priority_actions()[0]

        self.assertTrue(record["event_id"])
        self.assertEqual(record["status"], "dry_run")
        self.assertEqual(record["cpu_percent"], 81.5)
        self.assertEqual(record["average_cpu_percent"], 77.25)
        self.assertEqual(record["proposed_priority"], 1)
        self.assertIsNone(record["resulting_priority"])
        self.assertIsNone(record["success"])

    def test_priority_history_is_newest_first_and_filters_before_pagination(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "procnanny.log"
            logger = ProcessLogger(path)
            self.log_priority_event(logger, 10, "applied")
            self.log_priority_event(logger, 20, "failed")
            self.log_priority_event(logger, 30, "dry_run")

            newest = logger.read_priority_actions(limit=2)
            failed = logger.read_priority_actions(status="failed")
            by_name = logger.read_priority_actions(process_name="WORKER-3")
            applied = logger.read_priority_actions(status="applied")
            second_applied = logger.read_priority_actions(
                status="applied",
                offset=1,
            )

        self.assertEqual([item["pid"] for item in newest], [30, 20])
        self.assertEqual([item["status"] for item in failed], ["failed"])
        self.assertEqual(failed[0]["error"], "denied")
        self.assertEqual(applied[0]["resulting_priority"], 1)
        self.assertEqual([item["pid"] for item in by_name], [30])
        self.assertEqual(second_applied, [])

    def test_priority_history_skips_malformed_records_and_is_empty_when_missing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "procnanny.log"
            logger = ProcessLogger(path)
            self.assertEqual(logger.read_priority_actions(), [])
            with path.open("w", encoding="utf-8") as log_file:
                log_file.write("not-json\n")
                log_file.write(
                    '{"timestamp":"2026-10-24T12:00:00+00:00",'
                    '"process_name":"legacy-worker","pid":1,'
                    '"action":"priority_demote","success":null,'
                    '"dry_run":false,"reason":"legacy"}\n'
                )
            self.log_priority_event(logger, 42, "applied")

            records = ProcessLogger(path).read_priority_actions()
            stable_legacy_id = records[1]["event_id"]

        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["status"], "applied")
        self.assertEqual(records[1]["status"], "skipped")
        self.assertTrue(stable_legacy_id)

    def test_concurrent_priority_writes_produce_complete_records(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "procnanny.log"
            writers = [ProcessLogger(path) for _ in range(8)]
            with ThreadPoolExecutor(max_workers=8) as executor:
                results = list(
                    executor.map(
                        lambda item: self.log_priority_event(
                            writers[item % len(writers)],
                            item + 100,
                            "applied",
                        ),
                        range(64),
                    )
                )
            records = ProcessLogger(path).read_priority_actions(limit=100)

        self.assertTrue(all(results))
        self.assertEqual(len(records), 64)
        self.assertEqual(len({record["event_id"] for record in records}), 64)


if __name__ == "__main__":
    unittest.main()
