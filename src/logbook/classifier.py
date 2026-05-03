from __future__ import annotations

import re
from dataclasses import dataclass


FILLER_WORDS = frozenset({"um", "uh", "okay", "ok", "so", "well", "please"})

LOG_ALIASES = (
    ("log", "entry"),
    ("log", "entries"),
    ("log", "record"),
    ("logentry",),
    ("lock", "entry"),
    ("lock", "record"),
    ("block", "entry"),
)

MEETING_ALIASES = (
    ("meeting",),
    ("meeting", "note"),
    ("meeting", "notes"),
)

CATEGORY_ALIASES = {
    "idea": (("idea",), ("ideas",)),
    "task": (("task",), ("todo",), ("to", "do")),
    "research": (("research",), ("question",)),
    "reminder": (("reminder",), ("remind", "me")),
}


@dataclass(frozen=True)
class PrefixClassification:
    route_kind: str
    category: str | None
    matched_alias: str | None
    content: str

    @property
    def label(self) -> str:
        if self.category:
            return f"{self.route_kind}:{self.category}"
        return self.route_kind


@dataclass(frozen=True)
class WordToken:
    text: str
    start: int
    end: int


def classify_transcript(text: str) -> PrefixClassification:
    tokens = _meaningful_tokens(text)
    first_words = tuple(token.text for token in tokens[:20])

    if match := _match_alias(first_words, LOG_ALIASES):
        return _classification("log", None, match, text, tokens)
    if match := _match_alias(first_words, MEETING_ALIASES):
        return _classification("meeting", None, match, text, tokens)

    for category, aliases in CATEGORY_ALIASES.items():
        if match := _match_alias(first_words, aliases):
            return _classification("category", category, match, text, tokens)

    return PrefixClassification(
        route_kind="dead_letter",
        category=None,
        matched_alias=None,
        content=_strip_leading_fillers(text, tokens),
    )


def _meaningful_tokens(text: str) -> tuple[WordToken, ...]:
    tokens = tuple(
        WordToken(
            text=match.group(0).lower().replace("'", ""),
            start=match.start(),
            end=match.end(),
        )
        for match in re.finditer(r"[A-Za-z0-9']+", text)
    )
    return tuple(token for token in tokens if token.text not in FILLER_WORDS)


def _match_alias(words: tuple[str, ...], aliases: tuple[tuple[str, ...], ...]) -> tuple[str, ...] | None:
    for alias in sorted(aliases, key=len, reverse=True):
        if words[: len(alias)] == alias:
            return alias
    return None


def _classification(
    route_kind: str,
    category: str | None,
    alias: tuple[str, ...],
    text: str,
    tokens: tuple[WordToken, ...],
) -> PrefixClassification:
    content = _strip_prefix(text, tokens, len(alias))
    return PrefixClassification(
        route_kind=route_kind,
        category=category,
        matched_alias=" ".join(alias),
        content=content,
    )


def _strip_prefix(text: str, tokens: tuple[WordToken, ...], token_count: int) -> str:
    if len(tokens) <= token_count:
        return ""
    return text[tokens[token_count - 1].end :].lstrip(" \t\n\r:,-.")


def _strip_leading_fillers(text: str, tokens: tuple[WordToken, ...]) -> str:
    if not tokens:
        return text.strip()
    return text[tokens[0].start :].strip()
