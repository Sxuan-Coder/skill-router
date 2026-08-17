#!/usr/bin/env python3
"""构建、查询和审计用户级 skill 路书。"""

from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

from query_engine import QueryCandidate, RoutingDecision, decide, load_overrides, merge_overrides
from catalog_store import duplicate_report, load_registry, load_registry_instances, write_outputs
from router_core import CATEGORIES, SkillRecord, safe_markdown
from scan_engine import ScanStats, scan_roots_with_stats
from source_adapters import (
    DEFAULT_SOURCES,
    SourceStatus,
    discover_sources,
    scan_user_sources,
    select_sources,
)

SKILL_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = SKILL_ROOT / "references" / "generated"
DEFAULT_REGISTRY = DEFAULT_OUTPUT / "registry.json"


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="扫描用户级 skills 并生成可检索的分层路书")
    subcommands = root.add_subparsers(dest="command", required=True)

    scan = subcommands.add_parser("scan", help="扫描并重建路书")
    selection = scan.add_mutually_exclusive_group()
    selection.add_argument("--root", action="append", type=Path, help="自定义扫描根；可重复")
    selection.add_argument("--source", action="append", help="用户级 source ID；可重复")
    selection.add_argument("--all-user-sources", action="store_true", help="扫描全部用户级来源")
    scan.add_argument("--home", type=Path, default=Path.home(), help="用户来源相对目录的解析基准")
    scan.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="生成目录")
    scan.add_argument("--json", action="store_true", help="输出 JSON 摘要")
    scan.add_argument("--full", action="store_true", help="忽略旧 registry 并强制重新解析")

    find = subcommands.add_parser("query", help="按用户任务查询候选 skill")
    find.add_argument("intent", help="完整用户任务")
    find.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    find.add_argument("--catalog", type=Path, help="schema v2 公开 catalog；默认按 registry 名称推断")
    find.add_argument("--overrides", type=Path, help="通用或本机私有 overrides JSON")
    find.add_argument("--limit", type=int, default=5)
    find.add_argument("--json", action="store_true")

    audit = subcommands.add_parser("audit", help="审计重复、场景和描述质量")
    audit.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    audit.add_argument("--json", action="store_true")

    sources = subcommands.add_parser("sources", help="查看用户级来源可用性")
    sources.add_argument("--home", type=Path, default=Path.home(), help="用户来源相对目录的解析基准")
    sources.add_argument("--json", action="store_true")
    return root


def scan_command(args: argparse.Namespace) -> int:
    previous = []
    if not args.full and (args.output / "registry.json").exists():
        previous = load_registry_instances(args.output / "registry.json")
    try:
        records, errors, stats, statuses = _scan_records(args, previous)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    write_outputs(records, errors, args.output, [item.to_dict() for item in statuses])
    summary = _scan_summary(args, records, errors, stats, statuses)
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(f"已索引 {len(records)} 个实例，解析 {stats.parsed}，复用 {stats.reused}，错误 {len(errors)}。")
        print(f"输出目录：{args.output.resolve()}")
    return 0 if records else 2


def _scan_records(
    args: argparse.Namespace,
    previous: list[SkillRecord],
) -> tuple[list[SkillRecord], list[dict[str, str]], ScanStats, list[SourceStatus]]:
    if args.root:
        records, errors, stats = scan_roots_with_stats(args.root, previous, args.full)
        return records, errors, stats, []
    specs = select_sources(args.source, args.all_user_sources)
    return scan_user_sources(specs, args.home, previous, args.full)


def _scan_summary(
    args: argparse.Namespace,
    records: list[SkillRecord],
    errors: list[dict[str, str]],
    stats: ScanStats,
    statuses: list[SourceStatus],
) -> dict[str, object]:
    categories = collections.Counter(item.category for item in records)
    summary = {
        "instances": len(records),
        "skills": len(load_registry(args.output / "registry.json")),
        "errors": len(errors),
        "parsed": stats.parsed,
        "reused": stats.reused,
        "categories": {CATEGORIES[key]: categories[key] for key in CATEGORIES if categories[key]},
        "output": str(args.output.resolve()),
    }
    if args.root:
        summary["roots"] = [str(path.expanduser()) for path in args.root]
    else:
        summary["sources"] = [item.public_dict() for item in statuses]
    return summary


def query_command(args: argparse.Namespace) -> int:
    if not args.registry.exists():
        print(f"registry 不存在：{args.registry}；请先运行 scan。", file=sys.stderr)
        return 2
    overrides = load_overrides()
    if args.overrides:
        overrides = merge_overrides(overrides, load_overrides(args.overrides))
    records = load_registry(args.registry, args.catalog)
    decision = decide(records, args.intent, max(1, args.limit), overrides)
    payload = decision_payload(decision)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif decision.status == "no_match":
        print("没有达到最低可信分数的候选；请正常推理或补充一个关键约束。")
    elif decision.status == "ambiguous":
        print(f"路由状态：ambiguous（confidence={decision.confidence}，margin={decision.score_margin}）")
        for item in decision.alternatives:
            print_candidate(item)
    else:
        print(f"路由状态：matched（confidence={decision.confidence}，margin={decision.score_margin}）")
        print_candidate(decision.primary)
        if decision.alternatives:
            print("备选：")
            for item in decision.alternatives:
                print_candidate(item)
    return 0


def candidate_payload(candidate: QueryCandidate | None) -> dict[str, object] | None:
    if candidate is None:
        return None
    item = candidate.record
    return {
        "score": candidate.score,
        "name": item.name,
        "description": safe_markdown(item.description),
        "recommended_use": [safe_markdown(value) for value in item.recommended_use],
        "category": CATEGORIES[item.category],
        "skill_md": item.path,
        "reasons": list(candidate.reasons),
    }


def decision_payload(decision: RoutingDecision) -> dict[str, object]:
    return {
        "schema_version": 1,
        "status": decision.status,
        "confidence": decision.confidence,
        "score_margin": decision.score_margin,
        "reason": decision.reason,
        "primary": candidate_payload(decision.primary),
        "helpers": [],
        "alternatives": [candidate_payload(item) for item in decision.alternatives],
    }


def print_candidate(candidate: QueryCandidate | None) -> None:
    if candidate is None:
        return
    item = candidate.record
    print(f"- {item.name}（score={candidate.score}，{CATEGORIES[item.category]}）")
    print(f"  原因：{'；'.join(candidate.reasons)}")
    print(f"  场景：{'；'.join(safe_markdown(value) for value in item.recommended_use)}")
    print(f"  读取：{item.path}")


def audit_command(args: argparse.Namespace) -> int:
    if not args.registry.exists():
        print(f"registry 不存在：{args.registry}；请先运行 scan。", file=sys.stderr)
        return 2
    records = load_registry_instances(args.registry)
    names: dict[str, list[str]] = collections.defaultdict(list)
    for item in records:
        names[item.name].append(item.path)
    report = {
        "skills": len(records),
        "duplicate_names": {name: paths for name, paths in names.items() if len(paths) > 1},
        "inferred_scenarios": [item.name for item in records if item.scenario_source == "inferred"],
        "missing_scenarios": [item.name for item in records if item.scenario_source == "missing"],
        "long_descriptions": [item.name for item in records if len(item.description) > 500],
        **duplicate_report(records),
    }
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"skills：{report['skills']}")
        print(f"同名组：{len(report['duplicate_names'])}")
        print(f"推断场景：{len(report['inferred_scenarios'])}")
        print(f"缺失场景：{len(report['missing_scenarios'])}")
        print(f"超长描述：{len(report['long_descriptions'])}")
        print(f"精确重复组：{len(report['exact_duplicates'])}")
        print(f"改名重复组：{len(report['renamed_duplicates'])}")
        print(f"同名冲突组：{len(report['name_conflicts'])}")
        print(f"同一物理文件组：{len(report['physical_duplicates'])}")
    return 1 if report["missing_scenarios"] else 0


def sources_command(args: argparse.Namespace) -> int:
    _, statuses = discover_sources(DEFAULT_SOURCES, args.home)
    payload = {"schema_version": 1, "sources": [item.public_dict() for item in statuses]}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for item in statuses:
            state = "available" if item.available else "unavailable"
            print(f"- {item.source_id}：{state}，{item.instances} 个实例")
    return 0


def main() -> int:
    configure_utf8_output()
    args = parser().parse_args()
    if args.command == "scan":
        return scan_command(args)
    if args.command == "query":
        return query_command(args)
    if args.command == "sources":
        return sources_command(args)
    return audit_command(args)


def configure_utf8_output() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
