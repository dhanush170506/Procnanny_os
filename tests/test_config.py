"""Tests for ProcNanny configuration loading and validation."""

import tempfile
import unittest
from pathlib import Path

import yaml

from procnanny.config import (
    DEFAULT_CONFIG_PATH,
    ProcNannyConfig,
    ProcNannyConfigError,
    load_config,
    save_config,
)

VALID_CONFIG = {
    "cpu": {"high_threshold_percent": 70, "low_threshold_percent": 10},
    "monitoring": {
        "poll_interval_seconds": 5,
        "history_window_seconds": 30,
        "excluded_processes": [],
    },
    "nice": {"min_value": -20, "max_value": 19},
}


class LoadConfigTests(unittest.TestCase):
    def write_config(self, directory: str, config: object) -> Path:
        path = Path(directory) / "config.yaml"
        path.write_text(yaml.safe_dump(config), encoding="utf-8")
        return path

    def test_loads_project_default_configuration(self) -> None:
        config = load_config()

        self.assertEqual(DEFAULT_CONFIG_PATH.name, "config.yaml")
        self.assertEqual(config.cpu_high_threshold_percent, 70)
        self.assertEqual(config.cpu_low_threshold_percent, 10)
        self.assertEqual(config.poll_interval_seconds, 5)
        self.assertEqual(config.history_window_seconds, 30)
        self.assertEqual(config.min_nice_value, -20)
        self.assertEqual(config.max_nice_value, 19)
        self.assertEqual(config.excluded_processes, ())
        self.assertFalse(config.priority_adjustment_enabled)
        self.assertTrue(config.priority_adjustment_dry_run)
        self.assertEqual(config.priority_adjustment_cooldown_seconds, 30)
        self.assertEqual(config.priority_adjustment_high_duration_seconds, 15)
        self.assertEqual(config.priority_adjustment_recovery_duration_seconds, 15)
        self.assertEqual(config.priority_adjustment_step, 1)

    def test_loads_custom_configuration(self) -> None:
        custom = {
            "cpu": {"high_threshold_percent": 85, "low_threshold_percent": 15},
            "monitoring": {
                "poll_interval_seconds": 2.5,
                "history_window_seconds": 15,
                "excluded_processes": ["backup"],
            },
            "nice": {"min_value": 0, "max_value": 10},
        }
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_config(directory, custom)

            config = load_config(path)

        self.assertEqual(config.cpu_high_threshold_percent, 85)
        self.assertEqual(config.poll_interval_seconds, 2.5)
        self.assertEqual(config.min_nice_value, 0)
        self.assertEqual(config.excluded_processes, ("backup",))

    def test_persists_and_reloads_validated_settings(self) -> None:
        config = load_config()
        updated = ProcNannyConfig(
            **{
                **config.__dict__,
                "cpu_high_threshold_percent": 82,
                "priority_adjustment_enabled": True,
                "priority_adjustment_dry_run": True,
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            saved = save_config(updated, path)

            loaded = load_config(path)

        self.assertEqual(saved, updated)
        self.assertEqual(loaded, updated)

    def test_invalid_save_does_not_replace_existing_configuration(self) -> None:
        config = load_config()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            save_config(config, path)
            original = path.read_text(encoding="utf-8")
            invalid = ProcNannyConfig(
                **{
                    **config.__dict__,
                    "cpu_low_threshold_percent": config.cpu_high_threshold_percent,
                }
            )

            with self.assertRaisesRegex(
                ProcNannyConfigError, "must be less than"
            ):
                save_config(invalid, path)

            self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_missing_file_reports_path(self) -> None:
        with self.assertRaisesRegex(
            ProcNannyConfigError, "Cannot read configuration file"
        ):
            load_config("missing-config.yaml")

    def test_malformed_yaml_has_clear_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            path.write_text("cpu: [", encoding="utf-8")

            with self.assertRaisesRegex(ProcNannyConfigError, "Invalid YAML"):
                load_config(path)

    def test_rejects_duplicate_yaml_settings(self) -> None:
        contents = """\
cpu:
  high_threshold_percent: 70
  high_threshold_percent: 95
  low_threshold_percent: 10
monitoring:
  poll_interval_seconds: 5
  history_window_seconds: 30
  excluded_processes: []
nice:
  min_value: -20
  max_value: 19
"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.yaml"
            path.write_text(contents, encoding="utf-8")

            with self.assertRaisesRegex(ProcNannyConfigError, "duplicate key"):
                load_config(path)

    def test_rejects_non_mapping_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_config(directory, [])

            with self.assertRaisesRegex(
                ProcNannyConfigError, "must contain a YAML mapping"
            ):
                load_config(path)

    def test_rejects_missing_or_unknown_settings(self) -> None:
        missing = {
            **VALID_CONFIG,
            "cpu": {"high_threshold_percent": 70},
        }
        unknown = {
            **VALID_CONFIG,
            "monitoring": {**VALID_CONFIG["monitoring"], "extra": True},
        }

        for invalid in (missing, unknown):
            with (
                self.subTest(config=invalid),
                tempfile.TemporaryDirectory() as directory,
            ):
                path = self.write_config(directory, invalid)
                with self.assertRaises(ProcNannyConfigError):
                    load_config(path)

    def test_rejects_invalid_cpu_thresholds(self) -> None:
        invalid_configs = (
            {
                **VALID_CONFIG,
                "cpu": {"high_threshold_percent": -1, "low_threshold_percent": 0},
            },
            {
                **VALID_CONFIG,
                "cpu": {"high_threshold_percent": 10, "low_threshold_percent": 10},
            },
            {
                **VALID_CONFIG,
                "cpu": {"high_threshold_percent": True, "low_threshold_percent": 5},
            },
        )

        for invalid in invalid_configs:
            with (
                self.subTest(config=invalid),
                tempfile.TemporaryDirectory() as directory,
            ):
                path = self.write_config(directory, invalid)
                with self.assertRaises(ProcNannyConfigError):
                    load_config(path)

    def test_allows_thresholds_over_100_for_multi_core_process_usage(self) -> None:
        config = {
            **VALID_CONFIG,
            "cpu": {"high_threshold_percent": 150, "low_threshold_percent": 10},
        }
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_config(directory, config)

            loaded = load_config(path)

        self.assertEqual(loaded.cpu_high_threshold_percent, 150)

    def test_rejects_invalid_monitoring_intervals(self) -> None:
        invalid_configs = (
            {
                **VALID_CONFIG,
                "monitoring": {
                    **VALID_CONFIG["monitoring"],
                    "poll_interval_seconds": 0,
                },
            },
            {
                **VALID_CONFIG,
                "monitoring": {
                    **VALID_CONFIG["monitoring"],
                    "poll_interval_seconds": -1,
                },
            },
            {
                **VALID_CONFIG,
                "monitoring": {
                    **VALID_CONFIG["monitoring"],
                    "poll_interval_seconds": float("inf"),
                },
            },
            {
                **VALID_CONFIG,
                "monitoring": {
                    **VALID_CONFIG["monitoring"],
                    "history_window_seconds": 4,
                },
            },
        )

        for invalid in invalid_configs:
            with (
                self.subTest(config=invalid),
                tempfile.TemporaryDirectory() as directory,
            ):
                path = self.write_config(directory, invalid)
                with self.assertRaises(ProcNannyConfigError):
                    load_config(path)

    def test_rejects_invalid_excluded_process_names(self) -> None:
        invalid_configs = (
            {
                **VALID_CONFIG,
                "monitoring": {
                    **VALID_CONFIG["monitoring"],
                    "excluded_processes": "backup",
                },
            },
            {
                **VALID_CONFIG,
                "monitoring": {
                    **VALID_CONFIG["monitoring"],
                    "excluded_processes": ["   "],
                },
            },
        )

        for invalid in invalid_configs:
            with (
                self.subTest(config=invalid),
                tempfile.TemporaryDirectory() as directory,
            ):
                path = self.write_config(directory, invalid)
                with self.assertRaisesRegex(ProcNannyConfigError, "excluded_processes"):
                    load_config(path)

    def test_rejects_nice_values_outside_linux_range_or_inverted_limits(self) -> None:
        invalid_configs = (
            {**VALID_CONFIG, "nice": {"min_value": -21, "max_value": 19}},
            {**VALID_CONFIG, "nice": {"min_value": 10, "max_value": 9}},
            {**VALID_CONFIG, "nice": {"min_value": 0.5, "max_value": 19}},
        )

        for invalid in invalid_configs:
            with (
                self.subTest(config=invalid),
                tempfile.TemporaryDirectory() as directory,
            ):
                path = self.write_config(directory, invalid)
                with self.assertRaises(ProcNannyConfigError):
                    load_config(path)

    def test_rejects_invalid_priority_adjustment_settings(self) -> None:
        invalid_adjustments = (
            {"enabled": "yes", "dry_run": True, "cooldown_seconds": 1,
             "high_duration_seconds": 5, "recovery_duration_seconds": 5, "step": 1},
            {"enabled": True, "dry_run": False, "cooldown_seconds": -1,
             "high_duration_seconds": 5, "recovery_duration_seconds": 5, "step": 1},
            {"enabled": True, "dry_run": False, "cooldown_seconds": 1,
             "high_duration_seconds": 0, "recovery_duration_seconds": 5, "step": 1},
            {"enabled": True, "dry_run": False, "cooldown_seconds": 1,
             "high_duration_seconds": 5, "recovery_duration_seconds": 5, "step": 0},
        )
        for adjustment in invalid_adjustments:
            with (
                self.subTest(adjustment=adjustment),
                tempfile.TemporaryDirectory() as directory,
            ):
                path = self.write_config(
                    directory,
                    {**VALID_CONFIG, "priority_adjustment": adjustment},
                )
                with self.assertRaises(ProcNannyConfigError):
                    load_config(path)


if __name__ == "__main__":
    unittest.main()
