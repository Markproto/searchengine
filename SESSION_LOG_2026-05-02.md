# Profoundd Session Log — 2026-05-02

## Summary
Two threads. Morning: local-events search fix triggered by an "escrima class
Santos Community Center Medford OR" query that returned unrelated junk, plus
several pre-existing bugs uncovered in the maps tab + search logging.
Afternoon: Azure7 mirror went dark for 15h, dug into root cause, then turned
the post-mortem into structural fixes (per-host compose files, fail-fast stub,
watchdog cron). 6 commits.

---

## 1. Local events / maps tab search

### What was wrong
- `escrima class Santos Community Center Medford OR` (and any class/workshop
  query) returned "completely unrelated" results.
- Three layered causes:
  1. OSM business index has no event/class metadata, so the maps tab's
     `search_businesses()` couldn't match anything event-shaped.
  2. The default search route used Brave's **suggest** API (`api/suggest` —
     autocomplete) as the web fallback. SearXNG was only used after Brave
     returned empty, which it rarely did.
  3. `search_businesses()` silently threw `KeyError('_source')` whenever a
     location cookie was set, because ES drops `_source` from hits when
     `script_fields` is defined unless the request opts in. **This had been
     quietly breaking the maps tab + business side panel for anyone with a
     location cookie.**

### Event intent detection (`ad548a7`)
- **Files:** `profoundd/search/local_intent.py`, `profoundd/app.py`
- `EVENT_KEYWORDS` set: `class, classes, lesson(s), workshop(s), seminar,
  course, camp, meetup, event(s), training, coaching, tutorial, bootcamp,
  clinic, lecture(s), session, practice, schedule, calendar`
- `ACTIVITY_KEYWORDS` set: martial arts (escrima, jiu jitsu, karate, krav
  maga, muay thai, capoeira, …), movement/fitness (pilates, zumba, tai chi),
  dance (ballet, salsa dance, contra dance, ballroom), crafts (pottery,
  ceramics, watercolor), music lessons (guitar/piano/voice/drum)
- `detect_local_intent()` now returns `is_event` + `event_term` alongside
  `is_local`, `city`, `business_type`, `clean_query`
- New helper `build_event_web_query()` augments the query with the user's
  location city when the query doesn't name one

### `search_businesses` `_source` bug (`2d66385`)
- **File:** `profoundd/search/engine.py`
- Added `body["_source"] = True` in the geo-distance branch where
  `script_fields` is defined. ES then includes `_source` in each hit and the
  loop at line 1796 stops crashing.
- Fixed retroactively: any user with a location cookie has had this broken
  for an unknown number of weeks/months.

### Maps tab now always runs a real web search (`303f39a`)
- **File:** `profoundd/app.py` (maps tab handler ~line 787)
- Before: only ran web search when `is_event` was true. For non-event
  queries, `results.articles` was `[]`, which tripped the template gate at
  `search.html:271` (`{% if results and results.articles %}`) — so even when
  `business_results` had 20 hits, the panel never rendered.
- After: always issue a SearXNG (real engines) search with the location-
  augmented query. Brave's suggest API is now only the fallback.
- Result: maps tab works like Google-style local — real web pages in the main
  pane plus the business panel on the side.

### Search logging — broken since Apr 18 (`ad548a7`)
- **File:** `profoundd/app.py` `_bg_log_search` (~line 1093)
- Background thread tried to use `db.session.add()` outside Flask's app
  context. Logged tracebacks every search since 2026-04-18 02:35 — 14 days of
  zero analytics rows.
- Fix: wrapped with `with app.app_context():`. Verified new entries landing.

### SearXNG durability (`b36a78c`)
- **File:** `docker-compose.prod.yml` (now `apollo9.yml`)
- The maps tab's web fallback was reaching SearXNG via the docker bridge
  network, which isn't durable across compose restarts.
- Added `extra_hosts: host.docker.internal:host-gateway`. Updated
  `searxng_url` SiteSetting to `http://host.docker.internal:8080`.
- Verified: `urllib.request.urlopen('http://host.docker.internal:8080/healthz')`
  returns OK from inside the profoundd container.

### Verified live
- `https://profoundd.com/search?q=escrima+class+Santos+Community+Center+Medford+OR&tab=maps`
  returns 16 web result-cards + 10 businesses (40 `biz-name` markers).
- Top results include `medfordoregon.gov/.../Santo-Community-Center_`,
  `medfordoregonkungfu.com/tag/escrima-medford-oregon/`,
  `eliteacademyofmartialarts.com/kali-escrima-arnis/` — exactly the right thing.

### Recent searches reviewed (zero-result, since 2026-04-12)
Notable opportunities (not addressed this session, just logged here):
- `white hous east egg rule` — typo for "easter egg roll"; no spell-correction
  fallback on zero-result pages
- `KMED Education ROB`, `Rob KMED oregon eduction` — local Medford radio
  (Bill Meyer / KMED) content not indexed
- `tell me more about the new financial mechanism that went into effect on
  April 10th, that allows betting against the private credit market` —
  long-form natural-language queries get 0 results; no semantic / AI fallback
- Sports queries (`paige wwe`, `myles garrett contract`,
  `penguins playoff schedule`, etc.) — sports coverage is thin

---

## 2. Azure7 mirror outage — diagnosis + structural fixes

### Outage (`02:01 UTC` to `~17:00 UTC` 2026-05-02)
- Both `profoundd` + `profoundd-es` got `docker stop`'d at 02:01 UTC.
  `hasBeenManuallyStopped=true` flag set, so `unless-stopped` policy refused
  to bring them back.
- ~16:40 UTC: recovery attempt used **the wrong compose file** —
  `docker-compose.prod.yml` (Apollo9's), which binds `100.117.127.32:9201`
  (Apollo9 Tailscale) and `10.99.0.2:9201` (Apollo9 WireGuard). Azure7 has
  neither. Bind failed: `cannot assign requested address`.
- profoundd-es entered restart loop (15,022 attempts in journal), eventually
  hitting a separate ES log4j `getLocalHostname()` race: `UnknownHostException:
  b1dd78e3f23d` — Docker hadn't put the container's short-ID hostname into
  `/etc/hosts` before log4j probed.
- Azure7's existing `health-monitor.sh` cron DID alert (BigfootChat + email)
  but only **alerts** — does not auto-recover. So the mirror sat down for 15h.

### Diagnosis written
- `plans/azure7-mirror-resilience.md` — full timeline, three layered root
  causes, plan with immediate / short-term / medium-term / longer-term steps.

### Per-host compose files + fail-fast stub (`7c9e0f3`)
Make the wrong-file mistake structurally impossible:
- `git mv docker-compose.prod.yml → docker-compose.apollo9.yml` (host-named)
- `docker-compose.azure7.yml` committed to repo (was previously only on the
  host, untracked). Includes `restart: always` (mirror should never stay
  deliberately down) and `hostname: profoundd-es` (sidesteps the log4j DNS
  race).
- `docker-compose.dev.yml` carries the previous local-dev config.
- `docker-compose.yml` is now a **fail-fast stub**: running
  `docker compose up` with no `-f` runs an `alpine` service that prints which
  file to pick and `exit 1`s. Verified — does what it says.
- `Makefile` with `up-{apollo9,azure7,dev}`, `down-*`, `logs-*`, `ps`. So the
  `-f docker-compose.<host>.yml` selection is automatic.
- Updated `README.md` and `HANDOFF.md` references from `prod.yml` →
  `apollo9.yml`.
- Also added `hostname: profoundd-es` to the Apollo9 compose (same race could
  in theory hit production).
- Deployed both hosts: Apollo9 + Azure7 reattached cleanly to the same
  project name (`profoundd-build`, derived from the directory) so existing
  volumes/networks survived. Apollo9 ran `make up-apollo9`; Azure7 doesn't
  have `make` installed so used `docker compose -f docker-compose.azure7.yml
  up -d --build` directly.

### Watchdog cron (`b0704e3`)
- **File:** `scripts/profoundd-watchdog.sh` → installed at
  `/home/mark/profoundd-watchdog.sh` on both hosts.
- Cron: `*/5 * * * * /home/mark/profoundd-watchdog.sh` on both hosts.
- Picks the compose file by `hostname -s` (apollo9 → apollo9.yml,
  azure7 → azure7.yml).
- No-op when both `profoundd` + `profoundd-es` are in `docker ps`. Otherwise
  runs `docker compose -f <host>.yml up -d`, waits 30s, verifies, notifies.
- **Thrash guard**: max 3 restarts in a 30-min window. After that, refuses
  to auto-restart and pages a human via `~/notify.sh` (Azure7 has it; Apollo9
  falls back to log-only — see follow-up #3 below).
- Distinct from Azure7's existing `health-monitor.sh`: that script alerts but
  does not recover. The watchdog is the recovery half. They coexist.

### Verified state after fixes
- Apollo9: `profoundd` + `profoundd-es` healthy, live site HTTP 200, escrima
  query still returns the right results.
- Azure7: `profoundd-es` healthy, `restart=always`, `hostname=profoundd-es`,
  ES data volume intact (4,505 docs).
- Watchdog dry-run on both hosts returns 0 (no-op).
- Fail-fast stub tested: `docker compose up` (no `-f`) prints the helpful
  hint and exits 1.

### Three failure modes from the outage — now structurally blocked
| Cause | Mitigation |
|---|---|
| Wrong compose file used (`prod.yml` on Azure7) | `prod.yml` no longer exists; fail-fast stub catches `compose up` w/o `-f` |
| `unless-stopped` won't auto-restart after manual stop | Azure7 → `restart: always`; watchdog restarts on next cron tick regardless |
| ES log4j hostname race | `hostname: profoundd-es` set on both hosts |
| 15h gap between outage and recovery | Watchdog auto-recovers within 5 min on both hosts |

---

## Commits (in order)

```
ad548a7  Local events/classes search + fix SearchLog Flask context bug
2d66385  Fix search_businesses _source missing when location is set
303f39a  Always run location-augmented web search on maps tab
b36a78c  Make SearXNG reachable via host.docker.internal in profoundd compose
7c9e0f3  Per-host compose files + Makefile + fail-fast stub
b0704e3  Profoundd container watchdog cron — auto-recover on outage
```

All on branch `claude/custom-news-search-engine-YWN94`, pushed to Apollo9
bare repo, deployed to Apollo9 + Azure7.

---

## Follow-ups (documented, not done this session)

1. **Spell-correction / `did_you_mean`** on zero-result pages. ES has
   `suggest`/`fuzziness` already on some paths; expose it on the empty-state.
2. **Long natural-language queries** — when query >12 words and 0 results,
   route to AI-explain or relax to OR-of-terms.
3. **Apollo9 lacks `~/notify.sh`** (Azure7 has SendGrid + BigfootChat +
   webhook). Watchdog falls back to log-only on Apollo9. Port `notify.sh` so
   Apollo9 outages page out, not just log.
4. **`make` not installed on Azure7** — minor. `apt install make` activates
   the Makefile shortcuts.
5. (Optional) `docker events --filter event=die --filter name=profoundd`
   real-time listener as a systemd unit, if 5-min watchdog latency proves
   too slow for production. Skipped for now.
6. **Pre-commit lint**: fail if a compose file's host bindings reference
   IPs not present on the host the file is named for.
7. Search analytics: sports content thin, KMED Medford radio not indexed,
   Oregon-officials-in-Epstein-files returns 0 results despite 12,976
   epstein hits in "all" tab — worth re-tuning the epstein-files index
   queries.
