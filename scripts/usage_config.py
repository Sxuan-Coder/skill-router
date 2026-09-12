"""本地 usage consent 与触发式改善周期配置。"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from usage_privacy import atomic_json, ensure_key, usage_lock

SCHEMA_VERSION = 1
DEFAULT_REVIEW_INTERVAL_DAYS = 30
CHOICES = {"enabled", "disabled", "declined"}


def load_config(usage_dir: Path, required: bool = False) -> dict[str, object] | None:
    path = usage_dir / "config.json"
    if not path.exists():
        if required:
            raise ValueError("缺少本地 usage 文件：config.json")
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"无法读取本地 usage 文件 config.json：{exc}") from exc
    return _normalize_config(payload)


def config_status(usage_dir: Path, *, today: date | None = None) -> dict[str, object]:
    config = load_config(usage_dir)
    if config is None:
        return _unconfigured_status()
    current = today or date.today()
    next_day = _optional_date(config["next_review_day"])
    return {
        "configured": True,
        "choice": config["choice"],
        "enabled": config["enabled"],
        "enabled_day": config["enabled_day"],
        "review_interval_days": config["review_interval_days"],
        "last_review_day": config["last_review_day"],
        "next_review_day": config["next_review_day"],
        "review_due": bool(config["enabled"] and next_day and current >= next_day),
    }


def enable_config(
    usage_dir: Path,
    enabled_day: date | None = None,
    *,
    interval_days: int = DEFAULT_REVIEW_INTERVAL_DAYS,
) -> dict[str, object]:
    _validate_interval(interval_days)
    with usage_lock(usage_dir):
        ensure_key(usage_dir)
        existing = load_config(usage_dir)
        current = enabled_day or date.today()
        payload = _enabled_payload(existing, current, interval_days)
        atomic_json(usage_dir / "config.json", payload)
    return config_status(usage_dir, today=enabled_day)


def disable_config(usage_dir: Path) -> dict[str, object]:
    with usage_lock(usage_dir):
        existing = load_config(usage_dir)
        payload = dict(existing or _base_payload())
        payload.update({"enabled": False, "choice": "disabled"})
        atomic_json(usage_dir / "config.json", payload)
    return config_status(usage_dir)


def decline_config(usage_dir: Path) -> dict[str, object]:
    with usage_lock(usage_dir):
        payload = _base_payload()
        payload.update({"enabled": False, "choice": "declined"})
        atomic_json(usage_dir / "config.json", payload)
    return config_status(usage_dir)


def complete_review(usage_dir: Path, reviewed_day: date | None = None) -> dict[str, object]:
    with usage_lock(usage_dir):
        config = load_config(usage_dir, required=True)
        if not config["enabled"]:
            raise ValueError("改善计划未启用，不能完成 review 周期")
        current = reviewed_day or date.today()
        config["last_review_day"] = current.isoformat()
        config["next_review_day"] = _next_review_day(
            current, int(config["review_interval_days"])
        ).isoformat()
        atomic_json(usage_dir / "config.json", config)
    return config_status(usage_dir, today=reviewed_day)


def _normalize_config(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("usage config 仅支持 schema_version=1")
    enabled = payload.get("enabled")
    if not isinstance(enabled, bool):
        raise ValueError("usage config 格式无效")
    choice = payload.get("choice", "enabled" if enabled else "disabled")
    if choice not in CHOICES:
        raise ValueError("usage config choice 无效")
    interval = payload.get("review_interval_days", DEFAULT_REVIEW_INTERVAL_DAYS)
    _validate_interval(interval)
    enabled_day = payload.get("enabled_day")
    last_review = payload.get("last_review_day")
    next_review = payload.get("next_review_day")
    _validate_optional_days(enabled_day, last_review, next_review)
    if enabled and not enabled_day:
        raise ValueError("enabled usage config 缺少 enabled_day")
    if enabled_day and not next_review:
        next_review = _next_review_day(date.fromisoformat(enabled_day), interval).isoformat()
    return {
        "schema_version": SCHEMA_VERSION,
        "enabled": enabled,
        "choice": choice,
        "enabled_day": enabled_day,
        "review_interval_days": interval,
        "last_review_day": last_review,
        "next_review_day": next_review,
    }


def _enabled_payload(
    existing: dict[str, object] | None,
    current: date,
    interval_days: int,
) -> dict[str, object]:
    if existing and existing["enabled"]:
        payload = dict(existing)
        payload["choice"] = "enabled"
        return payload
    payload = dict(existing or _base_payload())
    payload.update({
        "enabled": True,
        "choice": "enabled",
        "enabled_day": current.isoformat(),
        "review_interval_days": interval_days,
        "next_review_day": _next_review_day(current, interval_days).isoformat(),
    })
    return payload


def _base_payload() -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "enabled": False,
        "choice": "disabled",
        "enabled_day": None,
        "review_interval_days": DEFAULT_REVIEW_INTERVAL_DAYS,
        "last_review_day": None,
        "next_review_day": None,
    }


def _unconfigured_status() -> dict[str, object]:
    return {
        "configured": False,
        "choice": "unconfigured",
        "enabled": False,
        "enabled_day": None,
        "review_interval_days": DEFAULT_REVIEW_INTERVAL_DAYS,
        "last_review_day": None,
        "next_review_day": None,
        "review_due": False,
    }


def _validate_interval(value: object) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 3650:
        raise ValueError("review_interval_days 必须是 1 到 3650 的整数")


def _validate_optional_days(*values: object) -> None:
    for value in values:
        if value is not None and not isinstance(value, str):
            raise ValueError("usage config 日期字段无效")
        if value:
            date.fromisoformat(value)


def _optional_date(value: object) -> date | None:
    return date.fromisoformat(str(value)) if value else None


def _next_review_day(start: date, interval_days: int) -> date:
    return start + timedelta(days=interval_days - 1)
