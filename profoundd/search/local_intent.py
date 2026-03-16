"""
Local intent detection for Profoundd search.

Determines whether a user's search query is looking for local businesses
(e.g., "pizza grants pass", "plumber near me", "best dentist in portland").
"""
import re

# Business-type keywords that signal local intent.
# These are common search terms people use when looking for local businesses.
LOCAL_BUSINESS_KEYWORDS = {
    # Food & Drink
    "restaurant", "restaurants", "pizza", "sushi", "tacos", "burgers", "bbq",
    "cafe", "coffee", "coffee shop", "bakery", "bar", "pub", "brewery",
    "winery", "diner", "buffet", "food", "takeout", "delivery", "catering",
    "brunch", "breakfast", "lunch", "dinner", "thai", "chinese", "mexican",
    "italian", "japanese", "indian", "korean", "vietnamese", "greek",
    "mediterranean", "fast food", "ice cream", "donut", "bagel", "deli",
    "steakhouse", "seafood", "vegan", "vegetarian",
    # Health
    "doctor", "dentist", "pharmacy", "hospital", "clinic", "urgent care",
    "optometrist", "optician", "chiropractor", "therapist", "counselor",
    "psychiatrist", "dermatologist", "pediatrician", "veterinarian", "vet",
    "physical therapy", "massage", "acupuncture",
    # Services
    "bank", "atm", "post office", "laundromat", "laundry", "dry cleaner",
    "hair salon", "haircut", "barber", "barbershop", "nail salon", "spa",
    "tattoo", "gym", "fitness", "yoga",
    # Automotive
    "gas station", "gas", "mechanic", "auto repair", "car wash", "tire shop",
    "oil change", "auto parts", "car dealer", "ev charging",
    # Shopping
    "grocery", "supermarket", "hardware store", "electronics", "clothing",
    "shoes", "jewelry", "pet store", "florist", "bookstore", "thrift store",
    "furniture", "appliance", "garden center", "pawn shop",
    # Professional
    "lawyer", "attorney", "accountant", "real estate", "insurance",
    "tax preparer", "notary", "architect",
    # Craft / Trade
    "plumber", "plumbing", "electrician", "carpenter", "roofer", "painter",
    "landscaper", "landscaping", "hvac", "locksmith", "handyman",
    "contractor", "cleaning service",
    # Lodging
    "hotel", "motel", "hostel", "inn", "lodge", "campground", "rv park",
    "bed and breakfast", "airbnb",
    # Recreation
    "cinema", "movie theater", "theatre", "bowling", "arcade",
    "swimming pool", "golf", "park",
    # Other
    "storage", "moving", "printing", "copy shop", "daycare",
    "school", "tutor", "driving school",
}

# Oregon cities (population > ~5000 or notable)
OREGON_CITIES = {
    "portland", "eugene", "salem", "gresham", "hillsboro", "bend",
    "beaverton", "medford", "springfield", "corvallis", "albany",
    "lake oswego", "tigard", "tualatin", "west linn", "oregon city",
    "woodburn", "grants pass", "mcminnville", "redmond", "roseburg",
    "ashland", "klamath falls", "central point", "newberg", "coos bay",
    "hermiston", "pendleton", "the dalles", "hood river", "florence",
    "cottage grove", "canby", "silverton", "dallas", "stayton",
    "happy valley", "milwaukie", "clackamas", "sherwood", "wilsonville",
    "sandy", "estacada", "molalla", "troutdale", "fairview",
    "ontario", "la grande", "baker city", "john day", "burns",
    "madras", "prineville", "sisters", "sunriver", "tillamook",
    "astoria", "seaside", "cannon beach", "lincoln city", "newport",
    "bandon", "brookings", "gold beach", "cave junction", "jacksonville",
    "rogue river", "eagle point", "shady cove", "white city", "talent",
    "phoenix",  # Phoenix, OR — small town near Medford
}

# Arizona cities (population > ~10000 or notable)
ARIZONA_CITIES = {
    "phoenix", "tucson", "mesa", "chandler", "scottsdale", "glendale",
    "tempe", "gilbert", "peoria", "surprise", "yuma", "avondale",
    "goodyear", "flagstaff", "buckeye", "casa grande", "lake havasu city",
    "maricopa", "sierra vista", "prescott", "prescott valley",
    "bullhead city", "apache junction", "queen creek", "san tan valley",
    "kingman", "florence", "sedona", "cottonwood", "camp verde",
    "payson", "show low", "pinetop-lakeside", "page", "nogales",
    "douglas", "safford", "globe", "willcox", "bisbee", "tombstone",
    "oro valley", "sahuarita", "green valley", "tubac",
}

# Combined set for matching — lowercased
KNOWN_CITIES = OREGON_CITIES | ARIZONA_CITIES

# Patterns that signal local intent even without a business keyword
NEAR_PATTERNS = re.compile(
    r'\b(near\s+me|nearby|close\s+by|around\s+here|in\s+my\s+area)\b',
    re.IGNORECASE,
)

# Pattern: "in <city>" or "near <city>"
IN_CITY_PATTERN = re.compile(
    r'\b(?:in|near|around)\s+([a-z][\w\s]{2,30})\s*$',
    re.IGNORECASE,
)


def detect_local_intent(query):
    """Analyze a search query for local business intent.

    Returns:
        dict with keys:
            is_local (bool): Whether the query has local intent
            city (str|None): Detected city name, or None
            business_type (str|None): Matched business keyword, or None
            clean_query (str): Query with city name stripped for better ES matching
    """
    query_lower = query.lower().strip()
    result = {
        "is_local": False,
        "city": None,
        "business_type": None,
        "clean_query": query,
    }

    if not query_lower or len(query_lower) < 3:
        return result

    # Check for "near me" patterns
    has_near_me = bool(NEAR_PATTERNS.search(query_lower))

    # Check for city name in query
    detected_city = None
    for city in sorted(KNOWN_CITIES, key=len, reverse=True):
        # Match whole words only (avoid "bend" matching in "bending")
        pattern = r'\b' + re.escape(city) + r'\b'
        if re.search(pattern, query_lower):
            detected_city = city.title()
            # Strip city from query for cleaner ES matching
            result["clean_query"] = re.sub(pattern, "", query_lower).strip()
            # Also strip prepositions left behind: "pizza in" → "pizza"
            result["clean_query"] = re.sub(
                r'\s+(?:in|near|around|at)\s*$', '', result["clean_query"]
            ).strip()
            if not result["clean_query"]:
                result["clean_query"] = query
            break

    # Check for business keywords
    detected_type = None
    for keyword in sorted(LOCAL_BUSINESS_KEYWORDS, key=len, reverse=True):
        pattern = r'\b' + re.escape(keyword) + r'\b'
        if re.search(pattern, query_lower):
            detected_type = keyword
            break

    # Determine if this is a local query
    if detected_type and (detected_city or has_near_me):
        result["is_local"] = True
    elif detected_type and not detected_city:
        # Business keyword alone (e.g., "plumber", "pizza") — still local
        result["is_local"] = True
    elif detected_city and has_near_me:
        result["is_local"] = True
    elif has_near_me:
        result["is_local"] = True

    result["city"] = detected_city
    result["business_type"] = detected_type

    return result
