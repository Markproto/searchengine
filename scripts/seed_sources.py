#!/usr/bin/env python3
"""Seed sources and sync credibility ratings from central config."""
import sys
sys.path.insert(0, "/opt/profoundd")

from profoundd.app import create_app
from profoundd.utils.models import db, Source
from profoundd.config.sources import ALL_SOURCES

app = create_app()
with app.app_context():
    added = 0
    updated = 0
    for src in ALL_SOURCES:
        existing = db.session.query(Source).filter_by(url=src["url"]).first()
        if not existing:
            source = Source(
                name=src["name"],
                url=src["url"],
                category=src["category"],
                credibility=src.get("credibility", 5),
                feed_type=src.get("feed_type", "rss"),
            )
            db.session.add(source)
            added += 1
        else:
            changed = False
            if existing.credibility != src.get("credibility", 5):
                existing.credibility = src.get("credibility", 5)
                changed = True
            if existing.name != src["name"]:
                existing.name = src["name"]
                changed = True
            if existing.category != src["category"]:
                existing.category = src["category"]
                changed = True
            if existing.feed_type != src.get("feed_type", "rss"):
                existing.feed_type = src.get("feed_type", "rss")
                changed = True
            if changed:
                updated += 1
                print(f"  Updated: {src['name']} (credibility={src.get('credibility', 5)})")
    db.session.commit()
    print(f"Seed complete: {added} new, {updated} updated from config.")
