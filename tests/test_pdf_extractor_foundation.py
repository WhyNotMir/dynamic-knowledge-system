from __future__ import annotations

import uuid

from app.domain.clustering.center_detector import (
    _cluster_orphan_buckets,
    _cluster_orphans,
    _looks_like_pdf_major_heading,
)
from app.domain.ingestion.common import ExtractedElement
from app.domain.ingestion.pdf.extractor import extract_pdf
from app.domain.ingestion.pdf.parsers import looks_like_pdf_visual_word_salad
from app.domain.ingestion.segmentor import segment
from app.models.source import SourceType
from app.models.source_fragment import ElementType, SourceFragment
from tests.helpers import (
    make_front_matter_pdf,
    make_hyphenated_pdf,
    make_pdf,
    make_repeated_header_footer_pdf,
    make_two_column_pdf,
)

extract = extract_pdf


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


def test_pdf_visual_word_salad_is_treated_as_noise():
    assert looks_like_pdf_visual_word_salad(
        "application perfect should never Law The just but will be be its -, "
        "The its , - be be perfect Law but just never should will application"
    )
    assert not looks_like_pdf_visual_word_salad(
        "We used values of 2.8, 3.7, 6.0 and 9.5 TFLOPS for K80, K40, M40 and P100 respectively."
    )
    assert not looks_like_pdf_visual_word_salad(
        "The encoder contains self-attention layers. In a self-attention layer all of the keys, values and queries come "
        "from the same place, in this case, the output of the previous layer in the encoder."
    )
    assert not looks_like_pdf_visual_word_salad(
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
