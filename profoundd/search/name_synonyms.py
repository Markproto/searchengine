"""
Query-time first-name nickname expansion for the main search.

When a user searches "Matt Gaetz" we also want to match articles that say
"Matthew Gaetz", and vice versa. This rewrites the query so that recognized
first-name tokens become an OR-group of all common forms of that name.

Rules applied in engine.search():
  - Token is expanded only if its first letter is uppercase in the raw query
    (so "bill" the legislation is not expanded, but "Bill Gates" is).
  - Expansion uses simple_query_string OR syntax: `(Matt | Matthew | Matty)`.
  - Tokens inside double quotes are left alone (exact-phrase wins).
  - Boolean operators (AND, OR, NOT) are passed through unchanged.
  - At most 3 expansions per query to keep the recall blast radius sane.
"""

# Each canonical full name maps to all its common forms (including itself).
# Keep entries lowercase; matching is case-insensitive but expansion-eligibility
# requires the first character of the user's token to be uppercase.
_NAME_GROUPS = [
    {"matthew", "matt", "matty"},
    {"robert", "rob", "bob", "bobby", "robbie"},
    {"william", "will", "bill", "billy", "willie"},
    {"michael", "mike", "mikey", "mick"},
    {"david", "dave", "davey"},
    {"joseph", "joe", "joey"},
    {"thomas", "tom", "tommy"},
    {"james", "jim", "jimmy", "jamie", "jim"},
    {"richard", "rick", "ricky", "dick", "rich"},
    {"charles", "charlie", "chuck", "chas"},
    {"anthony", "tony"},
    {"stephen", "steven", "steve", "stevie"},
    {"peter", "pete", "petey"},
    {"daniel", "dan", "danny"},
    {"donald", "don", "donnie"},
    {"edward", "ed", "eddie", "eddy", "ned", "ted", "teddy"},
    {"theodore", "theo", "ted", "teddy"},
    {"francis", "frank", "frankie"},
    {"john", "johnny", "jack", "jonathan", "jon"},
    {"jonathan", "jon", "jonny"},
    {"kenneth", "ken", "kenny"},
    {"lawrence", "laurence", "larry", "lawrie"},
    {"nicholas", "nick", "nicky"},
    {"philip", "phillip", "phil"},
    {"ronald", "ron", "ronnie"},
    {"samuel", "sam", "sammy"},
    {"timothy", "tim", "timmy"},
    {"walter", "walt", "wally"},
    {"andrew", "andy", "drew"},
    {"alexander", "alex", "al"},
    {"benjamin", "ben", "benny"},
    {"gregory", "greg"},
    {"jeffrey", "geoffrey", "jeff", "geoff"},
    {"joshua", "josh"},
    {"nathan", "nathaniel", "nate"},
    {"patrick", "pat", "patty"},
    {"christopher", "chris"},
    # Female
    {"elizabeth", "liz", "lizzie", "beth", "betty", "betsy", "eliza"},
    {"katherine", "catherine", "kate", "katie", "kathy", "cathy", "kat"},
    {"margaret", "maggie", "meg", "peggy", "marge"},
    {"susan", "sue", "susie", "suzy"},
    {"cynthia", "cindy"},
    {"jennifer", "jen", "jenny"},
    {"patricia", "trish", "pat", "patty", "patti"},
    {"christina", "christine", "chris", "christy", "tina"},
    {"alison", "allison", "ali", "ally"},
    {"sarah", "sara", "sally"},
    {"rebecca", "becky", "becca"},
    {"deborah", "debbie", "deb"},
    {"kimberly", "kim"},
    {"barbara", "barb", "babs"},
    {"victoria", "vicki", "vicky", "tori"},
]

# Build lookup: any form (lowercase) -> set of all forms in the group.
_FORM_TO_GROUP = {}
for group in _NAME_GROUPS:
    for form in group:
        existing = _FORM_TO_GROUP.get(form, set())
        _FORM_TO_GROUP[form] = existing | group

# Maximum expansions per query so we don't OR-explode recall.
MAX_EXPANSIONS = 3


def _is_capitalized(token):
    """Token starts with an uppercase letter — heuristic for proper noun."""
    return bool(token) and token[0].isalpha() and token[0].isupper()


def expand_names(query):
    """Rewrite query, expanding capitalized nickname tokens to OR-groups.

    Returns (expanded_query, expansions) where expansions is a list of
    {"original": "Matt", "forms": ["matthew", "matt", "matty"]} for UI display.
    Returns (query, []) when nothing to expand.
    """
    if not query or not query.strip():
        return query, []

    # Skip expansion entirely when user is using boolean syntax we'd risk breaking,
    # other than at the top level: phrases, parentheses we didn't add ourselves.
    # We still tokenize whitespace and skip any token inside a quoted phrase.
    parts = query.split()
    inside_quote = False
    expansions = []
    out = []

    for token in parts:
        # Track quoted regions — a token containing an odd number of quotes
        # toggles the inside-quote state.
        quote_count = token.count('"')
        token_starts_inside = inside_quote
        if quote_count % 2 == 1:
            inside_quote = not inside_quote

        if token_starts_inside or inside_quote:
            out.append(token)
            continue

        # Skip operators
        if token in ("AND", "OR", "NOT", "|", "+", "-"):
            out.append(token)
            continue

        # Strip leading/trailing punctuation we want to preserve
        prefix = ""
        suffix = ""
        core = token
        # Common leading: ( + -
        while core and core[0] in "(+-":
            prefix += core[0]
            core = core[1:]
        while core and core[-1] in "),.;:!?":
            suffix = core[-1] + suffix
            core = core[:-1]

        if not core or not core.isalpha():
            out.append(token)
            continue

        if not _is_capitalized(core):
            out.append(token)
            continue

        forms = _FORM_TO_GROUP.get(core.lower())
        if not forms or len(expansions) >= MAX_EXPANSIONS:
            out.append(token)
            continue

        # Build OR-group with deterministic ordering
        ordered = sorted(forms)
        or_group = "(" + " | ".join(ordered) + ")"
        out.append(f"{prefix}{or_group}{suffix}")
        expansions.append({"original": core, "forms": ordered})

    if not expansions:
        return query, []
    return " ".join(out), expansions
