#!/bin/bash
# Profoundd container watchdog
#
# Checks that both `profoundd` and `profoundd-es` are running. If either is
# missing, brings the stack up via the host-appropriate compose file and
# fires a notification through ~/notify.sh (Azure7) — falls back to log-only
# on hosts without notify.sh.
#
# Recommended cron (in `mark`'s crontab):
#   */5 * * * * /home/mark/profoundd-watchdog.sh
#
# Designed to be safe to run as often as you like — it's a no-op when both
# containers are already running. Includes a thrash guard so a chronic crash
# loop doesn't restart-storm the host (and instead pages a human).

set -euo pipefail

PROFOUNDD_DIR="${PROFOUNDD_DIR:-/home/mark/profoundd-build}"
LOG="${LOG:-/var/log/profoundd-watchdog.log}"
STATE_DIR="${STATE_DIR:-/var/lib/profoundd-watchdog}"
NOTIFY="${NOTIFY:-$HOME/notify.sh}"
THRASH_WINDOW_SEC=1800     # 30 minutes
THRASH_MAX_RESTARTS=3
EXPECTED_CONTAINERS=(profoundd profoundd-es)

# Make sure log + state dir exist (best-effort — won't fail script if not writable)
sudo mkdir -p "$STATE_DIR" 2>/dev/null || mkdir -p "$STATE_DIR" 2>/dev/null || STATE_DIR="$HOME/.profoundd-watchdog"
mkdir -p "$STATE_DIR" 2>/dev/null || true
sudo touch "$LOG" 2>/dev/null && sudo chmod 666 "$LOG" 2>/dev/null || LOG="$HOME/profoundd-watchdog.log"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG"; }

notify() {
    local level="$1" subject="$2" body="$3"
    log "[$level] $subject -- $body"
    if [ -x "$NOTIFY" ]; then
        "$NOTIFY" "$level" "$subject" "$body" || log "notify.sh returned non-zero"
    fi
}

# Pick the right compose file based on hostname
host="$(hostname -s)"
case "$host" in
    apollo9) compose_file="docker-compose.apollo9.yml" ;;
    azure7)  compose_file="docker-compose.azure7.yml" ;;
    *)
        log "ERROR: unknown host '$host' — extend the case in this script"
        exit 2
        ;;
esac

if ! cd "$PROFOUNDD_DIR" 2>/dev/null; then
    log "ERROR: cannot cd to $PROFOUNDD_DIR"
    exit 3
fi

if [ ! -f "$compose_file" ]; then
    log "ERROR: $PROFOUNDD_DIR/$compose_file does not exist"
    exit 4
fi

# What's running?
running=$(docker ps --format '{{.Names}}' 2>/dev/null | sort -u || true)
missing=()
for c in "${EXPECTED_CONTAINERS[@]}"; do
    if ! echo "$running" | grep -qx "$c"; then
        missing+=("$c")
    fi
done

if [ ${#missing[@]} -eq 0 ]; then
    # All expected containers are running. Nothing to do.
    exit 0
fi

log "MISSING: ${missing[*]}"

# Thrash guard — refuse to restart if we've already restarted N times recently
state_file="$STATE_DIR/restart-history"
now=$(date +%s)
cutoff=$((now - THRASH_WINDOW_SEC))
touch "$state_file"
awk -v c="$cutoff" '$1 >= c' "$state_file" > "${state_file}.tmp" && mv "${state_file}.tmp" "$state_file"
recent=$(wc -l < "$state_file" | tr -d ' ')

if [ "$recent" -ge "$THRASH_MAX_RESTARTS" ]; then
    notify "CRITICAL" \
        "Profoundd thrashing on $host" \
        "Refusing to auto-restart — already restarted $recent times in the last $((THRASH_WINDOW_SEC/60)) min. Missing: ${missing[*]}. Manual investigation required."
    exit 5
fi

log "RECOVERING via $compose_file (recent restarts in window: $recent)"
echo "$now" >> "$state_file"

if docker compose -f "$compose_file" up -d >>"$LOG" 2>&1; then
    # Wait briefly for healthchecks to start passing
    sleep 30
    after=$(docker ps --format '{{.Names}}' 2>/dev/null | sort -u || true)
    still_missing=()
    for c in "${EXPECTED_CONTAINERS[@]}"; do
        echo "$after" | grep -qx "$c" || still_missing+=("$c")
    done

    if [ ${#still_missing[@]} -eq 0 ]; then
        notify "RECOVERY" \
            "Profoundd recovered on $host" \
            "Brought ${missing[*]} back up via $compose_file. Restarts in last ${THRASH_WINDOW_SEC%??} min: $((recent + 1))/$THRASH_MAX_RESTARTS."
    else
        notify "CRITICAL" \
            "Profoundd recovery FAILED on $host" \
            "compose up returned 0 but these are still missing: ${still_missing[*]}"
        exit 6
    fi
else
    notify "CRITICAL" \
        "Profoundd compose up failed on $host" \
        "docker compose -f $compose_file up -d exited non-zero. See $LOG."
    exit 7
fi
