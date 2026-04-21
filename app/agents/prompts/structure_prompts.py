"""Prompt templates for the structure agent (Phase 0 Slice C).

Two roles:
* ``title`` — given the fragments of a candidate (and an optional source
  heading hint), compose a short article title.
* ``hierarchy`` — given a flat list of article titles, group them under
  2–5 top-level sections.

The raw string constants are the prompt source of truth. The
LangChain-flavoured templates (``TITLE_PROMPT``, ``HIERARCHY_PROMPT``)
wrap those same strings for the runnable chain implementation.
"""
from __future__ import annotations

from langchain_core.prompts import ChatPromptTemplate


# ---------------------------------------------------------------------------
# Raw string constants shared by the chain implementation.
# ---------------------------------------------------------------------------

TITLE_SYSTEM = (
    "You are a knowledge structure expert. "
    "Given document fragments propose a clear article title in 3-7 words. "
    "Return only the title, nothing else."
)

TITLE_USER = """\
{hint}Fragments:
{content}

Title:"""

HIERARCHY_SYSTEM = (
    "You are a knowledge structure expert. "
    "Given article titles group them into 2-5 top-level sections. "
    'Return JSON only: {"Section Name": ["Title 1", "Title 2"], ...}'
)

HIERARCHY_USER = """\
Articles:
{numbered}

JSON:"""


# ---------------------------------------------------------------------------
# LangChain ChatPromptTemplate-s.
#
# We escape the literal JSON example in HIERARCHY_SYSTEM so LangChain's
# curly-brace templating doesn't try to interpret it as a variable.
# ---------------------------------------------------------------------------

TITLE_PROMPT = ChatPromptTemplate.from_messages(
    [
        ("system", TITLE_SYSTEM),
        ("user", TITLE_USER),
    ]
)


# ChatPromptTemplate treats `{...}` as variable interpolation, so escape
# the JSON example in the system prompt by doubling the braces.
_HIERARCHY_SYSTEM_ESCAPED = HIERARCHY_SYSTEM.replace("{", "{{").replace("}", "}}")

HIERARCHY_PROMPT = ChatPromptTemplate.from_messages(
    [
        ("system", _HIERARCHY_SYSTEM_ESCAPED),
        ("user", HIERARCHY_USER),
    ]
)
