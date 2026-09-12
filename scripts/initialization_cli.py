"""首次扫描与本地改善计划知情选择。"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from pathlib import Path

from usage_config import decline_config
from usage_store import default_usage_dir, enable_usage, usage_status


def add_init_arguments(initialize: argparse.ArgumentParser) -> None:
    initialize.add_argument("--usage-dir", type=Path, default=default_usage_dir())
    initialize.add_argument(
        "--improvement",
        choices=("ask", "enable", "decline"),
        default="ask",
        help="交互询问、启用或拒绝本地周期改善计划",
    )
    initialize.set_defaults(json=False)


def init_command(
    args: argparse.Namespace,
    scan_command: Callable[[argparse.Namespace], int],
) -> int:
    try:
        choice = _choice_for_initialization(args)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    result = scan_command(args)
    if result != 0:
        return result
    status = _persist_choice(choice, args.usage_dir)
    print(f"改善计划：{status['choice']}，下次检查：{status['next_review_day'] or '-'}")
    return 0


def resolve_improvement_choice(
    mode: str,
    *,
    interactive: bool | None = None,
    prompt: Callable[[str], str] = input,
) -> str:
    if mode in {"enable", "decline"}:
        return mode
    if mode != "ask":
        raise ValueError(f"未知 improvement 选择：{mode}")
    active_terminal = sys.stdin.isatty() if interactive is None else interactive
    if not active_terminal:
        raise ValueError("非交互初始化必须显式传入 --improvement enable 或 decline")
    answer = prompt(_improvement_prompt()).strip().casefold()
    if answer in {"y", "yes", "是", "开启"}:
        return "enable"
    if answer in {"", "n", "no", "否", "拒绝"}:
        return "decline"
    raise ValueError("无法识别选择；请输入 y/yes 或 n/no")


def _choice_for_initialization(args: argparse.Namespace) -> str | None:
    status = usage_status(args.usage_dir)
    if args.improvement == "ask" and status["configured"]:
        return None
    return resolve_improvement_choice(args.improvement)


def _persist_choice(choice: str | None, usage_dir: Path) -> dict[str, object]:
    if choice is None:
        return usage_status(usage_dir)
    if choice == "enable":
        return enable_usage(usage_dir)
    if choice == "decline":
        decline_config(usage_dir)
        return usage_status(usage_dir)
    raise ValueError(f"未知 improvement 选择：{choice}")


def _improvement_prompt() -> str:
    return (
        "是否开启本地周期改善计划？它只保存 HMAC 标识和按天计数，不保存 Prompt、路径或正文，"
        "不会上传，也不会自动移动 skill；30 天到期后由下一次 router 使用触发分析。[y/N] "
    )
