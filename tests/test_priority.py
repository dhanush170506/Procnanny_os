"""Tests for safe, configurable process priority adjustment."""

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

import psutil

from procnanny.config import load_config
from procnanny.logger import ProcessLogger
from procnanny.monitor import ProcessInfo
from procnanny.priority import PriorityAdjuster


class PriorityAdjusterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.log_path = Path(self.directory.name) / "procnanny.log"
        self.logger = ProcessLogger(self.log_path)
        self.config = replace(
            load_config(),
            poll_interval_seconds=5,
            priority_adjustment_enabled=True,
            priority_adjustment_dry_run=False,
            priority_adjustment_cooldown_seconds=10,
            priority_adjustment_high_duration_seconds=10,
            priority_adjustment_recovery_duration_seconds=10,
            priority_adjustment_step=1,
            min_nice_value=0,
            max_nice_value=3,
        )
        self.adjuster = PriorityAdjuster(self.config, self.logger)
        self.process = ProcessInfo(4242, "worker", 80, 0, 1000.0)
        self.target = Mock()
        self.target.name.return_value = "worker"
        self.target.status.return_value = "running"
        self.current_nice = 0
        self.target.create_time.return_value = 1000.0

        def nice(value=None):
            if value is not None:
                self.current_nice = value
            return self.current_nice

        self.target.nice.side_effect = nice

    def tearDown(self) -> None:
        self.directory.cleanup()

    def evaluate_twice(self, process: ProcessInfo | None = None) -> None:
        subject = process or self.process
        self.evaluate_and_execute(self.adjuster, subject, 80)
        self.evaluate_and_execute(self.adjuster, subject, 80)

    @staticmethod
    def evaluate_and_execute(
        adjuster: PriorityAdjuster,
        process: ProcessInfo,
        average: float,
    ):
        decision = adjuster.evaluate(process, average)
        return adjuster.execute(process, decision)

    def create_proposal(
        self,
        adjuster: PriorityAdjuster | None = None,
        process: ProcessInfo | None = None,
    ):
        policy = adjuster or self.adjuster
        subject = process or self.process
        with patch("procnanny.priority.sys.platform", "linux"):
            policy.evaluate(subject, 80)
            return policy.evaluate(subject, 80)

    def read_actions(self) -> list[dict[str, object]]:
        return list(
            reversed(
                [
                    action
                    for action in self.logger.read_priority_actions(limit=200)
                    if action["action"] != "priority_evaluation"
                ]
            )
        )

    def test_demotes_sustained_high_cpu_by_one_nice_step_within_limit(self) -> None:
        with (
            patch("procnanny.priority.sys.platform", "linux"),
            patch("procnanny.priority.psutil.Process", return_value=self.target),
        ):
            self.evaluate_twice()

        self.assertEqual(self.target.nice.call_args_list[1].args, (1,))
        action = self.read_actions()[0]
        self.assertEqual(action["action"], "priority_demote")
        self.assertEqual(action["previous_priority"], 0)
        self.assertEqual(action["requested_priority"], 1)
        self.assertTrue(action["success"])
        self.assertEqual(
            self.adjuster.get_recent_decisions(1)[0]["action_status"],
            "applied",
        )

    def test_requires_sustained_average_at_or_above_high_threshold(self) -> None:
        with (
            patch("procnanny.priority.sys.platform", "linux"),
            patch("procnanny.priority.psutil.Process", return_value=self.target),
        ):
            self.adjuster.evaluate(self.process, 69.9)
            self.adjuster.evaluate(self.process, 70)

        self.assertEqual(self.read_actions(), [])
        self.target.nice.assert_not_called()

    def test_respects_maximum_nice_value(self) -> None:
        process = replace(self.process, nice_value=3)
        self.current_nice = 3
        with (
            patch("procnanny.priority.sys.platform", "linux"),
            patch("procnanny.priority.psutil.Process", return_value=self.target),
        ):
            self.evaluate_twice(process)

        self.target.nice.assert_not_called()
        self.assertEqual(self.read_actions(), [])

    def test_demotes_to_but_not_below_configured_minimum_nice(self) -> None:
        process = replace(self.process, nice_value=-1)
        self.current_nice = -1
        with (
            patch("procnanny.priority.sys.platform", "linux"),
            patch("procnanny.priority.psutil.Process", return_value=self.target),
        ):
            self.evaluate_twice(process)

        action = self.read_actions()[0]
        self.assertEqual(action["requested_priority"], self.config.min_nice_value)

    def test_cooldown_prevents_changes_on_each_poll(self) -> None:
        with (
            patch("procnanny.priority.sys.platform", "linux"),
            patch("procnanny.priority.psutil.Process", return_value=self.target),
        ):
            self.evaluate_twice()
            self.evaluate_and_execute(
                self.adjuster,
                replace(self.process, nice_value=1),
                80,
            )

        self.assertEqual(len(self.read_actions()), 1)
        self.assertEqual(self.current_nice, 1)

    def test_rejects_live_proposal_when_os_priority_changed_since_evaluation(self) -> None:
        self.current_nice = 2
        with (
            patch("procnanny.priority.sys.platform", "linux"),
            patch("procnanny.priority.psutil.Process", return_value=self.target),
        ):
            self.evaluate_twice()

        self.assertEqual(self.current_nice, 2)
        self.assertFalse(self.read_actions()[0]["success"])
        self.assertIn("changed after evaluation", str(self.read_actions()[0]["error"]))

    def test_gradually_restores_toward_original_priority(self) -> None:
        with (
            patch("procnanny.priority.sys.platform", "linux"),
            patch("procnanny.priority.psutil.Process", return_value=self.target),
        ):
            self.evaluate_twice()
            self.evaluate_and_execute(
                self.adjuster, replace(self.process, nice_value=1), 0
            )
            self.evaluate_and_execute(
                self.adjuster, replace(self.process, nice_value=1), 0
            )

        self.assertEqual(self.current_nice, 0)
        self.assertEqual(self.read_actions()[-1]["action"], "priority_restore")
        self.assertEqual(self.read_actions()[-1]["requested_priority"], 0)

    def test_dry_run_logs_proposal_without_calling_priority_setter(self) -> None:
        config = replace(self.config, priority_adjustment_dry_run=True)
        adjuster = PriorityAdjuster(config, self.logger)
        with (
            patch("procnanny.priority.sys.platform", "linux"),
            patch("procnanny.priority.psutil.Process", return_value=self.target),
        ):
            self.evaluate_and_execute(adjuster, self.process, 80)
            self.evaluate_and_execute(adjuster, self.process, 80)

        self.assertFalse(
            any(
                call.args or call.kwargs
                for call in self.target.nice.call_args_list
            ),
            "dry-run must not call psutil.Process.nice with a priority value",
        )
        action = self.read_actions()[0]
        self.assertTrue(action["dry_run"])
        self.assertIsNone(action["success"])
        self.assertEqual(action["requested_priority"], 1)

    def test_adjustment_disabled_before_execution_does_not_call_setter(self) -> None:
        decision = self.create_proposal()
        self.adjuster.config = replace(
            self.adjuster.config,
            priority_adjustment_enabled=False,
        )
        with patch("procnanny.priority.psutil.Process", return_value=self.target):
            result = self.adjuster.execute(self.process, decision)

        self.assertEqual(result.action_status, "not_executed")
        self.assertIn("disabled", result.execution_error)
        self.assertFalse(
            any(call.args or call.kwargs for call in self.target.nice.call_args_list)
        )
        self.assertEqual(self.read_actions(), [])

    def test_successful_execution_verifies_and_records_applied_priority(self) -> None:
        decision = self.create_proposal()
        with (
            patch("procnanny.priority.sys.platform", "linux"),
            patch("procnanny.priority.psutil.Process", return_value=self.target),
        ):
            result = self.adjuster.execute(self.process, decision)

        self.assertEqual(result.action_status, "applied")
        self.assertEqual(self.current_nice, 1)
        action = self.read_actions()[0]
        self.assertTrue(action["success"])
        self.assertEqual(action["resulting_priority"], 1)

    def test_dry_run_rejects_proposal_for_protected_process(self) -> None:
        adjuster = PriorityAdjuster(
            replace(self.config, priority_adjustment_dry_run=True),
            self.logger,
        )
        decision = self.create_proposal(adjuster)
        protected = replace(self.process, status=psutil.STATUS_ZOMBIE)
        with (
            patch("procnanny.priority.sys.platform", "linux"),
            patch("procnanny.priority.psutil.Process", return_value=self.target),
        ):
            result = adjuster.execute(protected, decision)

        self.assertEqual(result.action_status, "failed")
        self.assertIn("zombie", result.execution_error)
        self.assertFalse(
            any(call.args or call.kwargs for call in self.target.nice.call_args_list)
        )

    def test_dry_run_rejects_pid_reuse_before_recording_proposal(self) -> None:
        adjuster = PriorityAdjuster(
            replace(self.config, priority_adjustment_dry_run=True),
            self.logger,
        )
        decision = self.create_proposal(adjuster)
        self.target.create_time.return_value = 2000.0
        with (
            patch("procnanny.priority.sys.platform", "linux"),
            patch("procnanny.priority.psutil.Process", return_value=self.target),
        ):
            result = adjuster.execute(self.process, decision)

        self.assertEqual(result.action_status, "failed")
        self.assertIn("identity changed", result.execution_error)
        self.assertFalse(
            any(call.args or call.kwargs for call in self.target.nice.call_args_list)
        )

    def test_rejects_tampered_invalid_proposal_without_os_change(self) -> None:
        decision = self.create_proposal()
        tampered = replace(
            decision,
            proposed_priority={
                "scheme": "unix_nice",
                "value": 100,
                "label": "nice 100",
            },
        )
        with patch("procnanny.priority.psutil.Process", return_value=self.target):
            result = self.adjuster.execute(self.process, tampered)

        self.assertEqual(result.action_status, "failed")
        self.assertIn("not issued", result.execution_error)
        self.assertFalse(
            any(call.args or call.kwargs for call in self.target.nice.call_args_list)
        )
        self.assertEqual(self.read_actions(), [])

    def test_priority_readback_mismatch_is_reported_as_failed(self) -> None:
        decision = self.create_proposal()
        def mismatched_nice(priority=None):
            if priority is not None:
                return 0
            return self.current_nice

        self.target.nice.side_effect = mismatched_nice
        with (
            patch("procnanny.priority.sys.platform", "linux"),
            patch("procnanny.priority.psutil.Process", return_value=self.target),
        ):
            result = self.adjuster.execute(self.process, decision)

        self.assertEqual(result.action_status, "failed")
        self.assertIn("verification failed", result.execution_error)
        self.assertFalse(self.read_actions()[0]["success"])

    def test_unsupported_priority_operation_is_logged_as_failed(self) -> None:
        decision = self.create_proposal()
        self.target.nice.side_effect = NotImplementedError(
            "priority setting is unsupported"
        )
        with (
            patch("procnanny.priority.sys.platform", "linux"),
            patch("procnanny.priority.psutil.Process", return_value=self.target),
        ):
            result = self.adjuster.execute(self.process, decision)

        self.assertEqual(result.action_status, "failed")
        self.assertIn("NotImplementedError", result.execution_error)
        self.assertEqual(self.read_actions()[0]["status"], "failed")

    def test_failed_action_is_not_retried_for_same_process_policy_step(self) -> None:
        decision = self.create_proposal()

        def denied(value=None):
            if value is not None:
                raise psutil.AccessDenied(pid=4242)
            return 0

        self.target.nice.side_effect = denied
        with (
            patch("procnanny.priority.sys.platform", "linux"),
            patch("procnanny.priority.psutil.Process", return_value=self.target),
        ):
            result = self.adjuster.execute(self.process, decision)
            for _ in range(4):
                next_decision = self.adjuster.evaluate(self.process, 80)
                result = self.adjuster.execute(self.process, next_decision)

        self.assertEqual(result.decision, "action_blocked")
        self.assertEqual(len(self.read_actions()), 1)
        self.assertEqual(
            sum(
                bool(call.args or call.kwargs)
                for call in self.target.nice.call_args_list
            ),
            1,
        )

    def test_duplicate_execution_of_one_decision_is_idempotent(self) -> None:
        decision = self.create_proposal()
        with (
            patch("procnanny.priority.sys.platform", "linux"),
            patch("procnanny.priority.psutil.Process", return_value=self.target),
        ):
            first = self.adjuster.execute(self.process, decision)
            second = self.adjuster.execute(self.process, decision)

        self.assertEqual(first.action_status, "applied")
        self.assertEqual(second.action_status, "applied")
        self.assertEqual(
            sum(
                bool(call.args or call.kwargs)
                for call in self.target.nice.call_args_list
            ),
            1,
        )
        self.assertEqual(len(self.read_actions()), 1)

    def test_dry_run_tracks_projected_priority_for_gradual_recovery(self) -> None:
        adjuster = PriorityAdjuster(
            replace(
                self.config,
                priority_adjustment_dry_run=True,
                priority_adjustment_cooldown_seconds=0,
            ),
            self.logger,
        )
        with (
            patch("procnanny.priority.sys.platform", "linux"),
            patch("procnanny.priority.psutil.Process", return_value=self.target),
        ):
            self.evaluate_and_execute(adjuster, self.process, 80)
            self.evaluate_and_execute(adjuster, self.process, 80)
            self.evaluate_and_execute(adjuster, self.process, 0)
            self.evaluate_and_execute(adjuster, self.process, 0)

        actions = self.read_actions()
        self.assertEqual(
            [action["action"] for action in actions],
            ["priority_demote", "priority_restore"],
        )
        self.assertEqual(
            [action["requested_priority"] for action in actions],
            [1, 0],
        )
        self.assertEqual(
            [action["previous_priority"] for action in actions],
            [0, 1],
        )
        self.assertFalse(
            any(call.args or call.kwargs for call in self.target.nice.call_args_list)
        )

    def test_disabled_mode_does_not_log_or_change_priorities(self) -> None:
        adjuster = PriorityAdjuster(
            replace(self.config, priority_adjustment_enabled=False), self.logger
        )
        with patch("procnanny.priority.psutil.Process", return_value=self.target):
            self.evaluate_twice_with(adjuster, self.process)

        self.target.nice.assert_not_called()
        self.assertEqual(self.read_actions(), [])

    def test_disabled_mode_evaluates_threshold_duration_and_logs_without_proposal(
        self,
    ) -> None:
        config = replace(
            self.config,
            priority_adjustment_enabled=False,
            priority_adjustment_dry_run=True,
            priority_adjustment_high_duration_seconds=10,
            poll_interval_seconds=5,
        )
        adjuster = PriorityAdjuster(config, self.logger)

        first = self.evaluate_and_execute(adjuster, self.process, 80)
        second = self.evaluate_and_execute(adjuster, self.process, 80)

        self.assertEqual(first.decision, "waiting_for_sustained_cpu")
        self.assertEqual(second.decision, "adjustment_disabled")
        self.assertIsNone(first.proposed_priority)
        self.assertIsNone(second.proposed_priority)
        self.target.nice.assert_not_called()
        events = self.logger.read_priority_actions(limit=20)
        self.assertEqual(
            [event["action"] for event in events],
            ["priority_evaluation", "priority_evaluation"],
        )
        self.assertEqual(
            [event["status"] for event in events],
            ["skipped", "skipped"],
        )
        self.assertIn("meeting the sustained-usage duration", events[0]["reason"])

    def test_disabled_duration_resets_when_cpu_falls_below_threshold(self) -> None:
        config = replace(
            self.config,
            priority_adjustment_enabled=False,
            priority_adjustment_high_duration_seconds=10,
            poll_interval_seconds=5,
        )
        adjuster = PriorityAdjuster(config, self.logger)

        first = adjuster.evaluate(self.process, 80)
        adjuster.evaluate(replace(self.process, cpu_percent=60), 60)
        after_reset = adjuster.evaluate(self.process, 80)

        self.assertEqual(first.decision, "waiting_for_sustained_cpu")
        self.assertEqual(after_reset.decision, "waiting_for_sustained_cpu")
        self.assertIn("observed 5", after_reset.explanation)

    def test_executed_proposal_does_not_shrink_evaluation_history_limit(self) -> None:
        adjuster = PriorityAdjuster(
            replace(
                self.config,
                priority_adjustment_dry_run=True,
                priority_adjustment_high_duration_seconds=5,
            ),
            self.logger,
        )
        with (
            patch("procnanny.priority.sys.platform", "linux"),
            patch("procnanny.priority.psutil.Process", return_value=self.target),
        ):
            decision = adjuster.evaluate(self.process, 80)
            decision = adjuster.execute(
                self.process,
                adjuster.evaluate(self.process, 80),
            )

        self.assertEqual(decision.action_status, "proposed")
        for pid in range(5000, 5225):
            adjuster.evaluate(
                ProcessInfo(pid, "worker", 10, 0, float(pid)),
                10,
            )

        self.assertEqual(len(adjuster.get_recent_decisions(limit=500)), 227)

    def test_skips_observation_without_available_priority_value(self) -> None:
        process = replace(self.process, nice_value=None)

        self.evaluate_twice(process)

        self.target.nice.assert_not_called()
        self.assertEqual(self.read_actions(), [])

    def test_excluded_process_is_not_adjusted(self) -> None:
        adjuster = PriorityAdjuster(
            replace(self.config, excluded_processes=("worker",)), self.logger
        )
        with patch("procnanny.priority.psutil.Process", return_value=self.target):
            self.evaluate_twice_with(adjuster, self.process)

        self.target.nice.assert_not_called()
        self.assertEqual(self.read_actions(), [])

    def test_root_owned_process_is_protected(self) -> None:
        root_process = replace(self.process, user_id=0)
        with patch("procnanny.priority.psutil.Process", return_value=self.target):
            self.evaluate_twice_with(self.adjuster, root_process)

        self.target.nice.assert_not_called()
        self.assertEqual(self.read_actions(), [])

    def test_permission_failure_is_logged_without_raising(self) -> None:
        def denied(value=None):
            if value is not None:
                raise psutil.AccessDenied(pid=4242)
            return 0

        self.target.nice.side_effect = denied
        with (
            patch("procnanny.priority.sys.platform", "linux"),
            patch("procnanny.priority.psutil.Process", return_value=self.target),
        ):
            self.evaluate_twice()

        action = self.read_actions()[0]
        self.assertFalse(action["success"])
        self.assertIn("AccessDenied", str(action["error"]))

    def test_terminated_process_is_logged_as_failed(self) -> None:
        with (
            patch("procnanny.priority.sys.platform", "linux"),
            patch(
                "procnanny.priority.psutil.Process",
                side_effect=psutil.NoSuchProcess(4242),
            ),
        ):
            self.evaluate_twice()

        self.assertFalse(self.read_actions()[0]["success"])

    def test_pid_reuse_resets_original_priority_and_sustained_cpu_window(self) -> None:
        with (
            patch("procnanny.priority.sys.platform", "linux"),
            patch("procnanny.priority.psutil.Process", return_value=self.target),
        ):
            self.evaluate_twice()
            self.target.create_time.return_value = 2000.0
            self.target.name.return_value = "new-worker"
            self.current_nice = 2
            reused = ProcessInfo(4242, "new-worker", 80, 2, 2000.0)
            self.adjuster.evaluate(reused, 80)
            self.assertEqual(len(self.read_actions()), 1)
            self.evaluate_and_execute(self.adjuster, reused, 80)

        actions = self.read_actions()
        self.assertEqual(len(actions), 2)
        self.assertEqual(actions[-1]["create_time"], 2000.0)
        self.assertEqual(actions[-1]["previous_priority"], 2)
        self.assertEqual(actions[-1]["requested_priority"], 3)

    def test_rejects_pid_reuse_during_os_priority_operation(self) -> None:
        self.target.create_time.return_value = 2000.0
        with (
            patch("procnanny.priority.sys.platform", "linux"),
            patch("procnanny.priority.psutil.Process", return_value=self.target),
        ):
            self.evaluate_twice()

        self.target.nice.assert_not_called()
        action = self.read_actions()[0]
        self.assertFalse(action["success"])
        self.assertIn("identity changed", str(action["error"]))

    def test_decision_records_explain_dry_run_proposal(self) -> None:
        adjuster = PriorityAdjuster(
            replace(self.config, priority_adjustment_dry_run=True),
            self.logger,
        )
        with (
            patch("procnanny.priority.sys.platform", "linux"),
            patch("procnanny.priority.psutil.Process", return_value=self.target),
        ):
            adjuster.evaluate(self.process, 80)
            result = adjuster.evaluate(self.process, 80)
            result = adjuster.execute(self.process, result)

        self.assertEqual(result.decision, "proposal")
        self.assertEqual(result.average_cpu_percent, 80)
        self.assertEqual(result.threshold_percent, self.config.cpu_high_threshold_percent)
        self.assertEqual(result.current_priority["scheme"], "unix_nice")
        self.assertEqual(result.proposed_priority["value"], 1)
        self.assertEqual(result.action_status, "proposed")
        self.assertIn("Sustained rolling-average CPU", result.explanation)
        self.assertEqual(adjuster.get_recent_decisions(1)[0]["decision"], "proposal")
        self.assertFalse(any(call.args for call in self.target.nice.call_args_list))

    def test_invalid_cpu_measurement_is_ineligible(self) -> None:
        process = replace(self.process, cpu_percent=float("nan"))

        result = self.adjuster.evaluate(process, 80)

        self.assertEqual(result.decision, "invalid_observation")
        self.assertIsNone(result.cpu_percent)
        self.assertFalse(result.eligible)
        self.assertEqual(result.eligibility_reason, "invalid_observation")
        self.assertEqual(self.read_actions(), [])

    def test_rejects_invalid_proposed_nice_value(self) -> None:
        adjuster = PriorityAdjuster(
            replace(self.config, max_nice_value=100),
            self.logger,
        )
        process = replace(self.process, nice_value=19)
        with patch("procnanny.priority.sys.platform", "linux"):
            adjuster.evaluate(process, 80)
            result = adjuster.evaluate(process, 80)

        self.assertEqual(result.decision, "invalid_proposal")
        self.assertEqual(result.eligibility_reason, "invalid_proposal")
        self.assertEqual(self.read_actions(), [])

    def test_protected_process_decision_contains_exclusion_reason(self) -> None:
        adjuster = PriorityAdjuster(
            replace(self.config, excluded_processes=("worker",)),
            self.logger,
        )

        result = adjuster.evaluate(self.process, 80)

        self.assertEqual(result.decision, "excluded")
        self.assertFalse(result.eligible)
        self.assertEqual(
            result.eligibility_reason,
            "configured_protected_process",
        )

    def test_hysteresis_resets_sustained_cpu_timer(self) -> None:
        adjuster = PriorityAdjuster(
            replace(self.config, priority_adjustment_dry_run=True),
            self.logger,
        )
        with patch("procnanny.priority.sys.platform", "linux"):
            first = adjuster.evaluate(self.process, 80)
            middle = adjuster.evaluate(self.process, 40)
            after_reset = adjuster.evaluate(self.process, 80)

        self.assertEqual(first.decision, "waiting_for_sustained_cpu")
        self.assertEqual(middle.decision, "hysteresis_hold")
        self.assertEqual(after_reset.decision, "waiting_for_sustained_cpu")
        self.assertEqual(self.read_actions(), [])

    def test_windows_policy_uses_priority_classes_not_nice_values(self) -> None:
        adjuster = PriorityAdjuster(
            replace(self.config, priority_adjustment_dry_run=True),
            self.logger,
        )
        process = replace(self.process, nice_value=psutil.NORMAL_PRIORITY_CLASS)
        with patch("procnanny.priority.sys.platform", "win32"):
            adjuster.evaluate(process, 80)
            result = adjuster.evaluate(process, 80)

        self.assertEqual(
            result.current_priority["scheme"],
            "windows_priority_class",
        )
        self.assertEqual(
            result.proposed_priority["value"],
            psutil.BELOW_NORMAL_PRIORITY_CLASS,
        )
        self.assertEqual(
            result.proposed_priority["label"],
            "BELOW_NORMAL_PRIORITY_CLASS",
        )
        self.current_nice = psutil.NORMAL_PRIORITY_CLASS
        with patch("procnanny.priority.psutil.Process", return_value=self.target):
            result = adjuster.execute(process, result)
        self.assertEqual(result.action_status, "proposed")
        self.assertFalse(any(call.args for call in self.target.nice.call_args_list))

    def test_windows_live_execution_uses_psutil_priority_class_setter(self) -> None:
        adjuster = PriorityAdjuster(
            replace(self.config, priority_adjustment_dry_run=False),
            self.logger,
        )
        process = replace(self.process, nice_value=psutil.NORMAL_PRIORITY_CLASS)
        self.current_nice = psutil.NORMAL_PRIORITY_CLASS
        with (
            patch("procnanny.priority.sys.platform", "win32"),
            patch("procnanny.priority.psutil.Process", return_value=self.target),
        ):
            adjuster.evaluate(process, 80)
            decision = adjuster.evaluate(process, 80)
            result = adjuster.execute(process, decision)

        self.assertEqual(result.action_status, "applied")
        self.assertEqual(self.current_nice, psutil.BELOW_NORMAL_PRIORITY_CLASS)
        self.target.nice.assert_any_call(psutil.BELOW_NORMAL_PRIORITY_CLASS)
        self.assertEqual(
            self.read_actions()[0]["requested_priority"],
            psutil.BELOW_NORMAL_PRIORITY_CLASS,
        )

    def test_windows_live_recovery_calls_setter_to_restore_original_class(self) -> None:
        adjuster = PriorityAdjuster(
            replace(
                self.config,
                priority_adjustment_dry_run=False,
                priority_adjustment_cooldown_seconds=10,
                priority_adjustment_recovery_duration_seconds=10,
            ),
            self.logger,
        )
        process = replace(self.process, nice_value=psutil.NORMAL_PRIORITY_CLASS)
        self.current_nice = psutil.NORMAL_PRIORITY_CLASS
        with (
            patch("procnanny.priority.sys.platform", "win32"),
            patch("procnanny.priority.psutil.Process", return_value=self.target),
        ):
            self.evaluate_and_execute(adjuster, process, 80)
            self.evaluate_and_execute(adjuster, process, 80)
            recovery_process = replace(
                process,
                nice_value=psutil.BELOW_NORMAL_PRIORITY_CLASS,
                cpu_percent=0,
            )
            self.evaluate_and_execute(adjuster, recovery_process, 0)
            restored = self.evaluate_and_execute(adjuster, recovery_process, 0)

        self.assertEqual(restored.action_status, "applied")
        self.assertEqual(self.current_nice, psutil.NORMAL_PRIORITY_CLASS)
        self.target.nice.assert_any_call(psutil.BELOW_NORMAL_PRIORITY_CLASS)
        self.target.nice.assert_any_call(psutil.NORMAL_PRIORITY_CLASS)
        actions = self.read_actions()
        self.assertEqual(
            [action["action"] for action in actions],
            ["priority_demote", "priority_restore"],
        )
        self.assertEqual(
            actions[-1]["resulting_priority"],
            psutil.NORMAL_PRIORITY_CLASS,
        )

    def test_windows_realtime_priority_is_excluded(self) -> None:
        adjuster = PriorityAdjuster(
            replace(self.config, priority_adjustment_dry_run=True),
            self.logger,
        )
        process = replace(self.process, nice_value=psutil.REALTIME_PRIORITY_CLASS)
        with patch("procnanny.priority.sys.platform", "win32"):
            result = adjuster.evaluate(process, 80)

        self.assertEqual(result.decision, "excluded")
        self.assertEqual(
            result.eligibility_reason,
            "realtime_priority_process",
        )
        self.assertEqual(self.read_actions(), [])

    def test_windows_critical_processes_are_excluded_before_execution(self) -> None:
        config = replace(
            self.config,
            priority_adjustment_enabled=True,
            priority_adjustment_dry_run=False,
            priority_adjustment_high_duration_seconds=5,
        )
        adjuster = PriorityAdjuster(config, self.logger)
        protected_processes = [
            ProcessInfo(4, "System", 95, psutil.NORMAL_PRIORITY_CLASS, 1000.0),
            ProcessInfo(232, "Registry", 95, psutil.NORMAL_PRIORITY_CLASS, 1001.0),
            ProcessInfo(804, "smss.exe", 95, psutil.NORMAL_PRIORITY_CLASS, 1002.0),
            ProcessInfo(1064, "csrss.exe", 95, psutil.NORMAL_PRIORITY_CLASS, 1003.0),
            ProcessInfo(912, "svchost.exe", 95, psutil.NORMAL_PRIORITY_CLASS, 1004.0),
            ProcessInfo(1100, "lsass.exe", 95, psutil.NORMAL_PRIORITY_CLASS, 1005.0),
        ]

        with (
            patch("procnanny.priority.sys.platform", "win32"),
            patch("procnanny.priority.psutil.Process") as process_factory,
        ):
            results = [
                adjuster.execute(
                    process,
                    adjuster.evaluate(process, 95),
                )
                for process in protected_processes
            ]

        self.assertTrue(all(result.decision == "excluded" for result in results))
        self.assertTrue(
            all(
                result.eligibility_reason == "windows_system_process"
                for result in results
            )
        )
        self.assertTrue(all(result.proposed_priority is None for result in results))
        process_factory.assert_not_called()
        events = self.logger.read_priority_actions(limit=20)
        self.assertEqual(len(events), len(protected_processes))
        self.assertTrue(all(event["status"] == "excluded" for event in events))

    def evaluate_twice_with(
        self,
        adjuster: PriorityAdjuster,
        process: ProcessInfo,
    ) -> None:
        self.evaluate_and_execute(adjuster, process, 80)
        self.evaluate_and_execute(adjuster, process, 80)


if __name__ == "__main__":
    unittest.main()
