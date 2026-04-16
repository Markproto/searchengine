# Plan: Build Proactive Web Crawler at Real Scale

## Context
Current state of Profoundd's index (measured from ES):
- **11,282** `wayback-recovered` articles (local Oregon newspapers)
- **11,280** `local-news` / `southern-oregon`
- **4,380** `News` tagged articles
- **1,343** `web-indexed` (reactive Brave auto-index)
- **93,518** other articles in feed crawler output

Profoundd is over-reliant on Brave for web results. The user is right — 10 target domains is not enough. The GDELT crawler already tracks **80+ domains** and we have **60+ RSS feed sources**. A proper crawler should go wide: 200+ domains covering the full information landscape your audience searches.

Goal: Build a multi-tier crawler that goes broad (200+ domains), deep (entire archives), and smart (quality filter before indexing).

---

## Step 1: Consolidated Master Domain List (~250 domains)

Create a single authoritative list of every domain Profoundd crawls. Organized by tier:

### Tier 1 — Core Targeted Crawl (~150 domains)
Full archive crawl, highest priority. Union of current RSS sources + GDELT domains + new additions.

**News (Alternative/Independent):** zerohedge.com, theblaze.com, dailycaller.com, dailywire.com, breitbart.com, infowars.com, theepochtimes.com, justthenews.com, oann.com, newsmax.com, townhall.com, pjmedia.com, therightscoop.com, freebeacon.com, realclearpolitics.com, redstate.com, hotair.com, twitchy.com, legalinsurrection.com, spectator.org, amgreatness.com, americanthinker.com, frontpagemag.com, nationalreview.com, thefederalist.com, westernjournal.com, washingtontimes.com, nypost.com, foxnews.com, foxbusiness.com, thepostmillennial.com, revolver.news, nationalfile.com, gatewaypundit.com, vigilantcitizen.com, truepundit.com, hagmannreport.com, sputnikglobe.com, rt.com

**News (Mainstream) — crawled but low credibility (Pfizer-tagged):** reuters.com, apnews.com, bbc.com, nytimes.com, washingtonpost.com, cnn.com, msnbc.com, abcnews.go.com, cbsnews.com, nbcnews.com, usatoday.com, politico.com, thehill.com, axios.com, bloomberg.com, wsj.com, ft.com, cnbc.com, npr.org, pbs.org, theguardian.com, bostonglobe.com, latimes.com, chicagotribune.com, dallasnews.com, miamiherald.com, sfchronicle.com, denverpost.com, seattletimes.com, huffpost.com

**International:** aljazeera.com, france24.com, japantimes.co.jp, dw.com, spiegel.de, lemonde.fr, elpais.com, scmp.com, asiatimes.com

**Investigative / Independent:** unlimitedhangout.com, corbettreport.com, thelastamericanvagabond.com, mintpressnews.com, thegrayzone.com, consortiumnews.com, theintercept.com, propublica.org, icij.org, publicintegrity.org, reveal.org, firstlook.org, palmerreport.com

**Medical (Independent):** mercola.com, articles.mercola.com, naturalnews.com, childrenshealthdefense.org, greenmedinfo.com, retractionwatch.com, substack.com (RFK, Malone, Mikovits channels)

**Medical (Mainstream):** nejm.org, thelancet.com, who.int, cdc.gov, fda.gov, nih.gov, pubmed.ncbi.nlm.nih.gov, medscape.com, webmd.com, medicalnewstoday.com

**Legal / Court:** scotusblog.com, law.com, supremecourt.gov, courtlistener.com, courthousenews.com, lawfaremedia.org, abovethelaw.com, abajournal.com, jurist.org, judicialwatch.org, empoweroversite.org, thefire.org

**Government / Legislative:** congress.gov, govinfo.gov, govtrack.us, whitehouse.gov, federalregister.gov, regulations.gov, gao.gov, cbo.gov, crsreports.congress.gov

**Finance / Markets:** marketwatch.com, seekingalpha.com, investopedia.com, fool.com, thestreet.com, barrons.com, benzinga.com, wolfstreet.com, mishtalk.com, investing.com, thesolarireport.substack.com

**Crypto / Commodities:** coindesk.com, cointelegraph.com, bitcoinmagazine.com, decrypt.co, theblock.co, blockworks.co, goldtelegraph.com, silverdoctors.com, mining.com, oilprice.com

**Tech:** arstechnica.com, theverge.com, wired.com, techcrunch.com, tomshardware.com, bleepingcomputer.com, engadget.com, theregister.com, news.ycombinator.com

**Science:** nature.com, sciencedaily.com, newscientist.com, livescience.com, phys.org, space.com, nasa.gov, nasaspaceflight.com

**Oregon / Pacific NW local:** oregonlive.com, oregoncorner.com, statesmanjournal.com, kgw.com, koin.com, opb.org, willametteweek.com, portlandmercury.com, rvmag.com, mailtribune.com, ashland.oregon.localnewsdaily.com, dailytidings.com, kobi5.com, kdrv.com

**Podcasts / Substacks (audio/transcripts):** joerogan.com, tuckercarlson.com, benshapiroshow.com, louderwithcrowder.com, timcast.com, candaceowens.com, robertfkennedyjr.substack.com, rwmalonemd.substack.com, therealdr.substack.com, stillgray.substack.com

**Advocacy / Think Tanks:** heritage.org, cato.org, brookings.edu, rand.org, mises.org, fee.org, aei.org, aclu.org, nra.org, nraila.org

### Tier 2 — Long-tail (from Brave auto-index)
Continue reactive indexing from Brave results that pass the quality filter. Whatever users search for that doesn't match a Tier 1 domain.

### Tier 3 — Discovery (link graph)
During crawling, extract outbound links. New domains linked from Tier 1 articles get added to a "candidate" list with a counter. If a candidate domain is linked from 5+ different Tier 1 articles, promote it to Tier 1 for full crawling. This is how Google originally bootstrapped its index.

---

## Step 2: Build `scripts/domain_crawler.py`

General-purpose crawler. Reuses all existing patterns:

**Architecture:**
- **Config file:** `scripts/domain_crawler_config.json` — the 150+ Tier 1 domains with seed URLs, per-domain delays, max depth, categories
- **Seed URLs per domain:** homepage, archive index, sitemap.xml, category pages
- **Link discovery:** extract internal links, stay within domain
- **URL classification:** reuse patterns from `scripts/wayback_newspaper_crawler.py` — skip CSS/JS/PDFs/tags/search, keep article-like paths
- **robots.txt:** `urllib.robotparser`, respect `Crawl-delay`
- **Rate limiting:** 2 sec default per domain, configurable
- **User-Agent:** `ProfounddBot/1.0 (+https://profoundd.com/bot)`
- **Full-text extraction:** reuse `FeedCrawler.fetch_full_text()` from feed_crawler.py
- **Dedup:** URL MD5 hash against ES (existing pattern)
- **State DB:** SQLite per-domain, resumable (GDELT pattern)
- **Workers:** 8 parallel domains, 1 req/sec each = ~28K pages/hour theoretical max
- **Discovery:** extract outbound links, increment counter in candidate_domains table

**Command-line:**
```
python scripts/domain_crawler.py --config config.json --domains all
python scripts/domain_crawler.py --domains judicialwatch.org,unlimitedhangout.com --max-pages 5000
python scripts/domain_crawler.py --promote-candidates  # promote domains with 5+ backlinks
```

**Deployment:**
- Run on Azure7 in screen (state DBs on WD drive)
- Index to Apollo9 ES via Tailscale
- Log to `/home/mark/domain-crawler.log`

---

## Step 3: Quality Filter for Brave Auto-Index

**File:** `profoundd/app.py` (lines ~850-872)

Before auto-indexing Brave results, filter out:

**Skip domains:**
- reddit.com, quora.com, pinterest.com (forums/Q&A, low value for re-indexing)
- facebook.com, twitter.com, x.com, instagram.com (social — can't crawl further)
- youtube.com (videos — unless we fetch transcript)
- amazon.com, ebay.com, walmart.com (shopping)
- wikipedia.org (already good search, don't re-host)

**Skip content:**
- Title or summary < 50 chars
- URL matches search/tag/category regex (`/search/`, `/tag/`, `/category/`)

**Mark with tier:**
- If domain in Tier 1 master list → tag as `tier1-brave` (preferred)
- Otherwise → tag as `tier2-brave` (will get deprioritized in ranking)

---

## Step 4: Link Graph Discovery Table

**File:** `profoundd/utils/models.py`

Add a new SQLite table:
```python
class DiscoveredDomain(db.Model):
    domain = db.Column(db.String(200), primary_key=True)
    backlink_count = db.Column(db.Integer, default=0)
    first_seen = db.Column(db.DateTime, default=datetime.utcnow)
    last_seen = db.Column(db.DateTime)
    status = db.Column(db.String(20), default='candidate')  # candidate | promoted | excluded
    sample_linking_urls = db.Column(db.Text)  # JSON array of first 5 URLs that linked to it
```

The domain crawler populates this. Admin can review candidates at `/admin/discovered-domains` and promote to Tier 1.

---

## Step 5: Admin Dashboard for Crawler

**File:** `profoundd/admin/routes.py` and template

Add `/admin/crawler` page showing:
- Tag counts from ES (already have the query)
- Per-domain article counts (ES aggregation)
- Candidate domains from discovery (with backlink counts)
- Manual "Crawl now" button per domain
- Crawler logs tail

---

## Step 6: Add `/bot` Info Page

Already in previous plan — keep it. Webmasters who see `ProfounddBot/1.0 (+https://profoundd.com/bot)` in their logs will visit that URL. Be a good citizen:
- Who we are, what we crawl, rate limits
- Contact to request removal or delay
- robots.txt policy

---

## Files to Modify / Create

| File | Change |
|------|--------|
| `scripts/domain_crawler.py` | NEW — main crawler with discovery |
| `scripts/domain_crawler_config.json` | NEW — 150+ Tier 1 domain configs |
| `profoundd/app.py` | Quality filter on Brave auto-index, `/bot` route |
| `profoundd/utils/models.py` | Add `DiscoveredDomain` table |
| `profoundd/admin/routes.py` | Add `/admin/crawler` dashboard |
| `profoundd/admin/templates/crawler.html` | NEW — crawler dashboard |
| `profoundd/admin/templates/discovered_domains.html` | NEW |
| `profoundd/frontend/templates/bot.html` | NEW |
| `plans/crawler-architecture.md` | NEW — architecture docs |

## Scale Expectations
- 150 Tier 1 domains × avg 5,000 articles each = **~750K articles target** (7.5x current index)
- At 8 workers × 1 req/sec = 28K pages/hour theoretical, ~3 days for full initial crawl
- With retry/failure/rate-limit realities: ~1-2 weeks for complete first pass
- Discovered domains from link graph: likely 500-2000 candidates in first month
- ES storage: ~2-3 GB for 750K articles (current: 180 MB for 98K)

## Verification
1. Run `domain_crawler.py --domains judicialwatch.org --max-pages 100` as smoke test
2. Check ES: should see 100 new articles with `tags: ["targeted-crawl"]`
3. Check discovery table: should have ~10-20 candidate domains
4. Full run: `--domains all` in screen on Azure7
5. After 3 days: check per-domain counts, promote well-linked candidates
6. After 2 weeks: search queries that used to only return Brave results now return local results
