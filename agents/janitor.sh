#!/bin/bash
# Nightly janitor: cap runaway logs and compact the append-only jsonl queues so nothing
# grows without bound. Safe: loaders already do last-write-wins, so compaction only shrinks.
export PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin
cd [APP_ROOT] || exit 0

# Log rotation is owned by tools/rotate_logs.sh (gzip + 3 generations, now covering
# root + agents + ingest logs). The old blind `tail -c 4MB` truncate here raced it and
# silently discarded data rotate_logs would have archived, so it was removed (2026-07-07).

# Drop daily run stamps older than a week (one tiny file appears per day per lane).
find store -maxdepth 1 \( -name '.morning-done-*' -o -name '.evening-done-*' -o -name '.evening-held-*' \) -mtime +7 -delete 2>/dev/null

# VACUUM the recall SQLite store if it exists (grows forever otherwise; no compact_jsonl
# analog for it — 2026-07-07 D9 audit). Also cap the tts-cache to the newest 200 files.
[ -f store/recall.db ] && sqlite3 store/recall.db "VACUUM;" 2>/dev/null || true
if [ -d store/tts-cache ]; then
  ls -t store/tts-cache/*.mp3 2>/dev/null | tail -n +201 | xargs rm -f 2>/dev/null || true
fi

# Compact the append-heavy stores (keep last record per id).
if [ -x ".venv/bin/python" ]; then
  .venv/bin/python -c "
import sys; sys.path.insert(0,'.')
import store_lib
for f in ('store/jobs.jsonl','store/network.jsonl','store/replies.jsonl','store/todos.jsonl','store/proposals.jsonl'):
    try:
        n = store_lib.compact_jsonl(f)
        print(f, '->', n, 'records')
    except Exception as e:
        print(f, 'skip:', e)
try:
    print('store/cold_pipeline.jsonl ->', store_lib.compact_jsonl('store/cold_pipeline.jsonl', 'email'), 'records')
except Exception as e:
    print('cold_pipeline skip:', e)
# ghl_events.jsonl is an append-only event log with no id to compact by; the webhook
# processor keeps its own dedupe checkpoint, so old events are safe to drop. Tail-trim
# to the last 5000 lines so a chatty/retrying GHL webhook can't grow it forever (red-team).
import os
_ge = 'store/ghl_events.jsonl'
try:
    if os.path.exists(_ge):
        # R2-49: lock BEFORE reading (not just around the replace) -- a webhook
        # appended between an unlocked read and the locked trim/replace used to
        # get silently dropped (e.g. an unsubscribe event lost). Read, decide,
        # and replace now happen inside one lock.
        with store_lib._flock(store_lib.ROOT / 'store' / 'ghl_events.jsonl'):
            _lines = open(_ge).read().splitlines()
            if len(_lines) > 5000:
                tmp = _ge + '.tmp'
                open(tmp, 'w').write('\n'.join(_lines[-5000:]) + '\n')
                os.replace(tmp, _ge)
                print(_ge, '-> trimmed to 5000 lines')
except Exception as e:
    print('ghl_events skip:', e)
"
fi
