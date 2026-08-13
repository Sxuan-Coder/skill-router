#!/usr/bin/env python3
"""拒绝将本机 skill 清单、绝对路径或常见凭证形态加入 Git。"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

FORBIDDEN_PATHS = (
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


def git_files(staged: bool) -> list[Path]:
    command = ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"]
    if not staged:
        command = ["git", "ls-files", "--cached", "--others", "--exclude-standard"]
    result = subprocess.run(command, check=True, capture_output=True, text=True, encoding="utf-8")
    return [Path(line) for line in result.stdout.splitlines() if line.strip()]


def scan(paths: list[Path]) -> list[str]:
    findings = []
    for path in paths:
        portable = path.as_posix()
        if any(portable == prefix.rstrip("/") or portable.startswith(prefix) for prefix in FORBIDDEN_PATHS):
            findings.append(f"禁止跟踪本机生成目录：{portable}")
            continue
        try:
            data = path.read_bytes()
        except OSError as exc:
            findings.append(f"无法读取 {portable}: {exc}")
            continue
        if b"\x00" in data:
            continue
        text = data.decode("utf-8", errors="replace")
        for label, pattern in SENSITIVE_PATTERNS.items():
            if pattern.search(text):
                findings.append(f"{portable}: 命中{label}")
    return findings


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
