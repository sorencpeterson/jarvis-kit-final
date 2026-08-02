#!/bin/bash
# J190: nightly-style snapshot of store/ to a second disk location, 30-day retention.
#
# rsync's the live store/ directory into ~/Backups/second-brain/store-YYYYMMDD (creates
# the parent dir if needed), keeps the last 30 dated snapshots (deletes older ones), then
# verifies the newest snapshot by listing file count + a checksum spot-check against a
# handful of files from the source.
#
# Safe to run any time: it only reads store/ and writes under ~/Backups/, never touches
# the live store. Re-running the same day overwrites that day's snapshot (rsync --delete
# keeps it in sync with the current store/ state rather than silently drifting stale).
#
# Wire into morning.sh as: bash tools/snapshot_store.sh
set -uo pipefail

SB_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$SB_ROOT/store"
BACKUP_ROOT="$HOME/Backups/second-brain"
TODAY="$(date +%Y%m%d)"
DEST="$BACKUP_ROOT/store-$TODAY"
KEEP=30

if [ ! -d "$SRC" ]; then
  echo "snapshot_store: FAIL, source $SRC does not exist"
  exit 1
fi

mkdir -p "$BACKUP_ROOT"

echo "snapshot_store: rsync $SRC/ -> $DEST/"
mkdir -p "$DEST"
rsync -a --delete "$SRC/" "$DEST/"
rsync_status=$?
if [ "$rsync_status" -ne 0 ]; then
  echo "snapshot_store: FAIL, rsync exited $rsync_status"
  exit 1
fi

# Retention: keep the newest KEEP dated snapshot dirs (store-YYYYMMDD), delete the rest.
# Sort lexically (works for YYYYMMDD), oldest first, drop everything past the newest KEEP.
# (Portable across macOS's stock bash 3.2, which has no `mapfile`/`readarray`.)
all_snaps_str="$(ls -1d "$BACKUP_ROOT"/store-[0-9]* 2>/dev/null | sort)"
n_snaps=0
if [ -n "$all_snaps_str" ]; then
  n_snaps=$(printf '%s\n' "$all_snaps_str" | wc -l | tr -d ' ')
fi
if [ "$n_snaps" -gt "$KEEP" ]; then
  n_to_delete=$((n_snaps - KEEP))
  echo "snapshot_store: $n_snaps snapshots present, pruning oldest $n_to_delete (keep $KEEP)"
  printf '%s\n' "$all_snaps_str" | head -n "$n_to_delete" | while IFS= read -r old; do
    echo "  removing $old"
    rm -rf "$old"
  done
fi

# Verify: file count match + checksum spot-check on up to 5 files.
src_count=$(find "$SRC" -type f | wc -l | tr -d ' ')
dest_count=$(find "$DEST" -type f | wc -l | tr -d ' ')
echo "snapshot_store: verify file count src=$src_count dest=$dest_count"
if [ "$src_count" != "$dest_count" ]; then
  echo "snapshot_store: FAIL, file count mismatch"
  exit 1
fi

spot_fail=0
spot_checked=0
while IFS= read -r f; do
  rel="${f#"$SRC"/}"
  d="$DEST/$rel"
  if [ ! -f "$d" ]; then
    echo "  MISSING in backup: $rel"
    spot_fail=1
    continue
  fi
  src_sum=$(shasum -a 256 "$f" | awk '{print $1}')
  dest_sum=$(shasum -a 256 "$d" | awk '{print $1}')
  spot_checked=$((spot_checked + 1))
  if [ "$src_sum" != "$dest_sum" ]; then
    echo "  CHECKSUM MISMATCH: $rel"
    spot_fail=1
  fi
done < <(find "$SRC" -type f | head -5)

if [ "$spot_fail" -ne 0 ]; then
  echo "snapshot_store: FAIL, checksum spot-check failed"
  exit 1
fi

echo "snapshot_store: PASS, $dest_count files, $spot_checked spot-checked OK, snapshot at $DEST"
exit 0
