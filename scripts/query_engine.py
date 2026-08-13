"""本地、确定性且可解释的 skill 查询与路由决策。"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

from router_core import SkillRecord, normalize

DEFAULT_OVERRIDES = Path(__file__).resolve().parents[1] / "config" / "router-overrides.json"
CHINESE_STOP_GRAMS = {
    "一个", "以及", "任务", "使用", "用户", "技能", "需要", "相关", "当前", "进行",
    "文件", "完整", "这个", "那个", "帮我", "代码", "生成", "创建", "处理", "时候",
}


@dataclass(frozen=True)
class RouterSettings:
    minimum_score: int = 4
    high_score: int = 12
    high_margin: int = 4
    ambiguous_margin: int = 1


@dataclass(frozen=True)
class SkillOverride:
    add_scenarios: tuple[str, ...] = ()
    boost_terms: tuple[str, ...] = ()
    negative_terms: tuple[str, ...] = ()


@dataclass(frozen=True)
class RouterOverrides:
    aliases: dict[str, tuple[str, ...]] = field(default_factory=dict)
    settings: RouterSettings = RouterSettings()
    skills: dict[str, SkillOverride] = field(default_factory=dict)


@dataclass(frozen=True)
class QueryCandidate:
    score: int
    record: SkillRecord
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class RoutingDecision:
    status: str
    confidence: str
    primary: QueryCandidate | None
    alternatives: tuple[QueryCandidate, ...]
    score_margin: int | None
    reason: str


@dataclass(frozen=True)
class QueryContext:
    intent: str
    ascii_tokens: set[str]
    chinese_grams: set[str]
    gram_frequency: dict[str, int]
    target_ascii: set[str]
    target_grams: set[str]
    record_count: int
    overrides: RouterOverrides


def load_overrides(path: Path | None = None) -> RouterOverrides:
    config_path = path or DEFAULT_OVERRIDES
    if not config_path.exists():
        return RouterOverrides()
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise ValueError("router overrides 仅支持 schema_version=1")
    aliases = {
        normalize(str(key)): _string_tuple(value, f"aliases.{key}")
        for key, value in payload.get("aliases", {}).items()
    }
    raw_settings = payload.get("settings", {})
    settings = RouterSettings(**{
        key: _non_negative_int(raw_settings.get(key, default), f"settings.{key}")
        for key, default in RouterSettings().__dict__.items()
    })
    skills = {
        normalize(str(name)): SkillOverride(
            add_scenarios=_string_tuple(value.get("add_scenarios", []), f"skills.{name}.add_scenarios"),
            boost_terms=_string_tuple(value.get("boost_terms", []), f"skills.{name}.boost_terms"),
            negative_terms=_string_tuple(value.get("negative_terms", []), f"skills.{name}.negative_terms"),
        )
        for name, value in payload.get("skills", {}).items()
    }
    return RouterOverrides(aliases=aliases, settings=settings, skills=skills)


def merge_overrides(base: RouterOverrides, extra: RouterOverrides) -> RouterOverrides:
    aliases = dict(base.aliases)
    aliases.update(extra.aliases)
    skills = dict(base.skills)
    skills.update(extra.skills)
    return RouterOverrides(aliases=aliases, settings=base.settings, skills=skills)


def query(
    records: list[SkillRecord],
    intent: str,
    limit: int,
    overrides: RouterOverrides | None = None,
) -> list[tuple[int, SkillRecord]]:
    """保留 v0.1 的候选列表接口。"""
    return [(item.score, item.record) for item in rank(records, intent, limit, overrides)]


def decide(
    records: list[SkillRecord],
    intent: str,
    limit: int,
    overrides: RouterOverrides | None = None,
) -> RoutingDecision:
    active = overrides or load_overrides()
    candidates = rank(records, intent, limit, active)
    settings = active.settings
    if not candidates or candidates[0].score < settings.minimum_score:
        alternatives = tuple(candidates[:2])
        return RoutingDecision("no_match", "none", None, alternatives, None, "没有候选达到最低可信分数")
    top = candidates[0]
    second = candidates[1] if len(candidates) > 1 else None
    margin = top.score - second.score if second else top.score
    if second and margin <= settings.ambiguous_margin:
        return RoutingDecision(
            "ambiguous", "low", None, tuple(candidates[:2]), margin, "前两名分数过于接近，需要检查场景或补充约束",
        )
    confidence = "high" if top.score >= settings.high_score and margin >= settings.high_margin else "medium"
    return RoutingDecision("matched", confidence, top, tuple(candidates[1:]), margin, "候选达到路由阈值")


def rank(
    records: list[SkillRecord],
    intent: str,
    limit: int,
    overrides: RouterOverrides | None = None,
) -> list[QueryCandidate]:
    active = overrides or load_overrides()
    context, documents = _build_context(records, intent, active)
    ranked = []
    for record, document in zip(records, documents):
        score, reasons = _score(record, document, context)
        if score > 0:
            ranked.append(QueryCandidate(score, record, tuple(reasons)))
    ranked.sort(key=lambda item: (-item.score, item.record.name, item.record.path))
    return ranked[: max(1, limit)]


def _build_context(
    records: list[SkillRecord], intent: str, overrides: RouterOverrides
) -> tuple[QueryContext, list[str]]:
    intent_lower = normalize(intent).lower()
    expanded = _expand_aliases(intent_lower, overrides.aliases)
    ascii_tokens = set(re.findall(r"[a-z0-9][a-z0-9+.#-]{1,}", expanded))
    chinese_grams = _chinese_grams(intent_lower)
    documents = [_search_document(item, overrides.skills.get(item.name)) for item in records]
    frequency = {gram: sum(gram in document for document in documents) for gram in chinese_grams}
    target_text = _target_artifact_text(intent_lower)
    target_expanded = _expand_aliases(target_text, overrides.aliases)
    context = QueryContext(
        intent=intent_lower,
        ascii_tokens=ascii_tokens,
        chinese_grams=chinese_grams,
        gram_frequency=frequency,
        target_ascii=set(re.findall(r"[a-z0-9][a-z0-9+.#-]{1,}", target_expanded)),
        target_grams=_chinese_grams(target_text),
        record_count=len(records),
        overrides=overrides,
    )
    return context, documents


def _score(record: SkillRecord, text: str, context: QueryContext) -> tuple[int, list[str]]:
    name = record.name.lower()
    exact_name = name in context.intent
    name_hits = sum(token in name for token in context.ascii_tokens)
    text_hits = sum(token in text for token in context.ascii_tokens)
    gram_score = round(sum(
        1 + math.log((context.record_count + 1) / (context.gram_frequency[gram] + 1))
        for gram in context.chinese_grams if gram in text
    ))
    target_hits = sum(token in text for token in context.target_ascii)
    target_grams = sum(gram in text for gram in context.target_grams)
    skill_override = context.overrides.skills.get(record.name, SkillOverride())
    boosts = sum(normalize(term).lower() in context.intent for term in skill_override.boost_terms)
    negatives = sum(normalize(term).lower() in context.intent for term in skill_override.negative_terms)
    score = (12 if exact_name else 0) + 4 * name_hits + 2 * text_hits
    score += gram_score + 6 * target_hits + 4 * target_grams + 8 * boosts - 8 * negatives
    reasons = []
    if exact_name:
        reasons.append("任务直接提到 skill 名称")
    if name_hits or text_hits:
        reasons.append("名称、描述或推荐场景命中任务关键词")
    if gram_score:
        reasons.append("中文任务短语与推荐场景相关")
    if target_hits or target_grams:
        reasons.append("匹配任务要求的最终产物")
    if boosts:
        reasons.append("命中人工增强词")
    if negatives:
        reasons.append("命中人工排除词并降权")
    return score, reasons or ["弱相关文本匹配"]


def _expand_aliases(text: str, aliases: dict[str, tuple[str, ...]]) -> str:
    additions = [alias for phrase, values in aliases.items() if phrase.lower() in text for alias in values]
    return f"{text} {' '.join(additions)}".strip()


def _search_document(item: SkillRecord, override: SkillOverride | None) -> str:
    scenarios = list(item.recommended_use)
    if override:
        scenarios.extend(override.add_scenarios)
    return f"{item.name} {item.description} {' '.join(scenarios)}".lower()


def _chinese_grams(text: str) -> set[str]:
    grams = set()
    for chunk in re.findall(r"[\u4e00-\u9fff]{2,}", text):
        for size in (2, 3, 4):
            grams.update(
                chunk[index : index + size]
                for index in range(len(chunk) - size + 1)
                if chunk[index : index + size] not in CHINESE_STOP_GRAMS
            )
    return grams


def _target_artifact_text(intent: str) -> str:
    marker = re.search(r"(?:做成|转成|转换为|输出为|导出为|生成(?:一份|一个)?|交付(?:一份|一个)?)(.+)$", intent)
    return marker.group(1) if marker else ""


def _string_tuple(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{field_name} 必须是字符串数组")
    return tuple(normalize(item) for item in value if normalize(item))


def _non_negative_int(value: object, field_name: str) -> int:
    if not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} 必须是非负整数")
    return value
