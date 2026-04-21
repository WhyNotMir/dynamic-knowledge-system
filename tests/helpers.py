"""Shared helpers for tests.

The DKS pipeline is file-driven, so we need a minimal DOCX generator that
produces content with real section structure: headings that set section_path
on extracted elements, and body paragraphs that add up to at least
MIN_CANDIDATE_CHARS (100) so the center detector classifies the section as
its own candidate rather than falling back to orphan clustering.
"""
from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

from docx import Document


def make_docx(path: Path) -> Path:
    """Write a small DOCX with two sections, each with enough text to form a
    structure candidate.

    Layout:
        # Introduction                 (Heading 1)
            <paragraph x 3, total >100 chars>
        # Methods                      (Heading 1)
            <paragraph x 3, total >100 chars>
    """
    doc = Document()
    doc.add_heading("Introduction", level=1)
    doc.add_paragraph(
        "This is the opening section of a synthetic test document. "
        "It introduces the topic and establishes the context for the rest "
        "of the material that follows below."
    )
    doc.add_paragraph(
        "The introduction also lists the goals and the scope of the work, "
        "so that the reader knows what to expect."
    )
    doc.add_paragraph(
        "Finally, the introduction concludes with a short roadmap of "
        "the remaining sections in the document."
    )

    doc.add_heading("Methods", level=1)
    doc.add_paragraph(
        "In the methods section we describe the procedures used to produce "
        "the results reported later. The description is intentionally "
        "generic so that it resembles a real document."
    )
    doc.add_paragraph(
        "Each step is captured in enough detail to exceed the minimum "
        "fragment length threshold used by the center detector."
    )
    doc.add_paragraph(
        "The methods section ends with a short note on limitations."
    )

    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(path)
    return path


def unique_docx(tmp_dir: Path) -> Path:
    return tmp_dir / f"{uuid.uuid4().hex}.docx"


async def confirm_all_candidates(client, project_id: str, proposal_id: str) -> dict:
    """Flip every `proposed` candidate in a proposal to `confirmed`.

    Tests call this as a stand-in for a user clicking every ✓ in the Review
    UI — without it, `/articles/build` now skips every candidate (because
    `proposed` is treated as "not yet reviewed") and returns zero articles.
    """
    r = await client.post(
        f"/projects/{project_id}/structure/proposals/{proposal_id}/confirm-all"
    )
    assert r.status_code == 200, r.text
    return r.json()


async def wait_for_source_done(client, project_id: str, source_id: str, *, timeout: float = 2.0) -> dict:
    """Poll GET /sources/{id} until status == done or timeout.

    Only useful when the ingestion worker is actually running — in unit tests
    we normally drive `run_ingestion` synchronously, so this helper exists
    for UI-flow tests that pretend to observe the FastAPI endpoints.
    """
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        r = await client.get(f"/projects/{project_id}/sources/{source_id}")
        r.raise_for_status()
        s = r.json()
        if s["status"] in ("done", "failed"):
            return s
        if asyncio.get_event_loop().time() > deadline:
            return s
        await asyncio.sleep(0.05)
