"""Prompt templates shared across agents.

Each file here owns the templates for one agent. Templates are
``ChatPromptTemplate`` instances (LangChain) or raw strings — pick the
form that matches the consuming agent. Kept separate from the chain
wiring so non-LLM code (tests, eval tooling) can import the templates
without pulling LangChain into its import graph.
"""

__all__: list[str] = []
