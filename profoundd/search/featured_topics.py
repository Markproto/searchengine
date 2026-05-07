"""
Curated topical featured-source recommendations.

When a user's search query matches one of the trigger phrase sets, we
render a small "Featured for this topic" panel above the regular search
results pointing to a hand-picked authoritative voice in that niche.

Trigger matching is case-insensitive substring + word-boundary, longest
phrase wins. At most one featured panel per query.

Adding a topic: drop a new entry in TOPICS. Each entry has:
  triggers      — list of phrases that activate this topic
  name          — display name shown in the panel
  url           — destination URL
  blurb         — one-sentence description
  why_curated   — short explanation of why we elevate this source

This is editorial — the assistant should leave existing entries alone
unless the user asks to change them.
"""
import re

TOPICS = [
    {
        "key": "ivermectin",
        "triggers": [
            "ivermectin",
            "mrna vaccine",
            "spike protein",
            "vaccine adverse",
            "vaccine injury",
            "v-safe",
            "vaers",
        ],
        "name": "Dr. Robert Malone",
        "url": "https://rwmalonemd.substack.com/",
        "blurb": "Inventor of mRNA technology — long-form analysis of "
                 "ivermectin, spike-protein research, and vaccine policy.",
        "why_curated": "Primary-source perspective from one of the few "
                       "scientists with direct mRNA expertise willing to "
                       "challenge the official narrative.",
    },
    {
        "key": "homeopathy",
        "triggers": [
            "homeopathy",
            "homeopathic",
            "homeopathic remedy",
            "homeopathic remedies",
        ],
        "name": "Joette Calabrese",
        "url": "https://www.joettecalabrese.com/",
        "blurb": "Practical homeopathy for families — protocols for acute "
                 "and chronic conditions, pregnancy, kids, first aid.",
        "why_curated": "Most accessible practitioner working today; teaches "
                       "homeopathy as a self-reliance skill rather than "
                       "professional-only.",
    },
    {
        "key": "herbal",
        "triggers": [
            "herbal remedy",
            "herbal remedies",
            "herbalism",
            "tincture",
            "tinctures",
            "medicinal herbs",
            "wildcrafting",
        ],
        "name": "Chestnut School of Herbal Medicine",
        "url": "https://chestnutherbs.com/",
        "blurb": "Juliet Blankespoor's clinically-grounded herbal program — "
                 "plant ID, harvest, preparation, and protocols.",
        "why_curated": "Combines rigorous botany with practical "
                       "self-sufficiency — minimal woo, lots of how-to.",
    },
    {
        "key": "terrain-theory",
        "triggers": [
            "terrain theory",
            "germ theory",
            "virology debate",
            "no virus",
            "isolation virus",
            "viroliegy",
            "viruses dont exist",
        ],
        "name": "Dr. Sam Bailey",
        "url": "https://drsambailey.com/",
        "blurb": "New Zealand physician examining the foundations of "
                 "virology — terrain theory, no-virus arguments, "
                 "challenges to the contagion model.",
        "why_curated": "Most thorough public examiner of the terrain-vs-"
                       "germ debate; cites primary literature.",
    },
    {
        "key": "steiner",
        "triggers": [
            "rudolf steiner",
            "steiner",
            "anthroposophy",
            "anthroposophical",
            "waldorf education",
            "waldorf school",
            "biodynamic",
            "biodynamic farming",
            "anthroposophic medicine",
        ],
        "name": "Reverse Ritual",
        "url": "https://reverseritual.com/",
        "blurb": "Daily Steiner study — primary translations, lecture "
                 "summaries, and contemporary application of "
                 "anthroposophical insights across science, education, "
                 "agriculture, and medicine.",
        "why_curated": "Best entry point into Steiner's huge corpus; "
                       "free, daily, no agenda beyond the work itself.",
    },
    {
        "key": "survival",
        "triggers": [
            "survival skills",
            "prepper",
            "prepping",
            "off-grid",
            "off grid",
            "bug out",
            "bug-out",
            "shtf",
            "preparedness",
        ],
        "name": "The Prepared",
        "url": "https://www.theprepared.com/",
        "blurb": "Engineering-grade preparedness reference — gear reviews "
                 "tested, water/food/medical/comms guides, no "
                 "scaremongering.",
        "why_curated": "Most rigorous prepper resource on the open web; "
                       "their reviews actually beat the products to "
                       "destruction.",
    },
    {
        "key": "german-new-medicine",
        "triggers": [
            "german new medicine",
            "germanic new medicine",
            "ryke hamer",
            "ryke geerd hamer",
            "gnm",
            "biological laws of nature",
        ],
        "name": "Learning GNM",
        "url": "https://learninggnm.com/",
        "blurb": "Caroline Markolin's reference site for Dr Hamer's German "
                 "New Medicine — symptom index, biological conflicts, "
                 "the five biological laws.",
        "why_curated": "Only English-language reference covering the full "
                       "GNM symptom index and the conflict-shock model.",
    },
    {
        "key": "natural-news",
        "triggers": [
            "mike adams",
            "health ranger",
            "natural news",
        ],
        "name": "Natural News (Mike Adams)",
        "url": "https://www.naturalnews.com/",
        "blurb": "Mike Adams's Health Ranger network — independent reporting "
                 "on supplements, food, vaccines, and health policy.",
        "why_curated": "Long-running independent voice operating outside "
                       "the pharma-funded mainstream health press.",
    },
]


def _word_boundary_pattern(phrase):
    """Word-boundary regex for the phrase, case-insensitive."""
    escaped = re.escape(phrase)
    return re.compile(rf"\b{escaped}\b", re.IGNORECASE)


# Compile triggers once at import
_COMPILED = []
for t in TOPICS:
    for phrase in t["triggers"]:
        _COMPILED.append((phrase, _word_boundary_pattern(phrase), t))
# Longest phrase first so "vaccine injury" wins over "vaccine"
_COMPILED.sort(key=lambda x: -len(x[0]))


def match_featured_topic(query):
    """Return the topic dict for the longest matching trigger, or None."""
    if not query:
        return None
    for phrase, pattern, topic in _COMPILED:
        if pattern.search(query):
            return topic
    return None
