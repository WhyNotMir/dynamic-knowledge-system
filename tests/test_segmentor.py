from app.domain.ingestion.common import ExtractedElement
from app.domain.ingestion.segmentor import segment


def test_segment_merges_short_followup_paragraph_into_previous():
    elements = [
        ExtractedElement(
            content="This is a reasonably long paragraph that should remain the lead paragraph for the section and absorb a short follow-up sentence.",
            element_type="paragraph",
            page_number=1,
            section_path="Topic",
            position_index=0,
            heading_level=None,
        ),
        ExtractedElement(
            content="Short follow-up note.",
            element_type="paragraph",
            page_number=1,
            section_path="Topic",
            position_index=1,
            heading_level=None,
        ),
    ]

    fragments = segment(elements)

    assert len(fragments) == 1
    assert "Short follow-up note." in fragments[0].content
