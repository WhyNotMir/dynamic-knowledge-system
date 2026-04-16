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