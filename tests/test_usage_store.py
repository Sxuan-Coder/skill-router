from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from catalog_store import write_outputs
from placement_engine import placement_plan
from router_core import SkillRecord
from usage_store import (
    UsageDisabledError,
    clear_usage,
    enable_usage,
    record_event,
    usage_status,
    usage_summary,
)


class UsageStoreTests(unittest.TestCase):
    def test_default_status_is_unconfigured_without_creating_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            usage_dir = Path(temp) / "usage"
            status = usage_status(usage_dir)
            self.assertFalse(status["configured"])
            self.assertEqual("unconfigured", status["choice"])
            self.assertFalse(status["enabled"])
            self.assertEqual(0, status["buckets"])
            self.assertFalse(usage_dir.exists())

    def test_enable_and_daily_aggregation_do_not_store_raw_identifiers(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            usage_dir, project = root / "usage", root / "private-project"
            enable_usage(usage_dir, date(2026, 8, 17))
            record_event(
                usage_dir, "opened", "canonical-alpha", project=project, event_day=date(2026, 8, 17)
            )
            record_event(
                usage_dir, "opened", "canonical-alpha", project=project, event_day=date(2026, 8, 17)
            )
            payload = json.loads((usage_dir / "stats.json").read_text(encoding="utf-8"))
            raw = json.dumps(payload, ensure_ascii=False)
            self.assertNotIn("canonical-alpha", raw)
            self.assertNotIn(str(project), raw)
            self.assertEqual(2, payload["buckets"][0]["counts"]["opened"])
            self.assertEqual("2026-08-17", payload["buckets"][0]["day"])

    def test_disabled_store_rejects_explicit_feedback(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(UsageDisabledError):
                record_event(Path(temp) / "usage", "selected", "skill-a", project=Path(temp))

    def test_corrected_uses_anonymous_pair_aggregation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            usage_dir = root / "usage"
            enable_usage(usage_dir, date(2026, 8, 17))
            record_event(
                usage_dir,
                "corrected",
                "skill-from",
                project=root / "project",
                event_day=date(2026, 8, 17),
                to_skill_id="skill-to",
            )
            payload = json.loads((usage_dir / "stats.json").read_text(encoding="utf-8"))
            raw = json.dumps(payload, ensure_ascii=False)
            self.assertNotIn("skill-from", raw)
            self.assertNotIn("skill-to", raw)
            self.assertEqual(1, payload["corrections"][0]["count"])
            self.assertEqual(1, payload["buckets"][0]["counts"]["corrected"])

    def test_summary_maps_anonymous_stats_to_current_registry(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            usage_dir = root / "usage"
            records = [self.record("alpha", "canonical-alpha")]
            enable_usage(usage_dir, date(2026, 8, 17))
            record_event(usage_dir, "selected", "canonical-alpha", project=root / "project")
            summary = usage_summary(usage_dir, records)
            self.assertEqual("alpha", summary["skills"][0]["name"])
            self.assertEqual("canonical-alpha", summary["skills"][0]["skill_id"])
            self.assertEqual(1, summary["skills"][0]["counts"]["selected"])

    def test_placement_is_conservative_and_explainable(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            usage_dir = root / "usage"
            records = [
                self.record("project-skill", "project-id"),
                self.record("global-skill", "global-id"),
                self.record("cold-skill", "cold-id"),
            ]
            enable_usage(usage_dir, date(2026, 7, 1))
            for _ in range(4):
                record_event(usage_dir, "opened", "project-id", project=root / "project-a")
            for project in (root / "project-a", root / "project-b"):
                for _ in range(3):
                    record_event(usage_dir, "opened", "global-id", project=project)
            result = placement_plan(
                usage_dir,
                records,
                root / "project-a",
                today=date(2026, 8, 17),
            )
            by_name = {item["name"]: item for item in result["skills"]}
            self.assertEqual("project", by_name["project-skill"]["recommendation"])
            self.assertEqual("global", by_name["global-skill"]["recommendation"])
            self.assertEqual("router-store", by_name["cold-skill"]["recommendation"])
            self.assertFalse(by_name["project-skill"]["action_required"])
            self.assertTrue(by_name["global-skill"]["evidence"])

    def test_recent_usage_returns_insufficient_data(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            usage_dir = root / "usage"
            enable_usage(usage_dir, date(2026, 8, 17))
            result = placement_plan(
                usage_dir,
                [self.record("alpha", "alpha-id")],
                root,
                today=date(2026, 8, 17),
            )
            self.assertEqual("insufficient_data", result["skills"][0]["recommendation"])

    def test_clear_requires_confirmation_and_restores_unconfigured(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            usage_dir = Path(temp) / "usage"
            enable_usage(usage_dir, date(2026, 8, 17))
            with self.assertRaises(ValueError):
                clear_usage(usage_dir, confirmed=False)
            clear_usage(usage_dir, confirmed=True)
            self.assertFalse(usage_status(usage_dir)["configured"])
            self.assertFalse((usage_dir / "local.key").exists())

    def test_unknown_stats_schema_fails_without_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            usage_dir = Path(temp) / "usage"
            enable_usage(usage_dir, date(2026, 8, 17))
            stats = usage_dir / "stats.json"
            stats.write_text('{"schema_version": 99, "buckets": [], "corrections": []}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "schema_version=1"):
                usage_status(usage_dir)
            self.assertIn('"schema_version": 99', stats.read_text(encoding="utf-8"))

    @staticmethod
    def record(name: str, canonical_id: str) -> SkillRecord:
        return SkillRecord(
            name=name,
            description=f"Use {name} for tests.",
            recommended_use=[f"Use {name} when testing."],
            scenario_source="description",
            category="general",
            source="test",
            path=f"fixtures/{name}/SKILL.md",
            content_hash=canonical_id,
            boundaries=[],
            instance_id=f"instance-{name}",
            canonical_id=canonical_id,
        )


class UsageCliTests(unittest.TestCase):
    def test_query_is_read_only_until_usage_is_enabled_then_records_recommendation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            usage_dir, output = root / "usage", root / "out"
            records = [UsageStoreTests.record("alpha", "canonical-alpha")]
            write_outputs(records, [], output)
            self.run_cli(
                "query",
                "使用 alpha 完成任务",
                "--registry",
                str(output / "registry.json"),
                "--usage-dir",
                str(usage_dir),
                "--project",
                str(root / "project"),
                "--json",
            )
            self.assertFalse(usage_dir.exists())
            self.run_cli("usage", "enable", "--usage-dir", str(usage_dir))
            result = self.run_cli(
                "query",
                "使用 alpha 完成任务",
                "--registry",
                str(output / "registry.json"),
                "--usage-dir",
                str(usage_dir),
                "--project",
                str(root / "project"),
                "--json",
            )
            payload = json.loads(result.stdout)
            self.assertEqual("canonical-alpha", payload["primary"]["skill_id"])
            summary = usage_summary(usage_dir, records)
            self.assertEqual(1, summary["skills"][0]["counts"]["recommended"])
            stats = (usage_dir / "stats.json").read_text(encoding="utf-8")
            self.assertNotIn("使用 alpha 完成任务", stats)

    def test_ambiguous_records_returned_alternatives_and_no_match_records_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            usage_dir, output = root / "usage", root / "out"
            records = [
                UsageStoreTests.record("alpha", "canonical-alpha"),
                UsageStoreTests.record("beta", "canonical-beta"),
            ]
            records = [replace(item, description="Review service configuration.") for item in records]
            write_outputs(records, [], output)
            self.run_cli("usage", "enable", "--usage-dir", str(usage_dir))
            common = (
                "--registry",
                str(output / "registry.json"),
                "--usage-dir",
                str(usage_dir),
                "--project",
                str(root / "project"),
                "--json",
            )
            ambiguous = self.run_cli("query", "review service configuration", *common)
            self.assertEqual("ambiguous", json.loads(ambiguous.stdout)["status"])
            before = usage_summary(usage_dir, records)
            self.assertEqual([1, 1], [item["counts"]["recommended"] for item in before["skills"]])
            overrides = root / "overrides.json"
            overrides.write_text(
                '{"schema_version":1,"settings":{"minimum_score":999}}',
                encoding="utf-8",
            )
            no_match = self.run_cli("query", "unrelated request", *common, "--overrides", str(overrides))
            self.assertEqual("no_match", json.loads(no_match.stdout)["status"])
            after = usage_summary(usage_dir, records)
            self.assertEqual([1, 1], [item["counts"]["recommended"] for item in after["skills"]])

    def test_feedback_cli_records_only_when_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            usage_dir = root / "usage"
            disabled = self.run_cli(
                "feedback",
                "opened",
                "canonical-alpha",
                "--usage-dir",
                str(usage_dir),
                check=False,
            )
            self.assertEqual(2, disabled.returncode)
            self.run_cli("usage", "enable", "--usage-dir", str(usage_dir))
            self.run_cli(
                "feedback",
                "opened",
                "canonical-alpha",
                "--usage-dir",
                str(usage_dir),
                "--project",
                str(root / "project"),
            )
            records = [UsageStoreTests.record("alpha", "canonical-alpha")]
            self.assertEqual(1, usage_summary(usage_dir, records)["skills"][0]["counts"]["opened"])

    @staticmethod
    def run_cli(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
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
        )


if __name__ == "__main__":
    unittest.main()
