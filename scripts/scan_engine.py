"""扫描目标构建、增量复用与兼容根目录扫描。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from router_core import SkillRecord, identity_hash, parse_skill, physical_identity, portable_path


@dataclass(frozen=True)
class ScanTarget:
    path: Path
    source_id: str
    relative_path: str
    source_priority: int = 100


@dataclass(frozen=True)
class ScanStats:
    discovered: int
    parsed: int
    reused: int


def scan_roots(roots: list[Path]) -> tuple[list[SkillRecord], list[dict[str, str]]]:
    records, errors, _ = scan_roots_with_stats(roots)
    return records, errors


def scan_roots_with_stats(
    roots: list[Path],
    previous: list[SkillRecord] | None = None,
    force: bool = False,
) -> tuple[list[SkillRecord], list[dict[str, str]], ScanStats]:
    targets: list[ScanTarget] = []
    seen_paths: set[Path] = set()
    for priority, root in enumerate(roots, start=100):
        root = root.expanduser().resolve()
        if not root.exists():
            continue
        source = portable_path(root)
        for path in discover_skill_files(root, {".system"}):
            relative = path.relative_to(root)
            if ".system" in relative.parts or path.parent.name == "skill-router":
                continue
            resolved = path.resolve()
            if resolved in seen_paths:
                continue
            seen_paths.add(resolved)
            targets.append(ScanTarget(path, source, relative.as_posix(), priority))
    return scan_targets_with_stats(targets, previous, force)


def discover_skill_files(root: Path, excluded_parts: set[str]) -> list[Path]:
    excluded = {item.casefold() for item in excluded_parts}
    pending = [root]
    seen_directories: set[str] = set()
    skill_files: list[Path] = []
    while pending:
        directory = pending.pop()
        try:
            physical_id = physical_identity(directory)
            if physical_id in seen_directories:
                continue
            seen_directories.add(physical_id)
            children = sorted(directory.iterdir(), key=lambda item: item.name.casefold())
        except OSError:
            continue
        directories = []
        for child in children:
            if child.name.casefold() in excluded:
                continue
            try:
                if child.is_dir():
                    directories.append(child)
                elif child.name.casefold() == "skill.md" and child.is_file():
                    skill_files.append(child)
            except OSError:
                continue
        pending.extend(reversed(directories))
    return sorted(skill_files, key=lambda item: item.as_posix().casefold())


def scan_targets_with_stats(
    targets: list[ScanTarget],
    previous: list[SkillRecord] | None = None,
    force: bool = False,
) -> tuple[list[SkillRecord], list[dict[str, str]], ScanStats]:
    records: list[SkillRecord] = []
    errors: list[dict[str, str]] = []
    cached = {item.instance_id: item for item in (previous or []) if item.instance_id}
    parsed = reused = 0
    for target in targets:
        try:
            stat = target.path.stat()
            key = identity_hash(f"{target.source_id}\n{target.relative_path}")
            old = cached.get(key)
            if not force and _cache_matches(old, target, stat):
                records.append(old)
                reused += 1
                continue
            records.append(parse_skill(
                target.path,
                target.source_id,
                target.relative_path,
                target.source_priority,
            ))
            parsed += 1
        except (OSError, UnicodeError, ValueError) as exc:
            errors.append({"path": portable_path(target.path), "error": str(exc)})
    ordered = sorted(records, key=lambda item: (item.category, item.name, item.path))
    return ordered, errors, ScanStats(len(targets), parsed, reused)


def _cache_matches(old: SkillRecord | None, target: ScanTarget, stat: object) -> bool:
    return bool(
        old
        and old.size_bytes == stat.st_size
        and old.mtime_ns == stat.st_mtime_ns
        and old.source_priority == target.source_priority
    )
