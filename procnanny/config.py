"""Loading and validation for ProcNanny's YAML configuration."""

from __future__ import annotations

import math
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"
LINUX_MIN_NICE = -20
LINUX_MAX_NICE = 19


class _UniqueKeySafeLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects ambiguous duplicate mapping keys."""

    def construct_mapping(
        self,
        node: yaml.MappingNode,
        deep: bool = False,
    ) -> dict[Any, Any]:
        self.flatten_mapping(node)
        mapping: dict[Any, Any] = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                duplicate = key in mapping
            except TypeError as exc:
                raise yaml.constructor.ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    "found an unhashable key",
                    key_node.start_mark,
                ) from exc
            if duplicate:
                raise yaml.constructor.ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    f"duplicate key {key!r}",
                    key_node.start_mark,
                )
            value = self.construct_object(value_node, deep=deep)
            mapping[key] = value
        return mapping


class ProcNannyConfigError(ValueError):
    """Raised when configuration cannot be loaded or is invalid."""


class ProcNannyConfigPersistenceError(ProcNannyConfigError):
    """Raised when a validated configuration cannot be persisted."""


@dataclass(frozen=True)
class ProcNannyConfig:
    """Validated ProcNanny settings."""

    cpu_high_threshold_percent: float
    cpu_low_threshold_percent: float
    poll_interval_seconds: float
    history_window_seconds: float
    min_nice_value: int
    max_nice_value: int
    excluded_processes: tuple[str, ...]
    priority_adjustment_enabled: bool = False
    priority_adjustment_dry_run: bool = True
    priority_adjustment_cooldown_seconds: float = 30
    priority_adjustment_high_duration_seconds: float = 15
    priority_adjustment_recovery_duration_seconds: float = 15
    priority_adjustment_step: int = 1


def _require_section(
    data: dict[str, Any], section: str, expected: set[str]
) -> dict[str, Any]:
    value = data.get(section)
    if not isinstance(value, dict):
        raise ProcNannyConfigError(f"'{section}' must be a YAML mapping.")

    missing = expected - value.keys()
    unknown = value.keys() - expected
    if missing:
        raise ProcNannyConfigError(
            f"Missing setting(s) in '{section}': {', '.join(sorted(missing))}."
        )
    if unknown:
        raise ProcNannyConfigError(
            f"Unknown setting(s) in '{section}': {', '.join(sorted(unknown))}."
        )
    return value


def _require_number(section: str, setting: str, value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProcNannyConfigError(f"'{section}.{setting}' must be a number.")
    if not math.isfinite(value):
        raise ProcNannyConfigError(f"'{section}.{setting}' must be finite.")
    return float(value)


def _require_integer(section: str, setting: str, value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProcNannyConfigError(f"'{section}.{setting}' must be an integer.")
    return value


def load_config(path: str | Path = DEFAULT_CONFIG_PATH) -> ProcNannyConfig:
    """Load and validate a ProcNanny YAML configuration file."""
    config_path = Path(path)
    try:
        with config_path.open(encoding="utf-8") as config_file:
            data = yaml.load(config_file, Loader=_UniqueKeySafeLoader)
    except OSError as exc:
        raise ProcNannyConfigError(
            f"Cannot read configuration file '{config_path}': {exc}."
        ) from exc
    except yaml.YAMLError as exc:
        raise ProcNannyConfigError(
            f"Invalid YAML in configuration file '{config_path}': {exc}."
        ) from exc

    if not isinstance(data, dict):
        raise ProcNannyConfigError(
            "The configuration file must contain a YAML mapping."
        )

    unknown_sections = data.keys() - {
        "cpu",
        "monitoring",
        "nice",
        "priority_adjustment",
    }
    missing_sections = {"cpu", "monitoring", "nice"} - data.keys()
    if missing_sections:
        raise ProcNannyConfigError(
            f"Missing section(s): {', '.join(sorted(missing_sections))}."
        )
    if unknown_sections:
        raise ProcNannyConfigError(
            f"Unknown section(s): {', '.join(sorted(unknown_sections))}."
        )

    cpu = _require_section(
        data, "cpu", {"high_threshold_percent", "low_threshold_percent"}
    )
    monitoring = _require_section(
        data,
        "monitoring",
        {"poll_interval_seconds", "history_window_seconds", "excluded_processes"},
    )
    nice = _require_section(data, "nice", {"min_value", "max_value"})

    high = _require_number(
        "cpu", "high_threshold_percent", cpu["high_threshold_percent"]
    )
    low = _require_number("cpu", "low_threshold_percent", cpu["low_threshold_percent"])
    poll_interval = _require_number(
        "monitoring", "poll_interval_seconds", monitoring["poll_interval_seconds"]
    )
    history_window = _require_number(
        "monitoring", "history_window_seconds", monitoring["history_window_seconds"]
    )
    excluded_processes = monitoring["excluded_processes"]
    if not isinstance(excluded_processes, list) or any(
        not isinstance(name, str) or not name.strip() for name in excluded_processes
    ):
        raise ProcNannyConfigError(
            "'monitoring.excluded_processes' must be a list of non-empty process names."
        )
    min_nice = _require_integer("nice", "min_value", nice["min_value"])
    max_nice = _require_integer("nice", "max_value", nice["max_value"])
    priority = data.get(
        "priority_adjustment",
        {
            "enabled": False,
            "dry_run": True,
            "cooldown_seconds": 30,
            "high_duration_seconds": 15,
            "recovery_duration_seconds": 15,
            "step": 1,
        },
    )
    if not isinstance(priority, dict):
        raise ProcNannyConfigError("'priority_adjustment' must be a YAML mapping.")
    priority_keys = {
        "enabled",
        "dry_run",
        "cooldown_seconds",
        "high_duration_seconds",
        "recovery_duration_seconds",
        "step",
    }
    missing_priority = priority_keys - priority.keys()
    unknown_priority = priority.keys() - priority_keys
    if missing_priority:
        raise ProcNannyConfigError(
            "Missing setting(s) in 'priority_adjustment': "
            f"{', '.join(sorted(missing_priority))}."
        )
    if unknown_priority:
        raise ProcNannyConfigError(
            "Unknown setting(s) in 'priority_adjustment': "
            f"{', '.join(sorted(unknown_priority))}."
        )
    adjustment_enabled = priority["enabled"]
    dry_run = priority["dry_run"]
    if not isinstance(adjustment_enabled, bool):
        raise ProcNannyConfigError("'priority_adjustment.enabled' must be a boolean.")
    if not isinstance(dry_run, bool):
        raise ProcNannyConfigError("'priority_adjustment.dry_run' must be a boolean.")
    cooldown_seconds = _require_number(
        "priority_adjustment", "cooldown_seconds", priority["cooldown_seconds"]
    )
    high_duration_seconds = _require_number(
        "priority_adjustment",
        "high_duration_seconds",
        priority["high_duration_seconds"],
    )
    recovery_duration_seconds = _require_number(
        "priority_adjustment",
        "recovery_duration_seconds",
        priority["recovery_duration_seconds"],
    )
    adjustment_step = _require_integer(
        "priority_adjustment", "step", priority["step"]
    )

    if low < 0:
        raise ProcNannyConfigError("'cpu.low_threshold_percent' must not be negative.")
    if high < 0:
        raise ProcNannyConfigError("'cpu.high_threshold_percent' must not be negative.")
    if low >= high:
        raise ProcNannyConfigError(
            "'cpu.low_threshold_percent' must be less than 'cpu.high_threshold_percent'."
        )
    if poll_interval <= 0:
        raise ProcNannyConfigError(
            "'monitoring.poll_interval_seconds' must be greater than 0."
        )
    if history_window <= 0:
        raise ProcNannyConfigError(
            "'monitoring.history_window_seconds' must be greater than 0."
        )
    if history_window < poll_interval:
        raise ProcNannyConfigError(
            "'monitoring.history_window_seconds' must be at least "
            "'monitoring.poll_interval_seconds'."
        )
    if not LINUX_MIN_NICE <= min_nice <= LINUX_MAX_NICE:
        raise ProcNannyConfigError(
            f"'nice.min_value' must be between {LINUX_MIN_NICE} and {LINUX_MAX_NICE}."
        )
    if not LINUX_MIN_NICE <= max_nice <= LINUX_MAX_NICE:
        raise ProcNannyConfigError(
            f"'nice.max_value' must be between {LINUX_MIN_NICE} and {LINUX_MAX_NICE}."
        )
    if min_nice > max_nice:
        raise ProcNannyConfigError("'nice.min_value' must not exceed 'nice.max_value'.")
    if cooldown_seconds < 0:
        raise ProcNannyConfigError(
            "'priority_adjustment.cooldown_seconds' must not be negative."
        )
    if high_duration_seconds <= 0 or recovery_duration_seconds <= 0:
        raise ProcNannyConfigError(
            "Priority adjustment durations must be greater than 0."
        )
    if adjustment_step < 1:
        raise ProcNannyConfigError("'priority_adjustment.step' must be at least 1.")

    return ProcNannyConfig(
        cpu_high_threshold_percent=high,
        cpu_low_threshold_percent=low,
        poll_interval_seconds=poll_interval,
        history_window_seconds=history_window,
        min_nice_value=min_nice,
        max_nice_value=max_nice,
        excluded_processes=tuple(name.strip() for name in excluded_processes),
        priority_adjustment_enabled=adjustment_enabled,
        priority_adjustment_dry_run=dry_run,
        priority_adjustment_cooldown_seconds=cooldown_seconds,
        priority_adjustment_high_duration_seconds=high_duration_seconds,
        priority_adjustment_recovery_duration_seconds=recovery_duration_seconds,
        priority_adjustment_step=adjustment_step,
    )


def save_config(
    config: ProcNannyConfig,
    path: str | Path = DEFAULT_CONFIG_PATH,
) -> ProcNannyConfig:
    """Validate and atomically persist the active configuration as YAML."""
    config_path = Path(path)
    data = {
        "cpu": {
            "high_threshold_percent": config.cpu_high_threshold_percent,
            "low_threshold_percent": config.cpu_low_threshold_percent,
        },
        "monitoring": {
            "poll_interval_seconds": config.poll_interval_seconds,
            "history_window_seconds": config.history_window_seconds,
            "excluded_processes": list(config.excluded_processes),
        },
        "nice": {
            "min_value": config.min_nice_value,
            "max_value": config.max_nice_value,
        },
        "priority_adjustment": {
            "enabled": config.priority_adjustment_enabled,
            "dry_run": config.priority_adjustment_dry_run,
            "cooldown_seconds": config.priority_adjustment_cooldown_seconds,
            "high_duration_seconds": (
                config.priority_adjustment_high_duration_seconds
            ),
            "recovery_duration_seconds": (
                config.priority_adjustment_recovery_duration_seconds
            ),
            "step": config.priority_adjustment_step,
        },
    }

    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=config_path.parent,
            prefix=f".{config_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            yaml.safe_dump(data, temporary_file, sort_keys=False)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())

        validated = load_config(temporary_path)
        os.replace(temporary_path, config_path)
        return validated
    except OSError as exc:
        raise ProcNannyConfigPersistenceError(
            f"Cannot persist configuration file '{config_path}': {exc}."
        ) from exc
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
