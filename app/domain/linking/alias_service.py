from __future__ import annotations

import re

_GENERIC_TITLES = {
    "abstract",
    "background",
    "conclusion",
    "discussion",
    "general",
    "introduction",
    "methods",
    "references",
    "results",
    "summary",
}

_MULTISPACE_RE = re.compile(r"\s+")


def collapse_ws(value: str) -> str:
    return _MULTISPACE_RE.sub(" ", value).strip()


def normalise_alias_key(value: str) -> str:
    return collapse_ws(value).casefold()


def build_auto_aliases(title: str) -> list[str]:
    collapsed = collapse_ws(title)
    if not collapsed:
        return []

    aliases: list[str] = []
    lowered = collapsed.casefold()
    if lowered not in _GENERIC_TITLES:
        aliases.append(collapsed)

    if ":" in collapsed:
        lead = collapse_ws(collapsed.split(":", 1)[0])
        if lead and lead.casefold() not in _GENERIC_TITLES:
            aliases.append(lead)

    seen: set[str] = set()
    unique_aliases: list[str] = []
    for alias in aliases:
        key = normalise_alias_key(alias)
        if not key or key in seen:
            continue
        seen.add(key)
        unique_aliases.append(alias)
    return unique_aliases
