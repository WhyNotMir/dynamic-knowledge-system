from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class QASettings:
    top_k: int = 15
    max_per_article: int = 3
    min_score: float = 0.2
    min_evidence_score: float = 0.32
    min_evidence_blocks: int = 1


def _clamp_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


def _clamp_float(value: Any, *, default: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


def qa_settings_from_project_settings(settings: dict | None) -> QASettings:
    raw = settings or {}
    qa = raw.get("qa") if isinstance(raw.get("qa"), dict) else raw
    defaults = QASettings()
    return QASettings(
        top_k=_clamp_int(
            qa.get("top_k"),
            default=defaults.top_k,
            minimum=1,
            maximum=30,
        ),
        max_per_article=_clamp_int(
            qa.get("max_per_article"),
            default=defaults.max_per_article,
            minimum=1,
            maximum=10,
        ),
        min_score=_clamp_float(
            qa.get("min_score"),
            default=defaults.min_score,
            minimum=0.0,
            maximum=1.0,
        ),
        min_evidence_score=_clamp_float(
            qa.get("min_evidence_score"),
            default=defaults.min_evidence_score,
            minimum=0.0,
            maximum=1.0,
        ),
        min_evidence_blocks=_clamp_int(
            qa.get("min_evidence_blocks"),
            default=defaults.min_evidence_blocks,
            minimum=1,
            maximum=10,
        ),
    )


__all__ = ["QASettings", "qa_settings_from_project_settings"]
