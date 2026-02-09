# Profoundd - Multi-Domain Search Engine

A self-hosted search engine that aggregates and indexes content from verified sources across **6 domains**: News, Medical, Legal, Technology, Finance, and Science.

Built for deployment on a single $6/month Digital Ocean Droplet.

## Features

- **Multi-Domain Search** - Search across News, Medical, Legal, Tech, Finance, Science
- **Credibility Scoring** - Each source rated 1-10; high-credibility sources rank higher
- **RSS/Atom Crawler** - Automated hourly crawl from 40+ verified sources (Reuters, PubMed, Nature, SCOTUSblog, etc.)
- **Smart Ranking** - BM25 relevance + credibility boost + recency boost
- **Admin Panel** - Manage sources, adjust rankings, trigger crawls, view analytics
- **REST API** - JSON search API at `/api/search?q=query&category=news`
- **Dark Theme UI** - Clean, modern interface

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Backend | Python / Flask |
| Search | Elasticsearch 8.x |
| Crawler | feedparser + requests (RSS/Atom) |
| Database | SQLite (upgradeable to PostgreSQL) |
| Server | Gunicorn + Nginx |
| Frontend | HTML/CSS/JS (no framework dependencies) |

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

### Production (Digital Ocean Droplet)

```bash
# SSH into your Ubuntu 22.04 Droplet
ssh root@your-droplet-ip

# Clone and run setup
git clone <repo-url> /opt/profoundd
cd /opt/profoundd
sudo bash scripts/setup.sh

# Point DNS: profoundd.com -> your-droplet-ip
# Then install SSL:
sudo bash scripts/ssl_setup.sh
```

## Project Structure

```
profoundd/
├── app.py                  # Flask application factory
├── config/
│   ├── settings.py         # Configuration (env vars)
│   └── sources.py          # Feed sources (40+ across 6 categories)
├── search/
│   └── engine.py           # Elasticsearch integration & ranking
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

| Category | Sources | Examples |
|----------|---------|----------|
| News | 9 | Reuters, BBC, NPR, AP, Guardian |
| Medical | 8 | PubMed, NIH, WHO, Lancet, NEJM |
| Legal | 6 | SCOTUSblog, Cornell LII, Reuters Legal |
| Technology | 7 | Ars Technica, TechCrunch, MIT Tech Review |
| Finance | 5 | Financial Times, Bloomberg, Economist |
| Science | 6 | Nature, Science, NASA, Scientific American |

Add custom sources via the admin panel.

## API

```bash
# Search
GET /api/search?q=climate+change&category=science&page=1&sort=relevance

# Trending
GET /api/trending?category=news&hours=24
```

## Server Requirements

- **Minimum**: 1 vCPU, 1 GB RAM, 25 GB SSD ($6/month DO Droplet)
- **Recommended**: 2 vCPU, 2 GB RAM ($12/month) for heavier crawling
- Ubuntu 22.04 LTS
- Elasticsearch 8.x (configured for 256MB heap on small servers)

## Configuration

Copy `.env.example` to `.env` and update:

```bash
SECRET_KEY=your-random-secret
ADMIN_PASSWORD=your-admin-password
ELASTICSEARCH_URL=http://localhost:9200
CRAWL_INTERVAL_MINUTES=60
```

## License

MIT
