from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from placement_engine import placement_plan
from usage_store import disable_usage, enable_usage, usage_status


class UsageConcurrencyTests(unittest.TestCase):
    def test_reenable_restarts_continuous_observation_window(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            usage_dir = Path(temp) / "usage"
            enable_usage(usage_dir, date(2026, 7, 1))
            disable_usage(usage_dir)
            enable_usage(usage_dir, date(2026, 8, 17))
            self.assertEqual("2026-08-17", usage_status(usage_dir)["enabled_day"])

    def test_disabled_usage_does_not_accumulate_observation_days(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            enable_usage(root / "usage", date(2026, 7, 1))
            disable_usage(root / "usage")
            result = placement_plan(root / "usage", [], root, today=date(2026, 8, 17))
            self.assertEqual(0, result["observation_days"])

    def test_concurrent_feedback_does_not_lose_counts(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            usage_dir = root / "usage"
            enable_usage(usage_dir)
            command = [
                sys.executable,
                str(Path(__file__).resolve().parents[1] / "scripts" / "skill_router.py"),
                "feedback",
                "opened",
                "canonical-alpha",
                "--usage-dir",
                str(usage_dir),
                "--project",
                str(root / "project"),
            ]

            def run_feedback(_: int) -> int:
                result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
                return result.returncode

            with ThreadPoolExecutor(max_workers=4) as pool:
                codes = list(pool.map(run_feedback, range(8)))
            self.assertEqual([0] * 8, codes)
            payload = json.loads((usage_dir / "stats.json").read_text(encoding="utf-8"))
            self.assertEqual(8, payload["buckets"][0]["counts"]["opened"])


if __name__ == "__main__":
    unittest.main()
