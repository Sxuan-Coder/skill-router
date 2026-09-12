"""usage、feedback 与 placement 的 CLI 装配。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from catalog_store import load_registry
from placement_engine import placement_plan
from query_engine import RoutingDecision
from usage_config import complete_review, decline_config
from usage_store import (
    UsageDisabledError,
    clear_usage,
    default_usage_dir,
    disable_usage,
    enable_usage,
    record_event,
    usage_status,
    usage_summary,
)


def add_query_usage_arguments(find: argparse.ArgumentParser) -> None:
    find.add_argument("--usage-dir", type=Path, default=default_usage_dir())
    find.add_argument("--project", type=Path, default=Path.cwd())


def add_usage_parsers(subcommands: argparse._SubParsersAction, default_registry: Path) -> None:
    usage = subcommands.add_parser("usage", help="管理本地最小化使用统计")
    actions = usage.add_subparsers(dest="usage_action", required=True)
    for name, help_text in (
        ("enable", "启用本地聚合统计"),
        ("disable", "停止记录并保留已有统计"),
        ("decline", "明确拒绝本地改善计划"),
        ("status", "查看统计开关与桶数量"),
        ("clear", "清除本地统计与密钥"),
    ):
        action = actions.add_parser(name, help=help_text)
        _add_usage_dir(action)
        action.add_argument("--json", action="store_true")
        if name == "clear":
            action.add_argument("--yes", action="store_true")
    show = actions.add_parser("show", help="查看当前 registry 的可读使用摘要")
    _add_usage_dir(show)
    show.add_argument("--registry", type=Path, default=default_registry)
    show.add_argument("--catalog", type=Path)
    show.add_argument("--project", type=Path)
    show.add_argument("--json", action="store_true")

    feedback = subcommands.add_parser("feedback", help="显式记录 selected/opened/corrected")
    feedback.add_argument("event", choices=("selected", "opened", "corrected"))
    feedback.add_argument("skill_id", help="query 返回的 canonical skill ID")
    feedback.add_argument("--to", dest="to_skill_id", help="corrected 的目标 skill ID")
    feedback.add_argument("--usage-dir", type=Path, default=default_usage_dir())
    feedback.add_argument("--project", type=Path, default=Path.cwd())
    feedback.add_argument("--json", action="store_true")

    placement = subcommands.add_parser("placement", help="生成只读 skill 放置建议")
    placement_actions = placement.add_subparsers(dest="placement_action", required=True)
    plan = placement_actions.add_parser("plan", help="分析 project/global/router-store 建议")
    _add_usage_dir(plan)
    plan.add_argument("--registry", type=Path, default=default_registry)
    plan.add_argument("--catalog", type=Path)
    plan.add_argument("--project", type=Path, default=Path.cwd())
    plan.add_argument("--json", action="store_true")


def usage_command(args: argparse.Namespace) -> int:
    try:
        return _run_usage_command(args)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2


def _run_usage_command(args: argparse.Namespace) -> int:
    if args.usage_action == "show":
        return _show_usage(args)
    actions = {
        "enable": lambda: enable_usage(args.usage_dir),
        "disable": lambda: disable_usage(args.usage_dir),
        "decline": lambda: _decline_and_status(args.usage_dir),
        "clear": lambda: clear_usage(args.usage_dir, args.yes),
        "status": lambda: usage_status(args.usage_dir),
    }
    payload = actions[args.usage_action]()
    _print_status(payload, args.json)
    return 0


def feedback_command(args: argparse.Namespace) -> int:
    try:
        record_event(
            args.usage_dir,
            args.event,
            args.skill_id,
            project=args.project,
            to_skill_id=args.to_skill_id,
        )
    except (UsageDisabledError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    payload = {"recorded": True, "event": args.event}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"已记录本地聚合反馈：{args.event}")
    return 0


def placement_command(args: argparse.Namespace) -> int:
    try:
        records = _load_records(args.registry, args.catalog)
        payload = placement_plan(args.usage_dir, records, args.project)
        if usage_status(args.usage_dir)["review_due"]:
            complete_review(args.usage_dir)
        payload["improvement"] = improvement_payload(args.usage_dir)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for item in payload["skills"]:
            print(f"- {item['name']}：{item['recommendation']}（{item['confidence']}）")
            print(f"  依据：{'；'.join(item['evidence'])}")
    return 0


def record_recommendations(decision: RoutingDecision, args: argparse.Namespace) -> int:
    try:
        if not usage_status(args.usage_dir)["enabled"]:
            return 0
        candidates = _recommended_candidates(decision)
        for candidate in candidates:
            record = candidate.record
            record_event(
                args.usage_dir,
                "recommended",
                record.canonical_id or record.content_hash,
                project=args.project,
            )
        return len(candidates)
    except ValueError as exc:
        print(f"警告：未能记录本地 usage：{exc}", file=sys.stderr)
        return 0


def improvement_payload(usage_dir: Path) -> dict[str, object]:
    status = usage_status(usage_dir)
    fields = (
        "configured",
        "choice",
        "enabled",
        "review_interval_days",
        "next_review_day",
        "review_due",
    )
    return {field: status[field] for field in fields}


def print_improvement_notice(payload: dict[str, object]) -> None:
    if not payload["configured"]:
        print("改善计划尚未配置；首次初始化时请询问用户是否开启本地周期改善。")
    elif payload["review_due"]:
        print("改善计划已到期；可运行 placement plan 生成只读放置建议。")


def _show_usage(args: argparse.Namespace) -> int:
    records = _load_records(args.registry, args.catalog)
    payload = usage_summary(args.usage_dir, records, args.project)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        _print_status(payload, False)
        for item in payload["skills"]:
            counts = item["counts"]
            print(
                f"- {item['name']}：recommended={counts['recommended']}，"
                f"selected={counts['selected']}，opened={counts['opened']}，"
                f"corrected={counts['corrected']}"
            )
    return 0


def _load_records(registry: Path, catalog: Path | None) -> list:
    if not registry.exists():
        raise ValueError("registry 不存在；请先运行 scan")
    return load_registry(registry, catalog)


def _recommended_candidates(decision: RoutingDecision) -> tuple:
    if decision.status == "matched" and decision.primary:
        return (decision.primary,)
    if decision.status == "ambiguous":
        return decision.alternatives
    return ()


def _print_status(payload: dict[str, object], as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    print(f"usage：{payload['choice']}")
    print(f"启用日期：{payload['enabled_day'] or '-'}")
    print(f"下次改善：{payload['next_review_day'] or '-'}，到期：{payload['review_due']}")
    print(f"聚合桶：{payload['buckets']}，纠正对：{payload['corrections']}")


def _add_usage_dir(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--usage-dir", type=Path, default=default_usage_dir())


def _decline_and_status(usage_dir: Path) -> dict[str, object]:
    decline_config(usage_dir)
    return usage_status(usage_dir)

