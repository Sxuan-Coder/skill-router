#!/usr/bin/env python3
"""拒绝将本机 skill 清单、绝对路径或常见凭证形态加入 Git。"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

FORBIDDEN_PATHS = (
    ".spec/",
    "references/generated/",
    "evals/cases/",
    "evals/reports/",
    "evals/results/",
    ".skill-router/",
)
SENSITIVE_PATTERNS = {
    "Windows 用户绝对路径": re.compile(r"(?i)\b[A-Z]:[\\/]Users[\\/][^\\/\s]+"),
    "Unix 用户绝对路径": re.compile(r"/(?:home|Users)/[^/\s]+/"),
    "OpenAI 风格密钥": re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),
    "GitHub 风格令牌": re.compile(r"\bgh[opusr]_[A-Za-z0-9]{20,}"),
    "私钥": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
}
USAGE_STATS_MARKERS = {'"buckets"', '"corrections"', '"project_id"', '"skill_id"'}


def git_files(staged: bool) -> list[Path]:
    command = ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"]
    if not staged:
        command = ["git", "ls-files", "--cached", "--others", "--exclude-standard"]
    result = subprocess.run(command, check=True, capture_output=True, text=True, encoding="utf-8")
    return [Path(line) for line in result.stdout.splitlines() if line.strip()]


def scan(paths: list[Path]) -> list[str]:
    findings = []
    for path in paths:
        findings.extend(_scan_path(path))
    return findings


def _scan_path(path: Path) -> list[str]:
    portable = path.as_posix()
    if _is_forbidden_path(portable):
        return [f"禁止跟踪本机生成目录：{portable}"]
    if path.name == "local.key":
        return [f"禁止跟踪本地 usage 密钥：{portable}"]
    text, error = _read_text(path)
    if error:
        return [f"无法读取 {portable}: {error}"]
    if text is None:
        return []
    if _looks_like_usage_stats(path, text):
        return [f"禁止跟踪本地 usage 统计：{portable}"]
    return _sensitive_findings(portable, text)


def _is_forbidden_path(portable: str) -> bool:
    return any(portable == prefix.rstrip("/") or portable.startswith(prefix) for prefix in FORBIDDEN_PATHS)


def _looks_like_usage_stats(path: Path, text: str) -> bool:
    return path.suffix.casefold() == ".json" and all(marker in text for marker in USAGE_STATS_MARKERS)


def _sensitive_findings(portable: str, text: str) -> list[str]:
    return [f"{portable}: 命中{label}" for label, pattern in SENSITIVE_PATTERNS.items() if pattern.search(text)]


def _read_text(path: Path) -> tuple[str | None, OSError | None]:
    try:
        data = path.read_bytes()
    except OSError as exc:
        return None, exc
    if b"\x00" in data:
        return None, None
    return data.decode("utf-8", errors="replace"), None


def main() -> int:
    parser = argparse.ArgumentParser(description="检查即将进入 Git 的本机隐私信息")
    parser.add_argument("--staged", action="store_true", help="只检查暂存区")
    args = parser.parse_args()
    findings = scan(git_files(args.staged))
    if findings:
        print("隐私检查失败：", file=sys.stderr)
        for finding in findings:
            print(f"- {finding}", file=sys.stderr)
        return 1
    print("Git 隐私检查通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
