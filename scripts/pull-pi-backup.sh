#!/usr/bin/env bash
set -euo pipefail

if [[ "$#" -ne 3 ]]; then
  echo "Usage: $0 USER@PI_HOST BACKUP_FILENAME LOCAL_DIRECTORY" >&2
  exit 2
fi

HOST="$1"
NAME="$2"
DEST_DIR="$3"

SSH_BATCH_MODE="${HEALTHMON_SSH_BATCH:-no}"

case "$SSH_BATCH_MODE" in
  yes|no) ;;
  *)
    echo "Invalid SSH batch mode" >&2
    exit 2
    ;;
esac

# Reject unexpected filenames before constructing remote commands.
if [[ ! "$NAME" =~ ^healthmon-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}\.db$ ]]; then
  echo "Invalid backup filename" >&2
  exit 2
fi

umask 077
mkdir -p "$DEST_DIR"
chmod 700 "$DEST_DIR"

FINAL="$DEST_DIR/$NAME"
TEMP="$(mktemp "$DEST_DIR/.healthmon-transfer.XXXXXXXX")"
trap 'rm -f "$TEMP"' EXIT

REMOTE_CMD="cd ~/service-health-monitor-prod && docker compose -f compose.pi.yaml exec -T worker"
REMOTE_FILE="/var/lib/service-health-monitor/backups/$NAME"

echo "Reading remote backup checksum..."

REMOTE_OUTPUT="$(
  ssh -o "BatchMode=$SSH_BATCH_MODE" -o ConnectTimeout=10 "$HOST" "$REMOTE_CMD sha256sum '$REMOTE_FILE'"
)"
REMOTE_SHA="${REMOTE_OUTPUT%% *}"

if [[ ! "$REMOTE_SHA" =~ ^[a-f0-9]{64}$ ]]; then
  echo "Could not read a valid remote SHA-256 checksum" >&2
  exit 1
fi

echo "Transferring backup through SSH..."

ssh -o "BatchMode=$SSH_BATCH_MODE" -o ConnectTimeout=10 "$HOST" "$REMOTE_CMD cat '$REMOTE_FILE'" > "$TEMP"

LOCAL_SHA="$(shasum -a 256 "$TEMP" | awk '{print $1}')"

if [[ "$LOCAL_SHA" != "$REMOTE_SHA" ]]; then
  echo "Backup transfer failed: checksum mismatch" >&2
  exit 1
fi

echo "Checksum verified."
echo "Running temporary SQLite restore verification..."

python -m healthmon.backup restore-check --db "$TEMP"

# Atomic publication; never overwrite an existing backup.
python3 - "$TEMP" "$FINAL" <<'PY'
import os
import sys

os.link(sys.argv[1], sys.argv[2])
PY

echo "Off-device backup verified and saved:"
echo "$FINAL"
