"""用户级 skill 来源定义、只读发现与多来源扫描。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from router_core import SkillRecord, physical_identity
from scan_engine import (
    ScanStats,
    ScanTarget,
    discover_skill_files,
    scan_targets_with_stats,
)


@dataclass(frozen=True)
class SourceSpec:
    id: str
    label: str
    ecosystem: str
    relative_root: str
    priority: int
    excluded_parts: tuple[str, ...] = (".system", ".cache")


@dataclass(frozen=True)
class SourceStatus:
    source_id: str
    label: str
    ecosystem: str
    relative_root: str
    priority: int
    available: bool
    instances: int

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.source_id,
            "label": self.label,
            "ecosystem": self.ecosystem,
            "relative_root": self.relative_root,
            "priority": self.priority,
            "available": self.available,
            "instances": self.instances,
        }

    def public_dict(self) -> dict[str, object]:
        return {
            "id": self.source_id,
            "label": self.label,
            "available": self.available,
            "instances": self.instances,
        }


DEFAULT_SOURCES = (
    SourceSpec("codex-user", "Codex 用户级", "codex", ".codex/skills", 10),
    SourceSpec("agents-user", "Agents 用户级", "agents", ".agents/skills", 20),
    SourceSpec("agent-user", "Agent 用户级", "agent", ".agent/skills", 30),
    SourceSpec("claude-user", "Claude Code 用户级", "claude", ".claude/skills", 40),
)


def select_sources(source_ids: list[str] | None, all_sources: bool) -> list[SourceSpec]:
    by_id = {item.id: item for item in DEFAULT_SOURCES}
    requested = [item.id for item in DEFAULT_SOURCES] if all_sources else (source_ids or ["codex-user"])
    unknown = [source_id for source_id in requested if source_id not in by_id]
    if unknown:
        raise ValueError(f"未知 source ID：{', '.join(unknown)}")
    return [by_id[source_id] for source_id in dict.fromkeys(requested)]


def discover_sources(
    specs: list[SourceSpec] | tuple[SourceSpec, ...],
    home: Path | None = None,
) -> tuple[list[ScanTarget], list[SourceStatus]]:
    base = (home or Path.home()).expanduser().resolve()
    targets: list[ScanTarget] = []
    statuses: list[SourceStatus] = []
    for spec in specs:
        root = base / spec.relative_root
        discovered = _discover_source(spec, root) if root.is_dir() else []
        targets.extend(discovered)
        statuses.append(SourceStatus(
            spec.id,
            spec.label,
            spec.ecosystem,
            spec.relative_root,
            spec.priority,
            root.is_dir(),
            len(discovered),
        ))
    return targets, statuses


def scan_user_sources(
    specs: list[SourceSpec] | tuple[SourceSpec, ...],
    home: Path | None = None,
    previous: list[SkillRecord] | None = None,
    force: bool = False,
) -> tuple[list[SkillRecord], list[dict[str, str]], ScanStats, list[SourceStatus]]:
    targets, statuses = discover_sources(specs, home)
    records, errors, stats = scan_targets_with_stats(targets, previous, force)
    return records, errors, stats, statuses


def _discover_source(spec: SourceSpec, root: Path) -> list[ScanTarget]:
    targets: list[ScanTarget] = []
    seen_physical: set[str] = set()
    excluded = {item.casefold() for item in spec.excluded_parts}
    for path in discover_skill_files(root, excluded):
        relative = path.relative_to(root)
        if path.parent.name.casefold() == "skill-router":
            continue
        try:
            physical_id = physical_identity(path)
        except OSError:
            physical_id = ""
        if physical_id and physical_id in seen_physical:
            continue
        seen_physical.add(physical_id)
        targets.append(ScanTarget(path, spec.id, relative.as_posix(), spec.priority))
    return targets
