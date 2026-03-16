# Profoundd - Multi-Domain Search Engine

A self-hosted search engine that aggregates and indexes content from 80+ verified sources across **16 categories**: News, Medical, Legal, Technology, Finance, Science, Politics, Markets, Education, Environment, Legislative, State Legislative, Advocacy, and more.

**Live at:** [profoundd.com](https://profoundd.com)

## Features

- **Multi-Domain Search** - Search across 16 categories with 80+ curated sources
- **Credibility Scoring** - Each source rated 1-10; relevance-first ranking with credibility as a tiebreaker
- **Smart Ranking** - BM25 relevance + phrase matching + dampened credibility + Gaussian recency decay
- **External Search** - Every search also queries SearXNG/Brave for results beyond the curated index
- **Enhanced Providers** - CourtListener (legal), PubMed (medical), Congress.gov (legislative) integrated
- **RSS/Atom Crawler** - Automated hourly crawl from 80+ verified sources
- **NewsRoom Bob** - AI-generated news stories with editorial notes
- **Admin Panel** - Manage sources, adjust rankings, trigger crawls, view analytics
- **REST API** - JSON search API + topic feeds (stocks, crypto, metals) for external sites
- **Dark/Light Theme** - Clean, modern interface

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Backend | Python 3.11 / Flask 3.0 |
| Search | Elasticsearch 8.11.1 |
| Crawler | feedparser + BeautifulSoup4 + newspaper3k |
| Database | SQLite (metadata, admin, analytics) |
| Server | Gunicorn (Docker) |
| SSL | Cloudflare Tunnel |
| Frontend | HTML/CSS/JS (no framework dependencies) |
| AI | Anthropic Claude API |

## Infrastructure

| Server | Role | Details |
|--------|------|---------|
| **Apollo9** | Production | Ubuntu 24.04, 64GB RAM, port 3004, Cloudflare Tunnel |
| **Azure7** | Mirror / Failover | Ubuntu 24.04, 62GB RAM, Tailscale mesh, auto-failover |

Both servers connected via Tailscale VPN. Azure7 runs as a hot standby with automated failover (health checks every 30s, promotes after 3 failures).

## Quick Start

### Local Development (Docker)

```bash
docker-compose up
# App at http://localhost:5000
# Elasticsearch at http://localhost:9200
```

### Local Development (Manual)

```bash
# 1. Install Elasticsearch and start it
# 2. Set up Python environment
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 3. Run the app
python -m profoundd.app
# Visit http://localhost:5000

# 4. Log into admin (http://localhost:5000/admin)
#    Default: admin / changeme

# 5. Seed sources and run first crawl from admin panel
```

### Production Deployment

```bash
# From local Mac — push to Apollo9
git push apollo9 claude/custom-news-search-engine-YWN94:master

# SSH in and rebuild
ssh mark@192.168.1.99
cd /home/mark/profoundd-build && git pull
sudo docker compose -f docker-compose.prod.yml up -d --build
```

## Project Structure

```
profoundd/
├── app.py                  # Flask application factory
├── config/
│   ├── settings.py         # Configuration (env vars)
│   └── sources.py          # Feed sources (80+ across 16 categories)
├── search/
│   ├── engine.py           # Elasticsearch integration & ranking
│   └── external_providers.py # CourtListener, PubMed, Congress.gov, SearXNG, Brave
├── crawler/
│   └── feed_crawler.py     # RSS/Atom feed crawler
├── admin/
│   └── routes.py           # Admin panel routes
├── utils/
│   └── models.py           # Database models (SQLAlchemy)
├── frontend/
│   ├── templates/           # Jinja2 templates
│   └── static/              # CSS, JS, images
├── tests/
│   └── test_app.py         # Test suite
└── data/                    # SQLite database (created at runtime)
```

## Categories & Sources

| Category | # Sources | Examples |
|----------|-----------|---------|
| News | 21 | Washington Post, Al Jazeera, Daily Caller, Gateway Pundit |
| Medical | 10 | WHO, NEJM, The Lancet, Dr. Robert Malone |
| Legal | 9 | SCOTUSblog, Cornell LII, Courthouse News |
| Tech | 6 | TechCrunch, Hacker News, The Verge, Wired |
| Finance | 9 | Financial Times, WSJ, Yahoo Finance |
| Science | 6 | NASA, Phys.org, New Scientist, ScienceDaily |
| Politics | 16+ | Politico, Tucker Carlson, Ben Shapiro, Redacted |
| Markets | 16 | CoinDesk, Bitcoin Magazine, Gold Telegraph |
| Legislative | 20+ | Congress.gov, GovInfo, White House, EPA |
| + 7 more | 30+ | Education, Environment, Advocacy, Rumble, etc. |

## API

```bash
# Search
GET /api/search?q=climate+change&category=science&page=1&sort=relevance

# Trending
GET /api/trending?category=news&hours=24

# Autocomplete
GET /api/suggest?q=tru

# Topic Feeds (for external sites)
GET /api/feed/stocks?hours=72&limit=30&format=json
GET /api/feed/crypto?format=rss
GET /api/feed/metals

# Health
GET /health
```

## Configuration

Environment variables (see `.env.example`):

```bash
SECRET_KEY=your-random-secret
ADMIN_PASSWORD=your-admin-password
ELASTICSEARCH_URL=http://elasticsearch:9200
DATABASE_URL=sqlite:////app/data/profoundd.db
CRAWL_INTERVAL_MINUTES=60
ANTHROPIC_API_KEY=sk-ant-...
```

## License

MIT
