#!/usr/bin/env bash
set -euo pipefail

if [[ "$#" -ne 2 ]]; then
  echo "Usage: $0 SSH_HOST LOCAL_DIRECTORY" >&2
  exit 2
fi

HOST="$1"
DEST="$2"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="$ROOT/.venv/bin/python"

if [[ ! -x "$PYTHON" ]]; then
  echo "Python virtual environment unavailable: $PYTHON" >&2
  exit 1
fi

umask 077
mkdir -p "$DEST"
chmod 700 "$DEST"

echo "Discovering latest Raspberry Pi backup..."

REMOTE_CMD="cd ~/service-health-monitor-prod && docker compose -f compose.pi.yaml exec -T worker sh -c 'ls -1t /var/lib/service-health-monitor/backups/healthmon-*.db | head -n 1'"

REMOTE_PATH="$(
  ssh -o BatchMode=yes -o ConnectTimeout=10 \
    "$HOST" "$REMOTE_CMD"
)"

NAME="${REMOTE_PATH##*/}"

if [[ ! "$NAME" =~ ^healthmon-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}\.db$ ]]; then
  echo "No valid remote backup filename found" >&2
  exit 1
fi

if [[ -e "$DEST/$NAME" ]]; then
  echo "Backup already exists locally. Verifying..."
  "$PYTHON" -m healthmon.backup restore-check \
    --db "$DEST/$NAME"
  echo "Existing backup verified; transfer unnecessary."
  exit 0
fi

# The existing transfer script invokes 'python'.
# Ensure it uses this project's virtual environment.
export PATH="$ROOT/.venv/bin:$PATH"
export HEALTHMON_SSH_BATCH=yes

"$ROOT/scripts/pull-pi-backup.sh" \
  "$HOST" \
  "$NAME" \
  "$DEST"
