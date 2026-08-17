"""公开 catalog、内部 registry、去重审计与生成路书。"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import asdict, replace
from pathlib import Path
from typing import Callable

from router_core import CATEGORIES, SkillRecord, clip_text, safe_markdown

CATALOG_FIELDS = {"name", "description", "recommended_use"}


def write_outputs(records: list[SkillRecord], errors: list[dict[str, str]], output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    groups = canonical_groups(records)
    catalog = [_public_entry(group[2]) for group in groups]
    registry_groups = [
        {
            "canonical_id": canonical_id,
            "catalog_index": index,
            "preferred_instance_id": preferred.instance_id,
            "instance_ids": [item.instance_id for item in instances],
        }
        for index, (canonical_id, instances, preferred) in enumerate(groups)
    ]
    registry = {
        "schema_version": 2,
        "instances": [asdict(item) for item in records],
        "canonical_groups": registry_groups,
        "errors": errors,
    }
    _atomic_json(output / "catalog.json", {"schema_version": 2, "skills": catalog})
    _atomic_json(output / "registry.json", registry)
    _write_route_pages([group[2] for group in groups], output)


def load_registry(path: Path, catalog_path: Path | None = None) -> list[SkillRecord]:
    payload = _read_json(path)
    if payload.get("schema_version") == 1:
        return [_record(item) for item in payload["skills"]]
    if payload.get("schema_version") != 2:
        raise ValueError("registry 仅支持 schema_version 1 或 2")
    catalog = _read_catalog(catalog_path or infer_catalog_path(path))
    records = [_record(item) for item in payload["instances"]]
    instances = {item.instance_id: item for item in records}
    result = []
    for group in sorted(payload["canonical_groups"], key=lambda item: item["catalog_index"]):
        public = catalog[group["catalog_index"]]
        preferred = instances[group["preferred_instance_id"]]
        result.append(replace(
            preferred,
            name=public["name"],
            description=public["description"],
            recommended_use=list(public["recommended_use"]),
        ))
    return result


def load_registry_instances(path: Path) -> list[SkillRecord]:
    payload = _read_json(path)
    if payload.get("schema_version") == 1:
        return [_record(item) for item in payload["skills"]]
    if payload.get("schema_version") == 2:
        return [_record(item) for item in payload["instances"]]
    raise ValueError("registry 仅支持 schema_version 1 或 2")


def infer_catalog_path(registry_path: Path) -> Path:
    name = registry_path.name
    if "registry" in name:
        return registry_path.with_name(name.replace("registry", "catalog", 1))
    return registry_path.with_name("catalog.json")


def canonical_groups(
    records: list[SkillRecord],
) -> list[tuple[str, list[SkillRecord], SkillRecord]]:
    grouped: dict[str, list[SkillRecord]] = defaultdict(list)
    for item in records:
        grouped[item.canonical_id or item.content_hash].append(item)
    result = []
    for canonical_id, instances in grouped.items():
        ordered = sorted(instances, key=lambda item: (item.source, item.path, item.name))
        result.append((canonical_id, ordered, ordered[0]))
    return sorted(result, key=lambda group: (group[2].category, group[2].name, group[0]))


def duplicate_report(records: list[SkillRecord]) -> dict[str, list[dict[str, object]]]:
    exact = _duplicates(records, lambda item: item.content_hash)
    canonical = _duplicates(records, lambda item: item.canonical_id or item.content_hash)
    renamed = [item for item in canonical if len(set(item["names"])) > 1]
    by_name = _duplicates(records, lambda item: item.name.casefold())
    conflicts = [item for item in by_name if len(set(item["canonical_ids"])) > 1]
    return {
        "exact_duplicates": exact,
        "canonical_duplicates": canonical,
        "renamed_duplicates": renamed,
        "name_conflicts": conflicts,
    }


def _duplicates(
    records: list[SkillRecord],
    key_function: Callable[[SkillRecord], str],
) -> list[dict[str, object]]:
    grouped: dict[str, list[SkillRecord]] = defaultdict(list)
    for item in records:
        grouped[key_function(item)].append(item)
    return [
        {
            "key": key,
            "instance_ids": [item.instance_id for item in items],
            "canonical_ids": [item.canonical_id or item.content_hash for item in items],
            "names": [item.name for item in items],
            "paths": [item.path for item in items],
        }
        for key, items in sorted(grouped.items()) if len(items) > 1
    ]


def _public_entry(item: SkillRecord) -> dict[str, object]:
    return {
        "name": safe_markdown(item.name),
        "description": safe_markdown(item.description),
        "recommended_use": [safe_markdown(value) for value in item.recommended_use],
    }


def _read_catalog(path: Path) -> list[dict[str, object]]:
    payload = _read_json(path)
    if payload.get("schema_version") != 2:
        raise ValueError("catalog 仅支持 schema_version=2")
    for index, item in enumerate(payload.get("skills", [])):
        if set(item) != CATALOG_FIELDS:
            raise ValueError(f"catalog.skills[{index}] 包含非公开字段")
    return payload["skills"]


def _record(payload: dict[str, object]) -> SkillRecord:
    allowed = SkillRecord.__dataclass_fields__
    return SkillRecord(**{key: value for key, value in payload.items() if key in allowed})


def _read_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _atomic_json(path: Path, payload: dict[str, object]) -> None:
    _atomic_write(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _write_route_pages(records: list[SkillRecord], output: Path) -> None:
    grouped = {key: [] for key in CATEGORIES}
    for item in records:
        grouped[item.category].append(item)
    index = ["# Skill 路书", "", "按任务领域只读取一个相关路由页，再读取命中 skill 的完整 `SKILL.md`。", ""]
    expected = set()
    for key, label in CATEGORIES.items():
        if not grouped[key]:
            continue
        path = output / f"routes-{key}.md"
        expected.add(path)
        index.append(f"- [{label}]({path.name})：{len(grouped[key])} 个 skill")
        _write_route_page(label, grouped[key], path)
    _atomic_write(output / "route-index.md", "\n".join(index).rstrip() + "\n")
    for stale in set(output.glob("routes-*.md")) - expected:
        stale.unlink()


def _write_route_page(label: str, records: list[SkillRecord], path: Path) -> None:
    lines = [f"# {label}", "", "此文件由 skill-router 生成，请勿手工修改。", ""]
    for item in records:
        name = safe_markdown(item.name)
        description = clip_text(safe_markdown(item.description), 240)
        scenarios = "；".join(safe_markdown(value) for value in item.recommended_use)
        lines.extend([f"## {name}", "", f"- 描述：{description}", f"- 推荐使用场景：{scenarios}", ""])
    _atomic_write(path, "\n".join(lines).rstrip() + "\n")


def _atomic_write(path: Path, content: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8", newline="\n")
    temporary.replace(path)
