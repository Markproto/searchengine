"""
Query-time synonym expansion for climate/planning doc searches.

Some users search by the ideological label (e.g. "Agenda 2030", "15-minute city")
while the underlying planning documents use the bureaucratic terms ("climate action",
"climate-friendly area", "greenhouse gas"). This maps common labels to the
actual language that appears in the corpus so the search returns real hits.

Rules applied at query time in engine.search_climate_docs:
  - case-insensitive substring match on the raw query
  - longest matching phrase wins (so "15-minute city" beats "15-minute")
  - the matched phrase is replaced with an OR-group of its synonyms wrapped
    in parens, preserving AND semantics against any other query terms
  - at most one expansion per query (simple, predictable UX)

Synonym targets only include terms that actually appear in the climate_docs
index as of 2026-04-24 (verified with _count queries). Add more as the
corpus grows.
"""

# (trigger phrase lower-cased) -> list of synonyms to OR together.
# Multi-word synonyms must be quoted for simple_query_string.
SYNONYMS = {
    "agenda 2030": [
        '"climate action"',
        '"greenhouse gas"',
        '"climate-friendly area"',
        '"carbon neutral"',
        '"carbon neutrality"',
        "GHG",
    ],
    "agenda2030": [
        '"climate action"',
        '"greenhouse gas"',
        '"climate-friendly area"',
        '"carbon neutral"',
        '"carbon neutrality"',
        "GHG",
    ],
    "un agenda 2030": [
        '"climate action"',
        '"greenhouse gas"',
        '"climate-friendly area"',
        '"carbon neutral"',
        '"carbon neutrality"',
    ],
    "sustainable development goals": [
        '"climate action"',
        '"sustainable development"',
        '"greenhouse gas"',
        '"climate-friendly area"',
        '"equity"',
        '"equitable"',
    ],
    "sdg": [
        '"climate action"',
        '"sustainable development"',
        '"climate-friendly area"',
        '"equity"',
    ],
    "15-minute city": [
        '"climate-friendly area"',
        '"mixed use"',
        '"mixed-use"',
        '"transit oriented"',
        '"transit-oriented"',
        "walkable",
        '"urban growth boundary"',
        "UGB",
    ],
    "15 minute city": [
        '"climate-friendly area"',
        '"mixed use"',
        '"mixed-use"',
        '"transit oriented"',
        '"transit-oriented"',
        "walkable",
    ],
    "15-minute cities": [
        '"climate-friendly area"',
        '"mixed use"',
        '"mixed-use"',
        '"transit oriented"',
        '"transit-oriented"',
        "walkable",
    ],
    "smart growth": [
        '"climate-friendly area"',
        '"mixed use"',
        '"urban growth boundary"',
        "UGB",
        '"density"',
    ],
    "climate change": [
        '"climate action"',
        '"greenhouse gas"',
        "GHG",
        '"carbon neutral"',
        '"climate-friendly area"',
    ],
}


def expand_query(query):
    """Return (expanded_query, expansion_note or None).

    expansion_note is a dict like {"phrase": "agenda 2030", "synonyms": ["...", "..."]}
    so the template can tell the user what we substituted.
    """
    if not query:
        return query, None

    low = query.lower()
    # Longest phrase first so "15-minute city" matches before "agenda 2030" etc.
    triggers = sorted(SYNONYMS.keys(), key=len, reverse=True)
    for phrase in triggers:
        idx = low.find(phrase)
        if idx < 0:
            continue
        syns = SYNONYMS[phrase]
        # simple_query_string uses `|` as OR (the word OR is treated as a term)
        or_group = "(" + " | ".join(syns) + ")"
        # Rebuild query preserving non-matched portions (case-insensitive)
        before = query[:idx].strip()
        after = query[idx + len(phrase):].strip()
        parts = [p for p in (before, or_group, after) if p]
        expanded = " ".join(parts)
        return expanded, {"phrase": phrase, "synonyms": syns}

    return query, None
