from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from query_engine import (
    RouterOverrides,
    RouterSettings,
    SkillOverride,
    decide,
    load_overrides,
    merge_overrides,
    query,
)
from check_git_privacy import scan as privacy_scan
from router_core import SkillRecord, classify, infer_scenarios, load_registry, parse_frontmatter, scan_roots, write_outputs


class SkillRouterTests(unittest.TestCase):
    def make_skill(self, root: Path, folder: str, content: str) -> Path:
        path = root / folder / "SKILL.md"
        path.parent.mkdir(parents=True)
        path.write_text(content, encoding="utf-8")
        return path

    def test_folded_frontmatter_is_parsed(self) -> None:
        metadata, body = parse_frontmatter(
            "---\nname: demo\ndescription: >\n  First line.\n  Use when testing.\n---\n# Body\n"
        )
        self.assertEqual("demo", metadata["name"])
        self.assertEqual("First line. Use when testing.", metadata["description"])
        self.assertIn("# Body", body)

    def test_scan_excludes_system_and_extracts_body_scenarios(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.make_skill(
                root,
                "java-review",
                "---\nname: java-review\ndescription: Java code review.\n---\n"
                "# Guide\n## 使用场景\n- 审查 Java 或 Spring Boot 代码\n- 检查并发与异常处理\n",
            )
            self.make_skill(
                root,
                ".system/hidden",
                "---\nname: hidden\ndescription: Must not be scanned.\n---\n",
            )
            records, errors = scan_roots([root])
            self.assertEqual([], errors)
            self.assertEqual(["java-review"], [item.name for item in records])
            self.assertEqual("body", records[0].scenario_source)
            self.assertEqual("development", records[0].category)

    def test_generated_roadbook_only_has_public_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "skills"
            output = Path(temp) / "out"
            self.make_skill(
                root,
                "pdf",
                "---\nname: pdf\ndescription: Read and create PDF files. Use when PDF layout matters.\n---\n",
            )
            records, errors = scan_roots([root])
            write_outputs(records, errors, output)
            route = (output / "routes-documents.md").read_text(encoding="utf-8")
            self.assertIn("## pdf", route)
            self.assertIn("- 描述：", route)
            self.assertIn("- 推荐使用场景：", route)
            self.assertNotIn("content_hash", route)
            self.assertNotIn(str(root), route)
            registry = json.loads((output / "registry.json").read_text(encoding="utf-8"))
            self.assertEqual(1, len(registry["skills"]))

    def test_generation_removes_stale_route_pages(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "skills"
            output = Path(temp) / "out"
            output.mkdir()
            (output / "routes-stale.md").write_text("stale", encoding="utf-8")
            self.make_skill(root, "pdf", "---\nname: pdf\ndescription: Read PDF files.\n---\n")
            records, errors = scan_roots([root])
            write_outputs(records, errors, output)
            self.assertFalse((output / "routes-stale.md").exists())

    def test_roadbook_sanitizes_html_and_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "skills"
            output = Path(temp) / "out"
            fake_token = "sk-" + "1234567890abcdefghijklmnop"
            self.make_skill(
                root,
                "unsafe",
                f"---\nname: unsafe\ndescription: '<b>Use</b> {fake_token} when testing.'\n---\n",
            )
            records, errors = scan_roots([root])
            write_outputs(records, errors, output)
            route_pages = list(output.glob("routes-*.md"))
            self.assertEqual(1, len(route_pages))
            route = route_pages[0].read_text(encoding="utf-8")
            self.assertNotIn("<b>", route)
            self.assertNotIn(fake_token, route)
            self.assertIn("[REDACTED]", route)

    def test_query_prefers_matching_skill(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.make_skill(
                root,
                "java-review",
                "---\nname: java-review\ndescription: 审查 Java Spring Boot 代码。\n---\n",
            )
            self.make_skill(
                root,
                "pdf",
                "---\nname: pdf\ndescription: 创建和检查 PDF 文档。\n---\n",
            )
            records, _ = scan_roots([root])
            results = query(records, "帮我审查 Spring Boot 的 Java 代码", 2)
            self.assertEqual("java-review", results[0][1].name)

    def test_high_confidence_name_rules_avoid_category_false_positives(self) -> None:
        self.assertEqual("documents", classify("pdf", "Use Python to render PDF files.", []))
        self.assertEqual(
            "documents",
            classify("GordenSuperPPTSkill", "把图片逆向还原为可编辑 PPTX。", []),
        )
        self.assertEqual("operations", classify("cli-creator", "Create a CLI from web API docs.", []))
        self.assertEqual("documents", classify("cloudy-tech-diagrams", "Architecture diagrams.", []))

    def test_scenario_extraction_clips_at_word_boundary(self) -> None:
        description = "Use when " + "building reliable command line tools " * 12
        scenarios, source = infer_scenarios(description, "")
        self.assertEqual("description", source)
        self.assertTrue(scenarios[0].endswith("reliable…"))
        self.assertNotIn("reliab…", scenarios[0])

    def test_query_bridges_chinese_intent_to_english_resume_skill(self) -> None:
        records = [
            self.record("repo-to-resume-tailor", "Turn a repository into resume-ready project bullets."),
            self.record("backend-engineering", "Develop backend services and APIs."),
        ]
        results = query(records, "分析完整代码仓库并整理成简历项目经历", 2)
        self.assertEqual("repo-to-resume-tailor", results[0][1].name)

    def test_query_prioritizes_final_artifact_over_input_format(self) -> None:
        records = [
            self.record("pdf", "Read and inspect PDF files."),
            self.record("image-to-pptx", "Convert source pages into an editable PPTX presentation."),
        ]
        results = query(records, "把 PDF 做成可编辑的 PPT", 2)
        self.assertEqual("image-to-pptx", results[0][1].name)

    def test_decision_reports_high_confidence_and_reasons(self) -> None:
        records = [
            self.record("java-review", "Review Java Spring Boot code."),
            self.record("backend", "Build backend APIs."),
        ]
        decision = decide(records, "使用 java-review 审查 Spring Boot", 3)
        self.assertEqual("matched", decision.status)
        self.assertEqual("high", decision.confidence)
        self.assertEqual("java-review", decision.primary.record.name)
        self.assertGreater(decision.score_margin, 0)
        self.assertIn("任务直接提到 skill 名称", decision.primary.reasons)

    def test_decision_rejects_low_score_candidate(self) -> None:
        records = [self.record("generic-tool", "Handle an unrelated generic action.")]
        overrides = RouterOverrides(settings=RouterSettings(minimum_score=20))
        decision = decide(records, "generic action", 3, overrides)
        self.assertEqual("no_match", decision.status)
        self.assertIsNone(decision.primary)

    def test_decision_marks_tied_candidates_ambiguous(self) -> None:
        records = [
            self.record("alpha", "Review service configuration."),
            self.record("beta", "Review service configuration."),
        ]
        decision = decide(records, "review service configuration", 3, RouterOverrides())
        self.assertEqual("ambiguous", decision.status)
        self.assertIsNone(decision.primary)
        self.assertEqual(2, len(decision.alternatives))

    def test_skill_override_can_add_scenario_and_boost_term(self) -> None:
        records = [
            self.record("focused", "Handle focused work."),
            self.record("generic", "Handle generic work."),
        ]
        overrides = RouterOverrides(
            skills={"focused": SkillOverride(add_scenarios=("审计依赖许可证",), boost_terms=("许可证",))}
        )
        decision = decide(records, "请审计依赖许可证", 3, overrides)
        self.assertEqual("focused", decision.primary.record.name)
        self.assertIn("命中人工增强词", decision.primary.reasons)

    def test_load_overrides_validates_schema(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "overrides.json"
            path.write_text('{"schema_version": 2}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "schema_version=1"):
                load_overrides(path)

    def test_local_overrides_extend_generic_aliases(self) -> None:
        base = RouterOverrides(
            aliases={"简历": ("resume",)},
            settings=RouterSettings(minimum_score=9),
        )
        local = RouterOverrides(
            aliases={"内部术语": ("internal",)},
            skills={"focused": SkillOverride(boost_terms=("内部术语",))},
        )
        merged = merge_overrides(base, local)
        self.assertEqual(("resume",), merged.aliases["简历"])
        self.assertEqual(("internal",), merged.aliases["内部术语"])
        self.assertIn("focused", merged.skills)
        self.assertEqual(9, merged.settings.minimum_score)

    def test_cli_json_returns_structured_decision(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "skills"
            output = Path(temp) / "out"
            self.make_skill(
                root,
                "java-review",
                "---\nname: java-review\ndescription: Review Java Spring Boot code.\n---\n",
            )
            records, errors = scan_roots([root])
            write_outputs(records, errors, output)
            command = [
                sys.executable,
                str(Path(__file__).resolve().parents[1] / "scripts" / "skill_router.py"),
                "query",
                "使用 java-review 审查代码",
                "--registry",
                str(output / "registry.json"),
                "--json",
            ]
            result = subprocess.run(command, check=True, capture_output=True, text=True, encoding="utf-8")
            payload = json.loads(result.stdout)
            self.assertEqual("matched", payload["status"])
            self.assertEqual("java-review", payload["primary"]["name"])
            self.assertIn("helpers", payload)
            self.assertTrue(payload["primary"]["reasons"])

    def test_privacy_guard_rejects_generated_registry(self) -> None:
        findings = privacy_scan([Path("references/generated/registry.json")])
        self.assertTrue(any("禁止跟踪" in item for item in findings))

    def test_privacy_guard_rejects_user_absolute_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "sample.txt"
            sensitive = "C:" + "/Users/" + "example-user/skills/catalog.json"
            path.write_text(sensitive, encoding="utf-8")
            findings = privacy_scan([path])
            self.assertTrue(any("Windows 用户绝对路径" in item for item in findings))

    @staticmethod
    def record(name: str, description: str) -> SkillRecord:
        return SkillRecord(
            name=name,
            description=description,
            recommended_use=[description],
            scenario_source="description",
            category="general",
            source="test",
            path=f"/test/{name}/SKILL.md",
            content_hash=name,
            boundaries=[],
        )

    def test_registry_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "skills"
            output = Path(temp) / "out"
            self.make_skill(root, "demo", "---\nname: demo\ndescription: Use when testing routers.\n---\n")
            records, errors = scan_roots([root])
            write_outputs(records, errors, output)
            loaded = load_registry(output / "registry.json")
            self.assertEqual(records, loaded)


if __name__ == "__main__":
    unittest.main()
