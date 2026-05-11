#!/usr/bin/env python3
"""Seed the initial 20 presidential-candidate TrackedFigures.

Idempotent: adds rows that don't yet exist; never overwrites an existing
figure's settings (party, role, notes). The 20-person list reflects current
2028 speculation and can be edited at /admin/tracked-figures after seeding.

Usage:
  docker exec -e PYTHONPATH=/app profoundd python3 /app/scripts/seed_tracked_figures.py
"""
from __future__ import annotations

import logging
import sys

if "/app" not in sys.path:
    sys.path.insert(0, "/app")

from profoundd.app import create_app
from profoundd.utils.models import db, TrackedFigure


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


FIGURES = [
    # Democrats (10)
    ("kamala-harris",   "Kamala Harris",     "Kamala_Harris",     "Democrat",    "Former Vice President of the United States"),
    ("gavin-newsom",    "Gavin Newsom",      "Gavin_Newsom",      "Democrat",    "Governor of California"),
    ("pete-buttigieg",  "Pete Buttigieg",    "Pete_Buttigieg",    "Democrat",    "Former US Secretary of Transportation"),
    ("gretchen-whitmer","Gretchen Whitmer",  "Gretchen_Whitmer",  "Democrat",    "Governor of Michigan"),
    ("jb-pritzker",     "JB Pritzker",       "J._B._Pritzker",    "Democrat",    "Governor of Illinois"),
    ("josh-shapiro",    "Josh Shapiro",      "Josh_Shapiro",      "Democrat",    "Governor of Pennsylvania"),
    ("wes-moore",       "Wes Moore",         "Wes_Moore",         "Democrat",    "Governor of Maryland"),
    ("andy-beshear",    "Andy Beshear",      "Andy_Beshear",      "Democrat",    "Governor of Kentucky"),
    ("tim-walz",        "Tim Walz",          "Tim_Walz",          "Democrat",    "Governor of Minnesota; 2024 VP nominee"),
    ("cory-booker",     "Cory Booker",       "Cory_Booker",       "Democrat",    "US Senator from New Jersey"),

    # Republicans (10)
    ("jd-vance",        "JD Vance",          "JD_Vance",          "Republican",  "Vice President of the United States"),
    ("ron-desantis",    "Ron DeSantis",      "Ron_DeSantis",      "Republican",  "Governor of Florida"),
    ("vivek-ramaswamy", "Vivek Ramaswamy",   "Vivek_Ramaswamy",   "Republican",  "Entrepreneur; 2024 candidate"),
    ("marco-rubio",     "Marco Rubio",       "Marco_Rubio",       "Republican",  "US Secretary of State"),
    ("tulsi-gabbard",   "Tulsi Gabbard",     "Tulsi_Gabbard",     "Republican",  "Director of National Intelligence"),
    ("kristi-noem",     "Kristi Noem",       "Kristi_Noem",       "Republican",  "US Secretary of Homeland Security"),
    ("glenn-youngkin",  "Glenn Youngkin",    "Glenn_Youngkin",    "Republican",  "Governor of Virginia"),
    ("greg-abbott",     "Greg Abbott",       "Greg_Abbott",       "Republican",  "Governor of Texas"),
    ("brian-kemp",      "Brian Kemp",        "Brian_Kemp",        "Republican",  "Governor of Georgia"),
    ("nikki-haley",     "Nikki Haley",       "Nikki_Haley",       "Republican",  "Former US Ambassador to the UN"),
]


def main():
    app = create_app()
    added = 0
    skipped = 0
    with app.app_context():
        db.create_all()
        for slug, name, wiki_title, party, role in FIGURES:
            if TrackedFigure.query.filter_by(slug=slug).first():
                skipped += 1
                continue
            row = TrackedFigure(
                slug=slug,
                name=name,
                wikipedia_title=wiki_title,
                party=party,
                role=role,
                category="presidential-2028",
                is_active=True,
            )
            db.session.add(row)
            added += 1
        db.session.commit()
        total = TrackedFigure.query.count()
    log.info("Seeded %d new TrackedFigures (%d already present). Total now: %d",
             added, skipped, total)


if __name__ == "__main__":
    main()
