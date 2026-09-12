"""基于本地聚合反馈生成不执行操作的 placement 建议。"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from router_core import SkillRecord
from usage_store import usage_summary


def placement_plan(
    usage_dir: Path,
    records: list[SkillRecord],
    project: Path,
    *,
    today: date | None = None,
) -> dict[str, object]:
    total = usage_summary(usage_dir, records)
    current = usage_summary(usage_dir, records, project)
    observed = _observation_days(total.get("enabled_day"), today) if total["enabled"] else 0
    current_by_id = {item["skill_id"]: item for item in current["skills"]}
    skills = [
        _placement_item(item, current_by_id[item["skill_id"]], observed)
        for item in total["skills"]
    ]
    return {
        "schema_version": 1,
        "observation_days": observed,
        "action_required": False,
        "skills": skills,
    }


def _placement_item(
    total: dict[str, object],
    current: dict[str, object],
    observed: int,
) -> dict[str, object]:
    counts = total["counts"]
    current_counts = current["counts"]
    adoption = max(counts["selected"], counts["opened"])
    current_adoption = max(current_counts["selected"], current_counts["opened"])
    projects = total["projects"]
    correction_rate = counts["corrected"] / max(1, counts["recommended"])
    recommendation, confidence = _recommendation(
        observed,
        adoption=adoption,
        current_adoption=current_adoption,
        projects=projects,
        correction_rate=correction_rate,
    )
    return {
        "name": total["name"],
        "skill_id": total["skill_id"],
        "recommendation": recommendation,
        "confidence": confidence,
        "action_required": False,
        "evidence": [
            f"observation_days={observed}",
            f"adoption={adoption}",
            f"current_project_adoption={current_adoption}",
            f"projects={projects}",
            f"correction_rate={correction_rate:.2f}",
        ],
    }


def _recommendation(
    observed: int,
    *,
    adoption: int,
    current_adoption: int,
    projects: int,
    correction_rate: float,
) -> tuple[str, str]:
    if observed < 30:
        return "insufficient_data", "none"
    if correction_rate >= 0.25:
        return "keep", "low"
    if projects >= 2 and adoption >= 6:
        return "global", "high"
    share = current_adoption / max(1, adoption)
    if current_adoption >= 4 and share >= 0.75:
        return "project", "high"
    if adoption <= 1:
        return "router-store", "medium"
    return "keep", "low"


def _observation_days(enabled_day: object, today: date | None) -> int:
    if not enabled_day:
        return 0
    enabled = date.fromisoformat(str(enabled_day))
    return max(0, ((today or date.today()) - enabled).days + 1)
