from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from initialization_cli import resolve_improvement_choice
from usage_config import complete_review, decline_config
from usage_store import enable_usage, usage_status


class UsageInitializationTests(unittest.TestCase):
    def test_decline_is_persisted_without_creating_key(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            usage_dir = Path(temp) / "usage"
            decline_config(usage_dir)
            status = usage_status(usage_dir)
            self.assertTrue(status["configured"])
            self.assertEqual("declined", status["choice"])
            self.assertFalse(status["enabled"])
            self.assertFalse((usage_dir / "local.key").exists())

    def test_review_due_and_completion_advance_schedule(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            usage_dir = Path(temp) / "usage"
            enable_usage(usage_dir, date(2026, 7, 1))
            due = usage_status(usage_dir, today=date(2026, 7, 30))
            self.assertEqual("2026-07-30", due["next_review_day"])
            self.assertTrue(due["review_due"])
            complete_review(usage_dir, date(2026, 8, 17))
            completed = usage_status(usage_dir, today=date(2026, 8, 17))
            self.assertEqual("2026-08-17", completed["last_review_day"])
            self.assertEqual("2026-09-15", completed["next_review_day"])
            self.assertFalse(completed["review_due"])

    def test_improvement_choice_requires_explicit_noninteractive_decision(self) -> None:
        with self.assertRaisesRegex(ValueError, "非交互"):
            resolve_improvement_choice("ask", interactive=False)
        self.assertEqual(
            "enable",
            resolve_improvement_choice("ask", interactive=True, prompt=lambda _: "y"),
        )
        self.assertEqual(
            "decline",
            resolve_improvement_choice("ask", interactive=True, prompt=lambda _: ""),
        )

    def test_init_enable_scans_and_enables_improvement(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            skill_root = self.make_skill(root)
            result = self.run_cli(
                "init",
                "--root",
                str(skill_root),
                "--output",
                str(root / "out"),
                "--usage-dir",
                str(root / "usage"),
                "--improvement",
                "enable",
            )
            self.assertEqual(0, result.returncode)
            self.assertTrue((root / "out" / "registry.json").exists())
            self.assertEqual("enabled", usage_status(root / "usage")["choice"])

    def test_init_decline_scans_without_creating_key(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            result = self.run_cli(
                "init",
                "--root",
                str(self.make_skill(root)),
                "--output",
                str(root / "out"),
                "--usage-dir",
                str(root / "usage"),
                "--improvement",
                "decline",
            )
            self.assertEqual(0, result.returncode)
            self.assertEqual("declined", usage_status(root / "usage")["choice"])
            self.assertFalse((root / "usage" / "local.key").exists())

    def test_noninteractive_init_without_choice_fails_before_scan(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            result = self.run_cli(
                "init",
                "--root",
                str(self.make_skill(root)),
                "--output",
                str(root / "out"),
                "--usage-dir",
                str(root / "usage"),
                check=False,
                input_text="",
            )
            self.assertEqual(2, result.returncode)
            self.assertIn("--improvement", result.stderr)
            self.assertFalse((root / "out").exists())

    def test_init_reuses_declined_choice_without_asking_again(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            usage_dir = root / "usage"
            decline_config(usage_dir)
            result = self.run_cli(
                "init",
                "--root",
                str(self.make_skill(root)),
                "--output",
                str(root / "out"),
                "--usage-dir",
                str(usage_dir),
                check=False,
                input_text="",
            )
            self.assertEqual(0, result.returncode)
            self.assertEqual("declined", usage_status(usage_dir)["choice"])
            self.assertFalse((usage_dir / "local.key").exists())

    def test_query_exposes_unconfigured_improvement_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fixture = Path(__file__).resolve().parents[1] / "references" / "fixtures" / "public-v3-registry.json"
            result = self.run_cli(
                "query",
                "使用 java-code-review 审查代码",
                "--registry",
                str(fixture),
                "--usage-dir",
                str(root / "usage"),
                "--json",
            )
            payload = json.loads(result.stdout)
            self.assertFalse(payload["improvement"]["configured"])
            self.assertEqual("unconfigured", payload["improvement"]["choice"])
            self.assertFalse((root / "usage").exists())

    def test_due_placement_advances_review_cycle(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            usage_dir = root / "usage"
            enable_usage(usage_dir, date(2026, 1, 1))
            fixture = Path(__file__).resolve().parents[1] / "references" / "fixtures" / "public-v3-registry.json"
            result = self.run_cli(
                "placement",
                "plan",
                "--registry",
                str(fixture),
                "--usage-dir",
                str(usage_dir),
                "--json",
            )
            payload = json.loads(result.stdout)
            status = usage_status(usage_dir)
            self.assertFalse(payload["improvement"]["review_due"])
            self.assertEqual(date.today().isoformat(), status["last_review_day"])
            self.assertFalse(status["review_due"])

    @staticmethod
    def make_skill(root: Path) -> Path:
        skill_root = root / "skills"
        path = skill_root / "demo" / "SKILL.md"
        path.parent.mkdir(parents=True)
        path.write_text("---\nname: demo\ndescription: Use when testing init.\n---\n", encoding="utf-8")
        return skill_root

    @staticmethod
    def run_cli(
        *args: str,
        check: bool = True,
        input_text: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        command = [
            sys.executable,
            str(Path(__file__).resolve().parents[1] / "scripts" / "skill_router.py"),
            *args,
        ]
        return subprocess.run(
            command,
            check=check,
            capture_output=True,
            text=True,
            encoding="utf-8",
            input=input_text,
        )


if __name__ == "__main__":
    unittest.main()
