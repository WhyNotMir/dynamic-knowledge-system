from __future__ import annotations

from app.domain.ingestion.block_semantics import is_article_visible
from app.domain.ingestion.extractor import ExtractedElement, extract
from app.domain.ingestion.segmentor import segment
from tests.helpers import (
    make_pdf,
    make_references_and_footnotes_pdf,
    make_table_caption_pdf,
    make_table_pdf,
    make_two_column_pdf,
)


def test_extract_pdf_returns_canonical_docling_stream(tmp_path):
    pdf = make_pdf(tmp_path / "sample.pdf")

    elements = extract(str(pdf))

    assert len(elements) >= 2
    assert [item.position_index for item in elements] == list(range(len(elements)))
    assert all(item.meta_json and item.meta_json["extraction_method"].startswith("docling") for item in elements)
    assert all(is_article_visible(item.meta_json) for item in elements)

    heading = next(item for item in elements if item.element_type == "heading")
    paragraph = next(item for item in elements if item.element_type == "paragraph")

    assert heading.content == "PDF Introduction"
    assert heading.heading_level == 1
    assert paragraph.section_path == "PDF Introduction"
    assert heading.page_number == 1
    assert paragraph.page_number == 1
    assert heading.meta_json["docling"]["bbox"]["width"] > 0
    assert heading.meta_json["pdf"]["extraction_method"] == "docling"


def test_extract_pdf_reads_left_column_before_right_column(tmp_path):
    pdf = make_two_column_pdf(tmp_path / "two-column.pdf")

    elements = extract(str(pdf))
    paragraphs = [item.content for item in elements if item.element_type == "paragraph"]

    assert paragraphs[0].startswith("Left column paragraph one.")
    assert paragraphs[1].startswith("Right column paragraph two.")


def test_extract_pdf_tables_use_structured_rows(tmp_path):
    pdf = make_table_pdf(tmp_path / "table.pdf")

    table = next(item for item in extract(str(pdf)) if item.element_type == "table")

    assert table.content == "Model | BLEU\nTransformer | 28.4"
    assert table.meta_json is not None
    assert table.meta_json["rows"] == [["Model", "BLEU"], ["Transformer", "28.4"]]
    assert table.meta_json["table"]["extraction_method"] == "docling_table"
    assert table.meta_json["table"]["display_mode"] == "grid"
    assert table.meta_json["semantic"]["role"] == "table"


def test_extract_pdf_caption_links_to_nearby_table(tmp_path):
    pdf = make_table_caption_pdf(tmp_path / "table-caption.pdf")

    elements = extract(str(pdf))
    caption = next(item for item in elements if item.element_type == "caption")
    table = next(item for item in elements if item.element_type == "table")

    assert caption.meta_json is not None
    assert table.meta_json is not None
    assert caption.meta_json["caption_group_id"] == table.meta_json["caption_group_id"]
    assert caption.meta_json["caption"]["target_kind"] == "table"
    assert table.meta_json["caption"]["text"] == "Table 1: Scores by model."


def test_extract_pdf_marks_references_as_reference_role(tmp_path):
    pdf = make_references_and_footnotes_pdf(tmp_path / "references-footnotes.pdf")

    elements = extract(str(pdf))
    reference_blocks = [
        item
        for item in elements
        if item.meta_json and item.meta_json["semantic"]["role"] == "reference"
    ]

    assert len(reference_blocks) >= 2
    assert all(item.element_type == "footnote" for item in reference_blocks)
    assert any("First paper" in item.content for item in reference_blocks)
    assert all(item.meta_json and item.meta_json["references_section"] is True for item in reference_blocks)


def test_segment_merges_short_docling_paragraphs_without_losing_provenance():
    elements = [
        ExtractedElement(
            content="Short paragraph one.",
            element_type="paragraph",
            page_number=1,
            section_path="Section",
            position_index=0,
            heading_level=None,
            meta_json={"docling": {"label": "text", "bbox": {"x0": 10, "y0": 10, "x1": 20, "y1": 20}}},
        ),
        ExtractedElement(
            content="Short paragraph two.",
            element_type="paragraph",
            page_number=1,
            section_path="Section",
            position_index=1,
            heading_level=None,
            meta_json={"docling": {"label": "text", "bbox": {"x0": 10, "y0": 22, "x1": 20, "y1": 32}}},
        ),
    ]

    fragments = segment(elements)

    assert len(fragments) == 1
    assert fragments[0].content == "Short paragraph one. Short paragraph two."
    assert fragments[0].meta_json is not None
    assert fragments[0].meta_json["semantic"]["visibility"] == "article"
