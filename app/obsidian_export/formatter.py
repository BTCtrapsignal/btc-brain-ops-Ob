"""
obsidian_export/formatter.py

Converts the standard weekly markdown export into Obsidian-compatible format.

Adds:
  - YAML frontmatter (tags, week, generated_at)
  - [[wiki links]] to BTC-Brain intelligence nodes
  - Backlink section for Obsidian graph view
"""

from datetime import datetime


# Map of terms → Obsidian wiki links
# Add more as BTC-Brain wiki grows
WIKI_LINK_MAP = {
    "false recovery": "[[false_recovery]]",
    "false_recovery": "[[false_recovery]]",
    "participation intelligence": "[[participation_intelligence]]",
    "continuation failures": "[[continuation_failures]]",
    "signal decay": "[[signal_decay]]",
    "transition regime": "[[transition_regimes]]",
    "unstable transition": "[[transition_regimes]]",
    "unstable_transition": "[[transition_regimes]]",
    "liquidity trap": "[[liquidity_traps]]",
    "trapped continuation": "[[liquidity_traps]]",
    "healthy continuation": "[[continuation_state_engine]]",
    "weakening continuation": "[[continuation_state_engine]]",
    "decaying continuation": "[[continuation_state_engine]]",
    "exhausted continuation": "[[continuation_state_engine]]",
    "recovering continuation": "[[continuation_state_engine]]",
    "OI expansion": "[[participation_intelligence]]",
    "neutral OI": "[[participation_intelligence]]",
    "regime": "[[MARKET_REGIME]]",
    "W19 doctrine": "[[W19_core_lesson]]",
}

# Backlinks to always append (connecting to BTC-Brain wiki graph)
STANDARD_BACKLINKS = [
    "[[weekly_doctrine]]",
    "[[participation_intelligence]]",
    "[[continuation_state_engine]]",
    "[[signal_decay]]",
    "[[MARKET_REGIME]]",
    "[[performance]]",
    "[[mistakes]]",
]


def format_for_obsidian(week: str, markdown: str) -> str:
    """
    Take the standard weekly markdown and produce an Obsidian-compatible version.
    """
    frontmatter = _build_frontmatter(week)
    body = _inject_wiki_links(markdown)
    backlinks = _build_backlinks(week)

    return f"{frontmatter}\n\n{body}\n\n{backlinks}"


def _build_frontmatter(week: str) -> str:
    now = datetime.utcnow().strftime("%Y-%m-%d")
    return f"""---
week: {week}
type: weekly-intelligence-report
source: btc-brain-ops
generated: {now}
tags:
  - btc-brain
  - weekly-report
  - continuation-intelligence
  - {week.lower()}
---"""


def _inject_wiki_links(markdown: str) -> str:
    """
    Replace plain terms with Obsidian [[wiki links]] where appropriate.
    Conservative: only replaces exact term matches in prose lines.
    Does NOT modify code blocks, tables, or headings.
    """
    lines = markdown.split("\n")
    result = []
    in_code_block = False

    for line in lines:
        # Track code block boundaries
        if line.strip().startswith("```"):
            in_code_block = not in_code_block
            result.append(line)
            continue

        # Don't modify code blocks, headings, or table rows
        if in_code_block or line.startswith("#") or line.startswith("|"):
            result.append(line)
            continue

        # Replace terms with wiki links (case-insensitive match, replace once)
        modified = line
        for term, link in WIKI_LINK_MAP.items():
            if term in modified and link not in modified:
                modified = modified.replace(term, link, 1)

        result.append(modified)

    return "\n".join(result)


def _build_backlinks(week: str) -> str:
    links = STANDARD_BACKLINKS.copy()
    # Add week-specific backlink
    week_link = f"[[Week-{week}-2026]]"
    if week_link not in links:
        links.insert(0, week_link)

    return "\n".join(links)
