#!/usr/bin/env bash
# Elasticsearch snapshot management for Profoundd
# Usage: es_snapshot.sh [create|restore|list|setup]
# Cron: 0 3 * * * /home/mark/profoundd-build/scripts/es_snapshot.sh create >> /var/log/es-snapshot.log 2>&1
#
# Snapshots are stored in a Docker volume on Apollo9's NVMe (fast).
# Lsyncd or a separate rsync cron syncs them to Azure7's WD 16TB drive
# at /mnt/backup/apollo9/es-snapshots/ for cold archive.

set -euo pipefail

ES_URL="http://127.0.0.1:9201"
REPO_NAME="profoundd_backup"
SNAPSHOT_PREFIX="profoundd"
KEEP_SNAPSHOTS=14  # Keep last 14 daily snapshots locally

log() { echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] $*"; }

setup_repo() {
    log "Registering snapshot repository '$REPO_NAME'..."
    curl -s -X PUT "$ES_URL/_snapshot/$REPO_NAME" \
        -H 'Content-Type: application/json' \
        -d '{
            "type": "fs",
            "settings": {
                "location": "/usr/share/elasticsearch/snapshots",
                "compress": true,
                "max_snapshot_bytes_per_sec": "100mb",
                "max_restore_bytes_per_sec": "100mb"
            }
        }' | python3 -m json.tool
    log "Repository registered."
}

create_snapshot() {
    SNAP_NAME="${SNAPSHOT_PREFIX}-$(date -u '+%Y%m%d-%H%M%S')"
    log "Creating snapshot '$SNAP_NAME'..."

    # Ensure repo exists
    if ! curl -sf "$ES_URL/_snapshot/$REPO_NAME" > /dev/null 2>&1; then
        setup_repo
    fi

    curl -s -X PUT "$ES_URL/_snapshot/$REPO_NAME/$SNAP_NAME?wait_for_completion=true" \
        -H 'Content-Type: application/json' \
        -d '{
            "indices": "profoundd_articles,profoundd_businesses",
            "ignore_unavailable": true,
            "include_global_state": false
        }' | python3 -m json.tool

    log "Snapshot '$SNAP_NAME' complete."

    # Prune old snapshots (keep last N)
    log "Pruning old snapshots (keeping last $KEEP_SNAPSHOTS)..."
    SNAPSHOTS=$(curl -s "$ES_URL/_snapshot/$REPO_NAME/_all" | \
        python3 -c "
import sys, json
data = json.load(sys.stdin)
snaps = sorted([s['snapshot'] for s in data.get('snapshots', []) if s['snapshot'].startswith('$SNAPSHOT_PREFIX')])
if len(snaps) > $KEEP_SNAPSHOTS:
    for s in snaps[:-$KEEP_SNAPSHOTS]:
        print(s)
")
    for OLD_SNAP in $SNAPSHOTS; do
        log "  Deleting old snapshot: $OLD_SNAP"
        curl -s -X DELETE "$ES_URL/_snapshot/$REPO_NAME/$OLD_SNAP" > /dev/null
    done
    log "Pruning done."
}

list_snapshots() {
    curl -s "$ES_URL/_snapshot/$REPO_NAME/_all" | python3 -c "
import sys, json
data = json.load(sys.stdin)
for s in data.get('snapshots', []):
    print(f\"{s['snapshot']:40s}  state={s['state']:10s}  indices={','.join(s.get('indices',[]))}\")
"
}

restore_snapshot() {
    SNAP_NAME="${1:-}"
    if [ -z "$SNAP_NAME" ]; then
        echo "Usage: $0 restore <snapshot-name>"
        echo "Available snapshots:"
        list_snapshots
        exit 1
    fi

    log "Closing indices before restore..."
    curl -s -X POST "$ES_URL/profoundd_articles,profoundd_businesses/_close?ignore_unavailable=true" > /dev/null

    log "Restoring snapshot '$SNAP_NAME'..."
    curl -s -X POST "$ES_URL/_snapshot/$REPO_NAME/$SNAP_NAME/_restore?wait_for_completion=true" \
        -H 'Content-Type: application/json' \
        -d '{
            "indices": "profoundd_articles,profoundd_businesses",
            "ignore_unavailable": true,
            "include_global_state": false
        }' | python3 -m json.tool

    log "Restore complete."
}

case "${1:-help}" in
    setup)   setup_repo ;;
    create)  create_snapshot ;;
    list)    list_snapshots ;;
    restore) restore_snapshot "${2:-}" ;;
    *)
        echo "Usage: $0 {setup|create|list|restore <name>}"
        echo ""
        echo "  setup   - Register the snapshot repository"
        echo "  create  - Take a new snapshot and prune old ones"
        echo "  list    - List all snapshots"
        echo "  restore - Restore a specific snapshot"
        ;;
esac
