#!/usr/bin/env bash
# Sync ES snapshots from Apollo9 Docker volume to Azure7 WD 16TB drive.
# Run on Apollo9 after es_snapshot.sh create.
# Cron: 30 3 * * * /home/mark/profoundd-build/scripts/sync_snapshots_to_wd.sh >> /var/log/es-snapshot-sync.log 2>&1

set -euo pipefail

AZURE7="mark@100.71.230.11"
REMOTE_DIR="/mnt/backup/apollo9/es-snapshots"
LOG_PREFIX="[$(date -u '+%Y-%m-%d %H:%M:%S UTC')]"

echo "$LOG_PREFIX Starting ES snapshot sync to Azure7 WD drive..."

# Find the Docker volume path on the host
VOLUME_PATH=$(docker volume inspect profoundd-build_profoundd-es-snapshots --format '{{ .Mountpoint }}' 2>/dev/null || \
              docker volume inspect profoundd-es-snapshots --format '{{ .Mountpoint }}' 2>/dev/null || \
              echo "")

if [ -z "$VOLUME_PATH" ] || [ ! -d "$VOLUME_PATH" ]; then
    echo "$LOG_PREFIX ERROR: Cannot find ES snapshots volume. Run 'docker compose up' first."
    exit 1
fi

echo "$LOG_PREFIX Source: $VOLUME_PATH"
echo "$LOG_PREFIX Destination: $AZURE7:$REMOTE_DIR"

# Ensure remote directory exists
ssh "$AZURE7" "mkdir -p $REMOTE_DIR"

# Rsync with compression (WD drive is slow, minimize transfer)
rsync -az --delete --stats \
    "$VOLUME_PATH/" \
    "$AZURE7:$REMOTE_DIR/"

echo "$LOG_PREFIX Sync complete."
