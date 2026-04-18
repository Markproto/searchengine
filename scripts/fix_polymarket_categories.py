"""One-time fix: re-categorize articles incorrectly labeled as 'polymarket'.

Over 45K articles got miscategorized due to overly broad SPECIAL_SECTION_KEYWORDS
like "will trump", "will biden", "chances of". This script fixes them in ES.
"""
import json
import requests

ES = "http://elasticsearch:9200"
INDEX = "profoundd_articles"

# Legitimate polymarket sources — don't touch these
LEGIT_POLY = {"Polymarket", "PredictIt", "Manifold Markets", "Metaculus", "Kalshi"}

# Map source names → correct category
CORRECTIONS = {
    # Domain crawler sources
    "pjmedia.com": "news", "redstate.com": "news", "spectator.org": "news",
    "townhall.com": "news", "americanthinker.com": "news", "freebeacon.com": "news",
    "thelastamericanvagabond.com": "news", "theintercept.com": "news",
    "consortiumnews.com": "news", "dailycaller.com": "news", "breitbart.com": "news",
    "legalinsurrection.com": "legal", "washingtonexaminer.com": "news",
    "mishtalk.com": "markets", "propublica.org": "news", "fee.org": "politics",
    "foxnews.com": "news", "aclu.org": "politics", "nypost.com": "news",
    "lawfaremedia.org": "legal", "justthenews.com": "news",
    "bitcoinmagazine.com": "markets", "mises.org": "politics",
    "frontpagemag.com": "news", "thefire.org": "legal",
    "theepochtimes.com": "news", "icij.org": "news", "scotusblog.com": "legal",
    # RSS feed sources (by display name)
    "The Hill": "politics", "Gateway Pundit": "news", "Yahoo Finance": "finance",
    "Al Jazeera": "news", "Mail Tribune": "news", "Bloomberg": "finance",
    "Ashland Daily Tidings": "news", "ABC News": "news", "Investing.com": "markets",
    "Decrypt": "markets", "Courthouse News": "legal", "Politico": "politics",
    "CoinDesk": "markets", "France24": "news", "Zero Hedge (Markets)": "markets",
    "Breitbart": "news", "Daily Caller": "news", "CoinTelegraph": "markets",
    "Straight Arrow News": "news", "CNBC": "finance", "The Blaze": "news",
    "Japan Times": "news",
}


def main():
    # Count before
    r = requests.get(f"{ES}/{INDEX}/_count", json={"query": {"term": {"category": "polymarket"}}})
    before = r.json().get("count", 0)
    print(f"Before: {before} polymarket articles")

    # Scroll through all polymarket articles
    fixed = 0
    batch = []
    r = requests.post(f"{ES}/{INDEX}/_search?scroll=2m", json={
        "size": 500,
        "query": {"term": {"category": "polymarket"}},
        "_source": ["source_name"],
    })
    data = r.json()
    scroll_id = data.get("_scroll_id")
    hits = data["hits"]["hits"]

    while hits:
        for hit in hits:
            src = hit["_source"].get("source_name", "")
            if src in LEGIT_POLY:
                continue
            new_cat = CORRECTIONS.get(src, "news")
            batch.append(json.dumps({"update": {"_index": INDEX, "_id": hit["_id"]}}))
            batch.append(json.dumps({"doc": {"category": new_cat}}))
            fixed += 1

            if len(batch) >= 1000:
                body = "\n".join(batch) + "\n"
                requests.post(f"{ES}/_bulk", data=body.encode(),
                              headers={"Content-Type": "application/x-ndjson"})
                batch = []
                print(f"  Fixed {fixed}...")

        r = requests.post(f"{ES}/_search/scroll", json={"scroll": "2m", "scroll_id": scroll_id})
        data = r.json()
        hits = data.get("hits", {}).get("hits", [])

    if batch:
        body = "\n".join(batch) + "\n"
        requests.post(f"{ES}/_bulk", data=body.encode(),
                      headers={"Content-Type": "application/x-ndjson"})

    if scroll_id:
        try:
            requests.delete(f"{ES}/_search/scroll", json={"scroll_id": scroll_id})
        except Exception:
            pass

    # Count after
    r = requests.get(f"{ES}/{INDEX}/_count", json={"query": {"term": {"category": "polymarket"}}})
    after = r.json().get("count", 0)
    print(f"\nDone. Fixed {fixed} articles.")
    print(f"Before: {before} → After: {after} polymarket articles")


if __name__ == "__main__":
    main()
