.PHONY: help bootstrap doctor check check-web check-scripts hooks-install dev dev-api dev-web commands-doc commands-check dashboard-open search-open commands-open plan-open knowledge-open ingest-open teachers-open holdings-open watchlist-open calendar-open industry-open db-init db-sync db-reconcile holdings holdings-refresh watchlist notes-search db-search market-open market-json market-envelope review-open review-prefill pre post regulatory ingest-list ingest-run-post ingest-run-interface ingest-inspect ingest-health ingest-reconcile plan-draft plan-show-draft plan-confirm plan-diagnose plan-review knowledge-list knowledge-add-note knowledge-draft-from-asset knowledge-draft-from-teacher-note today-open today-close today-pre today-post today-regulatory today-evening today-watchlist today-obsidian today-ingest-inspect today-ingest-health

help:
	@echo "Available targets:"
	@echo "  make bootstrap     - install deps and repo-local hooks"
	@echo "  make doctor        - print local toolchain and env status"
	@echo "  make check         - run web + scripts checks"
	@echo "  make check-web     - run web checks only"
	@echo "  make check-scripts - run scripts checks only"
	@echo "  make commands-doc  - regenerate docs/commands.md from Makefile"
	@echo "  make commands-check - verify docs/commands.md is up to date"
	@echo "  make dev           - run api + web dev servers"
	@echo "  make dev-api       - run api dev server only"
	@echo "  make dev-web       - run web dev server only"
	@echo "  make dashboard-open - open dashboard in browser"
	@echo "  make search-open   - open search center in browser"
	@echo "  make commands-open - open commands center in browser"
	@echo "  make plan-open     - open plan workbench in browser"
	@echo "  make knowledge-open - open knowledge workbench in browser"
	@echo "  make ingest-open   - open ingest workbench in browser"
	@echo "  make teachers-open - open teacher notes page in browser"
	@echo "  make holdings-open - open holdings page in browser"
	@echo "  make watchlist-open - open watchlist page in browser"
	@echo "  make calendar-open - open calendar page in browser"
	@echo "  make industry-open - open industry page in browser"
	@echo "  make db-init       - initialize sqlite and import history"
	@echo "  make db-sync       - retry pending dual writes"
	@echo "  make db-reconcile  - reconcile YAML and DB"
	@echo "  make holdings      - list active holdings"
	@echo "  make holdings-refresh - refresh sqlite holding quotes for a date"
	@echo "  make watchlist     - list watchlist items"
	@echo "  make notes-search  - search teacher notes (requires KEYWORD)"
	@echo "  make db-search     - cross-table db search (requires KEYWORD)"
	@echo "  make market-open   - open market overview in browser"
	@echo "  make market-json   - fetch market summary JSON for a date"
	@echo "  make market-envelope - fetch post-market envelope JSON for a date"
	@echo "  make review-open   - open review workbench in browser"
	@echo "  make review-prefill - fetch review prefill JSON for a date"
	@echo "  make pre           - run pre-market report for today"
	@echo "  make post          - run post-market report for today"
	@echo "  make ingest-list   - list ingest interfaces"
	@echo "  make ingest-run-post - run post_core ingest for today"
	@echo "  make ingest-run-interface - run one ingest interface (requires NAME)"
	@echo "  make ingest-inspect - inspect ingest audit (DATE optional)"
	@echo "  make ingest-health - show recent ingest health summary"
	@echo "  make ingest-reconcile - reconcile stale running ingest records"
	@echo "  make plan-draft    - create today's minimal trade draft"
	@echo "  make plan-show-draft - show today's draft"
	@echo "  make plan-confirm  - confirm draft into plan (requires DRAFT_ID)"
	@echo "  make plan-diagnose - example diagnose command (requires PLAN_ID)"
	@echo "  make plan-review   - review plan outcome (requires PLAN_ID)"
	@echo "  make knowledge-list - list knowledge assets"
	@echo "  make knowledge-add-note - example add-note command"
	@echo "  make knowledge-draft-from-asset - example draft-from-asset command"
	@echo "  make knowledge-draft-from-teacher-note - draft from teacher_notes (NOTE_ID=)"
	@echo "  make today-open    - alias for today's pre-market flow"
	@echo "  make today-close   - alias for today's post-market flow"
	@echo "  make today-pre     - run today's pre-market flow"
	@echo "  make today-post    - run today's post-market flow"
	@echo "  make today-regulatory - run today's regulatory monitor ingest"
	@echo "  make today-evening - run today's evening flow"
	@echo "  make today-watchlist - run today's watchlist flow"
	@echo "  make today-obsidian - export today's obsidian notes"
	@echo "  make today-ingest-inspect - inspect today's ingest runs"
	@echo "  make today-ingest-health - show today's 7-day ingest health summary"
	@echo "  make hooks-install - enable repo-local git hooks"

bootstrap:
	python3 -m pip install -r scripts/requirements.txt
	cd web && npm install
	$(MAKE) hooks-install
	@echo "Bootstrap complete. Next: make check"

doctor:
	@echo "python: $$(python3 --version 2>/dev/null || echo missing)"
	@echo "node:   $$(node --version 2>/dev/null || echo missing)"
	@echo "npm:    $$(npm --version 2>/dev/null || echo missing)"
	@python3 -c "import pytest; print('pytest: installed')" 2>/dev/null || echo "pytest: missing"
	@test -f scripts/.env && echo "scripts/.env: present" || echo "scripts/.env: missing"
	@test -f scripts/.env.example && echo "scripts/.env.example: present" || echo "scripts/.env.example: missing"
	@echo "git hooksPath: $$(git config --get core.hooksPath 2>/dev/null || echo unset)"

check:
	bash check.sh

check-web:
	bash check.sh --web

check-scripts:
	bash check.sh --scripts

commands-doc:
	python3 scripts/generate_command_index.py

commands-check:
	python3 scripts/generate_command_index.py --check

dev:
	bash dev.sh

dev-api:
	cd scripts && python3 -m uvicorn api.main:app --reload --port 8000

dev-web:
	cd web && npm run dev

dashboard-open:
	open "http://localhost:5173/"

search-open:
	open "http://localhost:5173/search"

commands-open:
	open "http://localhost:5173/commands"

plan-open:
	open "http://localhost:5173/plans/$${DATE:-$$(date +%F)}"

knowledge-open:
	open "http://localhost:5173/knowledge"

ingest-open:
	open "http://localhost:5173/ingest"

teachers-open:
	open "http://localhost:5173/teachers"

holdings-open:
	open "http://localhost:5173/holdings"

watchlist-open:
	open "http://localhost:5173/watchlist"

calendar-open:
	open "http://localhost:5173/calendar"

industry-open:
	open "http://localhost:5173/industry"

db-init:
	cd scripts && python3 main.py db init

db-sync:
	cd scripts && python3 main.py db sync

db-reconcile:
	cd scripts && python3 main.py db reconcile

holdings:
	cd scripts && python3 main.py db holdings-list

holdings-refresh:
	cd scripts && python3 main.py db holdings-refresh --date "$${DATE:-$$(date +%F)}" --json

watchlist:
	cd scripts && python3 main.py db watchlist-list

notes-search:
	@test -n "$$KEYWORD" || (echo "Usage: make notes-search KEYWORD=主线 [FROM=YYYY-MM-DD] [TO=YYYY-MM-DD]" && exit 2)
	cd scripts && python3 main.py db query-notes --keyword "$$KEYWORD" $${FROM:+--from "$$FROM"} $${TO:+--to "$$TO"}

db-search:
	@test -n "$$KEYWORD" || (echo "Usage: make db-search KEYWORD=情绪 [FROM=YYYY-MM-DD] [TO=YYYY-MM-DD]" && exit 2)
	cd scripts && python3 main.py db db-search --keyword "$$KEYWORD" $${FROM:+--from "$$FROM"} $${TO:+--to "$$TO"}

market-open:
	open "http://localhost:5173/market/$${DATE:-$$(date +%F)}"

market-json:
	curl -fsS "http://localhost:8000/api/market/$${DATE:-$$(date +%F)}"

market-envelope:
	curl -fsS "http://localhost:8000/api/post-market/$${DATE:-$$(date +%F)}"

review-open:
	open "http://localhost:5173/review/$${DATE:-$$(date +%F)}"

review-prefill:
	curl -fsS "http://localhost:8000/api/review/$${DATE:-$$(date +%F)}/prefill"

pre:
	cd scripts && python3 main.py pre

post:
	cd scripts && python3 main.py post

ingest-list:
	cd scripts && python3 main.py ingest list-interfaces

ingest-run-post:
	cd scripts && python3 main.py ingest run --stage post_core --date "$$(date +%F)"

ingest-run-interface:
	@test -n "$$NAME" || (echo "Usage: make ingest-run-interface NAME=block_trade [DATE=YYYY-MM-DD]" && exit 2)
	cd scripts && python3 main.py ingest run-interface --name "$$NAME" --date "$${DATE:-$$(date +%F)}" --json

ingest-inspect:
	cd scripts && python3 main.py ingest inspect --date "$${DATE:-$$(date +%F)}" $${STAGE:+--stage "$$STAGE"} $${INTERFACE:+--interface "$$INTERFACE"} --json

ingest-health:
	cd scripts && python3 main.py ingest health --date "$${DATE:-$$(date +%F)}" --days "$${DAYS:-7}" --limit "$${LIMIT:-10}" $${STAGE:+--stage "$$STAGE"} --json

ingest-reconcile:
	cd scripts && python3 main.py ingest reconcile --stale-minutes "$${STALE_MINUTES:-5}" --json

plan-draft:
	cd scripts && python3 main.py plan draft --date "$$(date +%F)"

plan-show-draft:
	cd scripts && python3 main.py plan show-draft --date "$$(date +%F)"

plan-confirm:
	@test -n "$$DRAFT_ID" || (echo "Usage: make plan-confirm DRAFT_ID=draft_xxx [DATE=YYYY-MM-DD]" && exit 2)
	cd scripts && python3 main.py plan confirm --date "$${DATE:-$$(date +%F)}" --draft-id "$$DRAFT_ID"

plan-diagnose:
	@test -n "$$PLAN_ID" || (echo "Usage: make plan-diagnose PLAN_ID=plan_xxx" && exit 2)
	cd scripts && python3 main.py plan diagnose --date "$${DATE:-$$(date +%F)}" --plan-id "$$PLAN_ID" --json

plan-review:
	@test -n "$$PLAN_ID" || (echo "Usage: make plan-review PLAN_ID=plan_xxx [DATE=YYYY-MM-DD]" && exit 2)
	cd scripts && python3 main.py plan review --date "$${DATE:-$$(date +%F)}" --plan-id "$$PLAN_ID"

knowledge-list:
	cd scripts && python3 main.py knowledge list

knowledge-add-note:
	cd scripts && python3 main.py knowledge add-note --title "资讯摘录" --content "机器人回流，关注 002594.SZ"

knowledge-draft-from-asset:
	@test -n "$$ASSET_ID" || (echo "Usage: make knowledge-draft-from-asset ASSET_ID=asset_xxx" && exit 2)
	cd scripts && python3 main.py knowledge draft-from-asset --asset-id "$$ASSET_ID" --date "$$(date +%F)"

knowledge-draft-from-teacher-note:
	@test -n "$$NOTE_ID" || (echo "Usage: make knowledge-draft-from-teacher-note NOTE_ID=42" && exit 2)
	cd scripts && python3 main.py knowledge draft-from-teacher-note --note-id "$$NOTE_ID" --date "$${DATE:-$$(date +%F)}"

today-open: today-pre

today-close: today-post

today-pre:
	cd scripts && python3 main.py pre --date "$$(date +%F)"

today-post:
	cd scripts && python3 main.py post --date "$$(date +%F)"

today-regulatory:
	cd scripts && python3 main.py regulatory --date "$$(date +%F)"

today-evening:
	cd scripts && python3 main.py evening --date "$$(date +%F)"

today-watchlist:
	cd scripts && python3 main.py watchlist --date "$$(date +%F)"

today-obsidian:
	cd scripts && python3 main.py obsidian --date "$$(date +%F)"

today-ingest-inspect:
	cd scripts && python3 main.py ingest inspect --date "$$(date +%F)" --json

today-ingest-health:
	cd scripts && python3 main.py ingest health --date "$$(date +%F)" --days 7 --json

hooks-install:
	git config core.hooksPath .githooks
	chmod +x .githooks/pre-push check.sh scripts/check.sh dev.sh
	@echo "Installed repo-local hooks at .githooks"
