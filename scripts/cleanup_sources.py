#!/usr/bin/env python3
"""Delete removed sources from the live database."""
import sys
sys.path.insert(0, "/opt/profoundd")
from profoundd.app import create_app
from profoundd.utils.models import db, Source

app = create_app()
with app.app_context():
    names = [
        "Associated Press", "AFP", "PBS NewsHour", "CBS News",
        "The Guardian", "DW News", "PubMed Trending", "NIH News",
        "NIH Research", "WHO Disease Outbreaks", "CDC Newsroom",
        "BMJ", "JAMA Network", "Stat News", "Ars Technica",
        "MIT Technology Review", "IEEE Spectrum", "Nature",
        "Science Magazine", "Scientific American", "EPA Newsroom",
        "The Guardian - Environment", "CNN", "MSNBC", "NY Times",
        "Reuters", "BBC News", "BBC World", "NPR News",
        "Reuters (X)", "BBC Breaking (X)",
    ]
    deleted = 0
    for n in names:
        s = db.session.query(Source).filter_by(name=n).first()
        if s:
            db.session.delete(s)
            deleted += 1
            print(f"Deleted: {n}")
    db.session.commit()
    print(f"Total deleted: {deleted}")
