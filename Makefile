# second-brain verification sweep (J200). One command for a post-window model (or [OWNER])
# to confirm the codebase is in a good state before trusting it.
#
# `make doctor` is the full green-before-you-call-it-done gate:
#   1. ast-parse sweep of every agents/*.py and app/*.py (syntax gate, catches a broken
#      edit before it ever gets a chance to run at 6:30am)
#   2. tools/selftest.py       (10 environment/deploy smoke checks)
#   3. tools/config_check.py   (store/config.json schema validation)
#   4. pytest tests/ (the FULL non-LLM suite, 1000+ tests incl. the security + send-gate
#      regressions. Broadened 2026-07-07: doctor used to run only test_pure.py = ~5% of
#      tests, so "green" never re-verified the load-bearing double-send/token/SSRF fixes.)
#
# `make golden` is separate (NOT part of doctor) because it makes ~12 real LLM calls and
# costs real time/tokens — run it explicitly, or let the Monday-only line in
# agents/morning.sh run it weekly.
#
# `make snapshot` / `make rotate` are separate, explicit, safe-to-run-anytime operational
# tasks (backup store/, rotate oversized agent logs) — not part of the syntax/test gate.
.PHONY: doctor ast-check selftest config-check pytest golden snapshot rotate server-fresh

PY := .venv/bin/python

# `test` is the one to run on a fresh install: no server, no launchd, no config.
test: ast-check pytest
	@echo ""
	@echo "make test: ALL GREEN"

# `doctor` is the full sweep for a RUNNING system. It additionally checks that the
# server is up, launchd jobs are loaded and config is populated, so it will fail on
# a fresh clone by design. Use `make test` until you have the server running.
doctor: ast-check selftest config-check server-fresh pytest
	@echo ""
	@echo "make doctor: ALL GREEN"

# Is the RUNNING process newer than app/server.py? A green suite proves the file,
# not the process; a sibling install ran 10-day-old code under a green suite and
# burned 80 queued jobs on a guard that was fixed on disk but inert in memory
# (field report 2026-08-12, A3). Cheap, so doctor always runs it.
server-fresh:
	@echo "== server-fresh: does the :8765 process postdate app/server.py? =="
	@$(PY) tools/check_server_fresh.py

ast-check:
	@echo "== ast-check: parsing every agents/*.py and app/*.py =="
	@$(PY) tools/ast_check.py

selftest:
	@echo "== selftest =="
	@$(PY) tools/selftest.py

config-check:
	@echo "== config-check =="
	@$(PY) tools/config_check.py

pytest:
	@echo "== pytest tests/ (full non-LLM suite) =="
	@$(PY) -m pytest tests/ -q

pytest-fast:
	@echo "== pytest tests/test_pure.py (pure only) =="
	@$(PY) -m pytest tests/test_pure.py -q

golden:
	@echo "== golden: 12 frozen LLM-shape cases (real calls, costs time+tokens) =="
	@$(PY) tests/run_golden.py

snapshot:
	@echo "== snapshot: backing up store/ =="
	@bash tools/snapshot_store.sh

rotate:
	@echo "== rotate: gzip any agents/*.log over 5MB =="
	@bash tools/rotate_logs.sh
