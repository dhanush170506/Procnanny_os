"""Tests for read-only psutil process monitoring."""

import unittest
from dataclasses import replace
from unittest.mock import Mock, patch

import psutil

from procnanny.config import load_config
from procnanny.monitor import ProcessMonitor


def make_process(
    pid: int = 123,
    name: str = "worker",
    nice: int = 5,
    create_time: float = 1000.0,
    cpu_samples: tuple[float, ...] = (0.0, 27.5),
    uids: object | None = None,
    status: str | None = "running",
) -> Mock:
    process = Mock()
    process.pid = pid
    process.info = {
        "pid": pid,
        "name": name,
        "nice": nice,
        "create_time": create_time,
        "status": status,
    }
    process.nice.return_value = nice
    if uids is not None:
        process.info["uids"] = uids
    process.cpu_percent.side_effect = cpu_samples
    return process


class ProcessMonitorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = replace(load_config(), excluded_processes=())

    @patch("procnanny.monitor.psutil.process_iter")
    def test_primes_cpu_counter_then_returns_process_metrics(
        self, process_iter: Mock
    ) -> None:
        process = make_process()
        process_iter.return_value = [process]
        monitor = ProcessMonitor(self.config)

        self.assertEqual(monitor.get_processes(), [])
        self.assertEqual(
            process.cpu_percent.call_args_list, [unittest.mock.call(interval=None)]
        )

        result = monitor.get_processes()

        self.assertEqual(len(result), 1)
        self.assertEqual(
            (
                result[0].pid,
                result[0].name,
                result[0].cpu_percent,
                result[0].nice_value,
                result[0].create_time,
                result[0].status,
            ),
            (123, "worker", 27.5, 5, 1000.0, "running"),
        )
        self.assertIsNotNone(result[0].observed_at)
        self.assertEqual(
            process.cpu_percent.call_args_list,
            [
                unittest.mock.call(interval=None),
                unittest.mock.call(interval=None),
            ],
        )
        process_iter.assert_called_with(
            attrs=["pid", "name", "create_time", "status"]
            + ([] if psutil.WINDOWS else ["uids"]),
            ad_value=None,
        )

    @patch("procnanny.monitor.psutil.process_iter", return_value=[])
    @patch("procnanny.monitor.psutil.WINDOWS", True)
    def test_does_not_request_uids_on_windows(
        self, process_iter: Mock
    ) -> None:
        ProcessMonitor(self.config).get_processes()

        process_iter.assert_called_once_with(
            attrs=["pid", "name", "create_time", "status"],
            ad_value=None,
        )

    @patch("procnanny.monitor.psutil.WINDOWS", False)
    @patch("procnanny.monitor.psutil.process_iter")
    def test_preserves_user_id_when_uids_are_supported(
        self, process_iter: Mock
    ) -> None:
        uids = Mock(real=1001)
        process = make_process(uids=uids)
        process_iter.return_value = [process]
        monitor = ProcessMonitor(self.config)

        monitor.get_processes()
        result = monitor.get_processes()

        self.assertEqual(result[0].user_id, 1001)
        process_iter.assert_called_with(
            attrs=["pid", "name", "create_time", "status", "uids"],
            ad_value=None,
        )

    @patch("procnanny.monitor.psutil.process_iter")
    def test_keeps_cpu_sample_when_priority_or_status_is_unavailable(
        self, process_iter: Mock
    ) -> None:
        process = make_process()
        process.info.pop("nice")
        process.info.pop("status")
        process.nice.side_effect = psutil.AccessDenied(pid=process.pid)
        process_iter.return_value = [process]
        monitor = ProcessMonitor(self.config)

        monitor.get_processes()
        result = monitor.get_processes()

        self.assertEqual(len(result), 1)
        self.assertIsNone(result[0].nice_value)
        self.assertIsNone(result[0].status)
        self.assertIsNotNone(result[0].observed_at)

    @patch("procnanny.monitor.psutil.WINDOWS", True)
    @patch("procnanny.monitor.psutil.process_iter")
    def test_reads_and_labels_windows_priority_class(
        self, process_iter: Mock
    ) -> None:
        process = make_process(nice=psutil.NORMAL_PRIORITY_CLASS)
        process_iter.return_value = [process]
        monitor = ProcessMonitor(self.config)

        monitor.get_processes()
        result = monitor.get_processes()[0]

        process.nice.assert_called_once_with()
        self.assertEqual(result.nice_value, psutil.NORMAL_PRIORITY_CLASS)
        self.assertEqual(result.priority_scheme, "windows_priority_class")
        self.assertEqual(result.priority_label, "NORMAL_PRIORITY_CLASS")
        self.assertIsNone(result.priority_unavailable_reason)

    @patch("procnanny.monitor.psutil.WINDOWS", False)
    @patch("procnanny.monitor.psutil.process_iter")
    def test_reads_unix_nice_without_converting_its_value(
        self, process_iter: Mock
    ) -> None:
        process = make_process(nice=7)
        process_iter.return_value = [process]
        monitor = ProcessMonitor(self.config)

        monitor.get_processes()
        result = monitor.get_processes()[0]

        self.assertEqual(result.nice_value, 7)
        self.assertEqual(result.priority_scheme, "unix_nice")
        self.assertIsNone(result.priority_label)

    @patch("procnanny.monitor.psutil.process_iter")
    def test_retains_sample_and_reports_access_denied_priority(
        self, process_iter: Mock
    ) -> None:
        process = make_process()
        process.nice.side_effect = psutil.AccessDenied(pid=process.pid)
        process_iter.return_value = [process]
        monitor = ProcessMonitor(self.config)

        monitor.get_processes()
        result = monitor.get_processes()[0]

        self.assertEqual(result.cpu_percent, 27.5)
        self.assertIsNone(result.nice_value)
        self.assertEqual(result.priority_unavailable_reason, "AccessDenied")

    @patch("procnanny.monitor.psutil.process_iter")
    def test_reports_process_termination_during_priority_read(
        self, process_iter: Mock
    ) -> None:
        process = make_process()
        process.nice.side_effect = psutil.NoSuchProcess(pid=process.pid)
        process_iter.return_value = [process]
        monitor = ProcessMonitor(self.config)

        monitor.get_processes()
        result = monitor.get_processes()[0]

        self.assertIsNone(result.nice_value)
        self.assertEqual(result.priority_unavailable_reason, "NoSuchProcess")

    @patch("procnanny.monitor.psutil.process_iter")
    def test_reports_zombie_during_priority_read(
        self, process_iter: Mock
    ) -> None:
        process = make_process()
        process.nice.side_effect = psutil.ZombieProcess(pid=process.pid)
        process_iter.return_value = [process]
        monitor = ProcessMonitor(self.config)

        monitor.get_processes()
        result = monitor.get_processes()[0]

        self.assertIsNone(result.nice_value)
        self.assertEqual(result.priority_unavailable_reason, "ZombieProcess")

    @patch("procnanny.monitor.psutil.process_iter")
    def test_reports_unsupported_priority_read_without_dropping_sample(
        self, process_iter: Mock
    ) -> None:
        process = make_process()
        process.nice.side_effect = NotImplementedError("priority class unavailable")
        process_iter.return_value = [process]
        monitor = ProcessMonitor(self.config)

        monitor.get_processes()
        result = monitor.get_processes()[0]

        self.assertIsNone(result.nice_value)
        self.assertEqual(result.priority_unavailable_reason, "NotImplementedError")

    @patch("procnanny.monitor.psutil.process_iter")
    def test_skips_zombie_processes(self, process_iter: Mock) -> None:
        zombie = make_process(status=psutil.STATUS_ZOMBIE)
        process_iter.return_value = [zombie]
        monitor = ProcessMonitor(self.config)

        self.assertEqual(monitor.get_processes(), [])
        zombie.cpu_percent.assert_not_called()

    @patch("procnanny.monitor.psutil.process_iter")
    def test_skips_process_that_disappears_during_subsequent_sample(
        self, process_iter: Mock
    ) -> None:
        process = make_process(cpu_samples=(0.0, psutil.NoSuchProcess(pid=123)))
        process_iter.return_value = [process]
        monitor = ProcessMonitor(self.config)

        self.assertEqual(monitor.get_processes(), [])
        self.assertEqual(monitor.get_processes(), [])
        self.assertEqual(monitor._samplers, {})

    @patch("procnanny.monitor.psutil.process_iter")
    def test_excludes_configured_processes_case_insensitively(
        self, process_iter: Mock
    ) -> None:
        excluded = make_process(pid=1, name="Backup")
        included = make_process(pid=2, name="worker")
        process_iter.return_value = [excluded, included]
        config = replace(self.config, excluded_processes=("backup",))
        monitor = ProcessMonitor(config)

        monitor.get_processes()

        self.assertEqual(excluded.cpu_percent.call_count, 0)
        included.cpu_percent.assert_called_once_with(interval=None)

    @patch("procnanny.monitor.psutil.process_iter")
    def test_excludes_names_passed_to_monitor(self, process_iter: Mock) -> None:
        excluded = make_process(name="worker")
        process_iter.return_value = [excluded]
        monitor = ProcessMonitor(self.config, excluded_processes={"worker"})

        self.assertEqual(monitor.get_processes(), [])
        excluded.cpu_percent.assert_not_called()

    @patch("procnanny.monitor.psutil.process_iter")
    def test_skips_processes_with_unavailable_information(
        self, process_iter: Mock
    ) -> None:
        unavailable = make_process(pid=10, name=None)
        available = make_process(pid=11, cpu_samples=(0.0, 12.0))
        process_iter.return_value = [unavailable, available]
        monitor = ProcessMonitor(self.config)

        monitor.get_processes()
        result = monitor.get_processes()

        self.assertEqual(len(result), 1)
        self.assertEqual(
            (
                result[0].pid,
                result[0].name,
                result[0].cpu_percent,
                result[0].nice_value,
                result[0].status,
            ),
            (11, "worker", 12.0, 5, "running"),
        )
        unavailable.cpu_percent.assert_not_called()
        self.assertTrue(monitor.last_scan_complete)
        self.assertEqual(monitor.last_scan_observed_pids, {10, 11})

    @patch("procnanny.monitor.psutil.process_iter")
    def test_tracks_pid_when_cpu_sample_is_invalid(self, process_iter: Mock) -> None:
        process = make_process(cpu_samples=(0.0, float("nan")))
        process_iter.return_value = [process]
        monitor = ProcessMonitor(self.config)

        monitor.get_processes()
        self.assertEqual(monitor.get_processes(), [])

        self.assertTrue(monitor.last_scan_complete)
        self.assertEqual(monitor.last_scan_observed_pids, {123})

    @patch("procnanny.monitor.psutil.process_iter")
    def test_skips_access_denied_and_terminated_processes(
        self, process_iter: Mock
    ) -> None:
        denied = make_process(pid=10)
        denied.cpu_percent.side_effect = psutil.AccessDenied(pid=10)
        vanished = make_process(pid=11)
        vanished.cpu_percent.side_effect = psutil.NoSuchProcess(pid=11)
        available = make_process(pid=12, cpu_samples=(0.0, 12.0))
        process_iter.return_value = [denied, vanished, available]
        monitor = ProcessMonitor(self.config)

        result = monitor.get_processes()

        self.assertEqual(result, [])
        self.assertEqual(denied.cpu_percent.call_count, 1)
        self.assertEqual(vanished.cpu_percent.call_count, 1)
        available.cpu_percent.assert_called_once_with(interval=None)
        self.assertTrue(monitor.last_scan_complete)
        self.assertEqual(monitor.last_scan_observed_pids, {10, 11, 12})

    @patch("procnanny.monitor.psutil.process_iter")
    def test_returns_empty_list_when_process_scan_is_inaccessible(
        self, process_iter: Mock
    ) -> None:
        process_iter.side_effect = psutil.AccessDenied()
        monitor = ProcessMonitor(self.config)

        self.assertEqual(monitor.get_processes(), [])
        self.assertFalse(monitor.last_scan_complete)

    @patch("procnanny.monitor.psutil.process_iter", return_value=[])
    def test_empty_completed_scan_is_distinct_from_scan_failure(
        self, process_iter: Mock
    ) -> None:
        monitor = ProcessMonitor(self.config)

        self.assertEqual(monitor.get_processes(), [])
        self.assertTrue(monitor.last_scan_complete)
        process_iter.assert_called_once()

    @patch("procnanny.monitor.psutil.process_iter")
    def test_drops_cached_sampler_when_process_identity_changes(
        self, process_iter: Mock
    ) -> None:
        original = make_process(create_time=1000.0, cpu_samples=(0.0, 3.0))
        reused_pid = make_process(create_time=2000.0, cpu_samples=(0.0, 8.0))
        process_iter.side_effect = [[original], [reused_pid]]
        monitor = ProcessMonitor(self.config)

        self.assertEqual(monitor.get_processes(), [])
        self.assertEqual(monitor.get_processes(), [])
        self.assertEqual(len(monitor._samplers), 1)
        self.assertIn((123, 2000.0), monitor._samplers)

    def test_rejects_empty_explicit_exclusion_names(self) -> None:
        for exclusions in ([" "], "worker"):
            with (
                self.subTest(exclusions=exclusions),
                self.assertRaisesRegex(ValueError, "non-empty"),
            ):
                ProcessMonitor(self.config, excluded_processes=exclusions)


if __name__ == "__main__":
    unittest.main()
