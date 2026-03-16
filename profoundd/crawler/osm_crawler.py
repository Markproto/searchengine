"""
OpenStreetMap business data crawler for Profoundd.

Downloads Geofabrik .osm.pbf extracts and parses business POIs
(shops, amenities, offices, crafts, tourism, healthcare) into
Elasticsearch-ready documents.

To add a new state, just add an entry to SUPPORTED_STATES below.
"""
import hashlib
import logging
import os
from datetime import datetime, timezone

import osmium
import requests

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------ #
#  State Configuration — add new states here                          #
# ------------------------------------------------------------------ #

SUPPORTED_STATES = {
    "oregon": {
        "url": "https://download.geofabrik.de/north-america/us/oregon-latest.osm.pbf",
        "filename": "oregon-latest.osm.pbf",
        "abbrev": "OR",
        "label": "Oregon",
    },
    "arizona": {
        "url": "https://download.geofabrik.de/north-america/us/arizona-latest.osm.pbf",
        "filename": "arizona-latest.osm.pbf",
        "abbrev": "AZ",
        "label": "Arizona",
    },
}

# ------------------------------------------------------------------ #
#  OSM tag → business category mapping                                #
# ------------------------------------------------------------------ #

# Maps OSM tag values to (business_category, display_type) tuples.
# The display_type is a human-readable label for the UI.

AMENITY_MAP = {
    # Food & Drink
    "restaurant": ("food", "Restaurant"),
    "cafe": ("food", "Cafe"),
    "fast_food": ("food", "Fast Food"),
    "bar": ("food", "Bar"),
    "pub": ("food", "Pub"),
    "biergarten": ("food", "Beer Garden"),
    "ice_cream": ("food", "Ice Cream"),
    "food_court": ("food", "Food Court"),
    # Financial
    "bank": ("services", "Bank"),
    "atm": ("services", "ATM"),
    "bureau_de_change": ("services", "Currency Exchange"),
    # Health
    "pharmacy": ("health", "Pharmacy"),
    "hospital": ("health", "Hospital"),
    "clinic": ("health", "Clinic"),
    "dentist": ("health", "Dentist"),
    "doctors": ("health", "Doctor"),
    "veterinary": ("health", "Veterinarian"),
    # Education
    "school": ("education", "School"),
    "university": ("education", "University"),
    "college": ("education", "College"),
    "library": ("education", "Library"),
    "kindergarten": ("education", "Kindergarten"),
    "language_school": ("education", "Language School"),
    "driving_school": ("education", "Driving School"),
    "music_school": ("education", "Music School"),
    # Automotive
    "fuel": ("automotive", "Gas Station"),
    "car_wash": ("automotive", "Car Wash"),
    "car_rental": ("automotive", "Car Rental"),
    "charging_station": ("automotive", "EV Charging"),
    # Services
    "post_office": ("services", "Post Office"),
    "laundry": ("services", "Laundry"),
    "dry_cleaning": ("services", "Dry Cleaning"),
    "storage_rental": ("services", "Storage"),
    # Government
    "townhall": ("government", "Town Hall"),
    "courthouse": ("government", "Courthouse"),
    "police": ("government", "Police"),
    "fire_station": ("government", "Fire Station"),
    # Recreation
    "cinema": ("recreation", "Cinema"),
    "theatre": ("recreation", "Theatre"),
    "nightclub": ("recreation", "Nightclub"),
    "casino": ("recreation", "Casino"),
    "gym": ("recreation", "Gym"),
    "swimming_pool": ("recreation", "Swimming Pool"),
    "community_centre": ("recreation", "Community Center"),
    "arts_centre": ("recreation", "Arts Center"),
}

SHOP_MAP = {
    # Food retail
    "supermarket": ("shopping", "Supermarket"),
    "convenience": ("shopping", "Convenience Store"),
    "bakery": ("food", "Bakery"),
    "butcher": ("shopping", "Butcher"),
    "greengrocer": ("shopping", "Produce"),
    "deli": ("food", "Deli"),
    "seafood": ("shopping", "Seafood"),
    "alcohol": ("shopping", "Liquor Store"),
    "wine": ("shopping", "Wine Shop"),
    "beverages": ("shopping", "Beverage Store"),
    "coffee": ("food", "Coffee Shop"),
    "tea": ("shopping", "Tea Shop"),
    "confectionery": ("shopping", "Candy Shop"),
    "health_food": ("shopping", "Health Food"),
    "farm": ("shopping", "Farm Stand"),
    "cheese": ("shopping", "Cheese Shop"),
    "pastry": ("food", "Pastry Shop"),
    # General
    "department_store": ("shopping", "Department Store"),
    "general": ("shopping", "General Store"),
    "mall": ("shopping", "Shopping Mall"),
    "wholesale": ("shopping", "Wholesale"),
    "variety_store": ("shopping", "Variety Store"),
    # Clothing
    "clothes": ("shopping", "Clothing"),
    "shoes": ("shopping", "Shoe Store"),
    "jewelry": ("shopping", "Jewelry"),
    "watches": ("shopping", "Watch Shop"),
    "bag": ("shopping", "Bags"),
    "leather": ("shopping", "Leather Goods"),
    "fabric": ("shopping", "Fabric Store"),
    "tailor": ("craft", "Tailor"),
    # Beauty
    "hairdresser": ("services", "Hair Salon"),
    "beauty": ("services", "Beauty Salon"),
    "cosmetics": ("shopping", "Cosmetics"),
    "perfumery": ("shopping", "Perfume Shop"),
    "tattoo": ("services", "Tattoo Parlor"),
    "massage": ("services", "Massage"),
    "optician": ("health", "Optician"),
    # Hardware / Home
    "hardware": ("shopping", "Hardware Store"),
    "doityourself": ("shopping", "DIY / Home Improvement"),
    "garden_centre": ("shopping", "Garden Center"),
    "furniture": ("shopping", "Furniture"),
    "kitchen": ("shopping", "Kitchen Store"),
    "paint": ("shopping", "Paint Store"),
    "florist": ("shopping", "Florist"),
    "bed": ("shopping", "Bedding"),
    "carpet": ("shopping", "Carpet Store"),
    "curtain": ("shopping", "Curtain Shop"),
    "lighting": ("shopping", "Lighting"),
    "antiques": ("shopping", "Antiques"),
    # Electronics
    "electronics": ("shopping", "Electronics"),
    "computer": ("shopping", "Computer Store"),
    "mobile_phone": ("shopping", "Phone Store"),
    "hifi": ("shopping", "Audio / Hi-Fi"),
    # Auto
    "car": ("automotive", "Car Dealer"),
    "car_parts": ("automotive", "Auto Parts"),
    "car_repair": ("automotive", "Auto Repair"),
    "motorcycle": ("automotive", "Motorcycle Shop"),
    "tyres": ("automotive", "Tire Shop"),
    "bicycle": ("shopping", "Bike Shop"),
    # Sports / Outdoor
    "sports": ("shopping", "Sporting Goods"),
    "outdoor": ("shopping", "Outdoor Gear"),
    # Media
    "books": ("shopping", "Bookstore"),
    "stationery": ("shopping", "Stationery"),
    "newsagent": ("shopping", "Newsstand"),
    "gift": ("shopping", "Gift Shop"),
    "toys": ("shopping", "Toy Store"),
    "games": ("shopping", "Game Store"),
    "music": ("shopping", "Music Store"),
    "musical_instrument": ("shopping", "Musical Instruments"),
    "video_games": ("shopping", "Video Games"),
    # Pets
    "pet": ("shopping", "Pet Store"),
    "pet_grooming": ("services", "Pet Grooming"),
    # Other
    "tobacco": ("shopping", "Tobacco Shop"),
    "e-cigarette": ("shopping", "Vape Shop"),
    "art": ("shopping", "Art Gallery"),
    "photo": ("services", "Photo Studio"),
    "travel_agency": ("services", "Travel Agency"),
    "ticket": ("services", "Ticket Office"),
    "pawnbroker": ("services", "Pawn Shop"),
    "charity": ("shopping", "Thrift Store"),
    "second_hand": ("shopping", "Secondhand Store"),
    "copyshop": ("services", "Copy / Print Shop"),
    "dry_cleaning": ("services", "Dry Cleaning"),
    "laundry": ("services", "Laundry"),
    "funeral_directors": ("services", "Funeral Home"),
    "storage_rental": ("services", "Storage"),
    "medical_supply": ("health", "Medical Supply"),
    "hearing_aids": ("health", "Hearing Aids"),
    "herbalist": ("health", "Herbalist"),
}

OFFICE_MAP = {
    "accountant": ("professional", "Accountant"),
    "lawyer": ("professional", "Lawyer"),
    "estate_agent": ("professional", "Real Estate"),
    "insurance": ("professional", "Insurance"),
    "financial": ("professional", "Financial Services"),
    "tax_advisor": ("professional", "Tax Advisor"),
    "architect": ("professional", "Architect"),
    "engineer": ("professional", "Engineering"),
    "it": ("professional", "IT Services"),
    "company": ("professional", "Office"),
    "ngo": ("professional", "Non-Profit"),
    "political_party": ("government", "Political Office"),
    "government": ("government", "Government Office"),
    "therapist": ("health", "Therapist"),
    "physician": ("health", "Physician"),
    "notary": ("professional", "Notary"),
    "consulting": ("professional", "Consulting"),
    "telecommunication": ("professional", "Telecom"),
}

CRAFT_MAP = {
    "carpenter": ("craft", "Carpenter"),
    "electrician": ("craft", "Electrician"),
    "plumber": ("craft", "Plumber"),
    "painter": ("craft", "Painter"),
    "roofer": ("craft", "Roofer"),
    "locksmith": ("craft", "Locksmith"),
    "gardener": ("craft", "Landscaper"),
    "hvac": ("craft", "HVAC"),
    "photographer": ("craft", "Photographer"),
    "jeweller": ("craft", "Jeweler"),
    "blacksmith": ("craft", "Blacksmith"),
    "brewery": ("food", "Brewery"),
    "winery": ("food", "Winery"),
    "distillery": ("food", "Distillery"),
    "beekeeper": ("craft", "Beekeeper"),
    "caterer": ("food", "Caterer"),
    "glaziery": ("craft", "Glass Work"),
    "metal_construction": ("craft", "Metal Work"),
    "stonemason": ("craft", "Stonemason"),
    "insulation": ("craft", "Insulation"),
    "shoemaker": ("craft", "Cobbler"),
    "upholsterer": ("craft", "Upholsterer"),
    "key_cutter": ("craft", "Key Cutter"),
    "cleaning": ("services", "Cleaning"),
}

TOURISM_MAP = {
    "hotel": ("lodging", "Hotel"),
    "motel": ("lodging", "Motel"),
    "hostel": ("lodging", "Hostel"),
    "guest_house": ("lodging", "Guest House"),
    "apartment": ("lodging", "Vacation Rental"),
    "camp_site": ("lodging", "Campground"),
    "caravan_site": ("lodging", "RV Park"),
    "chalet": ("lodging", "Chalet"),
    "museum": ("tourism", "Museum"),
    "gallery": ("tourism", "Gallery"),
    "zoo": ("tourism", "Zoo"),
    "aquarium": ("tourism", "Aquarium"),
    "theme_park": ("tourism", "Theme Park"),
    "attraction": ("tourism", "Attraction"),
    "information": ("tourism", "Tourist Info"),
}

HEALTHCARE_MAP = {
    "hospital": ("health", "Hospital"),
    "clinic": ("health", "Clinic"),
    "doctor": ("health", "Doctor"),
    "dentist": ("health", "Dentist"),
    "pharmacy": ("health", "Pharmacy"),
    "optometrist": ("health", "Optometrist"),
    "audiologist": ("health", "Audiologist"),
    "physiotherapist": ("health", "Physical Therapy"),
    "psychotherapist": ("health", "Psychotherapist"),
    "podiatrist": ("health", "Podiatrist"),
    "speech_therapist": ("health", "Speech Therapy"),
    "occupational_therapist": ("health", "Occupational Therapy"),
    "rehabilitation": ("health", "Rehabilitation"),
    "laboratory": ("health", "Laboratory"),
    "alternative": ("health", "Alternative Medicine"),
    "midwife": ("health", "Midwife"),
    "nursing_home": ("health", "Nursing Home"),
    "hospice": ("health", "Hospice"),
    "counselling": ("health", "Counseling"),
    "blood_donation": ("health", "Blood Donation"),
    "vaccination_centre": ("health", "Vaccination Center"),
}


def _classify_tags(tags):
    """Classify an OSM element by its tags. Returns (category, display_type) or None."""
    for tag_key, mapping in [
        ("amenity", AMENITY_MAP),
        ("shop", SHOP_MAP),
        ("office", OFFICE_MAP),
        ("craft", CRAFT_MAP),
        ("tourism", TOURISM_MAP),
        ("healthcare", HEALTHCARE_MAP),
    ]:
        value = tags.get(tag_key)
        if value and value in mapping:
            return mapping[value]
    # Fallback: if tag key exists but value isn't in our map, use a generic label
    for tag_key, default_cat, default_label in [
        ("shop", "shopping", "Shop"),
        ("amenity", "services", "Business"),
        ("office", "professional", "Office"),
        ("craft", "craft", "Workshop"),
        ("tourism", "tourism", "Tourism"),
        ("healthcare", "health", "Healthcare"),
    ]:
        if tags.get(tag_key):
            return (default_cat, tags[tag_key].replace("_", " ").title())
    return None


def _build_address(tags):
    """Build a formatted address string from OSM addr:* tags."""
    parts = []
    housenumber = tags.get("addr:housenumber", "")
    street = tags.get("addr:street", "")
    if housenumber and street:
        parts.append(f"{housenumber} {street}")
    elif street:
        parts.append(street)

    city = tags.get("addr:city", "")
    state = tags.get("addr:state", "")
    postcode = tags.get("addr:postcode", "")

    city_state = ", ".join(filter(None, [city, state]))
    if city_state:
        parts.append(city_state)
    if postcode:
        parts.append(postcode)

    return ", ".join(parts)


class BusinessHandler(osmium.SimpleHandler):
    """Pyosmium handler that extracts business POIs from .osm.pbf files."""

    def __init__(self, state_abbrev=""):
        super().__init__()
        self.businesses = []
        self.state_abbrev = state_abbrev
        self._count = 0
        self._skipped_unnamed = 0

    def _process_element(self, osm_type, osm_id, tags, lat, lon):
        """Process a single OSM element (node or way centroid)."""
        if lat == 0.0 and lon == 0.0:
            return

        tag_dict = {t.k: t.v for t in tags}
        name = tag_dict.get("name", "").strip()
        if not name:
            self._skipped_unnamed += 1
            return

        classification = _classify_tags(tag_dict)
        if classification is None:
            return

        business_category, display_type = classification

        # Build the business document
        biz = {
            "osm_id": f"{osm_type}/{osm_id}",
            "name": name,
            "business_type": display_type,
            "business_category": business_category,
            "address": _build_address(tag_dict),
            "street": tag_dict.get("addr:street", ""),
            "housenumber": tag_dict.get("addr:housenumber", ""),
            "city": tag_dict.get("addr:city", ""),
            "state": tag_dict.get("addr:state", self.state_abbrev),
            "postcode": tag_dict.get("addr:postcode", ""),
            "phone": tag_dict.get("phone", "") or tag_dict.get("contact:phone", ""),
            "website": tag_dict.get("website", "") or tag_dict.get("contact:website", ""),
            "email": tag_dict.get("email", "") or tag_dict.get("contact:email", ""),
            "opening_hours": tag_dict.get("opening_hours", ""),
            "cuisine": tag_dict.get("cuisine", ""),
            "brand": tag_dict.get("brand", ""),
            "location": {"lat": lat, "lon": lon},
            "tags": self._extract_tags(tag_dict, business_category, display_type),
            "indexed_at": datetime.now(timezone.utc).isoformat(),
        }

        self.businesses.append(biz)
        self._count += 1

    def _extract_tags(self, tag_dict, category, display_type):
        """Build a list of searchable tags from the element."""
        tags = [category, display_type.lower()]
        for key in ("cuisine", "brand", "amenity", "shop", "craft", "office"):
            val = tag_dict.get(key, "")
            if val:
                # Cuisine can be semicolon-separated: "pizza;italian"
                for part in val.split(";"):
                    cleaned = part.strip().replace("_", " ")
                    if cleaned and cleaned not in tags:
                        tags.append(cleaned)
        return tags

    def node(self, n):
        self._process_element("node", n.id, n.tags, n.location.lat, n.location.lon)

    def way(self, w):
        # For ways (building outlines), compute centroid from the way's center
        # Note: osmium provides way centers when using locations=True
        try:
            if w.nodes:
                # Use the center of the bounding box as approximation
                lats = [n.lat for n in w.nodes if n.location.valid()]
                lons = [n.lon for n in w.nodes if n.location.valid()]
                if lats and lons:
                    lat = sum(lats) / len(lats)
                    lon = sum(lons) / len(lons)
                    self._process_element("way", w.id, w.tags, lat, lon)
        except Exception:
            pass


def parse_businesses(pbf_path, state_abbrev=""):
    """Parse a .osm.pbf file and return a list of business dicts.

    Args:
        pbf_path: Path to the .osm.pbf file
        state_abbrev: Two-letter state abbreviation (e.g., "OR", "AZ")

    Returns:
        List of business dicts ready for Elasticsearch indexing.
    """
    handler = BusinessHandler(state_abbrev=state_abbrev)
    handler.apply_file(pbf_path, locations=True)
    logger.info(
        "Parsed %s: %d businesses found, %d unnamed POIs skipped",
        os.path.basename(pbf_path), handler._count, handler._skipped_unnamed,
    )
    return handler.businesses


def download_state_extract(state_key, dest_dir):
    """Download a Geofabrik .osm.pbf extract for a state.

    Args:
        state_key: Key in SUPPORTED_STATES (e.g., "oregon")
        dest_dir: Directory to save the file

    Returns:
        Path to the downloaded file, or None on failure.
    """
    state = SUPPORTED_STATES.get(state_key)
    if not state:
        logger.error("Unknown state: %s", state_key)
        return None

    os.makedirs(dest_dir, exist_ok=True)
    dest_path = os.path.join(dest_dir, state["filename"])

    logger.info("Downloading %s extract from Geofabrik...", state["label"])
    try:
        resp = requests.get(state["url"], stream=True, timeout=300)
        resp.raise_for_status()
        total = int(resp.headers.get("content-length", 0))
        downloaded = 0
        with open(dest_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                f.write(chunk)
                downloaded += len(chunk)
                if total:
                    pct = downloaded * 100 // total
                    if pct % 25 == 0:
                        logger.info("  %s: %d%% downloaded", state["label"], pct)
        logger.info("Downloaded %s (%d MB)", state["label"], downloaded // (1024 * 1024))
        return dest_path
    except Exception as e:
        logger.error("Failed to download %s: %s", state["label"], e)
        return None


def import_state(search_engine, state_key, data_dir):
    """Download, parse, and index businesses for a single state.

    Returns:
        Dict with import stats, or None on failure.
    """
    state = SUPPORTED_STATES.get(state_key)
    if not state:
        return None

    pbf_path = download_state_extract(state_key, data_dir)
    if not pbf_path:
        return None

    businesses = parse_businesses(pbf_path, state_abbrev=state["abbrev"])
    if not businesses:
        logger.warning("No businesses found in %s extract", state["label"])
        return {"state": state_key, "found": 0, "indexed": 0}

    # Bulk index in batches of 5000
    total_indexed = 0
    batch_size = 5000
    for i in range(0, len(businesses), batch_size):
        batch = businesses[i:i + batch_size]
        indexed = search_engine.bulk_index_businesses(batch)
        total_indexed += indexed

    logger.info(
        "Imported %s: %d found, %d indexed",
        state["label"], len(businesses), total_indexed,
    )
    return {
        "state": state_key,
        "label": state["label"],
        "found": len(businesses),
        "indexed": total_indexed,
    }


def import_all_states(search_engine, data_dir):
    """Download, parse, and index businesses for all supported states.

    Returns:
        List of per-state stats dicts.
    """
    search_engine.create_business_index()
    results = []
    for state_key in SUPPORTED_STATES:
        stats = import_state(search_engine, state_key, data_dir)
        if stats:
            results.append(stats)
    return results


if __name__ == "__main__":
    # Quick CLI test: python -m profoundd.crawler.osm_crawler
    import sys
    logging.basicConfig(level=logging.INFO)

    if len(sys.argv) > 1:
        pbf_file = sys.argv[1]
        state_ab = sys.argv[2] if len(sys.argv) > 2 else ""
        businesses = parse_businesses(pbf_file, state_ab)
        print(f"Found {len(businesses)} businesses")
        for b in businesses[:5]:
            print(f"  {b['name']} ({b['business_type']}) - {b['address']}")
    else:
        print("Usage: python -m profoundd.crawler.osm_crawler <file.osm.pbf> [STATE_ABBREV]")
        print(f"\nSupported states: {', '.join(SUPPORTED_STATES.keys())}")
