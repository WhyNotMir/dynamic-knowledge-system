from __future__ import annotations

from PIL import Image

from app.domain.ingestion.block_semantics import is_article_visible
from app.domain.ingestion.extractor import ExtractedElement, extract
from app.domain.ingestion.extractor import (
    _attach_captions,
    _classify_text,
    _picture_base64,
    _reconstruct_document_stream,
)
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


def test_caption_linking_prefers_same_page_geometry_over_nearby_order():
    elements = [
        ExtractedElement(
            "Wrong nearby table",
            "table",
            1,
            "Section",
            0,
            None,
            meta_json={"docling": {"bbox": {"x0": 400, "y0": 100, "x1": 500, "y1": 180}}},
        ),
        ExtractedElement(
            "Table 2: Geometry should win.",
            "caption",
            1,
            "Section",
            1,
            None,
            meta_json={"docling": {"bbox": {"x0": 20, "y0": 220, "x1": 320, "y1": 245}}},
        ),
        ExtractedElement(
            "Right table",
            "table",
            1,
            "Section",
            2,
            None,
            meta_json={"docling": {"bbox": {"x0": 20, "y0": 110, "x1": 320, "y1": 210}}},
        ),
    ]

    _attach_captions(elements)

    assert elements[1].meta_json is not None
    assert elements[2].meta_json is not None
    assert elements[1].meta_json["caption_group_id"] == elements[2].meta_json["caption_group_id"]
    assert "caption_group_id" not in elements[0].meta_json


def test_caption_linking_groups_multi_panel_figure_images():
    elements = [
        ExtractedElement(
            "Left panel",
            "image",
            1,
            "Section",
            0,
            None,
            meta_json={"docling": {"bbox": {"x0": 20, "y0": 100, "x1": 220, "y1": 320}}},
        ),
        ExtractedElement(
            "Right panel",
            "image",
            1,
            "Section",
            1,
            None,
            meta_json={"docling": {"bbox": {"x0": 260, "y0": 100, "x1": 460, "y1": 320}}},
        ),
        ExtractedElement(
            "Figure 2: (left) First panel. (right) Second panel.",
            "caption",
            1,
            "Section",
            2,
            None,
            meta_json={"docling": {"bbox": {"x0": 20, "y0": 340, "x1": 460, "y1": 380}}},
        ),
    ]

    _attach_captions(elements)

    assert elements[0].meta_json is not None
    assert elements[1].meta_json is not None
    assert elements[0].meta_json["caption_group_id"] == elements[1].meta_json["caption_group_id"]
    assert elements[2].meta_json["caption_group_id"] == elements[0].meta_json["caption_group_id"]


def test_picture_payload_uses_docling_document_context_when_needed():
    document = object()

    class Picture:
        def get_image(self, doc):
            assert doc is document
            return Image.new("RGB", (12, 7), color=(255, 0, 0))

    payload = _picture_base64(Picture(), document)

    assert payload is not None
    assert payload["image_base64"]
    assert payload["ext"] == "png"
    assert payload["image_width"] == 12
    assert payload["image_height"] == 7


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


def test_reconstruction_restores_numbered_heading_hierarchy_and_hides_duplicate_prelude():
    elements = [
        ExtractedElement("3 Model Architecture", "heading", 1, "", 0, 1),
        ExtractedElement("3.2 Attention", "heading", 1, "", 1, 2),
        ExtractedElement("Scaled Dot-Product Attention", "heading", 1, "", 2, 1),
        ExtractedElement("intro text", "paragraph", 1, "", 3, None),
        ExtractedElement("3.2.1 Scaled Dot-Product Attention", "heading", 1, "", 4, 3),
        ExtractedElement("body text", "paragraph", 1, "", 5, None),
    ]

    _reconstruct_document_stream(elements)

    assert elements[2].meta_json is not None
    assert elements[2].meta_json["visibility"] == "hidden"
    assert elements[4].section_path == (
        "3 Model Architecture > 3.2 Attention > "
        "3.2.1 Scaled Dot-Product Attention"
    )
    assert elements[5].section_path == elements[4].section_path


def test_reconstruction_keeps_general_book_artifacts_out_of_article_candidates():
    elements = [
        ExtractedElement("COMPUTING MACHINERY AND INTELLIGENCE", "heading", 1, "", 0, 1),
        ExtractedElement(
            "By A. M. Turing",
            "heading",
            1,
            "",
            1,
            1,
            meta_json={"visibility": "metadata"},
        ),
        ExtractedElement("1. The Imitation Game", "heading", 1, "", 2, 1),
        ExtractedElement("C: Will X please tell me the length of his or her hair?", "paragraph", 1, "", 3, None),
        ExtractedElement("(TABLE DELETED)", "heading", 6, "", 4, 1, meta_json={"visibility": "metadata"}),
        ExtractedElement("6. Contrary Views on the Main Question", "heading", 8, "", 5, 1),
        ExtractedElement("(1) The Theological Objection", "heading", 9, "", 6, 2),
        ExtractedElement("objection body", "paragraph", 9, "", 7, None),
    ]

    _reconstruct_document_stream(elements)

    assert elements[1].section_path == "COMPUTING MACHINERY AND INTELLIGENCE"
    assert elements[3].section_path == "1. The Imitation Game"
    assert elements[4].section_path == "1. The Imitation Game"
    assert elements[6].section_path == (
        "6. Contrary Views on the Main Question > (1) The Theological Objection"
    )


def test_docling_heading_policy_handles_books_dialogue_and_figure_captions():
    assert _classify_text(
        label="section_header",
        text="C: Will X please tell me the length of his or her hair?",
        raw_level=1,
        is_first_content=False,
        in_references=False,
    ) == ("paragraph", None, None)

    assert _classify_text(
        label="section_header",
        text="(1) The Theological Objection",
        raw_level=1,
        is_first_content=False,
        in_references=False,
    ) == ("heading", 2, None)

    assert _classify_text(
        label="footnote",
        text="Figure 3: An example of the attention mechanism.",
        raw_level=None,
        is_first_content=False,
        in_references=False,
    ) == ("caption", None, None)

    assert _classify_text(
        label="footnote",
        text="arXiv preprint arXiv:1607.06450 , 2016.",
        raw_level=None,
        is_first_content=False,
        in_references=True,
    ) == ("footnote", None, None)


def test_docling_heading_policy_handles_ieee_roman_hierarchy():
    assert _classify_text(
        label="section_header",
        text="VIII. ADVANTAGES AND COMPUTATIONAL CONSTRAINTS",
        raw_level=1,
        is_first_content=False,
        in_references=False,
    ) == ("heading", 1, None)

    assert _classify_text(
        label="section_header",
        text="A. Efficient Attention Mechanisms",
        raw_level=1,
        is_first_content=False,
        in_references=False,
    ) == ("heading", 2, None)

    assert _classify_text(
        label="section_header",
        text="C. Computational Complexity",
        raw_level=1,
        is_first_content=False,
        in_references=False,
    ) == ("heading", 2, None)
