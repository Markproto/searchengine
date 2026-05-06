# Session Log — 2026-05-06 (terminal C)

> Companion to `SESSION_LOG_2026-05-02.md` (escrima fix + Azure7 outage)
> and `SESSION_LOG_2026-05-06.md` (AI Explain refactor + Cloudflare bot
> control). This terminal landed the FWP / Archive / COVID / Audio→Bob
> stack between 2026-05-04 and 2026-05-06, plus the cross-thread
> reconciliation work today.

---

## What this terminal built (verified live)

### 1. Apollo9 catch-up deploy (2026-05-04)

- 5 commits stuck on local branch `claude/custom-news-search-engine-YWN94` got pushed to apollo9 master + deployed
- Includes Grok fallback for AI Explain, Epstein principal alias expansion, Anthropic 529 graceful handling, Sonnet 4.6 model bump, name nickname expansion
- Azure7 mirror was actually already up (memory was stale); resynced

### 2. Sourcing expansion — `f7394f1`

24 new RSS sources + 2 domain-crawler seeds covering reader-base gaps:
- **Politics — Agenda 2030 critics**: Brownstone, Reclaim The Net, The Counter Signal, Winter Oak, Iain Davis, The Exposé, Activist Post, plus Eagle Forum
- **Education — critical of unions/DOE**: Free Press, City Journal, Christopher Rufo, American Mind, The 74, Education Next, Reason
- **Environment — climate-skeptic**: CO2 Coalition, Watts Up With That, Climate Depot, Climate Realism, Judith Curry, JunkScience, NoTricksZone, Manhattan Contrarian, Real Climate Science
- Domain crawler: defendinged.org, co2coalition.org

### 3. Domain crawler audit + repair — `f1d4e57`, `d035f49`, `bd030a1`

- Identified 14 stalled domains across 3 failure modes (UA-403, IPv6 URL crash, JS-rendered homepage). Ship 3 patches:
  - Browser-UA fallback retry on 403/406/429
  - urlparse guard for malformed IPv6 hrefs (fixes nypost, theintercept, americanthinker, consortiumnews, legalinsurrection, unlimitedhangout)
  - `seed_depth=True` lenient link discovery on homepages
- Net: 67 → 59 domain crawler entries. Dropped dead (mailtribune.com NXDOMAIN, empoweroversite.org NXDOMAIN), JS-rendered (cato.org, mercola.com), hard-blocked (heritage.org, newsmax.com).

### 4. IPRoyal residential proxy fallback — `63d852f`

- `domain_crawler.fetch_page` and `feed_crawler.fetch_feed` now route through IPRoyal residential proxy as final attempt when direct fails
- Credentials sourced from coin-appraiser DB (per user authorization), set in `.env.prod` on apollo9 + azure7
- Verified: off-guardian.org went from "Connection refused" → 200 OK

### 5. Federal Writers' Project (FWP) — `35da6a6`, `91040ad`

Searchable mirror of LOC's American Life Histories collection (~2,000 items):
- `scripts/fwp_indexer.py` walks LOC collection API, fetches per-page OCR text via word-coordinates service, bulk-indexes
- `engine.search_fwp_docs()` with OCR-tolerant fuzzy fallback (same approach as Epstein)
- `/fwp-docs` index + `/fwp-docs/<item_id>` viewer + `/api/fwp-explain`
- Phase 2 (Slave Narratives) + Phase 3 (State Guides) deferred per `project_fwp.md` memory

### 6. Archive collections — `708976a`, `1d2f906`, `07d0ad0`, `1fbecc2`

Unified `profoundd_archive_docs` index with `collection` discriminator so every future doc collection lands in one place:
- **FBI Vault** (`scripts/fbi_vault_indexer.py`): walks vault.fbi.gov/sitemap.xml.gz (~11.9K URLs), each URL serves PDF directly. ~9K docs ingested. PDFs are scanned image-only — title + case + sub_case are searchable, body text would require OCR.
- **SPLC Wayback** (`scripts/splc_wayback_indexer.py`): queries Wayback CDX, dedupes via `collapse=urlkey`, fetches latest 200 snapshot via `id_` flag, extracts main-text via BS4. ~7,830 docs indexed before archive.org IP-blocked apollo9.
- `/archive-docs` cross-collection + `/archive-docs/<collection>` filtered views

### 7. COVID primary-source mirror — `ca125a0`

Three new collections in the unified archive index, all bulk-download endpoints:
- **PHMPT** (4 collection pages, ~thousands of static phmpt.org/wp-content PDFs)
- **CDC ACIP** (cdc.gov/acip/ crawl, ~500 PDFs across agendas/slides/recs)
- **NIH RePORTER** (~16 pandemic-relevant search terms × FY 2019-2024, JSON API)

Hoisted `extract_pdf_text`, `bulk_index`, `init_state` etc. into `scripts/_archive_indexer_common.py` so future indexers (CIA CREST, JFK Records, GovInfo, etc.) only need their listing function.

FDA VRBPAC + CDC FOIA library probed — bulk endpoints are JS-rendered or non-existent. Deferred.

### 8. Audio→Bob URL / Podcast / YouTube ingest — `2e255d0`, `481a295`

Three new entry points to existing audio→Bob pipeline:
- `profoundd/search/media_ingest.py` — `fetch_audio_bytes`, `parse_podcast_feed`, `extract_video_audio` (yt-dlp), URL classifier
- `/admin/bob/audio/ingest-url` — single field, auto-routes to direct/RSS/YouTube
- `/admin/bob/audio/podcast` — paste an RSS feed → episode picker
- ffmpeg auto-recompress when audio > Groq's 25 MB limit (32 kbps mono, ~13 MB/hr)
- Dockerfile gains `ffmpeg` + `yt-dlp 2026.03.17`

**Demo**: AJC's "Who Blew Up The Guidestones?" Episode 1 ingested end-to-end:
- 45.9 MB MP3 → 7.6 MB recompressed → 22,083-char Whisper transcript → BobStory id=18 in draft

### 9. Cross-thread reconciliation (today, 2026-05-06)

- Wayback-tidings (other terminal's screen) was failing for hours against apollo9 archive.org block — stopped at user request
- Restarted partial COVID + FWP indexers (rebuild had killed them mid-run); all resumable via SQLite state
- gdelt-crawler healthy, left alone

### 10. Solo-list cleanup (today)

Eight items from the combined session-log follow-up backlog:

| Item | Status |
|---|---|
| `apt install make` on azure7 | done — `make help` works |
| Port `notify.sh` to apollo9 (BigfootChat alerting) | done — verified test ping landed in BigfootChat |
| Pre-commit lint for compose host bindings | done — `scripts/validate_compose_hosts.py` + `scripts/install_pre_commit_hook.sh` |
| Spell-correction on zero-result pages | done — async `/api/spelling` rendered as "Did you mean…" |
| Long-query (>12w, 0 results) → relax-to-OR fallback | done — auto-relaxes natural-language queries with min-should-match heuristic, banner explains |
| "Oregon officials in Epstein files" zero-result tune | already fixed by Epstein alias expansion (`ad3d581`) — returns 20+ now |
| FDA VRBPAC + CDC FOIA endpoints | investigated, blocked at source (JS-rendered, no public library) |
| Restart 4 partial indexers | running detached on apollo9 |

---

## Currently running on apollo9

| Process | Owner | Status |
|---|---|---|
| `phmpt_indexer.py` (in container) | this terminal | Running — ~40% → 100% |
| `acip_indexer.py` (in container) | this terminal | Running — ~90% → 100% |
| `nih_grants_indexer.py` (in container) | this terminal | Running — ongoing through 16 terms × FY 2019-2024 |
| `fwp_indexer.py` (in container) | this terminal | Running — ~80% → 100% |
| `gdelt-crawler` screen | other terminal | Healthy |
| `wayback-tidings` screen | other terminal | **Stopped** (was failing on archive.org block) |

---

## Outstanding follow-ups (carry forward)

User actions:
1. Paste GSC token at `/admin/seo`
2. Paste Bing token at `/admin/seo` (optional — IndexNow already feeds Bing)
3. Get a backlink (HN / Substack / personal blog)
4. Hardline Tark1 → flip `AI_EXPLAIN_PROVIDER=ollama`

Out-of-scope items deferred:
- FBI Vault OCR (image-only PDFs — needs tesseract, ~25-100 hrs apollo9 CPU; better on Tark1 GPU once available)
- FDA VRBPAC scrape (needs Playwright / JS-rendered)
- CDC FOIA library mirror (no public bulk endpoint)
- Sports content / KMED Medford radio (sourcing items)

---

## File location

`SESSION_LOG_2026-05-06b.md` — `b` suffix to distinguish from the
existing 5/6 log without colliding. Same dating convention as siblings.

Update or open a new dated log when other terminals finish substantive
work so the cross-terminal coordination story stays in one searchable
place.
