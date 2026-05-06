# Session Log — 2026-05-06

> Index of work tracked across terminals. This session logged the
> AI-Explain / Related-Docs / SEO-prep batch (originally landed on the
> 2026-04-24 working tree). Several other terminals have added
> substantial features since — those are listed under "Concurrent work
> elsewhere" so you can reference what's already in-tree before
> duplicating.

---

## What this session built (verified live)

### 1. Apr 18 → Apr 19 traffic drop — diagnosed, no fix needed

- DailyStats: Apr 18 humans 392 → Apr 19 110 (−72%); bots 81,577 → 5,436 (−93%)
- Root cause: Apr 17 commit `0b09f74` added Facebook's `meta-webindexer` to robots.txt with `Crawl-delay: 10`. Their bot cached the old robots.txt for ~12h, re-fetched at 08:00 UTC Apr 18, obeyed → went from 249,778 hits/day to 1
- **No reach-driving search engine was cut.** Googlebot, Bingbot, Applebot, GPTBot, ClaudeBot, PerplexityBot all untouched. Amzn-SearchBot actually grew 2,516 → 3,489
- "Real humans" in PageView (the JS-beacon counter) was 33 on Apr 18 — DailyStats was inflated by misclassified crawlers

### 2. robots.txt — explicit welcome for LLM crawlers

- `profoundd/app.py` `robots_txt()` now lists 16 AI / answer-engine crawlers under `Allow: /` blocks: GPTBot, OAI-SearchBot, ChatGPT-User, ClaudeBot, claude-web, anthropic-ai, PerplexityBot, Perplexity-User, Google-Extended, GoogleOther, Applebot, Applebot-Extended, DuckAssistBot, Bingbot, Meta-ExternalAgent, YouBot, Amazonbot
- Training crawlers carry `Crawl-delay: 2`; user-directed fetchers (ChatGPT-User, Perplexity-User) have no delay
- Default Allow list expanded to include `/climate-docs/` and `/wef-docs/` (was only `/epstein-docs/`)

### 3. Cloudflare AI Bot Control — disabled (user did this in dashboard)

- Discovered: Cloudflare was injecting its own block above ours, with `Disallow: /` for GPTBot, ClaudeBot, Google-Extended, Applebot-Extended, CCBot, Amazonbot, meta-externalagent + `Content-Signal: ai-train=no`
- Most robots.txt parsers honor first match, so our `Allow:` was never reached
- User toggled off in CF dashboard. Verified clean — every wanted bot now resolves to `Allow: /`

### 4. IndexNow ping fired

- Ran `scripts/submit_sitemaps.py` — HTTP 200, 8 URLs submitted
- Notifies Bing, Yandex, Yep, Seznam, Naver. Also feeds Brave Search via Bing
- Mojeek and Marginalia require manual submit (links printed by the script)

### 5. `detect_bot()` tightened (`profoundd/utils/bot_detection.py`)

- 28-test regression suite, 0 failures
- Added explicit substrings that previously slipped through:
  - `indexer|webindexer` (meta-webindexer was matching only via `/crawler` URL in UA — fragile)
  - LLM bots without "bot" substring: `chatgpt|claude-web|anthropic-ai|google-extended|googleother|applebot-extended|meta-externalagent|amazonbot|youbot|ccbot|searchgpt|copilot|gemini|duckassistbot`
  - HTTP-client UAs: `okhttp|apache-httpclient|java/|dalvik|node-fetch|axios`
  - Headless: `puppeteer|playwright`
  - Monitoring: `monitor|healthcheck`
- Effect: DailyStats "human" count should fall toward the PageView floor (~0-30/day)

### 6. Admin UI for search engine verification

- `/admin/seo` gained a "Search Engine Verification" section
- Three fields: `google_site_verification`, `bing_site_verification`, `yandex_verification`
- `base.html:48-50` renders all three meta tags conditionally
- Plumbing in place for the user to paste GSC/Bing tokens and run the rest of GSC submission

### 7. AI Explain refactor — `profoundd/search/ai_explain.py`

- Three near-duplicate endpoints (`/api/epstein-explain`, `/api/climate-explain`, `/api/wef-explain`) collapsed to ~12-line wrappers around one shared helper
- **Pre-flight guard**: normalize query, tokenize (≥3 chars, non-stopwords). If zero tokens appear in content → static msg, no LLM call
- **Smart content window**: ±4000 chars centered on first match instead of `content[:8000]` — catches matches deeper in long docs
- **Exact-key SQLite cache** keyed on `(doc_type, doc_id, page_number, normalized_query)`, 24h TTL via existing `cache.py`
- **LangChain wrapper**: `AI_EXPLAIN_PROVIDER` env (`anthropic` default, `ollama` for Tark1 once 24/7)
- New deps: `langchain`, `langchain-anthropic`, `langchain-ollama` in `requirements.txt`
- Verified live: pre-flight saves API call (0.38s, no spend), cold call 11.5s, cache hit 0.27s (**42× speedup**)

### 8. AI Explain summary fallback (your enhancement request)

- When pre-flight finds the search term nowhere on the page, helper now returns a **cached page summary** with a note instead of "not found"
- Summary cache key omits the query so every miss on the same page hits the same cached LLM response — bounded cost (one summary per page max)
- New `SUMMARY_PROMPTS` dict mirrors `PROMPTS` for each `doc_type`
- Response JSON: `summary_fallback: true` flag

### 9. Cross-collection Related Docs widget — `profoundd/search/related.py`

- New helper finds topically-related pages in every collection other than the source's via Elasticsearch More-Like-This
- Fires 4 parallel ES `search()` calls via `ThreadPoolExecutor` (msearch had ES-py 8.x format issues — see Gotchas)
- Cached 24h per `(source_type, source_id)`
- New `_related_docs.html` partial included in `epstein_doc.html`, `climate_doc_viewer.html`, `wef_doc_viewer.html`
- Verified: WEF Global Risks 2026 p.10 surfaces Ashland CEAP, Italy 2030 Epstein doc, Colombia mining article

### 10. Gotchas to remember

- **ES-py 8.x msearch** wants `searches=[{...}, {...}]` (list of dicts), not the old `body=` NDJSON string. Old format silently returns empty responses
- **Jinja2 `{% set %}` inside `{% for %}`** does NOT escape loop scope. Use `{% set ns = namespace(any=false) %}` then `{% set ns.any = true %}` for cross-iteration mutable state

---

## Concurrent work in other terminals (since 2026-04-24)

Major features that landed without going through this session — listed
so we don't redo them. From `git log`:

| Date | Commit | Feature |
|---|---|---|
| 2026-05-06 | `481a295` | media_ingest: ffmpeg auto-recompress when audio > Groq 25 MB |
| 2026-05-06 | `2e255d0` | URL / Podcast / YouTube ingest for Audio→Bob |
| 2026-05-06 | `ca125a0` | COVID primary-source mirror (PHMPT + ACIP + NIH RePORTER indexers) |
| 2026-05-05 | `1fbecc2` | FBI Vault: informative summary for image-only scanned PDFs |
| 2026-05-05 | `07d0ad0` | FBI Vault indexer: strip `/view` to fetch PDF binary |
| 2026-05-05 | `1d2f906` | FBI Vault indexer: sort URLs path-depth desc |
| 2026-05-05 | `708976a` | **Archive collections** — FBI Vault + SPLC Wayback indexers + unified UI |
| 2026-05-04 | `91040ad` | FWP viewer: shared `_related_docs` partial; fix unhashable slice |
| 2026-05-04 | `35da6a6` | **Federal Writers' Project (FWP)** mirror of LOC American Life Histories |
| 2026-05-04 | `63d852f` | IPRoyal proxy fallback in domain + feed crawlers |
| 2026-05-04 | `799c0be` | Drop revolver.news; diversify search results by source |
| 2026-05-04 | `f7394f1` | Sourcing expansion: Agenda-2030 critics, education, climate-skeptic |
| 2026-05-04 | `d035f49` | Cleanup stalled domain crawler seeds → RSS where possible |
| 2026-05-04 | `f1d4e57` | domain_crawler: browser-UA fallback + IPv6 guard + lenient seed crawl |
| 2026-05-04 | `bd030a1` | Drop dead Ian Carroll YouTube channel (404) |
| 2026-05-04 | `6ab5ea4` | **Grok fallback when Claude overloaded** — provider shown in UI |
| 2026-05-04 | `f7e512c` | Handle Anthropic 529 Overloaded gracefully on AI explain |
| 2026-05-03 | `ad3d581` | Epstein search: principal alias + OCR-tolerant fuzzy matching |
| 2026-05-03 | `209165d` | Name nickname expansion + Sonnet 4.6 model bump |
| 2026-05-02 | `b0704e3` | Profoundd container watchdog cron — auto-recover on outage |

### What that means for in-flight files

- `profoundd/search/related.py` — now also targets `profoundd_fwp_docs` and `profoundd_archive_docs`. Has `item_id` and `collection` fields in URL builders. Don't strip these out.
- `profoundd/search/ai_explain.py` — now has `_try_grok_fallback()` for when Claude returns 529 Overloaded. Default model is `claude-sonnet-4-6` (not `claude-sonnet-4-5-20250929` we set). UI shows provider badges (Claude / Grok / Cached).
- `profoundd/frontend/templates/_related_docs.html` — labels now include `fwp` (Federal Writers' Project) and `archive` (Archive Collections).
- `profoundd/frontend/templates/base.html` — navbar adds FWP and Archive nav links.
- `profoundd/frontend/templates/epstein_doc.html` — Explain UI now shows provider badge + Grok fallback warning when Claude couldn't answer.
- `scripts/domain_crawler.py` — now has IPRoyal proxy fallback, browser-UA fallback, IPv6 guard.
- New collections in tree: FWP (Federal Writers' Project) and Archive (FBI Vault + SPLC Wayback).
- New COVID mirror: PHMPT + ACIP + NIH RePORTER indexers (today's commit).

---

## Outstanding follow-ups (pre-existing, not yet picked up)

- **Google Search Console verification** — admin UI is ready; user pastes the token at `/admin/seo`, then submits sitemap-index.xml in GSC dashboard
- **Bing Webmaster Tools** — same flow; optional since IndexNow already feeds Bing
- **Backlinks** — zero external referrers since Apr 1; needs HN post / Substack / personal blog link
- **Tark1 24/7** — flip `AI_EXPLAIN_PROVIDER=ollama` once Tark1 has hardline. Mistral 7B Q4 on Apollo9 CPU is too slow (20–80 s) for interactive use
- **Memory `Next up` pointer** — `MEMORY.md` lists `project_next_up.md` (Sourcing expansion: thin categories Education / Environment / Tech / Science) — already partially addressed by `f7394f1`

---

## File location

This log: `SESSION_LOG_2026-05-06.md` in the repo root next to existing `SESSION_LOG_2026-04-10-11.md` and `SESSION_LOG_2026-04-15-17.md`.

Update this file (or open a new dated one) when other terminals finish substantive work so we have one place to scan before starting something that overlaps.
