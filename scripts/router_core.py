"""Skill Router 的解析、分类、生成和查询核心。"""

from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass
from pathlib import Path

MAX_SKILL_BYTES = 1_000_000
SCENARIO_HEADINGS = re.compile(
    r"(?:何时使用|使用场景|适用场景|适用范围|when to use|use cases?|triggers?)",
    re.IGNORECASE,
)
BOUNDARY_HEADINGS = re.compile(
    r"(?:不要使用|何时不使用|边界|限制|when not to use|avoid|not for)", re.IGNORECASE
)
USE_MARKERS = re.compile(
    r"(?:use when|use for|使用场景[:：]?|适用于|用于|当用户|当你|若用户|触发(?:词|场景)?[:：]?)",
    re.IGNORECASE,
)

CATEGORIES = {
    "development": "开发与代码",
    "frontend": "前端与界面",
    "documents": "文档与演示",
    "research": "调研与内容",
    "operations": "环境与工程流程",
    "data": "数据与表格",
    "reverse": "逆向与安全",
    "general": "其他能力",
}

CATEGORY_SIGNALS = {
    "reverse": ("reverse", "逆向", "apk", "ida", "radare", "frida", "binary"),
    "frontend": ("frontend", "ui", "ux", "vue", "react", "css", "页面", "界面", "设计"),
    "documents": ("pdf", "docx", "ppt", "slide", "document", "markdown", "文档", "演示", "周报"),
    "data": ("spreadsheet", "excel", "sheet", "表格", "数据分析"),
    "research": ("research", "search", "文章", "调研", "搜索", "写作", "小说"),
    "operations": ("windows", "cli", "commit", "agent", "workflow", "环境", "编排", "git"),
    "development": ("code", "backend", "java", "python", "golang", "test", "review", "开发", "代码", "测试"),
}

NAME_CATEGORY_RULES = (
    ("reverse", re.compile(r"(?:^|[-_])(apk-reverse|ida-reverse|radare2|reverse-engineering)(?:$|[-_])", re.I)),
    ("documents", re.compile(r"(?:pdf|docx|ppt|slide|presentation|markitdown|artifact|diagram|weekly-report)", re.I)),
    ("frontend", re.compile(r"(?:app-ui|ui-design|frontend|vue|uniapp|web-design)", re.I)),
    ("operations", re.compile(r"(?:windows-dev-host|cli-creator|git-commit|agent-orchestration|session-control|subagent|daily-summary)", re.I)),
    ("research", re.compile(r"(?:research|article-writing|humanizer|recallloom|resume)", re.I)),
    ("development", re.compile(r"(?:backend|go-dev|java-review|code-simplifier|test|code-review)", re.I)),
)

TOKEN_PATTERN = re.compile(
    r"(?i)(?:sk-[a-z0-9_-]{16,}|gh[opusr]_[a-z0-9]{20,}|bearer\s+[a-z0-9._~-]{16,})"
)


@dataclass(frozen=True)
class SkillRecord:
    name: str
    description: str
    recommended_use: list[str]
    scenario_source: str
    category: str
    source: str
    path: str
    content_hash: str
    boundaries: list[str]
    instance_id: str = ""
    canonical_id: str = ""
    size_bytes: int = 0
    mtime_ns: int = 0


@dataclass(frozen=True)
class ScanStats:
    discovered: int
    parsed: int
    reused: int


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def clip_text(text: str, limit: int) -> str:
    value = normalize(text)
    if len(value) <= limit:
        return value
    clipped = value[: limit + 1]
    if re.search(r"[A-Za-z0-9]$", clipped):
        boundary = max(clipped.rfind(" "), clipped.rfind(","), clipped.rfind("，"))
        if boundary >= limit // 2:
            clipped = clipped[:boundary]
    return clipped.rstrip(" ,，;；:-") + "…"


def safe_markdown(text: str) -> str:
    value = re.sub(r"<!--[\s\S]*?-->", "", text)
    value = re.sub(r"<[^>]+>", "", value)
    value = TOKEN_PATTERN.sub("[REDACTED]", value)
    value = html.unescape(value).replace("`", "\\`")
    return normalize(value)


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    match = re.match(r"^---\s*\r?\n(.*?)\r?\n---\s*(?:\r?\n|$)", text, re.DOTALL)
    if not match:
        raise ValueError("缺少 YAML frontmatter")
    block = match.group(1)
    metadata: dict[str, str] = {}
    lines = block.splitlines()
    index = 0
    while index < len(lines):
        item = re.match(r"^([A-Za-z][\w-]*):(?:\s*(.*))?$", lines[index])
        if not item:
            index += 1
            continue
        key, value = item.group(1), (item.group(2) or "").strip()
        if value in {">", "|-", "|", ">-"}:
            index += 1
            parts: list[str] = []
            while index < len(lines) and (not lines[index].strip() or lines[index][0].isspace()):
                parts.append(lines[index].strip())
                index += 1
            metadata[key] = normalize(" ".join(parts))
            continue
        metadata[key] = value.strip("\"'")
        index += 1
    return metadata, text[match.end() :]


def section_items(body: str, heading_pattern: re.Pattern[str]) -> list[str]:
    items: list[str] = []
    active = False
    in_code = False
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        heading = re.match(r"^#{1,6}\s+(.+)$", stripped)
        if heading:
            active = bool(heading_pattern.search(heading.group(1)))
            continue
        if not active:
            continue
        bullet = re.match(r"^(?:[-*+]\s+|\d+[.)]\s+)(.+)$", stripped)
        if bullet:
            value = normalize(re.sub(r"[`*_]", "", bullet.group(1)))
            if 4 <= len(value) <= 220:
                items.append(value)
        if len(items) >= 5:
            break
    return items


def infer_scenarios(description: str, body: str) -> tuple[list[str], str]:
    explicit = section_items(body, SCENARIO_HEADINGS)
    if explicit:
        return explicit[:3], "body"
    marker = USE_MARKERS.search(description)
    if marker:
        tail = description[marker.start() :]
        parts = [normalize(x) for x in re.split(r"[；;。]|\(\d+\)|\d+[.)]", tail)]
        usable = [clip_text(x, 180) for x in parts if len(x) >= 5]
        if usable:
            return usable[:3], "description"
    summary = normalize(description)
    if summary:
        return [f"任务需要该能力时使用：{clip_text(summary, 150)}"], "inferred"
    return ["缺少明确使用场景，使用前需人工检查原始 SKILL.md。"], "missing"


def classify(name: str, description: str, scenarios: list[str]) -> str:
    for category, pattern in NAME_CATEGORY_RULES:
        if pattern.search(name):
            return category
    haystack = f"{name} {description} {' '.join(scenarios)}".lower()
    scores = {
        category: sum(1 for signal in signals if signal in haystack)
        for category, signals in CATEGORY_SIGNALS.items()
    }
    best = max(scores, key=scores.get)
    return best if scores[best] else "general"


def portable_path(path: Path) -> str:
    home = Path.home().resolve()
    resolved = path.resolve()
    try:
        return "~/" + resolved.relative_to(home).as_posix()
    except ValueError:
        return resolved.as_posix()


def parse_skill(path: Path, source: str) -> SkillRecord:
    stat = path.stat()
    if stat.st_size > MAX_SKILL_BYTES:
        raise ValueError(f"文件超过 {MAX_SKILL_BYTES} 字节限制")
    raw = path.read_text(encoding="utf-8-sig")
    metadata, body = parse_frontmatter(raw)
    name = normalize(metadata.get("name", ""))
    description = normalize(metadata.get("description", ""))
    if not name or not description:
        raise ValueError("frontmatter 缺少 name 或 description")
    scenarios, scenario_source = infer_scenarios(description, body)
    boundaries = section_items(body, BOUNDARY_HEADINGS)[:3]
    portable = portable_path(path)
    semantic = f"{normalize(description).casefold()}\n{normalize(body)}"
    return SkillRecord(
        name=name,
        description=description,
        recommended_use=scenarios,
        scenario_source=scenario_source,
        category=classify(name, description, scenarios),
        source=source,
        path=portable,
        content_hash=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        boundaries=boundaries,
        instance_id=identity_hash(f"{source}\n{portable}"),
        canonical_id=identity_hash(semantic),
        size_bytes=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
    )


def scan_roots(roots: list[Path]) -> tuple[list[SkillRecord], list[dict[str, str]]]:
    records, errors, _ = scan_roots_with_stats(roots)
    return records, errors


def scan_roots_with_stats(
    roots: list[Path],
    previous: list[SkillRecord] | None = None,
    force: bool = False,
) -> tuple[list[SkillRecord], list[dict[str, str]], ScanStats]:
    records: list[SkillRecord] = []
    errors: list[dict[str, str]] = []
    seen_paths: set[Path] = set()
    cached = {item.instance_id: item for item in (previous or []) if item.instance_id}
    discovered = parsed = reused = 0
    for root in roots:
        root = root.expanduser().resolve()
        if not root.exists():
            continue
        source = portable_path(root)
        for path in sorted(root.rglob("SKILL.md")):
            relative = path.relative_to(root)
            if ".system" in relative.parts or path.parent.name == "skill-router":
                continue
            resolved = path.resolve()
            if resolved in seen_paths:
                continue
            seen_paths.add(resolved)
            discovered += 1
            try:
                stat = path.stat()
                key = identity_hash(f"{source}\n{portable_path(path)}")
                old = cached.get(key)
                if not force and old and old.size_bytes == stat.st_size and old.mtime_ns == stat.st_mtime_ns:
                    records.append(old)
                    reused += 1
                else:
                    records.append(parse_skill(path, source))
                    parsed += 1
            except (OSError, UnicodeError, ValueError) as exc:
                errors.append({"path": portable_path(path), "error": str(exc)})
    ordered = sorted(records, key=lambda item: (item.category, item.name, item.path))
    return ordered, errors, ScanStats(discovered, parsed, reused)


def identity_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
