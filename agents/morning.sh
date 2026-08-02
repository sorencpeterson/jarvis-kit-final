#!/bin/bash
# Morning routine — MONEY-FIRST ORDER (2026-07-11 reorder, see MORNING-REORDER-PLAN.md).
# The brief used to land 10:42-14:25 because daily_brief sat at step ~60 of 108 behind
# jobs + analytics. Money-lane sum-of-medians is ~5-6 min, so the lanes now run:
#   MONEY (mail -> warm -> attention -> BRIEF, target <15 min from launch)
#   JOBS  (scan/replies/interview/cold)
#   ANALYTICS/OTHER (organize, content, metrics, janitor, weekly blocks)
# launchd-safe: minimal PATH won't have `uv`/`claude`, so we use the venv python
# directly and add the usual bin dirs (planner finds the claude CLI by abs path).
set -uo pipefail
cd "$(dirname "$0")/.."
source agents/heartbeat.sh  # hb <name> markers; hbcheck.py reads store/.hb/ (D8 #30)
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
LOG="agents/morning.log"
# Self-lock: the 6:30 launchd fire and run.sh's 7:00 self-heal could otherwise run this
# chain CONCURRENTLY (a stalled network-wait is enough) over unlocked stores. Own lock,
# stale-cleared at 90 min so a crashed run can't wedge tomorrow (2026-07-06 audit H1).
# A LIVE chain can legitimately run hours (5.7h observed 2026-07-07 with the full fleet),
# which the flat 90-min clear would falsely treat as stale and spawn a 2nd concurrent chain
# (red-team). Fix: a background heartbeat touches the lock every 60s while the chain lives,
# so "no touch in 90 min" now means DEAD, not just slow, regardless of total runtime.
SELFLOCK="store/.morning-run.lock"
if [ -d "$SELFLOCK" ] && [ -n "$(find "$SELFLOCK" -maxdepth 0 -mmin +90 2>/dev/null)" ]; then
  rmdir "$SELFLOCK" 2>/dev/null || true
fi
if ! mkdir "$SELFLOCK" 2>/dev/null; then
  echo "$(date '+%H:%M') morning.sh: another instance holds $SELFLOCK, exiting" >> "$LOG"
  exit 0
fi
( while [ -d "$SELFLOCK" ]; do touch "$SELFLOCK" 2>/dev/null; sleep 60; done ) &
HEARTBEAT_PID=$!
trap 'kill "$HEARTBEAT_PID" 2>/dev/null; rmdir "$SELFLOCK" 2>/dev/null' EXIT
# $RUN wraps every step in a 900s per-agent timeout (agents/run_step.sh) so one hung
# agent can't stall the chain (red-team). It resolves the venv python itself; falls back
# to the bare interpreter if the wrapper is somehow missing.

# ---- plan profile (lite keeps a $20 Claude plan alive) ----------------------
# store/config.json "morning_profile": lite | full   (default: full)
# lite runs only the lanes that produce something you act on today, because a
# Pro plan cannot absorb 100+ LLM-calling agents before breakfast.
PROFILE="$(.venv/bin/python -c "import json;print(json.load(open('store/config.json')).get('morning_profile','full'))" 2>/dev/null || echo full)"
lane() {  # lane <name>  -> 0 if this lane should run under the current profile
  case "$PROFILE" in
    lite) case "$1" in core|jobs) return 0;; *) return 1;; esac ;;
    *)    return 0 ;;
  esac
}
echo "morning profile: $PROFILE"

if [ -x "agents/run_step.sh" ]; then RUN="bash agents/run_step.sh"
elif [ -x ".venv/bin/python" ]; then RUN=".venv/bin/python"; else RUN="python3"; fi
# Keep the Mac awake for the whole run so a lid-closed DarkWake can't re-sleep mid-run.
caffeinate -i -w $$ &
{
  echo "=== $(date '+%Y-%m-%d %H:%M:%S') morning run ==="
  # J184: bad config knobs fail LOUDLY here, first thing, instead of silently breaking
  # some feature at 6:30am. Report-only (doesn't exit the run) so a config problem still
  # surfaces the rest of the morning log instead of blocking everything else.
  $RUN tools/config_check.py || echo "WARNING: config_check failed, see above"
  # Wait for network before any API call (a scheduled DarkWake can boot without it yet).
  for i in $(seq 1 24); do curl -m 5 -sf https://api.anthropic.com >/dev/null 2>&1 && break; sleep 5; done

  # ============ MONEY LANE (brief-ready target: first ~15 minutes) ============
  if lane money; then
  $RUN capture/pull_reminders.py
  $RUN agents/triage_inbox.py
  # Gmail intelligence chain (built 2026-07-03, never scheduled — went dark for 83h
  # until the cadence checker caught it 2026-07-06). Do NOT add a standalone
  # mail_sync.py step here: mail_brain runs sync() itself and consumes the returned
  # ids; a prior standalone sync ADVANCES THE CURSOR and throws the ids away, so the
  # classifier only ever sees the seconds between the two runs (2026-07-07 audit H1).
  # B82 sender reputation seeds store/sender_scores.json and must land BEFORE
  # mail_brain's classify pass reads get_score() per message.
  $RUN agents/mail_sender_scores.py
  $RUN agents/mail_brain.py
  $RUN agents/mail_threads.py     # B87/B93/B94: thread summaries, task suggestions, ghost drafts
  $RUN agents/mail_signals.py     # B88/B89/B90/B97: attachment/meeting/payment/archive suggestions
  $RUN agents/mail_drafts.py
  $RUN agents/mail_hygiene.py     # B86: unsubscribe report (reads sender_scores + triage)
  $RUN agents/mail_digest.py      # last of mail chain: folds triage + summaries + drafts
  $RUN agents/warm_block.py       # before day_plan (it reads the warm block)
  $RUN agents/warmest_five.py     # daily human shortlist (jobs data is yesterday's: accepted, LOW)
  $RUN agents/li_conveyor.py      # accepted-connection day-2 DM ladder (drafts only, pending)
  $RUN agents/li_engager_dm.py    # content-engager fit-DM drafts (drafts only, pending)
  $RUN agents/transcript_miner.py # promises + objections from call transcripts (attention reads)
  $RUN agents/promises.py         # hoisted above attention so promise scores are TODAY's
  $RUN agents/care_upsell.py      # +7d post-win care pitch (once per client)
  $RUN agents/deposit_nudge.py    # signed-but-unpaid watcher (48h windows)
  $RUN agents/dropoff_audit.py    # booked-call -> proposal leak list
  $RUN agents/reactivation_triage.py  # replier lanes for the 423 drip
  $RUN agents/tier_winrate.py     # acceptance rate per price point
  $RUN agents/quiet_worklist.py   # opened-then-quiet proposals + stale warm = re-engage list
  $RUN agents/call_prep.py
  $RUN agents/proposal_open_pulse.py  # baseline opens + catch overnight first-opens
  $RUN agents/send_finger_nag.py  # staged/pending past 24h/48h -> push, todo at 48h
  $RUN agents/call_escalator.py   # exits 0 before 15:00; afternoon runs do the real work
  $RUN agents/honesty_agent.py    # exits 0 except Sunday
  $RUN agents/attention.py        # AFTER transcript_miner+promises (it consumes both)
  $RUN agents/one_thing.py        # push the ONE action of the day (needs attention.py above)
  $RUN agents/day_plan.py         # needs attention + warm_block above
  $RUN agents/watch_brief.py      # render + sync the Apple Watch card (needs attention/one_thing)
  # sleep_aware must run BEFORE daily_brief: the brief reads store/.gentle-morning at
  # import to pick gentle mode; running it after the brief left the flag a day stale.
  $RUN agents/sleep_aware.py
  $RUN agents/owner_report.py
  $RUN agents/morning_chain.py    # config-gated apply kick (ships 0; evening chain is the live lane)
  $RUN agents/daily_brief.py      # <=== BRIEF READY
  hb morning-money                # heartbeat: money lane completed (brief freshness signal)

  fi  # end MONEY LANE

  # ============ JOBS LANE ============
  $RUN agents/jobs.py
  $RUN agents/job_ats_watch.py   # curated-company ATS feeds (the archetype-hiring employers)
  $RUN agents/job_replies.py
  $RUN agents/job_fit_signals.py    # rebuild geo-blocklist + print state-eligibility would-skip count
  $RUN agents/job_network_bridge.py   # apply->LinkedIn pairing: stage hiring-mgr sourcing for fresh high-fit applies (2026-07-12)
  $RUN agents/company_risk.py         # pre-apply risk flags, local data, 5 LLM notes/run
  $RUN agents/stage_coach.py          # static next-step per live-stage job, one feed line
  $RUN agents/interview_postmortem.py # post-interview todo once the war room is 2d old
  $RUN agents/takehome_helper.py      # take-home detection -> todo + scaffold
  $RUN agents/salary_ladder.py        # comp-band conversion table + feed read
  $RUN agents/resume_ab.py            # resume variant outcome rates (scaffold)
  $RUN agents/job_cover.py            # per-job cover_override cache (apply prompt prefers it)
  $RUN agents/resume_tailor.py        # per-job tailored resume PDFs (apply prompt prefers them)
  $RUN agents/job_pipeline_quality.py # funnel analytics
  $RUN agents/job_efficiency.py       # velocity governance
  $RUN agents/job_answer_growth.py    # answer-bank growth tracking
  $RUN agents/interview_prep.py
  $RUN agents/interview_war_room.py  # assemble prep+STAR+salary anchor per live interview
  $RUN agents/interview_followup.py  # day-5/day-10 nudge on silent post-interview jobs
  $RUN agents/ghost_check.py
  $RUN agents/cold_import.py
  $RUN agents/cold_feeder.py
  # H161/162/163: sentiment auto-pause, bounce watcher, unsub/dnd suppress scan — all
  # read-only against GHL, only ever writes a cold knob DOWN to 0, never up.
  $RUN agents/campaign_guard.py
  # H164: SPF/DKIM/DMARC + DNSBL blacklist probe, logs to store/domain_health.jsonl.
  $RUN agents/cold_preflight.py --daily
  $RUN agents/atsstats.py
  $RUN agents/referral_timer.py

  # ============ ANALYTICS / OTHER ============
  $RUN agents/organize.py         # board.json for the dashboard; MUST stay before build_dashboard
  $RUN agents/standup.py
  $RUN agents/content_gen.py
  $RUN agents/content_readback.py
  $RUN agents/portfolio_teardown.py   # 8 gated fetches/day walks the candidate list
  $RUN dashboard/build_dashboard.py
  bash agents/janitor.sh
  $RUN agents/metrics_rollup.py
  $RUN agents/selflint.py
  $RUN agents/load_forecast.py
  $RUN agents/archiver.py
  $RUN agents/daily_insight.py
  $RUN agents/postmortem.py
  $RUN agents/snapshot.py
  # J190: nightly backup of store/ to a second disk location (30-day retention).
  bash tools/snapshot_store.sh
  # J192: gzip any agents/*.log that crossed 5MB (3 generations kept).
  bash tools/rotate_logs.sh
  $RUN agents/travel_mode.py
  $RUN agents/thread_memory.py
  $RUN agents/template_learn.py
  $RUN agents/answer_bank.py
  $RUN agents/defib.py
  $RUN agents/thankyou.py
  $RUN agents/meeting_prep.py
  $RUN agents/repurpose.py
  $RUN agents/client_pack.py
  # intel lane (544 buildout): market truth refreshed daily/weekly
  $RUN agents/niche_db.py
  $RUN agents/close_prob.py
  $RUN agents/anomaly_watch.py
  # weekly deep checks (Sundays)
  if [ "$(date +%u)" = "7" ]; then
    $RUN agents/rejection_digest.py         # weekly rejection-pattern read (self-gated too)
    $RUN agents/job_rescan.py                # weekly funnel re-scan: replay submitted jobs vs real mail (2026-07-12)
    $RUN agents/prospect_trigger_watch.py   # open-deal trigger watch (self-gates 6d)
    $RUN agents/egress_audit.py
    $RUN agents/backup_verify.py
    $RUN agents/chaos_drill.py
    $RUN agents/correlate.py
    $RUN agents/voice_drift.py
    $RUN agents/contact_graph.py
    $RUN agents/salary_intel.py
    $RUN agents/win_loss.py
    $RUN agents/bakeoff.py
    $RUN agents/competitor_watch.py
    $RUN agents/energy_blocks.py
    $RUN agents/forecast_close.py
    $RUN agents/besttime.py
    $RUN agents/channel_cac.py
    $RUN agents/source_scorecard.py
    $RUN agents/best_day.py
    $RUN agents/ltv_model.py
  fi
  # J182: golden shape-test set (Mondays) — 12 frozen prompt->shape cases, catches
  # model-router / prompt drift. Real LLM calls (~12 x few seconds), so weekly not daily.
  if [ "$(date +%u)" = "1" ]; then
    $RUN tests/run_golden.py
    $RUN tests/run_quality.py
  fi
  # Success stamp: only when today's brief REALLY generated (net-dependent step furthest
  # downstream). run.sh re-runs this whole routine every 10 min until the stamp exists,
  # so a net-down 6:30 (Mac asleep, wifi not up yet) self-heals once the network returns.
  if grep -q "$(date +%Y-%m-%d)" store/brief.json 2>/dev/null \
     && ! grep -q "Brief unavailable" store/brief.json 2>/dev/null; then
    touch "store/.morning-done-$(date +%Y-%m-%d)"
    hb morning-chain  # heartbeat: proves the chain FINISHED, not just started (D8 #30)
  fi
} >> "$LOG" 2>&1
echo "morning run complete -> $LOG"
