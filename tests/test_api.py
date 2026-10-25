"""Tests for ProcNanny's local HTTP API."""

import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from procnanny.api import create_app
from procnanny.config import load_config, save_config
from procnanny.logger import ProcessLogger
from procnanny.monitor import ProcessInfo


class ProcNannyApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = replace(load_config(), poll_interval_seconds=0.05)
        self.directory = tempfile.TemporaryDirectory()
        self.log_path = Path(self.directory.name) / "procnanny.log"
        self.app = create_app(self.config, self.log_path)
        self.published = threading.Event()
        self.sample = ProcessInfo(
            42,
            "worker",
            28.5,
            7,
            1234.0,
            status="running",
            observed_at="2026-10-24T12:00:00+00:00",
        )

    def tearDown(self) -> None:
        self.directory.cleanup()

    def settings_payload(self) -> dict[str, object]:
        adjustment = self.config
        return {
            "high_cpu_threshold_percent": adjustment.cpu_high_threshold_percent,
            "low_cpu_threshold_percent": adjustment.cpu_low_threshold_percent,
            "poll_interval_seconds": adjustment.poll_interval_seconds,
            "history_window_seconds": adjustment.history_window_seconds,
            "excluded_processes": list(adjustment.excluded_processes),
            "priority_adjustment_enabled": adjustment.priority_adjustment_enabled,
            "priority_adjustment_dry_run": adjustment.priority_adjustment_dry_run,
            "cooldown_seconds": adjustment.priority_adjustment_cooldown_seconds,
            "high_duration_seconds": adjustment.priority_adjustment_high_duration_seconds,
            "recovery_duration_seconds": adjustment.priority_adjustment_recovery_duration_seconds,
            "step": adjustment.priority_adjustment_step,
        }

    def test_initial_state_has_no_fabricated_process_data(self) -> None:
        with TestClient(self.app) as client:
            status = client.get("/api/status").json()
            processes = client.get("/api/processes").json()
            logs = client.get("/api/logs").json()
            priority_actions = client.get("/api/priority/actions").json()
            evaluations = client.get("/api/priority/evaluations").json()
            settings_write = client.post(
                "/api/settings",
                json={"priority_adjustment": {"enabled": True}},
            )

        self.assertEqual(status["state"], "stopped")
        self.assertFalse(status["monitoring"])
        self.assertFalse(status["priority_adjustment_enabled"])
        self.assertEqual(status["high_cpu_threshold_percent"], 70)
        self.assertEqual(status["low_cpu_threshold_percent"], 10)
        self.assertFalse(status["priority_adjustment"]["enabled"])
        self.assertTrue(status["priority_adjustment"]["dry_run"])
        self.assertEqual(processes["source"], "live")
        self.assertEqual(processes["processes"], [])
        self.assertIsNone(processes["system_cpu_percent"])
        self.assertEqual(logs, {"source": "live", "logs": []})
        self.assertEqual(priority_actions["actions"], [])
        self.assertFalse(priority_actions["enabled"])
        self.assertTrue(priority_actions["dry_run"])
        self.assertFalse(evaluations["enabled"])
        self.assertTrue(evaluations["dry_run"])
        self.assertEqual(settings_write.status_code, 405)
        self.assertEqual(status["poll_interval_seconds"], self.config.poll_interval_seconds)
        self.assertEqual(status["history_window_seconds"], self.config.history_window_seconds)
        self.assertEqual(status["excluded_processes"], list(self.config.excluded_processes))
        self.assertEqual(
            status["priority_adjustment"]["cooldown_seconds"],
            self.config.priority_adjustment_cooldown_seconds,
        )
        self.assertEqual(
            status["priority_adjustment"]["high_duration_seconds"],
            self.config.priority_adjustment_high_duration_seconds,
        )
        self.assertEqual(
            status["priority_adjustment"]["recovery_duration_seconds"],
            self.config.priority_adjustment_recovery_duration_seconds,
        )

    def test_settings_are_persisted_and_applied_to_active_services(self) -> None:
        config_path = Path(self.directory.name) / "config.yaml"
        save_config(self.config, config_path)
        app = create_app(self.config, self.log_path, config_path)
        payload = self.settings_payload()
        payload.update(
            high_cpu_threshold_percent=83,
            low_cpu_threshold_percent=13,
            priority_adjustment_enabled=True,
            priority_adjustment_dry_run=True,
            excluded_processes=["worker"],
        )

        with TestClient(app) as client:
            response = client.put("/api/settings", json=payload)
            status = client.get("/api/status").json()
            settings = client.get("/api/settings").json()
            evaluations = client.get("/api/priority/evaluations").json()

        service = app.state.monitoring_service
        self.assertEqual(response.status_code, 200)
        self.assertEqual(status["high_cpu_threshold_percent"], 83)
        self.assertEqual(status["low_cpu_threshold_percent"], 13)
        self.assertEqual(status["excluded_processes"], ["worker"])
        self.assertTrue(status["priority_adjustment"]["enabled"])
        self.assertTrue(status["priority_adjustment"]["dry_run"])
        self.assertTrue(settings["settings_persistent"])
        self.assertTrue(evaluations["enabled"])
        self.assertTrue(evaluations["dry_run"])
        self.assertEqual(service.config, load_config(config_path))
        self.assertEqual(service.priority_adjuster.config, service.config)

    def test_live_settings_require_confirmation_and_remain_dry_run_by_default(self) -> None:
        config_path = Path(self.directory.name) / "config.yaml"
        save_config(self.config, config_path)
        app = create_app(self.config, self.log_path, config_path)
        payload = self.settings_payload()
        payload.update(
            priority_adjustment_enabled=True,
            priority_adjustment_dry_run=False,
        )

        with TestClient(app) as client:
            rejected = client.put("/api/settings", json=payload)
            unchanged = client.get("/api/status").json()
            payload["confirm_live_adjustment"] = True
            confirmed = client.put("/api/settings", json=payload)
            active = client.get("/api/status").json()

        self.assertEqual(rejected.status_code, 409)
        self.assertFalse(unchanged["priority_adjustment"]["enabled"])
        self.assertTrue(unchanged["priority_adjustment"]["dry_run"])
        self.assertEqual(confirmed.status_code, 200)
        self.assertTrue(active["priority_adjustment"]["enabled"])
        self.assertFalse(active["priority_adjustment"]["dry_run"])
        persisted = load_config(config_path)
        self.assertTrue(persisted.priority_adjustment_enabled)
        self.assertFalse(persisted.priority_adjustment_dry_run)

    def test_saved_live_settings_reach_monitor_and_priority_setter(self) -> None:
        config_path = Path(self.directory.name) / "config.yaml"
        save_config(self.config, config_path)
        app = create_app(self.config, self.log_path, config_path)
        payload = self.settings_payload()
        payload.update(
            priority_adjustment_enabled=True,
            priority_adjustment_dry_run=False,
            high_duration_seconds=0.1,
            confirm_live_adjustment=True,
        )
        process = ProcessInfo(
            4242,
            "worker",
            90,
            0,
            1234.0,
            user_id=1000,
            status="running",
        )
        monitor = Mock()
        monitor.get_processes.return_value = [process]
        monitor.last_scan_complete = True
        monitor.last_scan_observed_pids = {process.pid}
        target = Mock()
        target.create_time.return_value = process.create_time
        target.name.return_value = process.name
        target.status.return_value = "running"
        current_priority = 0
        priority_set = threading.Event()

        def set_priority(value: int | None = None) -> int:
            nonlocal current_priority
            if value is not None:
                current_priority = value
                priority_set.set()
            return current_priority

        target.nice.side_effect = set_priority

        with (
            patch("procnanny.api.ProcessMonitor", return_value=monitor),
            patch("procnanny.priority.sys.platform", "linux"),
            patch("procnanny.priority.psutil.Process", return_value=target),
            patch("procnanny.api.psutil.cpu_percent", return_value=10.0),
            TestClient(app) as client,
        ):
            saved = client.put("/api/settings", json=payload)
            self.assertEqual(saved.status_code, 200)
            active = client.get("/api/status").json()["priority_adjustment"]
            self.assertTrue(active["enabled"])
            self.assertFalse(active["dry_run"])
            self.assertTrue(client.post("/api/monitor/start").json()["monitoring"])
            self.assertTrue(priority_set.wait(timeout=2))
            client.post("/api/monitor/stop")
            actions = client.get("/api/priority/actions").json()["actions"]

        self.assertEqual(current_priority, 1)
        target.nice.assert_any_call(1)
        self.assertTrue(
            any(
                action["action"] == "priority_demote"
                and action["status"] == "applied"
                and action["resulting_priority"] == 1
                and action["dry_run"] is False
                for action in actions
            )
        )

    def test_invalid_settings_are_rejected_without_persisting(self) -> None:
        config_path = Path(self.directory.name) / "config.yaml"
        save_config(self.config, config_path)
        app = create_app(self.config, self.log_path, config_path)
        payload = self.settings_payload()
        payload["low_cpu_threshold_percent"] = payload["high_cpu_threshold_percent"]

        with TestClient(app) as client:
            response = client.put("/api/settings", json=payload)
            status = client.get("/api/status").json()

        self.assertEqual(response.status_code, 422)
        self.assertEqual(status["low_cpu_threshold_percent"], 10)
        self.assertEqual(load_config(config_path), self.config)

    def test_settings_reject_boolean_cpu_thresholds(self) -> None:
        config_path = Path(self.directory.name) / "config.yaml"
        save_config(self.config, config_path)
        app = create_app(self.config, self.log_path, config_path)
        payload = self.settings_payload()
        payload["high_cpu_threshold_percent"] = True

        with TestClient(app) as client:
            response = client.put("/api/settings", json=payload)

        self.assertEqual(response.status_code, 422)
        self.assertEqual(load_config(config_path), self.config)

    def test_settings_cannot_be_persisted_for_an_in_memory_configuration(self) -> None:
        with TestClient(self.app) as client:
            response = client.put("/api/settings", json=self.settings_payload())

        self.assertEqual(response.status_code, 409)

    def test_cors_allows_configured_local_development_origins(self) -> None:
        origins = (
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://172.16.160.134:5173",
        )
        with TestClient(self.app) as client:
            for origin in origins:
                with self.subTest(origin=origin):
                    response = client.options(
                        "/api/status",
                        headers={
                            "Origin": origin,
                            "Access-Control-Request-Method": "GET",
                            "Access-Control-Request-Headers": "content-type",
                        },
                    )
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(
                        response.headers["access-control-allow-origin"],
                        origin,
                    )
                    self.assertIn(
                        "GET",
                        response.headers["access-control-allow-methods"],
                    )
                    self.assertIn(
                        "content-type",
                        response.headers[
                            "access-control-allow-headers"
                        ].lower(),
                    )
            update_preflight = client.options(
                "/api/settings",
                headers={
                    "Origin": "http://localhost:5173",
                    "Access-Control-Request-Method": "PUT",
                    "Access-Control-Request-Headers": "content-type",
                },
            )
            self.assertEqual(update_preflight.status_code, 200)
            self.assertIn("PUT", update_preflight.headers["access-control-allow-methods"])

    def test_cors_does_not_allow_unconfigured_origin(self) -> None:
        origin = "http://172.16.160.135:5173"
        with TestClient(self.app) as client:
            response = client.options(
                "/api/status",
                headers={
                    "Origin": origin,
                    "Access-Control-Request-Method": "GET",
                    "Access-Control-Request-Headers": "content-type",
                },
            )
            status_response = client.get(
                "/api/status",
                headers={"Origin": origin},
            )

        self.assertEqual(response.status_code, 400)
        self.assertNotIn("access-control-allow-origin", response.headers)
        self.assertEqual(status_response.status_code, 200)
        self.assertNotIn(
            "access-control-allow-origin",
            status_response.headers,
        )

    def test_status_endpoint_remains_functional_with_allowed_origin(self) -> None:
        origin = "http://172.16.160.134:5173"
        with TestClient(self.app) as client:
            response = client.get("/api/status", headers={"Origin": origin})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.headers["access-control-allow-origin"],
            origin,
        )
        self.assertEqual(response.json()["state"], "stopped")

    def test_status_reports_the_same_custom_config_used_by_services(self) -> None:
        config = replace(
            self.config,
            cpu_high_threshold_percent=82,
            cpu_low_threshold_percent=12,
            poll_interval_seconds=0.25,
            history_window_seconds=12,
            excluded_processes=("backup", "worker"),
            priority_adjustment_enabled=True,
            priority_adjustment_dry_run=True,
            priority_adjustment_cooldown_seconds=17,
            priority_adjustment_high_duration_seconds=8,
            priority_adjustment_recovery_duration_seconds=9,
        )
        app = create_app(config, self.log_path)

        with TestClient(app) as client:
            status = client.get("/api/status").json()
            actions = client.get("/api/priority/actions").json()

        self.assertEqual(status["high_cpu_threshold_percent"], 82)
        self.assertEqual(status["low_cpu_threshold_percent"], 12)
        self.assertEqual(status["poll_interval_seconds"], 0.25)
        self.assertEqual(status["history_window_seconds"], 12)
        self.assertEqual(status["excluded_processes"], ["backup", "worker"])
        self.assertTrue(status["priority_adjustment_enabled"])
        self.assertTrue(status["priority_adjustment"]["enabled"])
        self.assertTrue(status["priority_adjustment"]["dry_run"])
        self.assertEqual(status["priority_adjustment"]["cooldown_seconds"], 17)
        self.assertEqual(status["priority_adjustment"]["high_duration_seconds"], 8)
        self.assertEqual(status["priority_adjustment"]["recovery_duration_seconds"], 9)
        self.assertTrue(actions["enabled"])
        self.assertTrue(actions["dry_run"])
        self.assertEqual(app.state.monitoring_service.config, config)
        self.assertEqual(app.state.monitoring_service.priority_adjuster.config, config)

    def test_start_publishes_live_processes_and_stop_ends_worker(self) -> None:
        monitor = Mock()
        monitor.get_processes.return_value = [self.sample]
        monitor.last_scan_complete = True
        monitor.last_scan_observed_pids = {self.sample.pid}
        with (
            patch("procnanny.api.ProcessMonitor", return_value=monitor) as factory,
            patch("procnanny.api.psutil.cpu_percent", side_effect=[0.0, 42.25]),
            TestClient(self.app) as client,
        ):
            service = self.app.state.monitoring_service
            publish_snapshot = service._publish_snapshot

            def publish_and_signal(
                processes: list[ProcessInfo],
                scan_complete: bool,
            ) -> None:
                publish_snapshot(processes, scan_complete)
                if service.get_snapshot()["system_cpu_percent"] == 42.25:
                    self.published.set()

            service._publish_snapshot = publish_and_signal
            started = client.post("/api/monitor/start")
            self.assertTrue(self.published.wait(timeout=2))
            processes = client.get("/api/processes").json()
            evaluations = client.get("/api/priority/evaluations").json()
            status = client.get("/api/status").json()
            duplicate_start = client.post("/api/monitor/start")
            stopped = client.post("/api/monitor/stop")

        self.assertEqual(started.status_code, 200)
        self.assertEqual(duplicate_start.status_code, 200)
        factory.assert_called_once_with(self.config)
        self.assertGreaterEqual(monitor.get_processes.call_count, 2)
        self.assertEqual(processes["processes"][0]["pid"], 42)
        self.assertEqual(processes["processes"][0]["create_time"], 1234.0)
        self.assertEqual(processes["processes"][0]["average_cpu_percent"], 28.5)
        self.assertEqual(processes["processes"][0]["status"], "running")
        self.assertIn("priority_value", processes["processes"][0])
        self.assertIn("priority_scheme", processes["processes"][0])
        self.assertIn("priority_label", processes["processes"][0])
        self.assertIn(
            "priority_unavailable_reason", processes["processes"][0]
        )
        self.assertEqual(
            processes["processes"][0]["observed_at"],
            "2026-10-24T12:00:00+00:00",
        )
        self.assertEqual(processes["system_cpu_percent"], 42.25)
        self.assertEqual(evaluations["evaluations"][0]["pid"], self.sample.pid)
        self.assertEqual(evaluations["evaluations"][0]["decision"], "below_threshold")
        self.assertEqual(status["state"], "running")
        self.assertEqual(stopped.json()["state"], "stopped")

    def test_duplicate_start_is_ignored_and_worker_can_restart(self) -> None:
        first_entered = threading.Event()
        second_entered = threading.Event()
        first_monitor = Mock()
        first_monitor.get_processes.side_effect = lambda: (
            first_entered.set(),
            [],
        )[1]
        first_monitor.last_scan_complete = True
        second_monitor = Mock()
        second_monitor.get_processes.side_effect = lambda: (
            second_entered.set(),
            [],
        )[1]
        second_monitor.last_scan_complete = True

        with (
            patch(
                "procnanny.api.ProcessMonitor",
                side_effect=[first_monitor, second_monitor],
            ) as factory,
            patch("procnanny.api.psutil.cpu_percent", return_value=10.0),
            TestClient(self.app) as client,
        ):
            client.post("/api/monitor/start")
            self.assertTrue(first_entered.wait(timeout=2))
            client.post("/api/monitor/start")
            first_stopped = client.post("/api/monitor/stop")
            client.post("/api/monitor/start")
            self.assertTrue(second_entered.wait(timeout=2))
            second_stopped = client.post("/api/monitor/stop")

        self.assertEqual(first_stopped.json()["state"], "stopped")
        self.assertEqual(second_stopped.json()["state"], "stopped")
        self.assertEqual(factory.call_count, 2)

    def test_process_snapshot_request_does_not_wait_for_process_scan(self) -> None:
        entered_scan = threading.Event()
        release_scan = threading.Event()
        monitor = Mock()
        monitor.last_scan_complete = True

        def blocking_scan() -> list[ProcessInfo]:
            entered_scan.set()
            release_scan.wait(timeout=3)
            return []

        monitor.get_processes.side_effect = blocking_scan
        with (
            patch("procnanny.api.ProcessMonitor", return_value=monitor),
            TestClient(self.app) as client,
        ):
            client.post("/api/monitor/start")
            self.assertTrue(entered_scan.wait(timeout=2))
            try:
                with ThreadPoolExecutor(max_workers=1) as executor:
                    response = executor.submit(client.get, "/api/processes").result(
                        timeout=1
                    )
                self.assertEqual(response.status_code, 200)
            finally:
                release_scan.set()
                client.post("/api/monitor/stop")

    def test_incomplete_scan_marks_last_good_data_stale(self) -> None:
        service = self.app.state.monitoring_service
        service._publish_snapshot([self.sample], True)
        service._publish_snapshot([], False)

        snapshot = service.get_snapshot()

        self.assertTrue(snapshot["stale"])
        self.assertEqual(snapshot["processes"][0]["pid"], 42)
        self.assertIn("incomplete", service.get_status()["last_error"])

    def test_recent_logs_returns_real_structured_records(self) -> None:
        ProcessLogger(self.log_path).log_process(self.sample, 28.5)
        self.app.state.monitoring_service.process_logger.log_priority_event(
            pid=42,
            process_name="worker",
            create_time=1234.0,
            cpu_percent=28.5,
            average_cpu_percent=28.5,
            previous_priority=7,
            proposed_priority=None,
            resulting_priority=None,
            action="priority_evaluation",
            status="skipped",
            reason="Below threshold",
            dry_run=True,
        )

        with TestClient(self.app) as client:
            response = client.get("/api/logs?limit=10")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["source"], "live")
        self.assertEqual(len(payload["logs"]), 1)
        self.assertEqual(payload["logs"][0]["pid"], 42)
        self.assertEqual(payload["logs"][0]["process_name"], "worker")
        self.assertEqual(payload["logs"][0]["action"], "monitor")

    def test_priority_actions_endpoint_returns_recent_actions_and_mode(self) -> None:
        self.app.state.monitoring_service.process_logger.log_priority_action(
            pid=42,
            process_name="worker",
            create_time=1234.0,
            previous_priority=0,
            requested_priority=1,
            action="priority_demote",
            reason="sustained_high_cpu",
            success=None,
            dry_run=True,
        )
        with TestClient(self.app) as client:
            response = client.get("/api/priority/actions")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["enabled"])
        self.assertTrue(payload["dry_run"])
        self.assertEqual(payload["actions"][0]["action"], "priority_demote")
        self.assertEqual(payload["actions"][0]["status"], "dry_run")
        self.assertTrue(payload["actions"][0]["event_id"])
        self.assertEqual(payload["limit"], 20)
        self.assertEqual(payload["offset"], 0)

    def test_priority_action_endpoint_filters_and_paginates_newest_first(self) -> None:
        logger = self.app.state.monitoring_service.process_logger
        for pid, name, status in (
            (10, "worker-alpha", "applied"),
            (20, "worker-beta", "failed"),
            (30, "worker-alpha", "dry_run"),
        ):
            logger.log_priority_event(
                pid=pid,
                process_name=name,
                create_time=1000.0 + pid,
                cpu_percent=82.5,
                average_cpu_percent=78.0,
                previous_priority=0,
                proposed_priority=1,
                resulting_priority=1 if status == "applied" else None,
                action="priority_demote",
                status=status,
                reason="sustained_high_cpu",
                dry_run=status == "dry_run",
                error="permission denied" if status == "failed" else None,
            )

        with TestClient(self.app) as client:
            newest = client.get("/api/priority/actions?limit=2").json()
            filtered = client.get(
                "/api/priority/actions?status=failed&process_name=BETA&pid=20"
            ).json()
            page = client.get(
                "/api/priority/actions?status=applied&offset=1"
            ).json()
            empty = client.get(
                "/api/priority/actions?status=excluded"
            ).json()
            invalid_status = client.get(
                "/api/priority/actions?status=unknown"
            )

        self.assertEqual(
            [item["pid"] for item in newest["actions"]],
            [30, 20],
        )
        self.assertEqual(filtered["actions"][0]["process_name"], "worker-beta")
        self.assertEqual(filtered["actions"][0]["cpu_percent"], 82.5)
        self.assertEqual(filtered["actions"][0]["status"], "failed")
        self.assertEqual(page["actions"], [])
        self.assertEqual(empty["actions"], [])
        self.assertEqual(invalid_status.status_code, 422)

    def test_priority_evaluations_endpoint_returns_backend_decision_schema(self) -> None:
        service = self.app.state.monitoring_service
        service.priority_adjuster.evaluate(self.sample, 75)

        with TestClient(self.app) as client:
            response = client.get("/api/priority/evaluations?limit=1")
            invalid_limit = client.get("/api/priority/evaluations?limit=1001")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["source"], "live")
        self.assertFalse(payload["enabled"])
        self.assertTrue(payload["dry_run"])
        self.assertEqual(len(payload["evaluations"]), 1)
        evaluation = payload["evaluations"][0]
        self.assertEqual(evaluation["pid"], self.sample.pid)
        self.assertEqual(evaluation["create_time"], self.sample.create_time)
        self.assertEqual(evaluation["process_name"], self.sample.name)
        self.assertEqual(evaluation["average_cpu_percent"], 75)
        self.assertEqual(evaluation["threshold_percent"], 70)
        self.assertEqual(evaluation["decision"], "waiting_for_sustained_cpu")
        self.assertIn("Priority adjustment is disabled", evaluation["explanation"])
        self.assertIsNone(evaluation["proposed_priority"])
        self.assertEqual(evaluation["action_status"], "not_executed")
        self.assertEqual(invalid_limit.status_code, 422)

    def test_priority_evaluation_history_covers_snapshots_over_200_processes(self) -> None:
        service = self.app.state.monitoring_service
        for pid in range(1, 226):
            process = ProcessInfo(
                pid,
                f"worker-{pid}",
                1.0,
                0,
                1000.0 + pid,
            )
            service.priority_adjuster.evaluate(process, 1.0)

        with TestClient(self.app) as client:
            response = client.get("/api/priority/evaluations?limit=500")

        self.assertEqual(response.status_code, 200)
        evaluations = response.json()["evaluations"]
        self.assertEqual(len(evaluations), 225)
        self.assertEqual(evaluations[0]["pid"], 1)
        self.assertEqual(evaluations[-1]["pid"], 225)

    def test_log_limit_is_bounded(self) -> None:
        with TestClient(self.app) as client:
            response = client.get("/api/logs?limit=201")

        self.assertEqual(response.status_code, 422)

    def test_priority_modification_route_is_not_exposed(self) -> None:
        with TestClient(self.app) as client:
            response = client.post("/api/priority")

        self.assertEqual(response.status_code, 404)

    def test_log_read_failures_return_service_unavailable(self) -> None:
        service = self.app.state.monitoring_service
        with (
            patch.object(
                service.process_logger,
                "read_recent",
                side_effect=OSError("log file unavailable"),
            ),
            patch.object(
                service.process_logger,
                "read_priority_actions",
                side_effect=OSError("history file unavailable"),
            ),
            TestClient(self.app) as client,
        ):
            logs = client.get("/api/logs")
            actions = client.get("/api/priority/actions")

        self.assertEqual(logs.status_code, 503)
        self.assertEqual(
            logs.json()["detail"],
            "ProcNanny log records are temporarily unavailable.",
        )
        self.assertEqual(actions.status_code, 503)
        self.assertEqual(
            actions.json()["detail"],
            "ProcNanny priority action records are temporarily unavailable.",
        )


if __name__ == "__main__":
    unittest.main()
