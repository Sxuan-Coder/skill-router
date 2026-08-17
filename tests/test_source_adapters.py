from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from catalog_store import duplicate_report, load_registry, write_outputs
from source_adapters import discover_sources, scan_user_sources, select_sources


class SourceAdapterTests(unittest.TestCase):
    def make_skill(self, home: Path, relative: str, name: str = "shared") -> Path:
        path = home / relative / "SKILL.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"---\nname: {name}\ndescription: Use when reviewing shared service code.\n---\n"
            "# Guide\nUse the same workflow.\n",
            encoding="utf-8",
        )
        return path

    def test_default_selection_and_unknown_source(self) -> None:
        selected = select_sources(None, all_sources=False)
        self.assertEqual(["codex-user"], [item.id for item in selected])
        with self.assertRaisesRegex(ValueError, "unknown-source"):
            select_sources(["unknown-source"], all_sources=False)

    def test_missing_source_is_unavailable_not_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            specs = select_sources(["agent-user"], all_sources=False)
            targets, statuses = discover_sources(specs, Path(temp))
            self.assertEqual([], targets)
            self.assertFalse(statuses[0].available)
            self.assertEqual(0, statuses[0].instances)

    def test_directory_link_cycle_is_cut_by_physical_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            root = home / ".codex/skills"
            self.make_skill(home, ".codex/skills/only")
            loop = root / "loop"
            try:
                loop.symlink_to(root, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"当前环境不能创建目录链接：{exc}")
            specs = select_sources(["codex-user"], all_sources=False)
            targets, statuses = discover_sources(specs, home)
            self.assertEqual(1, len(targets))
            self.assertEqual(1, statuses[0].instances)

    def test_cross_source_locators_are_retained_and_system_is_excluded(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            first = self.make_skill(home, ".codex/skills/shared")
            second = home / ".agents/skills/shared/SKILL.md"
            second.parent.mkdir(parents=True)
            os.link(first, second)
            self.make_skill(home, ".codex/skills/.system/hidden", "hidden")
            self.make_skill(home, ".codex/skills/.cache/cached", "cached")
            specs = select_sources(["codex-user", "agents-user"], all_sources=False)
            records, errors, stats, statuses = scan_user_sources(specs, home)
            self.assertEqual([], errors)
            self.assertEqual(2, stats.discovered)
            self.assertEqual([1, 1], [item.instances for item in statuses])
            self.assertNotEqual(records[0].instance_id, records[1].instance_id)
            self.assertEqual(records[0].canonical_id, records[1].canonical_id)
            self.assertEqual(records[0].physical_id, records[1].physical_id)
            self.assertEqual({"codex-user", "agents-user"}, {item.source_id for item in records})

    def test_registry_v3_prefers_lower_source_priority(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home, output = Path(temp), Path(temp) / "out"
            first = self.make_skill(home, ".codex/skills/shared")
            second = home / ".agents/skills/shared/SKILL.md"
            second.parent.mkdir(parents=True)
            second.write_bytes(first.read_bytes())
            specs = select_sources(["agents-user", "codex-user"], all_sources=False)
            records, errors, _, statuses = scan_user_sources(specs, home)
            write_outputs(records, errors, output, [item.to_dict() for item in statuses])
            registry = json.loads((output / "registry.json").read_text(encoding="utf-8"))
            loaded = load_registry(output / "registry.json")
            self.assertEqual(3, registry["schema_version"])
            self.assertEqual(2, len(registry["sources"]))
            self.assertEqual("codex-user", loaded[0].source_id)
            self.assertIn("source_priority", registry["canonical_groups"][0]["preferred_reason"])

    def test_physical_duplicate_report_is_separate_from_exact_content(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            first = self.make_skill(home, ".codex/skills/shared")
            second = home / ".agents/skills/shared/SKILL.md"
            second.parent.mkdir(parents=True)
            os.link(first, second)
            specs = select_sources(["codex-user", "agents-user"], all_sources=False)
            records, _, _, _ = scan_user_sources(specs, home)
            report = duplicate_report(records)
            self.assertEqual(1, len(report["exact_duplicates"]))
            self.assertEqual(1, len(report["physical_duplicates"]))

    def test_sources_cli_reports_availability_without_home_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            self.make_skill(home, ".codex/skills/only")
            result = self.run_cli("sources", "--home", str(home), "--json")
            payload = json.loads(result.stdout)
            by_id = {item["id"]: item for item in payload["sources"]}
            self.assertTrue(by_id["codex-user"]["available"])
            self.assertFalse(by_id["agent-user"]["available"])
            self.assertNotIn(str(home), result.stdout)

    def test_scan_cli_all_sources_writes_two_instances_one_skill(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home, output = Path(temp), Path(temp) / "out"
            first = self.make_skill(home, ".codex/skills/shared")
            second = home / ".agents/skills/shared/SKILL.md"
            second.parent.mkdir(parents=True)
            second.write_bytes(first.read_bytes())
            result = self.run_cli(
                "scan",
                "--home",
                str(home),
                "--all-user-sources",
                "--output",
                str(output),
                "--json",
            )
            payload = json.loads(result.stdout)
            registry = json.loads((output / "registry.json").read_text(encoding="utf-8"))
            self.assertEqual(2, payload["instances"])
            self.assertEqual(1, payload["skills"])
            self.assertEqual(3, registry["schema_version"])

    def test_scan_cli_defaults_to_codex_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            home, output = Path(temp), Path(temp) / "out"
            self.make_skill(home, ".codex/skills/codex-only", "codex-only")
            self.make_skill(home, ".agents/skills/agents-only", "agents-only")
            result = self.run_cli(
                "scan",
                "--home",
                str(home),
                "--output",
                str(output),
                "--json",
            )
            payload = json.loads(result.stdout)
            self.assertEqual(1, payload["instances"])
            self.assertEqual(["codex-user"], [item["id"] for item in payload["sources"]])

    @staticmethod
    def run_cli(*arguments: str) -> subprocess.CompletedProcess[str]:
        command = [
            sys.executable,
            str(Path(__file__).resolve().parents[1] / "scripts" / "skill_router.py"),
            *arguments,
        ]
        return subprocess.run(command, check=True, capture_output=True, text=True, encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
