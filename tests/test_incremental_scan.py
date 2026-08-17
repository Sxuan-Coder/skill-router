from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from scan_engine import scan_roots_with_stats


class IncrementalScanTests(unittest.TestCase):
    def make_skill(self, root: Path) -> None:
        path = root / "demo" / "SKILL.md"
        path.parent.mkdir(parents=True)
        path.write_text(
            "---\nname: demo\ndescription: Use when testing incremental scans.\n---\n",
            encoding="utf-8",
        )

    def test_unchanged_skill_is_reused(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.make_skill(root)
            first, errors, first_stats = scan_roots_with_stats([root])
            second, second_errors, second_stats = scan_roots_with_stats([root], first)
            self.assertEqual([], errors)
            self.assertEqual([], second_errors)
            self.assertEqual(1, first_stats.parsed)
            self.assertEqual(0, first_stats.reused)
            self.assertEqual(0, second_stats.parsed)
            self.assertEqual(1, second_stats.reused)
            self.assertEqual(first, second)

    def test_force_scan_does_not_reuse(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.make_skill(root)
            first, _, _ = scan_roots_with_stats([root])
            _, _, stats = scan_roots_with_stats([root], first, force=True)
            self.assertEqual(1, stats.parsed)
            self.assertEqual(0, stats.reused)


if __name__ == "__main__":
    unittest.main()
