"""
Polling data provider for the Polls category.
Fetches pollster ratings from 538 GitHub, and aggregates polling data
from available sources. Stores snapshots for accuracy tracking.
"""
import csv
import io
import logging
from datetime import datetime, timezone

import requests

logger = logging.getLogger(__name__)

API_TIMEOUT = 8

# 538 GitHub raw URLs
POLLSTER_RATINGS_URL = "https://raw.githubusercontent.com/fivethirtyeight/data/master/pollster-ratings/pollster-ratings-combined.csv"
RAW_POLLS_URL = "https://raw.githubusercontent.com/fivethirtyeight/data/master/pollster-ratings/raw_polls.csv"

# RealClearPolitics race IDs — curated list of active/important races
RCP_RACES = {
    # National
    "trump_approval": {"id": 8656, "label": "Trump Job Approval (2nd Term)", "type": "national", "state": "US"},
    "trump_economy": {"id": 8666, "label": "Trump Approval - Economy", "type": "national", "state": "US"},
    "trump_foreign_policy": {"id": 8667, "label": "Trump Approval - Foreign Policy", "type": "national", "state": "US"},
    "trump_immigration": {"id": 8659, "label": "Trump Approval - Immigration", "type": "national", "state": "US"},
    "direction": {"id": 902, "label": "Direction of Country", "type": "national", "state": "US"},
    "congressional_ballot": {"id": 8670, "label": "2026 Generic Congressional Vote", "type": "national", "state": "US"},
    # 2026 Governor Races
    "pa_gov_2026": {"id": 8860, "label": "Pennsylvania Governor 2026", "type": "governor", "state": "PA"},
    "va_gov_2026": {"id": 8870, "label": "Virginia Governor 2026", "type": "governor", "state": "VA"},
    "il_gov_2026": {"id": 8905, "label": "Illinois Governor 2026", "type": "governor", "state": "IL"},
    "nh_sen_2026": {"id": 8840, "label": "New Hampshire Senate 2026", "type": "senate", "state": "NH"},
    "ma_sen_2026": {"id": 8900, "label": "Massachusetts Senate 2026", "type": "senate", "state": "MA"},
    "tx_sen_2026_1": {"id": 8864, "label": "Texas Senate 2026 (Paxton)", "type": "senate", "state": "TX"},
    "tx_sen_2026_2": {"id": 8865, "label": "Texas Senate 2026 (Cornyn)", "type": "senate", "state": "TX"},
    "mi_gov_2026": {"id": 8700, "label": "Michigan Governor 2026 (R Primary)", "type": "governor", "state": "MI"},
    "ky_gov_2026": {"id": 8885, "label": "Kentucky Governor 2026", "type": "governor", "state": "KY"},
    "az_gov_2026": {"id": 8890, "label": "Arizona Governor 2026", "type": "governor", "state": "AZ"},
    "tx_gov_2026": {"id": 8910, "label": "Texas Governor 2026", "type": "governor", "state": "TX"},
    "me_gov_2026": {"id": 8875, "label": "Maine Governor 2026", "type": "governor", "state": "ME"},
}

# US state abbreviations for validation
US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
    "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
    "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
    "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY", "DC",
}

STATE_NAMES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
    "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
    "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming", "DC": "District of Columbia",
}

# Cache pollster ratings in memory (refreshed on restart)
_pollster_ratings_cache = {}


def fetch_pollster_ratings():
    """
    Fetch 538 pollster ratings from GitHub. Returns dict of pollster_name -> rating info.
    Cached in memory after first call.
    """
    global _pollster_ratings_cache
    if _pollster_ratings_cache:
        return _pollster_ratings_cache

    def _safe_float(val, default=0):
        try:
            if not val or val.upper() == "NA":
                return default
            return float(val)
        except (ValueError, TypeError):
            return default

    def _safe_int(val, default=0):
        try:
            if not val or str(val).upper() == "NA":
                return default
            return int(float(val))
        except (ValueError, TypeError):
            return default

    try:
        resp = requests.get(POLLSTER_RATINGS_URL, timeout=API_TIMEOUT)
        resp.raise_for_status()

        reader = csv.DictReader(io.StringIO(resp.text))
        ratings = {}
        for row in reader:
            name = row.get("pollster", "").strip()
            if not name:
                continue
            ratings[name.lower()] = {
                "pollster": name,
                "grade": _safe_float(row.get("numeric_grade")),
                "rank": _safe_int(row.get("rank"), 999),
                "pollscore": _safe_float(row.get("POLLSCORE")),
                "bias": _safe_float(row.get("bias_ppm")),
                "error": _safe_float(row.get("error_ppm")),
                "transparency": _safe_float(row.get("wtd_avg_transparency")),
                "total_polls": _safe_int(row.get("number_polls_pollster_total")),
                "aapor": row.get("aapor_roper", "").upper() == "TRUE",
            }
        _pollster_ratings_cache = ratings
        logger.info("Loaded %d pollster ratings from 538", len(ratings))
        return ratings

    except Exception as e:
        logger.warning("Failed to fetch pollster ratings: %s", e)
        return {}


def fetch_538_polls(cycle=None, state=None, race_type=None, max_results=50):
    """
    Fetch raw polls from 538 GitHub dataset.
    Returns list of poll dicts with national and state-level data.
    """
    try:
        resp = requests.get(RAW_POLLS_URL, timeout=15)
        resp.raise_for_status()

        reader = csv.DictReader(io.StringIO(resp.text))
        ratings = fetch_pollster_ratings()

        polls = []
        for row in reader:
            row_cycle = row.get("cycle", "")
            location = row.get("location", "").strip().upper()
            race = row.get("type_simple", "")

            # Filter by cycle if specified
            if cycle and row_cycle != cycle:
                continue

            # Filter by state
            if state:
                if state == "US" and location not in ("US", ""):
                    continue
                elif state != "US" and location != state:
                    continue

            # Filter by race type
            if race_type and race_type.lower() not in race.lower():
                continue

            pollster = row.get("pollster", "")
            pollster_key = pollster.lower()
            rating_info = ratings.get(pollster_key, {})

            def _sf(v, d=0):
                try:
                    return float(v) if v and str(v).upper() != "NA" else d
                except (ValueError, TypeError):
                    return d

            cand1_pct = _sf(row.get("cand1_pct"))
            cand2_pct = _sf(row.get("cand2_pct"))

            poll = {
                "poll_id": row.get("poll_id", ""),
                "source": "538",
                "pollster": pollster,
                "pollster_grade": rating_info.get("grade", 0),
                "pollster_rank": rating_info.get("rank", 999),
                "pollster_bias": rating_info.get("bias", 0),
                "pollster_error": rating_info.get("error", 0),
                "pollster_aapor": rating_info.get("aapor", False),
                "race": race,
                "state": location if location in US_STATES else "US",
                "state_name": STATE_NAMES.get(location, "National"),
                "cycle": row_cycle,
                "candidate_1": row.get("cand1_name", ""),
                "candidate_1_pct": cand1_pct,
                "candidate_1_party": row.get("cand1_party", ""),
                "candidate_2": row.get("cand2_name", ""),
                "candidate_2_pct": cand2_pct,
                "candidate_2_party": row.get("cand2_party", ""),
                "margin": round(cand1_pct - cand2_pct, 1),
                "sample_size": int(_sf(row.get("samplesize"))),
                "methodology": row.get("methodology", ""),
                "poll_date": row.get("polldate", ""),
                "election_date": row.get("electiondate", ""),
            }
            polls.append(poll)

        # Sort by most recent poll date
        polls.sort(key=lambda p: p.get("poll_date", ""), reverse=True)
        polls = polls[:max_results]

        logger.info("538 raw_polls: %d results (cycle=%s, state=%s)", len(polls), cycle, state)
        return polls

    except Exception as e:
        logger.warning("Failed to fetch 538 polls: %s", e)
        return []


def _save_poll_snapshots(polls):
    """Save poll data to SQLite for accuracy tracking."""
    try:
        from profoundd.utils.models import db, PollSnapshot
        from flask import current_app
        if not current_app:
            return

        for p in polls:
            snapshot = PollSnapshot(
                poll_id=p.get("poll_id", ""),
                source=p.get("source", "538"),
                pollster=p.get("pollster", ""),
                pollster_rating=p.get("pollster_grade"),
                race=p.get("race", ""),
                state=p.get("state", "US"),
                question=f"{p.get('candidate_1', '')} vs {p.get('candidate_2', '')}",
                candidate_1=p.get("candidate_1", ""),
                candidate_1_pct=p.get("candidate_1_pct"),
                candidate_1_party=p.get("candidate_1_party", ""),
                candidate_2=p.get("candidate_2", ""),
                candidate_2_pct=p.get("candidate_2_pct"),
                candidate_2_party=p.get("candidate_2_party", ""),
                margin=p.get("margin"),
                sample_size=p.get("sample_size"),
                methodology=p.get("methodology", ""),
                poll_date=p.get("poll_date", ""),
                cycle=p.get("cycle", ""),
            )
            db.session.add(snapshot)
        db.session.commit()
        logger.info("Saved %d poll snapshots", len(polls))
    except Exception as e:
        try:
            db.session.rollback()
        except Exception:
            pass
        logger.debug("Poll snapshot save skipped: %s", e)


def fetch_rcp_race(race_id, max_polls=20):
    """
    Fetch polling data from RealClearPolitics JSON API for a specific race.
    Returns list of poll dicts.
    """
    try:
        resp = requests.get(
            f"https://www.realclearpolitics.com/poll/race/{race_id}/polling_data.json",
            headers={"User-Agent": "Mozilla/5.0 (compatible; Profoundd/1.0)"},
            timeout=API_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()

        raw_polls = data.get("poll", [])
        if not raw_polls:
            return []

        ratings = fetch_pollster_ratings()
        polls = []

        for row in raw_polls[:max_polls]:
            pollster = row.get("pollster", "")
            if pollster == "rcp_average":
                pollster = "RCP Average"

            candidates = row.get("candidate", [])
            if len(candidates) < 2:
                continue

            c1 = candidates[0]
            c2 = candidates[1]

            def _sf(v, d=0):
                try:
                    return float(v) if v and str(v).upper() != "NA" else d
                except (ValueError, TypeError):
                    return d

            c1_pct = _sf(c1.get("value"))
            c2_pct = _sf(c2.get("value"))

            rating_info = ratings.get(pollster.lower(), {})

            poll = {
                "poll_id": row.get("id", ""),
                "source": "rcp",
                "pollster": pollster,
                "pollster_grade": rating_info.get("grade", 0),
                "pollster_rank": rating_info.get("rank", 999),
                "pollster_bias": rating_info.get("bias", 0),
                "pollster_error": rating_info.get("error", 0),
                "pollster_aapor": rating_info.get("aapor", False),
                "race": "",
                "state": "US",
                "state_name": "National",
                "cycle": "2026",
                "candidate_1": c1.get("name", ""),
                "candidate_1_pct": c1_pct,
                "candidate_1_party": c1.get("affiliation", ""),
                "candidate_2": c2.get("name", ""),
                "candidate_2_pct": c2_pct,
                "candidate_2_party": c2.get("affiliation", ""),
                "margin": round(c1_pct - c2_pct, 1),
                "sample_size_str": row.get("sampleSize", ""),
                "sample_size": 0,
                "methodology": "",
                "poll_date": row.get("date", ""),
                "election_date": "",
                "link": row.get("link", ""),
                "is_average": row.get("type") == "rcp_average",
            }

            # Parse sample size from string like "800 RV" or "1200 LV"
            ss = row.get("sampleSize", "")
            if ss:
                import re
                m = re.search(r'(\d+)', ss)
                if m:
                    poll["sample_size"] = int(m.group(1))
                poll["methodology"] = "LV" if "LV" in ss else ("RV" if "RV" in ss else "")

            polls.append(poll)

        logger.info("RCP race %s: %d polls", race_id, len(polls))
        return polls

    except Exception as e:
        logger.warning("RCP fetch error for race %s: %s", race_id, e)
        return []


def fetch_all_rcp_polls():
    """
    Fetch polls from all tracked RCP races.
    Returns dict of {race_key: {info: {...}, polls: [...]}}.
    """
    results = {}
    for key, info in RCP_RACES.items():
        polls = fetch_rcp_race(info["id"], max_polls=15)
        if polls:
            # Set state and race info on each poll
            for p in polls:
                p["state"] = info["state"]
                p["state_name"] = STATE_NAMES.get(info["state"], "National")
                p["race"] = info["label"]
            results[key] = {"info": info, "polls": polls}
    return results


def get_polls_for_category(state=None, max_results=50):
    """
    Get polls formatted for the Polls category page.
    Returns (national_polls, state_polls_map, rcp_races, pollster_ratings).
    Uses live RCP data for 2026 races.
    """
    rcp_data = fetch_all_rcp_polls()

    # Split national vs state races
    national_races = {}
    state_races = {}
    for key, data in rcp_data.items():
        info = data["info"]
        if info["type"] == "national":
            national_races[key] = data
        else:
            st = info["state"]
            if st not in state_races:
                state_races[st] = []
            state_races[st].append(data)

    # Build flat national polls list (all polls from national races)
    national = []
    for key in ("trump_approval", "direction", "congressional_ballot", "trump_favorability", "congress_approval"):
        if key in national_races:
            for p in national_races[key]["polls"]:
                p["_race_label"] = national_races[key]["info"]["label"]
                national.append(p)

    # Build state map: state -> list of polls
    state_map = {}
    for st, races in state_races.items():
        state_map[st] = []
        for race_data in races:
            for p in race_data["polls"]:
                p["_race_label"] = race_data["info"]["label"]
                state_map[st].append(p)

    # Save snapshots
    all_polls = national[:50]
    for st_polls in state_map.values():
        all_polls.extend(st_polls[:10])
    if all_polls:
        _save_poll_snapshots(all_polls[:100])

    ratings = fetch_pollster_ratings()

    # If specific state requested, return just that
    if state and state != "US" and state in US_STATES:
        return national[:10], {state: state_map.get(state, [])}, national_races, ratings

    return national, state_map, national_races, ratings


def get_state_color(polls):
    """
    Determine map color for a state based on latest poll margin.
    Returns CSS color string.
    """
    if not polls:
        return "#374151"  # gray (no data)

    latest = polls[0]
    margin = latest.get("margin", 0)
    dem_party = latest.get("candidate_1_party", "")

    # If candidate_1 is DEM, positive margin = blue
    # If candidate_1 is REP, positive margin = red
    if dem_party == "DEM":
        if margin > 10:
            return "#1d4ed8"   # solid blue
        elif margin > 3:
            return "#60a5fa"   # lean blue
        elif margin > -3:
            return "#a78bfa"   # toss-up purple
        elif margin > -10:
            return "#f87171"   # lean red
        else:
            return "#dc2626"   # solid red
    else:
        # candidate_1 is REP
        if margin > 10:
            return "#dc2626"
        elif margin > 3:
            return "#f87171"
        elif margin > -3:
            return "#a78bfa"
        elif margin > -10:
            return "#60a5fa"
        else:
            return "#1d4ed8"
