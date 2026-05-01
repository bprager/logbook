from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from logbook.markdown import atomic_write_text


DAILY_LOG_RE = re.compile(r"^(?P<date>\d{4}-\d{2}-\d{2})-[A-Za-z]+-Log\.md$")
DATE_PREFIX_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\s+")
WIKI_LINK_RE = re.compile(r"!?\[\[[^\]\n]+?\]\]")
MARKDOWN_LINK_RE = re.compile(r"!?\[[^\]\n]*\]\([^)]+\)")


@dataclass(frozen=True)
class EntityReference:
    kind: str
    note_path: Path
    aliases: tuple[str, ...]

    @property
    def vault_relative_stem(self) -> str:
        return self.note_path.with_suffix("").as_posix()


@dataclass(frozen=True)
class EntityLink:
    kind: str
    alias: str
    target: str


@dataclass(frozen=True)
class EntityLinkFileResult:
    path: Path
    date: date
    status: str
    inserted_count: int
    links: tuple[EntityLink, ...]


@dataclass(frozen=True)
class EntityLinkResult:
    vault_root: Path
    since: date
    until: date
    execute: bool
    files_considered: int
    files_changed: int
    inserted_count: int
    people_count: int
    event_count: int
    object_count: int
    items: tuple[EntityLinkFileResult, ...]


def link_daily_log_entities(
    *,
    vault_root: Path,
    months: int = 3,
    execute: bool = False,
    today: date | None = None,
) -> EntityLinkResult:
    if months < 1:
        raise ValueError("months must be at least 1")

    current_date = today or datetime.now().date()
    since = _subtract_months(current_date, months)
    until = current_date
    entities = discover_entities(vault_root)
    alias_map = _build_alias_map(entities)
    daily_logs = _discover_daily_logs(vault_root, since, until)

    items: list[EntityLinkFileResult] = []
    for daily_log_path, log_date in daily_logs:
        original = daily_log_path.read_text(encoding="utf-8")
        linked, links = _link_text(original, alias_map)
        status = "unchanged"
        if links:
            status = "linked" if execute else "would_link"
            if execute:
                atomic_write_text(daily_log_path, linked)
        items.append(
            EntityLinkFileResult(
                path=daily_log_path,
                date=log_date,
                status=status,
                inserted_count=len(links),
                links=tuple(links),
            )
        )

    return EntityLinkResult(
        vault_root=vault_root,
        since=since,
        until=until,
        execute=execute,
        files_considered=len(items),
        files_changed=sum(1 for item in items if item.inserted_count),
        inserted_count=sum(item.inserted_count for item in items),
        people_count=sum(1 for entity in entities if entity.kind == "person"),
        event_count=sum(1 for entity in entities if entity.kind == "event"),
        object_count=sum(1 for entity in entities if entity.kind == "object"),
        items=tuple(items),
    )


def discover_entities(vault_root: Path) -> tuple[EntityReference, ...]:
    roots = (
        ("person", vault_root / "04 - People"),
        ("object", vault_root / "03 - Objects"),
        ("event", vault_root / "06 - Timestamps" / "Meetings"),
    )
    entities: list[EntityReference] = []
    for kind, root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.md")):
            if not path.is_file():
                continue
            relative_path = path.relative_to(vault_root)
            aliases = _aliases_for_note(path, kind)
            if aliases:
                entities.append(
                    EntityReference(
                        kind=kind,
                        note_path=relative_path,
                        aliases=aliases,
                    )
                )
    return tuple(entities)


def _aliases_for_note(path: Path, kind: str) -> tuple[str, ...]:
    title = path.stem.strip()
    date_stripped_title = DATE_PREFIX_RE.sub("", title).strip()
    aliases = [title]
    if date_stripped_title and date_stripped_title != title:
        aliases.append(date_stripped_title)
    aliases.extend(_frontmatter_aliases(path))
    if kind == "person":
        first_name = _first_name(date_stripped_title or title)
        if first_name:
            aliases.append(first_name)
    cleaned = _clean_aliases(aliases, allow_short=False)
    cleaned.extend(
        alias
        for alias in _clean_aliases(list(_frontmatter_aliases(path)), allow_short=True)
        if alias.casefold() not in {known.casefold() for known in cleaned}
    )
    return tuple(cleaned)


def _frontmatter_aliases(path: Path) -> tuple[str, ...]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return ()
    end_index = text.find("\n---", 4)
    if end_index == -1:
        return ()
    frontmatter = text[4:end_index].splitlines()
    aliases: list[str] = []
    index = 0
    while index < len(frontmatter):
        line = frontmatter[index]
        if not line.startswith("aliases:"):
            index += 1
            continue
        value = line.split(":", 1)[1].strip()
        if value:
            aliases.extend(_parse_alias_value(value))
            index += 1
            continue
        index += 1
        while index < len(frontmatter) and frontmatter[index].startswith((" ", "-")):
            item = frontmatter[index].strip()
            if item.startswith("-"):
                aliases.append(item[1:].strip().strip("\"'"))
            index += 1
    return tuple(alias for alias in aliases if alias)


def _parse_alias_value(value: str) -> list[str]:
    stripped = value.strip()
    if not stripped:
        return []
    if stripped.startswith("[") and stripped.endswith("]"):
        try:
            parsed = ast.literal_eval(stripped)
        except (ValueError, SyntaxError):
            parsed = None
        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed if str(item).strip()]
    return [part.strip().strip("\"'") for part in stripped.split(",") if part.strip()]


def _clean_aliases(aliases: list[str], *, allow_short: bool) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for alias in aliases:
        normalized = " ".join(alias.strip().split())
        if not _is_linkable_alias(normalized, allow_short=allow_short):
            continue
        key = normalized.casefold()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(normalized)
    return cleaned


def _is_linkable_alias(alias: str, *, allow_short: bool) -> bool:
    if len(alias) < 4 and not (allow_short and len(alias) >= 2):
        return False
    if "[[" in alias or "]]" in alias:
        return False
    if alias.lower() in {"home", "work", "note", "notes", "meeting", "template"}:
        return False
    return any(character.isalnum() for character in alias)


def _first_name(name: str) -> str | None:
    match = re.match(r"[A-Za-z][A-Za-z'’-]+", name)
    if match is None:
        return None
    return match.group(0)


def _build_alias_map(entities: tuple[EntityReference, ...]) -> dict[str, EntityReference]:
    by_alias: dict[str, list[EntityReference]] = {}
    first_name_counts: dict[str, int] = {}
    for entity in entities:
        if entity.kind == "person":
            first = _first_name(DATE_PREFIX_RE.sub("", entity.note_path.stem).strip())
            if first:
                first_name_counts[first.casefold()] = first_name_counts.get(first.casefold(), 0) + 1

    for entity in entities:
        for alias in entity.aliases:
            if entity.kind == "person" and alias == _first_name(entity.note_path.stem):
                if first_name_counts.get(alias.casefold(), 0) > 1:
                    continue
            by_alias.setdefault(alias.casefold(), []).append(entity)
    return {
        alias: matches[0]
        for alias, matches in by_alias.items()
        if len({match.vault_relative_stem for match in matches}) == 1
    }


def _discover_daily_logs(
    vault_root: Path,
    since: date,
    until: date,
) -> tuple[tuple[Path, date], ...]:
    timestamps_root = vault_root / "06 - Timestamps"
    if not timestamps_root.exists():
        return ()
    daily_logs: list[tuple[Path, date]] = []
    for path in sorted(timestamps_root.rglob("*.md")):
        if "Meetings" in path.relative_to(timestamps_root).parts:
            continue
        match = DAILY_LOG_RE.match(path.name)
        if match is None:
            continue
        log_date = date.fromisoformat(match.group("date"))
        if since <= log_date <= until:
            daily_logs.append((path, log_date))
    return tuple(daily_logs)


def _link_text(text: str, alias_map: dict[str, EntityReference]) -> tuple[str, list[EntityLink]]:
    if not alias_map:
        return text, []

    aliases = sorted(alias_map, key=len, reverse=True)
    pattern = re.compile(
        r"(?<![\w])(" + "|".join(re.escape(alias) for alias in aliases) + r")(?![\w])",
        re.IGNORECASE,
    )
    frontmatter, body = _split_frontmatter(text)
    linked_body, links = _link_markdown_body(body, pattern, alias_map)
    return frontmatter + linked_body, links


def _link_markdown_body(
    body: str,
    pattern: re.Pattern[str],
    alias_map: dict[str, EntityReference],
) -> tuple[str, list[EntityLink]]:
    parts = re.split(r"(```.*?```)", body, flags=re.DOTALL)
    linked_parts: list[str] = []
    links: list[EntityLink] = []
    for part in parts:
        if part.startswith("```") and part.endswith("```"):
            linked_parts.append(part)
            continue
        linked_part, part_links = _link_plain_markdown(part, pattern, alias_map)
        linked_parts.append(linked_part)
        links.extend(part_links)
    return "".join(linked_parts), links


def _link_plain_markdown(
    text: str,
    pattern: re.Pattern[str],
    alias_map: dict[str, EntityReference],
) -> tuple[str, list[EntityLink]]:
    protected_ranges = _protected_ranges(text)
    links: list[EntityLink] = []

    def replace(match: re.Match[str]) -> str:
        if _inside_range(match.start(), protected_ranges):
            return match.group(0)
        alias = match.group(0)
        entity = alias_map[alias.casefold()]
        target = entity.vault_relative_stem
        links.append(EntityLink(kind=entity.kind, alias=alias, target=target))
        return f"[[{target}|{alias}]]"

    return pattern.sub(replace, text), links


def _protected_ranges(text: str) -> tuple[tuple[int, int], ...]:
    ranges = [
        (match.start(), match.end())
        for regex in (WIKI_LINK_RE, MARKDOWN_LINK_RE)
        for match in regex.finditer(text)
    ]
    return tuple(sorted(ranges))


def _inside_range(offset: int, ranges: tuple[tuple[int, int], ...]) -> bool:
    return any(start <= offset < end for start, end in ranges)


def _split_frontmatter(text: str) -> tuple[str, str]:
    if not text.startswith("---\n"):
        return "", text
    end_index = text.find("\n---", 4)
    if end_index == -1:
        return "", text
    body_start = text.find("\n", end_index + 4)
    if body_start == -1:
        return text, ""
    return text[: body_start + 1], text[body_start + 1 :]


def _subtract_months(value: date, months: int) -> date:
    month_index = value.year * 12 + (value.month - 1) - months
    year = month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, _days_in_month(year, month))
    return date(year, month, day)


def _days_in_month(year: int, month: int) -> int:
    if month == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month + 1, 1)
    return (next_month - date(year, month, 1)).days
