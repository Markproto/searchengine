# Azure7 Profoundd Mirror — Outage Diagnosis & Resilience Plan

## What happened (2026-05-02)

**Timeline (UTC):**
- `02:01:15` — first profoundd container received SIGTERM. `hasBeenManuallyStopped=true` flag set on all three (profoundd, profoundd-es, frontdesk-db).
- `02:01:34` — profoundd briefly auto-started, was stopped again 4s later.
- `02:01:38` — profoundd manually stopped.
- `15:39:41`–`16:40:11` — multiple stop/start attempts, eventually using `docker-compose.prod.yml` (Apollo9's compose).
- `16:40:14` — `docker compose up` failed: `failed to bind host port 10.99.0.2:9201/tcp: cannot assign requested address`. Apollo9's WireGuard IP isn't on Azure7 (Azure7's WG IP is `10.99.0.3`).
- `16:40:14` onwards — profoundd-es entered restart loop, but inside the container ES died with `UnknownHostException: b1dd78e3f23d` — log4j couldn't resolve the container's own hostname during `InetAddress.getLocalHost()`.
- `16:51:36` — I stopped the loop (`docker rm -f profoundd-es`).

**Status now:** profoundd + profoundd-es are both stopped on Azure7. The mirror is offline.

## Root causes (three, layered)

1. **Manual stop with `unless-stopped` restart policy.** When `docker compose stop` (or any explicit stop) runs, Docker sets `hasBeenManuallyStopped=true`. After that, daemon restarts and reboots will **not** bring the container back. Only an explicit `docker start` / `docker compose up` clears the flag.

2. **Wrong compose file used during recovery.** Azure7 has two compose files:
   - `docker-compose.azure7.yml` — correct for Azure7. Binds `127.0.0.1:9201`. Volumes are `external: true` (named `profoundd-data`, `profoundd-es-data`).
   - `docker-compose.prod.yml` — Apollo9's. Binds `100.117.127.32:9201` (Apollo9 Tailscale) and `10.99.0.2:9201` (Apollo9 WireGuard). Volumes get prefixed `profoundd-build_*`.

   Running `prod.yml` on Azure7 fails to bind the host ports AND uses a different set of volumes (so any data written through it would not be visible to azure7.yml).

3. **ES log4j hostname resolution.** Once the wrong-compose attempt is made, the container ID changes and the leftover restart loop can hit Docker's intermittent issue where the container's own short-ID hostname isn't in `/etc/hosts` yet at boot. ES's log4j `getLocalHostname()` blows up before logging is even configured.

## Plan to prevent recurrence

### Immediate (this session, with your approval)
1. Bring profoundd back up cleanly with the right compose file:
   ```
   cd /home/mark/profoundd-build
   docker compose -f docker-compose.azure7.yml up -d
   ```
2. Confirm both `profoundd` and `profoundd-es` are healthy and the data volume (`profoondd-es-data`) is intact.

### Short-term hardening (this week)
1. **Make the wrong compose impossible.** Rename the files so each is unambiguous:
   - `docker-compose.prod.yml` → `docker-compose.apollo9.yml`
   - Add a stub `docker-compose.yml` at repo root that errors out: `echo "Use -f docker-compose.{apollo9,azure7}.yml" && exit 1`.
   - Or commit a `Makefile` with `make up-azure7` / `make up-apollo9` targets that pick the right file.
2. **Add `hostname:` to the compose's elasticsearch service** so log4j has a stable name to resolve, sidestepping the Docker DNS race.
3. **Switch restart policy on Azure7 from `unless-stopped` to `always`** — for a hot-standby mirror, the operator never wants a "stay down after manual stop" semantic. `always` brings it back on reboot regardless.

### Medium-term (this month)
1. ~~**systemd unit** wrapping `docker compose up`~~ — Skipped. With `restart: always` on the containers and `docker.service` enabled, boot recovery is already handled. The systemd unit added no coverage that the existing setup doesn't already give.
2. **Watchdog cron — DONE 2026-05-02.** `scripts/profoundd-watchdog.sh` installed on both hosts at `/home/mark/profoundd-watchdog.sh`, cron `*/5 * * * *`. Auto-recovers `profoundd` + `profoundd-es` when either is missing, picks the right `docker-compose.<host>.yml` based on `hostname -s`, includes a thrash guard (max 3 restarts per 30 min before paging a human), and routes through `~/notify.sh` when present (Azure7) — log-only otherwise (Apollo9, see follow-up below).
3. **Notification on container exit.** Skipped for now. The watchdog catches outages within 5 min, which is acceptable for a hot-standby. If we ever want real-time, drop a `docker events --filter event=die --filter name=profoundd` listener as a systemd unit that pipes into `~/notify.sh`. Worth adding only if 5-min latency proves too slow.

### Longer-term
1. **Apollo9 ↔ Azure7 mesh stability** is already a tracked project (per memory). Once stable, the failover should auto-promote Azure7 and notify on Apollo9 outage.
2. **Per-host compose-file linting in CI**: pre-commit hook that fails if a compose file's host bindings reference IPs not present on the host the file is named for.
3. **Apollo9 lacks `~/notify.sh`** (Azure7 has the SendGrid + BigfootChat + webhook one). The profoundd-watchdog falls back to log-only on Apollo9. Port `notify.sh` over so Apollo9 outages are also paged out, not just logged.
4. **Azure7's `health-monitor.sh` already alerts on profoundd stop** but does NOT auto-recover — only alerts. The new watchdog is the recovery half. They're complementary; no need to merge.

## Existing watchdog (worth knowing)

`/home/mark/docker-net-watchdog.sh` runs every 10 min via `crontab -u mark`. It detects containers that lost their network (`NetworkSettings.Networks == {}`) and `docker network connect` + `docker restart` them. **Limitation: it only acts on already-running containers — it cannot start a stopped container.** That's why this outage (containers fully stopped, not just netless) wasn't caught.

Also: `/var/log/docker-watchdog.log` doesn't exist, suggesting the cron may not actually be running (path/permissions, or it has nothing to log because nothing's broken in the way it checks for). Worth verifying as part of step 2 below.

## Open questions
- Was the 02:01 stop intentional (a manual `compose stop` you forgot about) or scripted? The docker-net-watchdog runs `docker restart` not `docker stop`, so it shouldn't set `hasBeenManuallyStopped=true`. Either you ran a stop manually, or some other tool did. Worth checking shell history / journal of `mark`'s session.
- Should Apollo9 and Azure7 share a single `docker-compose.yml` with environment-driven port binding, or stay as separate files? (Either works; separate is simpler to read but easier to mix up.)
