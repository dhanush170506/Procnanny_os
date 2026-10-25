"""Tests for ProcNanny's monitoring loop."""

import json
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, call, patch

import psutil

from procnanny.config import ProcNannyConfigError, load_config
from procnanny.history import ProcessHistory
from procnanny.logger import ProcessLogger
from procnanny.main import main, run_monitor
from procnanny.monitor import ProcessInfo


class ProcNannyMainTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = replace(load_config(), poll_interval_seconds=0.25)

    def test_runs_poll_history_and_logging_then_shuts_down(self) -> None:
        process = ProcessInfo(42, "worker", 30, 5)
        monitor = Mock()
        monitor.get_processes.return_value = [process]
        history = Mock()
        history.add_sample.return_value = 30
        record_logger = Mock()
        stop_event = Mock(spec=threading.Event)
        stop_event.is_set.side_effect = [False, True]
        stop_event.wait.return_value = False

        run_monitor(
            self.config,
            monitor=monitor,
            history=history,
            process_logger=record_logger,
            stop_event=stop_event,
        )

        monitor.get_processes.assert_called_once_with()
        history.add_sample.assert_called_once_with(42, 30, None)
        history.retain_pids.assert_called_once_with({42})
        record_logger.log_process.assert_called_once_with(process, 30)
        stop_event.wait.assert_called_once_with(0.25)
        record_logger.close.assert_called_once_with()

    def test_monitoring_loop_applies_new_configuration_on_next_cycle(self) -> None:
        updated = replace(
            self.config,
            cpu_high_threshold_percent=85,
            priority_adjustment_enabled=True,
        )
        monitor = Mock()
        monitor.get_processes.return_value = []
        history = Mock()
        adjuster = Mock()
        stop_event = Mock(spec=threading.Event)
        stop_event.is_set.side_effect = [False, False, True]
        stop_event.wait.return_value = False
        config_provider = Mock(side_effect=[self.config, updated])

        run_monitor(
            self.config,
            monitor=monitor,
            history=history,
            process_logger=Mock(),
            stop_event=stop_event,
            priority_adjuster=adjuster,
            config_provider=config_provider,
        )

        monitor.update_config.assert_called_once_with(updated)
        history.update_config.assert_called_once_with(updated)
        adjuster.update_config.assert_called_once_with(updated)
        stop_event.wait.assert_has_calls(
            [call(0.25), call(0.25)]
        )

    def test_integrates_monitor_history_and_structured_file_logging(self) -> None:
        process = ProcessInfo(42, "worker", 30, 5)
        monitor = Mock()
        monitor.get_processes.return_value = [process]
        stop_event = Mock(spec=threading.Event)
        stop_event.is_set.side_effect = [False, True]
        stop_event.wait.return_value = False
        history = ProcessHistory(self.config)

        with tempfile.TemporaryDirectory() as directory:
            log_path = Path(directory) / "procnanny.log"
            run_monitor(
                self.config,
                monitor=monitor,
                history=history,
                process_logger=ProcessLogger(log_path),
                stop_event=stop_event,
            )

            records = [
                json.loads(line)
                for line in log_path.read_text(encoding="utf-8").splitlines()
            ]

        record = next(item for item in records if item["action"] == "monitor")
        self.assertEqual(record["pid"], 42)
        self.assertEqual(record["cpu_percent"], 30)
        self.assertEqual(record["average_cpu_percent"], 30)
        self.assertTrue(
            any(
                item["action"] == "priority_evaluation"
                and item["status"] == "skipped"
                for item in records
            )
        )
        self.assertEqual(history.get_average(42), 30)

    def test_monitoring_loop_runs_enabled_policy_in_dry_run_mode(self) -> None:
        config = replace(
            self.config,
            poll_interval_seconds=5,
            priority_adjustment_enabled=True,
            priority_adjustment_dry_run=True,
            priority_adjustment_high_duration_seconds=5,
        )
        process = ProcessInfo(4242, "worker", 80, 0, 1000.0)
        monitor = Mock()
        monitor.get_processes.return_value = [process]
        monitor.last_scan_complete = True
        monitor.last_scan_observed_pids = {process.pid}
        target = Mock()
        target.name.return_value = "worker"
        target.create_time.return_value = process.create_time
        target.status.return_value = "running"
        target.nice.return_value = 0
        stop_event = Mock(spec=threading.Event)
        stop_event.is_set.side_effect = [False, True]
        stop_event.wait.return_value = False

        with tempfile.TemporaryDirectory() as directory:
            log_path = Path(directory) / "procnanny.log"
            with (
                patch("procnanny.priority.sys.platform", "linux"),
                patch("procnanny.priority.psutil.Process", return_value=target),
            ):
                run_monitor(
                    config,
                    monitor=monitor,
                    history=ProcessHistory(config),
                    process_logger=ProcessLogger(log_path),
                    stop_event=stop_event,
                )

            actions = ProcessLogger(log_path).read_priority_actions(limit=20)

        proposals = [item for item in actions if item["action"] == "priority_demote"]
        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0]["status"], "dry_run")
        self.assertTrue(proposals[0]["dry_run"])
        target.nice.assert_called_once_with()

    def test_cleans_up_history_for_pids_absent_from_next_scan(self) -> None:
        first = ProcessInfo(42, "worker", 30, 5)
        monitor = Mock()
        monitor.get_processes.side_effect = [[first], []]
        history = Mock()
        history.add_sample.return_value = 30
        stop_event = Mock(spec=threading.Event)
        stop_event.is_set.side_effect = [False, False, True]
        stop_event.wait.return_value = False

        run_monitor(
            self.config,
            monitor=monitor,
            history=history,
            process_logger=Mock(),
            stop_event=stop_event,
        )

        self.assertEqual(history.retain_pids.call_args_list[0].args, ({42},))
        self.assertEqual(history.retain_pids.call_args_list[1].args, (set(),))

    def test_pid_reuse_after_incomplete_scan_starts_fresh_history(self) -> None:
        old_process = ProcessInfo(42, "old-worker", 80, 5, 1000.0)
        new_process = ProcessInfo(42, "new-worker", 10, 5, 2000.0)
        monitor = Mock()
        monitor.get_processes.side_effect = [
            [old_process],
            [],
            [new_process],
        ]
        monitor.last_scan_complete = False
        history = ProcessHistory(self.config)
        stop_event = Mock(spec=threading.Event)
        stop_event.is_set.side_effect = [False, False, False, True]
        stop_event.wait.return_value = False

        run_monitor(
            self.config,
            monitor=monitor,
            history=history,
            process_logger=Mock(),
            stop_event=stop_event,
        )

        self.assertEqual(history.get_average(42), 10)

    def test_preserves_history_after_an_incomplete_scan(self) -> None:
        monitor = Mock()
        monitor.get_processes.return_value = []
        monitor.last_scan_complete = False
        history = Mock()
        stop_event = Mock(spec=threading.Event)
        stop_event.is_set.side_effect = [False, True]
        stop_event.wait.return_value = False

        run_monitor(
            self.config,
            monitor=monitor,
            history=history,
            process_logger=Mock(),
            stop_event=stop_event,
        )

        history.retain_pids.assert_not_called()

    def test_preserves_history_for_visible_process_with_unavailable_metrics(
        self,
    ) -> None:
        monitor = Mock()
        monitor.get_processes.return_value = []
        monitor.last_scan_complete = True
        monitor.last_scan_observed_pids = {42}
        history = Mock()
        stop_event = Mock(spec=threading.Event)
        stop_event.is_set.side_effect = [False, True]
        stop_event.wait.return_value = False

        run_monitor(
            self.config,
            monitor=monitor,
            history=history,
            process_logger=Mock(),
            stop_event=stop_event,
        )

        history.retain_pids.assert_called_once_with({42})

    def test_keeps_running_and_only_cleans_history_after_recovery(self) -> None:
        monitor = Mock()
        monitor.get_processes.side_effect = [
            psutil.AccessDenied(),
            [],
        ]
        history = Mock()
        stop_event = Mock(spec=threading.Event)
        stop_event.is_set.side_effect = [False, False, True]
        stop_event.wait.return_value = False

        with self.assertLogs("procnanny.main", level="ERROR"):
            run_monitor(
                self.config,
                monitor=monitor,
                history=history,
                process_logger=Mock(),
                stop_event=stop_event,
            )

        self.assertEqual(monitor.get_processes.call_count, 2)
        history.retain_pids.assert_called_once_with(set())

    def test_keyboard_interrupt_closes_logger_cleanly(self) -> None:
        monitor = Mock()
        monitor.get_processes.side_effect = KeyboardInterrupt
        record_logger = Mock()

        run_monitor(
            self.config,
            monitor=monitor,
            history=Mock(),
            process_logger=record_logger,
            stop_event=Mock(spec=threading.Event),
        )

        record_logger.close.assert_called_once_with()

    def test_continues_monitoring_when_a_record_cannot_be_written(self) -> None:
        process = ProcessInfo(42, "worker", 30, 5)
        monitor = Mock()
        monitor.get_processes.return_value = [process]
        record_logger = Mock()
        record_logger.log_process.return_value = False
        stop_event = Mock(spec=threading.Event)
        stop_event.is_set.side_effect = [False, True]
        stop_event.wait.return_value = False

        run_monitor(
            self.config,
            monitor=monitor,
            history=Mock(),
            process_logger=record_logger,
            stop_event=stop_event,
        )

        monitor.get_processes.assert_called_once_with()
        record_logger.close.assert_called_once_with()

    def test_evaluates_priority_policy_with_the_rolling_average(self) -> None:
        process = ProcessInfo(42, "worker", 80, 0, 1234.0)
        monitor = Mock()
        monitor.get_processes.return_value = [process]
        history = Mock()
        history.add_sample.return_value = 75
        priority_adjuster = Mock()
        stop_event = Mock(spec=threading.Event)
        stop_event.is_set.side_effect = [False, True]
        stop_event.wait.return_value = False

        run_monitor(
            self.config,
            monitor=monitor,
            history=history,
            process_logger=Mock(),
            stop_event=stop_event,
            priority_adjuster=priority_adjuster,
        )

        priority_adjuster.evaluate.assert_called_once_with(process, 75)
        priority_adjuster.execute.assert_called_once_with(
            process,
            priority_adjuster.evaluate.return_value,
        )

    def test_entrypoint_reports_configuration_errors(self) -> None:
        with (
            patch(
                "procnanny.main.load_config",
                side_effect=ProcNannyConfigError("invalid settings"),
            ),
            self.assertLogs("procnanny.main", level="ERROR"),
        ):
            self.assertEqual(main(), 1)


if __name__ == "__main__":
    unittest.main()
