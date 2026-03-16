# Profoundd — Developer Handoff Sheet

**Live URL:** https://profoundd.com (Apollo9 via Cloudflare Tunnel, port 3004)
**Status:** Running on Apollo9, 80+ feed sources across 16 categories, hourly crawl cycle
**Date:** March 9, 2026

---

## 1. Infrastructure

| Component | Details |
|-----------|---------|
| **Server** | Apollo9 — `192.168.1.99` (Ubuntu 24.04, MSI Pro DP21, 64GB RAM) |
| **SSH** | `ssh mark@192.168.1.99` / password: `1879Matt` |
| **App Container** | Docker Compose, port **3004** → container port 5000 |
| **Elasticsearch** | Docker container `profoundd-es`, port 9201 (host), 9200 (internal), 1GB heap |
| **Database** | SQLite (persisted via Docker volume `profoundd-data`) |
| **SSL** | Cloudflare Tunnel (auto SSL, no Certbot) |
| **Git Repo (bare)** | `/home/mark/profoundd.git` on Apollo9 |
| **Git Repo (working)** | `/home/mark/profoundd-build` on Apollo9 |
| **Env File** | `/home/mark/profoundd-build/.env.prod` (chmod 600) |
| **Compose File** | `/home/mark/profoundd-build/docker-compose.prod.yml` |
| **Crawler Log** | `/home/mark/profoundd-crawler.log` |
| **Local Dev** | `/Users/markhutto/Projects/profoundd` |
| **GitHub** | https://github.com/Markproto/searchengine |
| **Branch** | `claude/custom-news-search-engine-YWN94` |

### Apollo9 Port Map (all apps)

| Port | Service |
|------|---------|
| 3000 | Privacyfolio (production) |
| 3001 | DealerCharts |
| 3002 | Privacyfolio (staging) |
| 3003 | Oregon Corner (rvnews) |
| **3004** | **Profoundd** |
| 5432 | PostgreSQL 16 |
| 8000 | Coolify |
| 8001 | Frontdesk |
| 8081 | RMM Staging (WordPress) |
| 8082 | WordPress (pow) |
| 9201 | Profoundd Elasticsearch (localhost only) |
| 27017 | MongoDB |

---

## 2. Tech Stack

| Layer | Technology | Version |
|-------|-----------|---------|
| Framework | Flask | 3.0.0 |
| Language | Python | 3.11 |
| Search | Elasticsearch | 8.11.1 |
| ORM | Flask-SQLAlchemy | 3.1.1 |
| Migrations | Flask-Migrate / Alembic | 4.0.5 / 1.13.0 |
| Auth | Flask-Login | 0.6.3 |
| CORS | Flask-CORS | 4.0.0 |
| Server | Gunicorn | 21.2.0 |
| Crawler | feedparser + BeautifulSoup4 + newspaper3k | 6.0.11 / 4.12.2 / 0.2.8 |
| Scheduler | APScheduler (in-app) + crontab (external) | 3.10.4 |
| AI | anthropic + openai SDKs | latest |
| Password Hash | bcrypt / Werkzeug | 4.1.2 / 3.0.1 |
| Testing | pytest + pytest-flask | 7.4.4 / 1.3.0 |

---

## 3. Environment Variables

File: `/home/mark/profoundd-build/.env.prod`

```env
FLASK_APP=profoundd.app
FLASK_ENV=production
SECRET_KEY=<generated>
DOMAIN=profoundd.com

# Elasticsearch — uses Docker Compose service name
ELASTICSEARCH_URL=http://elasticsearch:9200

# Database — absolute path inside container, persisted by Docker volume
DATABASE_URL=sqlite:////app/data/profoundd.db

# Admin
ADMIN_USERNAME=admin
ADMIN_PASSWORD=<generated>

# Crawler
CRAWL_INTERVAL_MINUTES=60
MAX_ARTICLES_PER_FEED=50
RESPECT_ROBOTS_TXT=true
USER_AGENT=ProfounddBot/1.0 (+https://profoundd.com/bot)
CRAWL_DELAY_SECONDS=2
CONCURRENT_REQUESTS=4

# AI
ANTHROPIC_API_KEY=sk-ant-api03-xxxxx
```

**Important:** `ELASTICSEARCH_URL` uses the Docker Compose service name `elasticsearch`, NOT `localhost`. `DATABASE_URL` uses 4 slashes for absolute path.

---

## 4. News Sources (80+ feeds, 16 categories)

| Category | # Sources | Examples |
|----------|-----------|---------|
| News | 21 | Washington Post, Al Jazeera, Breitbart, InfoWars, Zero Hedge |
| Medical | 10 | WHO, NEJM, The Lancet, Dr. Robert Malone, Dr. Judy Mikovits |
| Legal | 9 | SCOTUSblog, Cornell LII, Reuters Legal, Courthouse News |
| Tech | 6 | TechCrunch, Hacker News, The Verge, Wired |
| Finance | 9 | Financial Times, WSJ, Yahoo Finance, Solari Report |
| Science | 6 | NASA, Phys.org, New Scientist, ScienceDaily |
| Education | 5 | Inside Higher Ed, Chronicle, EdSurge |
| Environment | 4 | Carbon Brief, Mongabay, Yale E360 |
| Politics | 16+ | Politico, Tucker Carlson, Joe Rogan, Ben Shapiro, Redacted |
| Markets | 16 | CoinDesk, Bitcoin Magazine, Gold Telegraph, Wolf Street |
| Epstein Files | 16 | Courthouse News, Unlimited Hangout, The Intercept |
| Charlie Kirk | 12 | Charlie Kirk Show, TPUSA, Daily Wire, The Federalist |
| Legislative | 20+ | Congress.gov, GovInfo, White House, EPA, CBO |
| State Legislative | 10 | Oregon Governor, Oregon Firearms Federation, CA AG |
| Advocacy | 15 | NRA-ILA, Heritage Foundation, Judicial Watch, FIRE |
| Rumble | 11 | Tucker, Crowder, Tim Pool, Charlie Kirk (Rumble mirrors) |

Sources defined in `profoundd/config/sources.py`. Each has: name, URL, category, credibility (1-10), feed_type (rss/atom/api), optional sponsor tags.

---

## 5. Deployment

### Deploy changes

```bash
# From local Mac
cd ~/Projects/profoundd
git push apollo9 claude/custom-news-search-engine-YWN94:master

# SSH in and rebuild
ssh mark@192.168.1.99
cd /home/mark/profoundd-build && git pull
sudo docker compose -f docker-compose.prod.yml up -d --build
```

### Useful commands

```bash
# View logs
sudo docker logs profoundd --tail 100 -f

# Check container status
sudo docker ps --filter name=profoundd

# Check Elasticsearch
curl http://127.0.0.1:9201/_cat/indices?v
curl http://127.0.0.1:9201/profoundd_articles/_count

# Manual crawl
sudo docker exec profoundd python -m profoundd.crawler.feed_crawler

# Restart
sudo docker compose -f /home/mark/profoundd-build/docker-compose.prod.yml restart web

# Full rebuild (after code changes)
cd /home/mark/profoundd-build && git pull
sudo docker compose -f docker-compose.prod.yml up -d --build

# Health check
curl http://localhost:3004/health
```

### Crawler scheduling

Crontab on Apollo9 (runs every hour):
```
0 * * * * /usr/bin/sudo /usr/bin/docker exec profoundd python -m profoundd.crawler.feed_crawler >> /home/mark/profoundd-crawler.log 2>&1
```

---

## 6. Database Schema (SQLite / SQLAlchemy)

```
AdminUser        — id, username, password_hash, created_at
Source           — id, name, url (unique), category, credibility (1-10),
                   bias_score, feed_type, is_active, sponsor_tags, subcategory
SearchLog        — id, query, category, results_count, ip_address, searched_at
CrawlLog         — id, source_id → Source, articles_found, articles_new,
                   duplicates_skipped, errors, status, started_at, duration_seconds
SourceSubmission — id, name, url, category, reason, submitted_by,
                   ip_address, status (pending/approved/rejected), submitted_at
SiteSetting      — key (PK), value (admin-editable content)
ResearchDocument — id, title, slug, content, category, published_at, status
BobStory         — id, title, slug, body, summary, source_article_url,
                   category, extra_categories, status, published_at
NewsroomNote     — id, article_url, note_text, created_at
SourceNote       — id, source_name, note_text, created_at
PageView         — id, path, visitor_id, ip_address, user_agent, referrer, viewed_at
AdminRankingAction — id, query, article_url, action, admin_username, created_at
VerifyBotConfig  — id, config_json
```

Articles are stored in **Elasticsearch** (index: `profoundd_articles`), not SQLite.

---

## 7. Admin Panel

Access: `https://profoundd.com/admin` — Credentials in `.env.prod`

Dashboard, Sources CRUD, Source Ratings, Source Notes, Public Submissions, Crawl History, Historical Crawl, Search Analytics, AI Settings, URL Analysis, Edit About Page, Edit SEO Tags, NewsRoom Bob Stories, The Man Section, Verify Bot, Research Documents.

---

## 8. API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /api/search?q=&category=&page=&sort=` | JSON search results |
| `GET /api/trending?category=&hours=` | Trending articles |
| `GET /api/suggest?q=` | Search autocomplete |
| `GET /api/sources` | Category list with source counts |
| `GET /api/feed/<topic>?hours=&limit=&format=` | Topic feeds (stocks, crypto, metals, or any category) |
| `POST /api/analytics/beacon` | Page view tracking |
| `GET /health` | Health check (ES status, article count) |

---

## 9. Local Business Search (OpenStreetMap)

Profoundd includes local business search powered by OpenStreetMap data.

| Component | Details |
|-----------|---------|
| **Data Source** | OpenStreetMap via Geofabrik .osm.pbf extracts |
| **ES Index** | `profoundd_businesses` (separate from articles) |
| **States** | Oregon (OR) + Arizona (AZ) |
| **Parser** | `profoundd/crawler/osm_crawler.py` using pyosmium |
| **Intent Detection** | `profoundd/search/local_intent.py` — keyword + city detection |
| **Location** | Browser GPS (with consent) or ZIP code fallback, stored in cookie |
| **Admin Panel** | `/admin/osm` — import controls, stats, per-state management |
| **Refresh** | Daily via APScheduler (24h interval) |
| **License** | ODbL — attribution in footer required |

### How to add a new state

1. Add entry to `SUPPORTED_STATES` in `profoundd/crawler/osm_crawler.py`
2. Add city names to `KNOWN_CITIES` in `profoundd/search/local_intent.py`
3. Add ZIP codes for the state to `profoundd/data/zipcodes.json`
4. Import via admin panel (`/admin/osm`) or wait for daily refresh

### API Endpoints

| Endpoint | Description |
|----------|-------------|
| `POST /api/set-location` | Set user location (GPS lat/lon or ZIP code) |
| `POST /api/clear-location` | Clear user location cookie |

---

## 10. Contacts

| Role | Contact |
|------|---------|
| Admin email | mark.hutto@protonmail.com |
| GitHub | https://github.com/Markproto/searchengine |
| Related project | Oregon Corner (https://oregoncorner.com) — Apollo9 port 3003 |
