"""
Principal alias expansion for the Epstein document search.

Bates documents and exhibits routinely refer to principals by initials,
honorifics, or shorthand ("JE" for Jeffrey Epstein, "GM" for Ghislaine
Maxwell, "HRH" for Prince Andrew, "DJT" for Donald Trump). The DOJ search
treats these as unrelated tokens. We expand recognized principal tokens
into an OR-group covering all known aliases so a search for "Maxwell"
also surfaces docs that only say "GM" or "Ghislaine".

Rules applied in engine.search_epstein_docs():
  - Token is matched case-insensitively against the alias map
  - Multi-word aliases (e.g. "prince andrew") are matched as phrases
  - Expansion uses simple_query_string OR syntax with quoted phrases
  - Quoted regions in the user query are passed through untouched
  - Cap MAX_EXPANSIONS per query so recall doesn't explode

Add new principals as the corpus reveals them — keep aliases factual
(documented usage in the released files), not speculative.
"""

# Each canonical principal maps to a list of forms found in the documents.
# All forms must be lowercase. Multi-word forms are matched as phrases.
PRINCIPAL_ALIASES = {
    "jeffrey epstein": [
        "jeffrey epstein",
        "jeff epstein",
        "epstein",
        "mr. epstein",
        "mr epstein",
        "je",
        "jeffrey e. epstein",
    ],
    "ghislaine maxwell": [
        "ghislaine maxwell",
        "ghislaine",
        "maxwell",
        "ms. maxwell",
        "ms maxwell",
        "gm",
        "ghislaine n. maxwell",
        "ghislaine noelle maxwell",
    ],
    "donald trump": [
        "donald trump",
        "donald j. trump",
        "donald j trump",
        "djt",
        "president trump",
        "mr. trump",
        "mr trump",
        "trump",
    ],
    "bill clinton": [
        "bill clinton",
        "william clinton",
        "william j. clinton",
        "wjc",
        "president clinton",
        "mr. clinton",
        "mr clinton",
    ],
    "hillary clinton": [
        "hillary clinton",
        "hillary rodham clinton",
        "hrc",
        "secretary clinton",
        "mrs. clinton",
        "mrs clinton",
    ],
    "prince andrew": [
        "prince andrew",
        "andrew windsor",
        "duke of york",
        "hrh prince andrew",
        "hrh",
        "andrew albert christian edward",
    ],
    "alan dershowitz": [
        "alan dershowitz",
        "dershowitz",
        "alan m. dershowitz",
        "professor dershowitz",
        "mr. dershowitz",
    ],
    "les wexner": [
        "les wexner",
        "leslie wexner",
        "leslie h. wexner",
        "wexner",
        "lhw",
        "mr. wexner",
    ],
    "bill gates": [
        "bill gates",
        "william gates",
        "william h. gates",
        "mr. gates",
    ],
    "larry summers": [
        "larry summers",
        "lawrence summers",
        "lawrence h. summers",
        "summers",
    ],
    "leon black": [
        "leon black",
        "leon d. black",
        "mr. black",
    ],
    "glenn dubin": [
        "glenn dubin",
        "glenn r. dubin",
        "dubin",
    ],
    "virginia giuffre": [
        "virginia giuffre",
        "virginia roberts giuffre",
        "virginia roberts",
        "v. giuffre",
        "v giuffre",
        "vrg",
        "ms. giuffre",
    ],
    "jean-luc brunel": [
        "jean-luc brunel",
        "jean luc brunel",
        "brunel",
        "jl brunel",
    ],
    "sarah kellen": [
        "sarah kellen",
        "sarah kellen vickers",
        "sarah vickers",
        "kellen",
    ],
    "nadia marcinkova": [
        "nadia marcinkova",
        "nadia marcinko",
        "marcinkova",
        "marcinko",
    ],
    "jes staley": [
        "jes staley",
        "james staley",
        "james e. staley",
        "staley",
    ],
    "steven hoffenberg": [
        "steven hoffenberg",
        "steve hoffenberg",
        "hoffenberg",
    ],
    "noel rocknroll": [
        # Maxwell's nephew/in-law occasionally referenced in flight logs
        "noel rocknroll",
    ],
    "ehud barak": [
        "ehud barak",
        "barak",
        "prime minister barak",
    ],
    "jean luc brunel": [
        "jean luc brunel",
        "jean-luc brunel",
        "brunel",
    ],
    # Locations + properties (frequently searched, often nicknamed)
    "little saint james": [
        "little saint james",
        "little st. james",
        "little st james",
        "lsj",
        "epstein island",
        "orgy island",
    ],
    "great saint james": [
        "great saint james",
        "great st. james",
        "great st james",
    ],
    "zorro ranch": [
        "zorro ranch",
        "stanley ranch",
    ],
    "lolita express": [
        "lolita express",
        "n908je",
        "n908 je",
        "boeing 727",
    ],
}

# Build a flat lookup: any form -> list of all forms in its principal group.
# Forms are lowercased. We index by exact form (single or multi-word).
_FORM_TO_GROUP = {}
for canonical, forms in PRINCIPAL_ALIASES.items():
    group = list(dict.fromkeys(forms))  # dedupe, preserve order
    for form in forms:
        _FORM_TO_GROUP[form.lower()] = group

# Sorted by length descending so multi-word phrases match before single words.
_SORTED_FORMS = sorted(_FORM_TO_GROUP.keys(), key=len, reverse=True)

MAX_EXPANSIONS = 3


def _quote(form):
    """Wrap multi-word forms in quotes for simple_query_string."""
    if " " in form or "-" in form or "." in form:
        return f'"{form}"'
    return form


def expand_aliases(query):
    """Rewrite query, expanding recognized principals into OR-groups.

    Returns (expanded_query, expansions) where expansions is a list of
    {"original": "...", "canonical": "...", "forms": [...]} for UI display.
    """
    if not query or not query.strip():
        return query, []

    # Walk the query left-to-right, finding the longest alias match at each
    # position. Skip regions inside double quotes — the user wants exact match.
    expansions = []
    out = []
    i = 0
    low = query.lower()
    n = len(query)
    inside_quote = False

    while i < n:
        ch = query[i]
        if ch == '"':
            inside_quote = not inside_quote
            out.append(ch)
            i += 1
            continue
        if inside_quote:
            out.append(ch)
            i += 1
            continue

        # Try to match an alias starting at position i. We require the match
        # to be on a word boundary (start-of-string or non-alphanumeric before).
        if i > 0 and (query[i - 1].isalnum() or query[i - 1] == "."):
            out.append(ch)
            i += 1
            continue

        matched = None
        if len(expansions) < MAX_EXPANSIONS:
            for form in _SORTED_FORMS:
                end = i + len(form)
                if end > n:
                    continue
                if low[i:end] != form:
                    continue
                # Ensure word boundary at end
                if end < n and (query[end].isalnum() or query[end] == "."):
                    continue
                matched = form
                break

        if matched is None:
            out.append(ch)
            i += 1
            continue

        group = _FORM_TO_GROUP[matched]
        # Find canonical (the first key whose forms include matched)
        canonical = matched
        for k, v in PRINCIPAL_ALIASES.items():
            if matched in (f.lower() for f in v):
                canonical = k
                break
        or_group = "(" + " | ".join(_quote(f) for f in group) + ")"
        out.append(or_group)
        expansions.append({
            "original": query[i:i + len(matched)],
            "canonical": canonical,
            "forms": group,
        })
        i += len(matched)

    expanded = "".join(out)
    return expanded, expansions
