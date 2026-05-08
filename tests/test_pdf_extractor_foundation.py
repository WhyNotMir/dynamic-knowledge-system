from __future__ import annotations

from pathlib import Path
import re
import uuid

import fitz
import pytest

from app.domain.clustering.center_detector import (
    _cluster_orphan_buckets,
    _cluster_orphans,
    _looks_like_pdf_major_heading,
)
from app.domain.ingestion.extractor import (
    ExtractedElement,
    _looks_like_pdf_visual_word_salad,
    extract,
)
from app.domain.ingestion.segmentor import segment
from app.models.source import SourceType
from app.models.source_fragment import ElementType, SourceFragment
from tests.helpers import (
    make_front_matter_pdf,
    make_display_math_pdf,
    make_hyphenated_pdf,
    make_image_caption_pdf,
    make_inline_math_pdf,
    make_pdf,
    make_references_and_footnotes_pdf,
    make_repeated_header_footer_pdf,
    make_split_display_math_pdf,
    make_table_caption_pdf,
    make_two_column_pdf,
    make_unruled_text_table_pdf,
)


def test_extract_pdf_includes_raw_layout_metadata(tmp_path):
    pdf = make_pdf(tmp_path / "sample.pdf")

    elements = extract(str(pdf))

    assert len(elements) >= 2

    heading = next((item for item in elements if item.element_type == "heading"), None)
    paragraph = next((item for item in elements if item.element_type == "paragraph"), None)

    assert heading is not None
    assert paragraph is not None
    assert heading.meta_json is not None
    assert paragraph.meta_json is not None

    heading_pdf = heading.meta_json["pdf"]
    paragraph_pdf = paragraph.meta_json["pdf"]

    assert heading_pdf["page"]["number"] == 1
    assert heading_pdf["bbox"]["width"] > 0
    assert heading_pdf["bbox"]["height"] > 0
    assert heading_pdf["layout"]["line_count"] >= 1
    assert paragraph_pdf["layout"]["span_count"] >= 1
    assert heading_pdf["font"]["avg_size"] > paragraph_pdf["font"]["avg_size"]
    assert paragraph_pdf["font"]["dominant_name"] is not None


def test_segment_merges_short_pdf_paragraphs_without_losing_raw_blocks():
    elements = [
        ExtractedElement(
            content="Short paragraph one.",
            element_type="paragraph",
            page_number=1,
            section_path="Section",
            position_index=0,
            heading_level=None,
            meta_json={"pdf": {"block_no": 0, "bbox": {"x0": 10, "y0": 10, "x1": 20, "y1": 20}}},
        ),
        ExtractedElement(
            content="Short paragraph two.",
            element_type="paragraph",
            page_number=1,
            section_path="Section",
            position_index=1,
            heading_level=None,
            meta_json={"pdf": {"block_no": 1, "bbox": {"x0": 10, "y0": 22, "x1": 20, "y1": 32}}},
        ),
    ]

    fragments = segment(elements)

    assert len(fragments) == 1
    assert fragments[0].content == "Short paragraph one. Short paragraph two."
    assert fragments[0].meta_json == {
        "pdf_blocks": [
            {"block_no": 0, "bbox": {"x0": 10, "y0": 10, "x1": 20, "y1": 20}},
            {"block_no": 1, "bbox": {"x0": 10, "y0": 22, "x1": 20, "y1": 32}},
        ]
    }


def test_extract_pdf_reads_left_column_before_right_column(tmp_path):
    pdf = make_two_column_pdf(tmp_path / "two-column.pdf")

    elements = extract(str(pdf))
    paragraphs = [item.content for item in elements if item.element_type == "paragraph"]

    assert paragraphs[0].startswith("Left column paragraph one.")
    assert paragraphs[1].startswith("Right column paragraph two.")


def test_extract_pdf_strips_repeated_headers_and_footers(tmp_path):
    pdf = make_repeated_header_footer_pdf(tmp_path / "repeated-artifacts.pdf")

    elements = extract(str(pdf))
    contents = [item.content for item in elements]

    assert "Conference Header" not in contents
    assert "Page 1" not in contents
    assert "Page 2" not in contents
    assert any(content.startswith("Body Section 1") for content in contents)
    assert any(content.startswith("Body Section 2") for content in contents)


def test_extract_pdf_repairs_hyphenated_line_wrap(tmp_path):
    pdf = make_hyphenated_pdf(tmp_path / "hyphenated.pdf")

    contents = [item.content for item in extract(str(pdf))]

    assert any("architecture is designed" in content for content in contents)
    assert not any("archi- tecture" in content for content in contents)


def test_extract_pdf_filters_front_matter_but_keeps_abstract(tmp_path):
    pdf = make_front_matter_pdf(tmp_path / "front-matter.pdf")

    contents = [item.content for item in extract(str(pdf))]

    assert "ashish@example.com" not in contents
    assert "Google Research" not in contents
    assert "University of Somewhere" not in contents
    assert "Abstract" in contents
    assert any("transformer model architecture" in content for content in contents)


def test_extract_pdf_table_has_display_metadata(tmp_path):
    pdf = make_table_caption_pdf(tmp_path / "table-caption.pdf")

    table = next(item for item in extract(str(pdf)) if item.element_type == "table")

    assert table.meta_json is not None
    assert table.meta_json["rows"][0] == ["Model", "BLEU"]
    assert table.meta_json["markdown"].startswith("| Model | BLEU |")
    assert table.meta_json["html"].startswith("<table>")
    assert table.meta_json["table"]["extraction_method"] == "pymupdf_find_tables"
    assert table.meta_json["table"]["display_mode"] == "grid"
    assert table.meta_json["table"]["confidence"] >= 0.65
    assert table.meta_json["table"]["bbox"]["width"] > 0
    assert table.meta_json["table"]["reconstruction_method"] == "word_bbox_columns"
    assert table.meta_json["table"]["display_rows"][0] == ["Model", "BLEU"]
    assert table.meta_json["table"]["reconstructed_rows"][1] == ["Transformer", "28.4"]
    assert "Model" in table.meta_json["table"]["plain_text"]
    assert table.meta_json["table"]["text_lines"]


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


def test_extract_pdf_recovers_unruled_table_after_caption(tmp_path):
    pdf = make_unruled_text_table_pdf(tmp_path / "unruled-table.pdf")

    elements = extract(str(pdf))
    table = next(item for item in elements if item.element_type == "table")

    assert table.meta_json is not None
    assert table.meta_json["table"]["extraction_method"] == "text_table_fallback"
    assert table.meta_json["rows"][0] == ["Model", "BLEU", "Params"]
    assert table.meta_json["rows"][1] == ["base", "25.8", "65"]
    assert any("after the unruled table" in item.content for item in elements if item.element_type == "paragraph")


def test_extract_pdf_inline_table_reference_is_not_caption(tmp_path):
    pdf = make_pdf(tmp_path / "inline-table-reference.pdf")

    doc = fitz.open(str(pdf))
    page = doc[0]
    page.insert_text((72, 260), "The result shown in Table 2 is discussed in prose.", fontsize=12)
    doc.saveIncr()
    doc.close()

    paragraph = next(
        item
        for item in extract(str(pdf))
        if "shown in Table 2" in item.content
    )

    assert paragraph.element_type == "paragraph"


def test_extract_pdf_image_keeps_page_order_and_links_caption(tmp_path):
    pdf = make_image_caption_pdf(tmp_path / "image-caption.pdf")

    elements = extract(str(pdf))
    image_index = next(index for index, item in enumerate(elements) if item.element_type == "image")
    caption_index = next(index for index, item in enumerate(elements) if item.element_type == "caption")
    image = elements[image_index]
    caption = elements[caption_index]

    assert image_index < caption_index
    assert image.meta_json is not None
    assert caption.meta_json is not None
    assert image.meta_json["image"]["extraction_method"] == "pymupdf_extract_image"
    assert image.meta_json["caption_group_id"] == caption.meta_json["caption_group_id"]
    assert caption.meta_json["caption"]["target_kind"] == "image"


def test_extract_pdf_marks_inline_math_without_creating_formula_block(tmp_path):
    pdf = make_inline_math_pdf(tmp_path / "inline-math.pdf")

    elements = extract(str(pdf))
    paragraph = next(item for item in elements if "P_drop" in item.content)

    assert paragraph.element_type == "paragraph"
    assert not any(item.element_type == "formula" for item in elements)
    assert paragraph.meta_json is not None
    assert paragraph.meta_json["has_inline_math"] is True
    assert paragraph.meta_json["math_spans"]
    assert any(span["style"] == "math" for span in paragraph.inline_spans or [])


def test_extract_pdf_keeps_standalone_math_as_inline_paragraph(tmp_path):
    pdf = make_display_math_pdf(tmp_path / "display-math.pdf")

    elements = extract(str(pdf))
    expression = next(item for item in elements if "d_model" in item.content)

    assert expression.element_type == "paragraph"
    assert not any(item.element_type == "formula" for item in elements)
    assert expression.meta_json is not None
    assert "display_math" not in expression.meta_json
    assert expression.meta_json["has_inline_math"] is True
    assert expression.meta_json["math_spans"]


def test_extract_pdf_merges_split_math_blocks_as_one_paragraph(tmp_path):
    pdf = make_split_display_math_pdf(tmp_path / "split-display-math.pdf")

    elements = extract(str(pdf))
    expression = next(item for item in elements if "Attention(" in item.content)

    assert expression.element_type == "paragraph"
    assert "softmax( QK T sqrt d k ) V (1)" in expression.content
    assert not any(item.content.startswith("sqrt d k") for item in elements)
    assert expression.meta_json is not None
    assert expression.meta_json["has_inline_math"] is True


def test_extract_pdf_marks_footnotes_and_references_section(tmp_path):
    pdf = make_references_and_footnotes_pdf(tmp_path / "references-footnotes.pdf")

    elements = extract(str(pdf))
    footnote = next(item for item in elements if item.content.startswith("* Equal"))
    references = [item for item in elements if item.content.startswith("[")]

    assert footnote.element_type == "footnote"
    assert footnote.meta_json is not None
    assert footnote.meta_json["footnote"] == {"marker": "*", "marker_style": "symbol"}
    assert [item.content[:3] for item in references] == ["[1]", "[2]"]
    assert all(item.meta_json and item.meta_json["references_section"] is True for item in references)


def test_extract_attention_pdf_preserves_tables_formulas_and_visual_appendix():
    pdf = Path("/Users/mir/Downloads/Attention Is All You Need.pdf")
    if not pdf.exists():
        pytest.skip("local Attention Is All You Need fixture is not available")

    elements = extract(str(pdf))
    contents = [item.content for item in elements]
    tables = [item for item in elements if item.element_type == "table"]
    images = [item for item in elements if item.element_type == "image"]

    assert any("Attention( Q, K, V ) = softmax" in content and "√ d k ) V (1)" in content for content in contents)
    assert not any(content.startswith("√ d k ) V") for content in contents)
    assert any("Table 1: Maximum path lengths" in content for content in contents)
    table_1 = next(table for table in tables if "Layer Type" in table.content)
    assert "Self-Attention" in table_1.content
    assert "Positional Encoding" not in table_1.content
    assert table_1.meta_json and table_1.meta_json["table"]["snapshot_image_base64"]
    assert any("Transformer (base model)" in table.content for table in tables)
    assert any("Parser | Training | WSJ 23 F1" in table.content for table in tables)
    reference_numbers = [
        int(match.group(1))
        for item in elements
        if item.meta_json and item.meta_json.get("references_section") is True
        if (match := re.match(r"^\[(\d+)\]", item.content.strip()))
    ]
    assert reference_numbers == list(range(1, 41))
    table_group_ids = [
        table.meta_json.get("caption_group_id")
        for table in tables
        if table.meta_json and table.meta_json.get("caption_group_id")
    ]
    assert len(table_group_ids) == len(set(table_group_ids))
    assert sum("positional embedding instead of sinusoids" in table.content for table in tables) == 1
    assert not any(table.content.startswith("N\nd model\nd ff") for table in tables)
    assert any("Table 2 summarizes our results" in item.content and item.element_type == "paragraph" for item in elements)
    assert sum(1 for image in images if image.meta_json and image.meta_json.get("visualization_snapshot")) == 3


def test_pdf_visual_word_salad_is_treated_as_noise():
    assert _looks_like_pdf_visual_word_salad(
        "application perfect should never Law The just but will be be its -, "
        "The its , - be be perfect Law but just never should will application"
    )
    assert not _looks_like_pdf_visual_word_salad(
        "We used values of 2.8, 3.7, 6.0 and 9.5 TFLOPS for K80, K40, M40 and P100 respectively."
    )
    assert not _looks_like_pdf_visual_word_salad(
        "The encoder contains self-attention layers. In a self-attention layer all of the keys, values and queries come "
        "from the same place, in this case, the output of the previous layer in the encoder."
    )
    assert not _looks_like_pdf_visual_word_salad(
        "Similarly to other sequence transduction models, we use learned embeddings to convert the input tokens and output "
        "tokens to vectors of dimension d_model."
    )


def test_pdf_orphan_candidates_do_not_mix_across_sources():
    source_a = uuid.uuid4()
    source_b = uuid.uuid4()

    orphan_buckets = {
        (source_a, SourceType.PDF.value): [
            SourceFragment(
                source_id=source_a,
                content="This paragraph describes transformer attention in enough detail to stay as one source-local orphan candidate without mixing into another document.",
                element_type=ElementType.PARAGRAPH,
                heading_level=None,
                list_level=None,
                group_id=None,
                page_number=1,
                section_path=None,
                position_index=0,
                inline_spans=None,
                meta_json=None,
                embedding=None,
            ),
            SourceFragment(
                source_id=source_a,
                content="It continues the same transformer-focused orphan material so the PDF fallback has enough body text to survive candidate creation.",
                element_type=ElementType.PARAGRAPH,
                heading_level=None,
                list_level=None,
                group_id=None,
                page_number=1,
                section_path=None,
                position_index=1,
                inline_spans=None,
                meta_json=None,
                embedding=None,
            ),
            SourceFragment(
                source_id=source_a,
                content="This final paragraph keeps the orphan content source-local and coherent for the detector.",
                element_type=ElementType.PARAGRAPH,
                heading_level=None,
                list_level=None,
                group_id=None,
                page_number=1,
                section_path=None,
                position_index=2,
                inline_spans=None,
                meta_json=None,
                embedding=None,
            ),
        ],
        (source_b, SourceType.PDF.value): [
            SourceFragment(
                source_id=source_b,
                content="This paragraph belongs to a completely different source document about legal process and should never mix with the transformer orphan candidate even when both documents are ingested into the same project for later review and article building.",
                element_type=ElementType.PARAGRAPH,
                heading_level=None,
                list_level=None,
                group_id=None,
                page_number=1,
                section_path=None,
                position_index=0,
                inline_spans=None,
                meta_json=None,
                embedding=None,
            ),
            SourceFragment(
                source_id=source_b,
                content="It stays in its own PDF orphan bucket and therefore becomes a separate candidate or nothing at all, but never a cross-source blend with transformer content from another source because the fallback now keeps PDF leftovers source-local.",
                element_type=ElementType.PARAGRAPH,
                heading_level=None,
                list_level=None,
                group_id=None,
                page_number=1,
                section_path=None,
                position_index=1,
                inline_spans=None,
                meta_json=None,
                embedding=None,
            ),
            SourceFragment(
                source_id=source_b,
                content="A third body paragraph makes the fallback threshold explicit for this second source too and provides enough body text for the bucket to survive as its own legal-themed candidate during source-local orphan handling.",
                element_type=ElementType.PARAGRAPH,
                heading_level=None,
                list_level=None,
                group_id=None,
                page_number=1,
                section_path=None,
                position_index=2,
                inline_spans=None,
                meta_json=None,
                embedding=None,
            ),
        ],
    }

    candidates = _cluster_orphan_buckets(orphan_buckets)

    assert len(candidates) == 2
    candidate_source_sets = [
        {fragment.source_id for fragment in candidate["fragments"]}
        for candidate in candidates
    ]
    assert all(len(source_ids) == 1 for source_ids in candidate_source_sets)


def test_pdf_orphans_without_body_are_discarded():
    source_id = uuid.uuid4()
    candidates = _cluster_orphans(
        [
            SourceFragment(
                source_id=source_id,
                content="Input-Input Layer5",
                element_type=ElementType.HEADING,
                heading_level=1,
                list_level=None,
                group_id=None,
                page_number=1,
                section_path=None,
                position_index=0,
                inline_spans=None,
                meta_json=None,
                embedding=None,
            ),
            SourceFragment(
                source_id=source_id,
                content="Figure 3: Attention visualizations for layer 5.",
                element_type=ElementType.CAPTION,
                heading_level=None,
                list_level=None,
                group_id=None,
                page_number=1,
                section_path=None,
                position_index=1,
                inline_spans=None,
                meta_json=None,
                embedding=None,
            ),
        ],
        source_type=SourceType.PDF.value,
    )

    assert candidates == []


def test_pdf_major_heading_split_ignores_subsection_heading_levels():
    assert _looks_like_pdf_major_heading(
        SourceFragment(
            source_id=uuid.uuid4(),
            content="3 Model Architecture",
            element_type=ElementType.HEADING,
            heading_level=1,
            list_level=None,
            group_id=None,
            page_number=1,
            section_path="Paper > 3 Model Architecture",
            position_index=0,
            inline_spans=None,
            meta_json=None,
            embedding=None,
        )
    )
    assert not _looks_like_pdf_major_heading(
        SourceFragment(
            source_id=uuid.uuid4(),
            content="3.2 Multi-Head Attention",
            element_type=ElementType.HEADING,
            heading_level=2,
            list_level=None,
            group_id=None,
            page_number=1,
            section_path="Paper > 3 Model Architecture > 3.2 Multi-Head Attention",
            position_index=1,
            inline_spans=None,
            meta_json=None,
            embedding=None,
        )
    )
