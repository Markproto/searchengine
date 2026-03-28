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


def get_polls_for_category(state=None, max_results=50):
    """
    Get polls formatted for the Polls category page.
    Returns (national_polls, state_polls_map, pollster_ratings).
    Uses most recent available cycle (538 data goes up to 2023).
    """
    # Try latest cycles in order
    all_polls = []
    for cycle in ("2024", "2023", "2022"):
        all_polls = fetch_538_polls(cycle=cycle, max_results=500)
        if all_polls:
            break

    # Save snapshots
    if all_polls:
        _save_poll_snapshots(all_polls[:100])

    # Split national vs state
    national = [p for p in all_polls if p["state"] == "US"][:max_results]

    # Build state map: state_abbrev -> list of polls
    state_map = {}
    for p in all_polls:
        st = p["state"]
        if st != "US" and st in US_STATES:
            if st not in state_map:
                state_map[st] = []
            if len(state_map[st]) < 10:
                state_map[st].append(p)

    # If specific state requested, return just that
    if state and state != "US" and state in US_STATES:
        return national[:10], {state: state_map.get(state, [])}, fetch_pollster_ratings()

    return national, state_map, fetch_pollster_ratings()


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
