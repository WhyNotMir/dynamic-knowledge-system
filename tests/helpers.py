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
import base64
from pathlib import Path

from docx import Document
from docx.shared import Pt


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


def make_rich_docx(path: Path) -> Path:
    """Write a DOCX that exercises richer source-driven extraction.

    Includes:
    - heading + formatted paragraph (bold, italic, monospace)
    - quote paragraph
    - bullet list
    - simple table
    """
    doc = Document()
    doc.add_heading("Rich Content", level=1)

    para = doc.add_paragraph()
    para.add_run("This paragraph has ")
    run = para.add_run("bold")
    run.bold = True
    para.add_run(", ")
    run = para.add_run("italic")
    run.italic = True
    para.add_run(", and ")
    run = para.add_run("inline code")
    run.font.name = "Courier New"
    run.font.size = Pt(10)
    para.add_run(" spans.")

    quote = doc.add_paragraph("Quoted material from the source document.")
    for style_name in ("Intense Quote", "Quote"):
        try:
            quote.style = style_name
            break
        except KeyError:
            continue

    item = doc.add_paragraph("First bullet item", style="List Bullet")
    item = doc.add_paragraph("Second bullet item", style="List Bullet")
    item.paragraph_format.left_indent = item.paragraph_format.left_indent

    code = doc.add_paragraph()
    code.add_run("print('hello world')").font.name = "Courier New"

    image_path = path.parent / "tiny.png"
    image_path.write_bytes(base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNgYAAAAAMAASsJTYQAAAAASUVORK5CYII="
    ))
    doc.add_picture(str(image_path))

    doc.add_paragraph("[1] Supplemental footnote from the source.")

    table = doc.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "Column A"
    table.cell(0, 1).text = "Column B"
    table.cell(1, 0).text = "Value 1"
    table.cell(1, 1).text = "Value 2"

    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(path)
    return path


def make_multitopic_docx(path: Path) -> Path:
    """Write a DOCX with one H1 topic and nested H2/H3 headings."""
    doc = Document()
    doc.add_heading("Platform", level=1)
    doc.add_paragraph(
        "The platform section introduces the broader topic and provides enough "
        "context to comfortably exceed the clustering threshold for a single topic."
    )
    doc.add_heading("Architecture", level=2)
    doc.add_paragraph(
        "Architecture explains how the platform is decomposed into services, "
        "pipelines, and runtime boundaries."
    )
    doc.add_heading("Worker Layer", level=3)
    doc.add_paragraph(
        "The worker layer handles asynchronous execution, background jobs, "
        "and durable retries."
    )
    doc.add_heading("Operations", level=2)
    doc.add_paragraph(
        "Operations focuses on monitoring, deployment ergonomics, and incident response."
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
