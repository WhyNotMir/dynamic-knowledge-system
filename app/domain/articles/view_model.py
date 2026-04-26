from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

from app.models.article import Article, ArticleBlock
from app.models.source_fragment import ElementType
from app.models.structural_block import StructuralBlock

LIST_PREFIX_RE = re.compile(r"^(([\u2022*•◦-])|(\d+[\.\)]))\s*")
HEADING_PREFIX_RE = re.compile(r"^(\d+(?:\.\d+)*)\s+(.+)$")
MULTISPACE_RE = re.compile(r"\s+")
NOISY_SECTION_LABELS = {"unrelated", "general", "uncategorized"}
UNASSIGNED_NODES_LABEL = "Unassigned Nodes"
UNASSIGNED_ARTICLES_LABEL = "Unassigned Articles"
STRUCTURED_FALLBACK_LABEL = "Structured"
GENERIC_ALIAS_TOKENS = {
    "abstract",
    "appendix",
    "article",
    "background",
    "chapter",
    "conclusion",
    "discussion",
    "figure",
    "general",
    "introduction",
    "method",
    "methods",
    "references",
    "results",
    "section",
    "summary",
    "table",
}


@dataclass(slots=True)
class HeadingInfo:
    depth: int
    label: str
    prefix: str | None


def normalize_text(value: str) -> str:
    return MULTISPACE_RE.sub(" ", value).strip()


def escape_regex(value: str) -> str:
    return re.escape(value)


def looks_like_real_heading(value: str) -> bool:
    stripped = normalize_text(value)
    if not stripped or len(stripped) > 90:
        return False

    lowered = stripped.lower()
    if any(marker in lowered for marker in ("<eos>", "arxiv:", "[cs.", "wsj", "gnmt", "en-de", "en-fr")):
        return False

    alpha = sum(1 for char in stripped if char.isalpha())
    digits = sum(1 for char in stripped if char.isdigit())
    if alpha < 3:
        return False
    if digits > alpha:
        return False

    return True


def parse_heading_label(value: str) -> HeadingInfo:
    stripped = normalize_text(value)
    match = HEADING_PREFIX_RE.match(stripped)
    if not match:
        return HeadingInfo(depth=1, label=stripped, prefix=None)
    return HeadingInfo(
        depth=len(match.group(1).split(".")),
        label=match.group(2).strip(),
        prefix=match.group(1),
    )


def is_noise_block(block: ArticleBlock) -> bool:
    stripped = normalize_text(block.content)
    if not stripped:
        return True
    if block.element_type in {
        ElementType.IMAGE,
        ElementType.TABLE,
        ElementType.QUOTE,
        ElementType.CODE_BLOCK,
        ElementType.FORMULA,
    }:
        return False
    if block.element_type == ElementType.HEADING:
        return not looks_like_real_heading(stripped)

    lowered = stripped.lower()
    if lowered in {"<eos>", "eos"}:
        return True
    if any(marker in lowered for marker in ("arxiv:", "[cs.", "gnmt", "en-de", "en-fr", "wsj")):
        return True
    if re.fullmatch(r"[\W\d_]+", stripped):
        return True
    if not any(char.isalpha() for char in stripped) and any(char.isdigit() for char in stripped):
        return True
    return False


def build_source_context_label(block: ArticleBlock, source_title: str | None) -> str | None:
    parts = [
        source_title,
        f"p.{block.page_number}" if block.page_number else None,
        block.section_path,
    ]
    visible = [part for part in parts if part]
    return " • ".join(visible) if visible else None


def is_reasonable_manual_alias(value: str) -> bool:
    collapsed = normalize_text(value)
    if not collapsed:
        return False
    if len(collapsed) < 2 or len(collapsed) > 96:
        return False

    lowered = collapsed.lower()
    if lowered in GENERIC_ALIAS_TOKENS:
        return False

    alpha = sum(1 for char in collapsed if char.isalpha())
    digits = sum(1 for char in collapsed if char.isdigit())
    if alpha == 0 and digits == 0:
        return False
    if digits > alpha and alpha < 2:
        return False

    words = collapsed.replace("/", " ").split()
    if len(words) == 1:
        word = words[0]
        if word.lower() in GENERIC_ALIAS_TOKENS:
            return False
        if len(word) <= 2 and word != word.upper():
            return False

    punctuation = sum(1 for char in collapsed if not re.match(r"[A-Za-z0-9\s]", char))
    if punctuation > max(4, len(collapsed) // 3):
        return False

    return True


def collect_link_ranges(
    content: str,
    targets: list[dict[str, str]],
) -> list[dict[str, str | int]]:
    candidates: list[dict[str, str | int]] = []
    for target in targets:
        label = target["label"]
        escaped = escape_regex(label)
        if re.search(r"[A-Za-z0-9]", label):
            pattern = re.compile(rf"(?<!\w){escaped}(?!\w)", re.IGNORECASE)
        else:
            pattern = re.compile(escaped, re.IGNORECASE)
        for match in pattern.finditer(content):
            candidates.append(
                {
                    "start": match.start(),
                    "end": match.end(),
                    "article_id": target["article_id"],
                    "label": match.group(0),
                }
            )

    candidates.sort(
        key=lambda item: (
            int(item["start"]),
            -(int(item["end"]) - int(item["start"])),
        )
    )

    accepted: list[dict[str, str | int]] = []
    cursor = -1
    for candidate in candidates:
        start = int(candidate["start"])
        end = int(candidate["end"])
        if start < cursor:
            continue
        accepted.append(candidate)
        cursor = end
    return accepted


def normalize_sidebar_section_label(article: Article) -> str:
    raw = (article.suggested_section or "").strip()
    if not raw:
        return STRUCTURED_FALLBACK_LABEL if article.structural_block_id else UNASSIGNED_ARTICLES_LABEL
    if raw.lower() in NOISY_SECTION_LABELS:
        return STRUCTURED_FALLBACK_LABEL if article.structural_block_id else UNASSIGNED_ARTICLES_LABEL
    return raw


def sort_articles_for_sidebar(articles: list[Article]) -> list[Article]:
    return sorted(articles, key=lambda article: (article.created_at, article.title.casefold()))


def build_article_breadcrumb(
    article: Article,
    block_by_id: dict[uuid.UUID, StructuralBlock] | None = None,
) -> list[dict[str, str | uuid.UUID]]:
    breadcrumb: list[dict[str, str | uuid.UUID]] = []
    if article.structural_block_id is None:
        return breadcrumb

    if block_by_id is not None:
        cursor = block_by_id.get(article.structural_block_id)
        while cursor is not None:
            breadcrumb.append({"id": cursor.id, "name": cursor.name})
            cursor = block_by_id.get(cursor.parent_id) if cursor.parent_id else None
    else:
        cursor = article.structural_block
        while cursor is not None:
            breadcrumb.append({"id": cursor.id, "name": cursor.name})
            cursor = cursor.parent
    breadcrumb.reverse()
    return breadcrumb


def _serialize_sidebar_article(article: Article) -> dict:
    return {
        "id": article.id,
        "title": article.title,
        "slug": article.slug,
        "kind": article.kind,
        "suggested_section": article.suggested_section,
        "status": article.status,
        "block_count": len(article.blocks),
    }


def build_sidebar_tree(
    structural_blocks: list[StructuralBlock],
    articles: list[Article],
) -> list[dict]:
    articles_by_block: dict[uuid.UUID, list[Article]] = {}
    for article in articles:
        if article.structural_block_id:
            articles_by_block.setdefault(article.structural_block_id, []).append(article)

    def build_structured_node(block: StructuralBlock) -> dict:
        article_items = [
            _serialize_sidebar_article(article)
            for article in sort_articles_for_sidebar(articles_by_block.get(block.id, []))
        ]
        child_nodes = [build_structured_node(child) for child in block.children]
        total_count = len(article_items) + sum(child["total_count"] for child in child_nodes)
        return {
            "key": f"structured:{block.id}",
            "label": block.name,
            "kind": "structured",
            "block_id": block.id,
            "articles": article_items,
            "groups": child_nodes,
            "total_count": total_count,
        }

    structured_nodes = [build_structured_node(block) for block in structural_blocks]
    structured_nodes = [
        node for node in structured_nodes
        if node["total_count"] > 0
    ]

    grouped_unassigned: dict[tuple[str, str], list[Article]] = {}
    for article in articles:
        if article.structural_block_id:
            continue
        if article.kind.value == "node":
            key = ("nodes", UNASSIGNED_NODES_LABEL)
        else:
            label = normalize_sidebar_section_label(article)
            key = ("unassigned", label)
        grouped_unassigned.setdefault(key, []).append(article)

    ordered_unassigned = []
    for (group_kind, label), group_articles in grouped_unassigned.items():
        ordered_unassigned.append(
            {
                "key": f"{group_kind}:{label}",
                "label": label,
                "kind": group_kind,
                "block_id": None,
                "articles": [
                    _serialize_sidebar_article(article)
                    for article in sort_articles_for_sidebar(group_articles)
                ],
                "groups": [],
                "total_count": len(group_articles),
            }
        )

    ordered_unassigned.sort(
        key=lambda item: (
            2 if item["label"] == UNASSIGNED_NODES_LABEL else 1 if item["kind"] == "unassigned" else 0,
            item["label"].casefold(),
        )
    )

    return [*structured_nodes, *ordered_unassigned]
