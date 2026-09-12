from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from check_git_privacy import scan as privacy_scan


class UsagePrivacyTests(unittest.TestCase):
    def test_privacy_guard_rejects_usage_key_in_custom_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            key = Path(temp) / "custom" / "local.key"
            key.parent.mkdir()
            key.write_text("a" * 64, encoding="ascii")
            findings = privacy_scan([key])
            self.assertTrue(any("usage 密钥" in item for item in findings))

    def test_privacy_guard_rejects_usage_stats_in_custom_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stats = Path(temp) / "custom-stats.json"
            stats.write_text(
                '{"buckets":[],"corrections":[],"project_id":"x","skill_id":"y"}',
                encoding="utf-8",
            )
            findings = privacy_scan([stats])
            self.assertTrue(any("usage 统计" in item for item in findings))


if __name__ == "__main__":
    unittest.main()
