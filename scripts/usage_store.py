"""本地最小化使用反馈、匿名聚合与只读 placement 建议。"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from router_core import SkillRecord
from usage_config import config_status, disable_config, enable_config, load_config
from usage_privacy import (
    atomic_json,
    load_key,
    normalized_project,
    optional_key,
    token,
    usage_lock,
)

EVENTS = ("recommended", "selected", "opened", "corrected")
SCHEMA_VERSION = 1


class UsageDisabledError(ValueError):
    """usage 未启用时拒绝写入。"""


def default_usage_dir() -> Path:
    return Path.home() / ".skill-router" / "usage"


def usage_status(usage_dir: Path, *, today: date | None = None) -> dict[str, object]:
    stats = _load_stats(usage_dir, required=False)
    return {
        "schema_version": SCHEMA_VERSION,
        **config_status(usage_dir, today=today),
        "buckets": len(stats["buckets"]) if stats else 0,
        "corrections": len(stats["corrections"]) if stats else 0,
    }


def enable_usage(usage_dir: Path, enabled_day: date | None = None) -> dict[str, object]:
    enable_config(usage_dir, enabled_day)
    return usage_status(usage_dir, today=enabled_day)


def disable_usage(usage_dir: Path) -> dict[str, object]:
    disable_config(usage_dir)
    return usage_status(usage_dir)


def clear_usage(usage_dir: Path, confirmed: bool) -> dict[str, object]:
    if not confirmed:
        raise ValueError("usage clear 会删除本地统计；请显式传入 --yes")
    with usage_lock(usage_dir):
        for name in ("stats.json", "local.key", "config.json"):
            path = usage_dir / name
            if path.exists():
                path.unlink()
    return usage_status(usage_dir)


def record_event(
    usage_dir: Path,
    event: str,
    skill_id: str,
    *,
    project: Path,
    event_day: date | None = None,
    to_skill_id: str | None = None,
) -> None:
    _validate_event(event, skill_id, to_skill_id)
    with usage_lock(usage_dir):
        config = load_config(usage_dir)
        if not config or not config["enabled"]:
            raise UsageDisabledError("本地使用统计未启用；先运行 usage enable")
        key = load_key(usage_dir)
        day = _day_value(event_day)
        project_id = token(key, "project", normalized_project(project))
        anonymous_skill = token(key, "skill", skill_id)
        stats = _load_stats(usage_dir, required=False) or _empty_stats()
        bucket = _bucket(stats["buckets"], day, project_id=project_id, skill_id=anonymous_skill)
        bucket["counts"][event] += 1
        _record_correction(
            stats,
            event,
            day,
            project_id=project_id,
            from_id=anonymous_skill,
            to_skill_id=to_skill_id,
            key=key,
        )
        _sort_stats(stats)
        atomic_json(_stats_path(usage_dir), stats)


def usage_summary(
    usage_dir: Path,
    records: list[SkillRecord],
    project: Path | None = None,
) -> dict[str, object]:
    status = usage_status(usage_dir)
    key = optional_key(usage_dir)
    stats = _load_stats(usage_dir, required=False) or _empty_stats()
    project_id = token(key, "project", normalized_project(project)) if key and project else None
    skills = []
    for record in records:
        skill_id = record.canonical_id or record.content_hash
        anonymous_id = token(key, "skill", skill_id) if key else ""
        buckets = [item for item in stats["buckets"] if item["skill_id"] == anonymous_id]
        if project_id:
            buckets = [item for item in buckets if item["project_id"] == project_id]
        skills.append({
            "name": record.name,
            "skill_id": skill_id,
            "counts": _sum_counts(buckets),
            "projects": len(_adopted_projects(buckets)),
        })
    return {**status, "skills": skills}


def _record_correction(
    stats: dict[str, object],
    event: str,
    day: str,
    *,
    project_id: str,
    from_id: str,
    to_skill_id: str | None,
    key: bytes,
) -> None:
    if event != "corrected":
        return
    target = token(key, "skill", str(to_skill_id))
    item = _correction(
        stats["corrections"], day, project_id=project_id, from_id=from_id, to_id=target
    )
    item["count"] += 1


def _validate_event(event: str, skill_id: str, to_skill_id: str | None) -> None:
    if event not in EVENTS:
        raise ValueError(f"未知 feedback 事件：{event}")
    if not skill_id.strip():
        raise ValueError("skill ID 不能为空")
    if event == "corrected" and not (to_skill_id and to_skill_id.strip()):
        raise ValueError("corrected 事件必须提供目标 skill ID")
    if event != "corrected" and to_skill_id:
        raise ValueError("只有 corrected 事件可以提供目标 skill ID")


def _load_stats(usage_dir: Path, required: bool) -> dict[str, object] | None:
    payload = _read_json(_stats_path(usage_dir), required)
    if payload is None:
        return None
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("usage stats 仅支持 schema_version=1")
    if not isinstance(payload.get("buckets"), list) or not isinstance(payload.get("corrections"), list):
        raise ValueError("usage stats 格式无效")
    return payload


def _read_json(path: Path, required: bool) -> dict[str, object] | None:
    if not path.exists():
        if required:
            raise ValueError(f"缺少本地 usage 文件：{path.name}")
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"无法读取本地 usage 文件 {path.name}：{exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"本地 usage 文件 {path.name} 必须是 JSON object")
    return payload


def _bucket(
    buckets: list[dict[str, object]], day: str, *, project_id: str, skill_id: str
) -> dict[str, object]:
    for item in buckets:
        if (item["day"], item["project_id"], item["skill_id"]) == (day, project_id, skill_id):
            return item
    item = {"day": day, "project_id": project_id, "skill_id": skill_id, "counts": _zero_counts()}
    buckets.append(item)
    return item


def _correction(
    corrections: list[dict[str, object]],
    day: str,
    *,
    project_id: str,
    from_id: str,
    to_id: str,
) -> dict[str, object]:
    key = (day, project_id, from_id, to_id)
    for item in corrections:
        if (item["day"], item["project_id"], item["from_skill_id"], item["to_skill_id"]) == key:
            return item
    item = {
        "day": day,
        "project_id": project_id,
        "from_skill_id": from_id,
        "to_skill_id": to_id,
        "count": 0,
    }
    corrections.append(item)
    return item


def _sum_counts(buckets: list[dict[str, object]]) -> dict[str, int]:
    result = _zero_counts()
    for item in buckets:
        for event in EVENTS:
            result[event] += int(item["counts"].get(event, 0))
    return result


def _adopted_projects(buckets: list[dict[str, object]]) -> set[str]:
    return {
        str(item["project_id"])
        for item in buckets
        if max(int(item["counts"].get("selected", 0)), int(item["counts"].get("opened", 0))) > 0
    }


def _sort_stats(stats: dict[str, object]) -> None:
    stats["buckets"].sort(key=lambda item: (item["day"], item["project_id"], item["skill_id"]))
    stats["corrections"].sort(key=lambda item: (
        item["day"], item["project_id"], item["from_skill_id"], item["to_skill_id"]
    ))


def _empty_stats() -> dict[str, object]:
    return {"schema_version": SCHEMA_VERSION, "buckets": [], "corrections": []}


def _zero_counts() -> dict[str, int]:
    return {event: 0 for event in EVENTS}


def _day_value(value: date | None) -> str:
    return _today(value).isoformat()


def _today(value: date | None) -> date:
    return value or date.today()


def _stats_path(usage_dir: Path) -> Path:
    return usage_dir / "stats.json"
