"""Tests for per-process rolling CPU history."""

import unittest
from dataclasses import replace

from procnanny.config import load_config
from procnanny.history import ProcessHistory


class ProcessHistoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = replace(
            load_config(),
            poll_interval_seconds=5,
            history_window_seconds=10,
        )

    def test_calculates_average_for_each_pid(self) -> None:
        history = ProcessHistory(self.config)

        self.assertEqual(history.add_sample(10, 10), 10)
        self.assertEqual(history.add_sample(10, 20), 15)
        self.assertEqual(history.add_sample(11, 80), 80)
        self.assertEqual(history.get_average(10), 15)
        self.assertEqual(history.get_average(11), 80)

    def test_discards_oldest_sample_when_window_is_full(self) -> None:
        history = ProcessHistory(self.config)
        for sample in (10, 20, 30):
            history.add_sample(10, sample)

        self.assertEqual(history.get_average(10), 25)

    def test_returns_none_for_unseen_pid(self) -> None:
        self.assertIsNone(ProcessHistory(self.config).get_average(999))

    def test_removes_stale_processes(self) -> None:
        history = ProcessHistory(self.config)
        history.add_sample(10, 25)
        history.add_sample(11, 50)

        history.retain_pids({11})

        self.assertIsNone(history.get_average(10))
        self.assertEqual(history.get_average(11), 50)
        self.assertEqual(len(history), 1)

    def test_removes_explicit_pid(self) -> None:
        history = ProcessHistory(self.config)
        history.add_sample(10, 25)

        history.remove(10)

        self.assertIsNone(history.get_average(10))

    def test_resets_history_when_pid_is_reused(self) -> None:
        history = ProcessHistory(self.config)
        history.add_sample(10, 80, create_time=1000)

        self.assertEqual(history.add_sample(10, 10, create_time=2000), 10)
        self.assertEqual(history.get_average(10), 10)

    def test_rejects_invalid_pid_and_cpu_samples(self) -> None:
        history = ProcessHistory(self.config)
        invalid_samples = (
            (0, 10, None),
            (True, 10, None),
            (1, -1, None),
            (1, float("nan"), None),
            (1, 10, float("inf")),
        )

        for pid, cpu_percent, create_time in invalid_samples:
            with (
                self.subTest(pid=pid, cpu_percent=cpu_percent, create_time=create_time),
                self.assertRaises(ValueError),
            ):
                history.add_sample(pid, cpu_percent, create_time)


if __name__ == "__main__":
    unittest.main()
