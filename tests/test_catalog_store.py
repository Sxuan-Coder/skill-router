from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from catalog_store import duplicate_report, load_registry, load_registry_instances, write_outputs
from scan_engine import scan_roots


class CatalogStoreTests(unittest.TestCase):
    def make_skill(self, root: Path, folder: str, name: str, description: str, body: str = "") -> Path:
        path = root / folder / "SKILL.md"
        path.parent.mkdir(parents=True)
        path.write_text(
            f"---\nname: {name}\ndescription: {description}\n---\n{body}",
            encoding="utf-8",
        )
        return path

    def test_catalog_only_contains_public_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root, output = Path(temp) / "skills", Path(temp) / "out"
            self.make_skill(root, "review", "review", "Review service code.")
            records, errors = scan_roots([root])
            write_outputs(records, errors, output)
            catalog = json.loads((output / "catalog.json").read_text(encoding="utf-8"))
            registry = json.loads((output / "registry.json").read_text(encoding="utf-8"))
            self.assertEqual(2, catalog["schema_version"])
            self.assertEqual(3, registry["schema_version"])
            self.assertEqual(
                {"name", "description", "recommended_use"},
                set(catalog["skills"][0]),
            )

    def test_identity_distinguishes_instances_but_groups_renamed_content(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            body = "# Guide\nUse the same workflow.\n"
            self.make_skill(root, "first", "alpha", "Shared description.", body)
            self.make_skill(root, "second", "beta", "Shared description.", body)
            records, _ = scan_roots([root])
            self.assertNotEqual(records[0].instance_id, records[1].instance_id)
            self.assertEqual(records[0].canonical_id, records[1].canonical_id)
            report = duplicate_report(records)
            self.assertEqual(1, len(report["renamed_duplicates"]))

    def test_duplicate_report_detects_exact_and_name_conflicts(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = self.make_skill(root, "one", "same-name", "First behavior.")
            duplicate = root / "copy" / "SKILL.md"
            duplicate.parent.mkdir(parents=True)
            duplicate.write_bytes(first.read_bytes())
            self.make_skill(root, "conflict", "same-name", "Different behavior.")
            records, _ = scan_roots([root])
            report = duplicate_report(records)
            self.assertEqual(1, len(report["exact_duplicates"]))
            self.assertEqual(1, len(report["name_conflicts"]))

    def test_v3_registry_loads_catalog_representatives_and_all_instances(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root, output = Path(temp) / "skills", Path(temp) / "out"
            first = self.make_skill(root, "one", "shared", "Shared behavior.")
            duplicate = root / "copy" / "SKILL.md"
            duplicate.parent.mkdir(parents=True)
            duplicate.write_bytes(first.read_bytes())
            records, errors = scan_roots([root])
            write_outputs(records, errors, output)
            query_records = load_registry(output / "registry.json")
            instances = load_registry_instances(output / "registry.json")
            self.assertEqual(1, len(query_records))
            self.assertEqual(2, len(instances))
            self.assertEqual("shared", query_records[0].name)

    def test_v1_registry_remains_loadable(self) -> None:
        fixture = Path(__file__).resolve().parents[1] / "references" / "fixtures" / "public-registry.json"
        records = load_registry(fixture)
        self.assertEqual(2, len(records))
        self.assertEqual("java-code-review", records[0].name)

    def test_public_v2_fixture_uses_inferred_catalog(self) -> None:
        fixture = Path(__file__).resolve().parents[1] / "references" / "fixtures" / "public-v2-registry.json"
        records = load_registry(fixture)
        self.assertEqual(["java-code-review", "document-export"], [item.name for item in records])
        self.assertEqual("fixtures/java-code-review/SKILL.md", records[0].path)

    def test_public_v3_fixture_uses_preferred_cross_source_instance(self) -> None:
        fixture = Path(__file__).resolve().parents[1] / "references" / "fixtures" / "public-v3-registry.json"
        records = load_registry(fixture)
        instances = load_registry_instances(fixture)
        self.assertEqual(2, len(records))
        self.assertEqual(3, len(instances))
        self.assertEqual("codex-user", records[0].source_id)


if __name__ == "__main__":
    unittest.main()
