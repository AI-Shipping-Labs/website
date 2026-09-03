.PHONY: css-build css-watch check-tailwind collectstatic run run2 worker dev migrate qcache sync seed test test-core test-affected test-judge test-live-slack-announcement coverage playwright test-playwright test-playwright-core test-playwright-manual-visual test-visual-regression lint lint-fix lint-advisory check-openapi-drift boot-profile clean

# Default SITE_BASE_URL for local dev so generated links (unsubscribe,
# calendar invites, password resets, share URLs) point at the running
# dev server instead of the production hostname. Override with:
#   SITE_BASE_URL=http://localhost:8001 make run2
SITE_BASE_URL ?= http://localhost:8000

# Tailwind is compiled from committed inputs on every build. The generated
# bundle is intentionally gitignored; npm's lockfile keeps clean-checkout
# installs deterministic and the Make dependency avoids reinstalling when the
# local dependency tree is already current.
node_modules/.package-lock.json: package.json package-lock.json
	npm ci --ignore-scripts

css-build: node_modules/.package-lock.json
	npm run css:build

css-watch: node_modules/.package-lock.json
	npm run css:watch

check-tailwind: css-build
	uv run python scripts/verify_tailwind_build.py --rebuild

collectstatic: css-build
	uv run python manage.py collectstatic --noinput

# Start dev server
run: migrate css-build
	SITE_BASE_URL=$(SITE_BASE_URL) uv run python manage.py runserver

# Start dev server on port 8001
run2: migrate css-build
	SITE_BASE_URL=http://localhost:8001 uv run python manage.py runserver 8001

# Start django-q worker
worker: migrate
	SITE_BASE_URL=$(SITE_BASE_URL) uv run python manage.py qcluster

# Start dev server + django-q worker together (Ctrl-C kills both).
# Procfile.dev sets SITE_BASE_URL=http://localhost:8000 on each line.
dev: migrate css-build
	uv run honcho -f Procfile.dev start

# Run migrations. The ``email_app`` ``0013_create_django_q_cache_table``
# migration creates the django-q DatabaseCache table used by the local
# worker heartbeat, so no separate ``createcachetable`` step is needed.
migrate:
	uv run python manage.py makemigrations
	uv run python manage.py migrate

# Backwards-compat alias for older muscle-memory: ``make qcache`` used to
# run ``migrate`` then ``createcachetable``. Now it's just ``migrate``.
qcache: migrate

# Sync content from local content repo clone
# Override repo path: make sync CONTENT_REPO=~/other/path
CONTENT_REPO ?= _content-repo
sync:
	uv run python manage.py seed_content_sources
	uv run python manage.py sync_content --from-disk $(CONTENT_REPO)

# Seed dev-only data (fake users, events, polls, notifications)
seed:
	uv run python manage.py seed_data

# Run all Django tests
test:
	uv run python manage.py test --exclude-tag=visual_regression --exclude-tag=postgres_migration --parallel

# Run only the core subset of Django tests (auth, access control, payments,
# sync happy paths, critical model invariants). Targeted at <45s wall time.
# See _docs/testing-guidelines.md ("Core test subset") for the tagging policy.
# ``--parallel 4`` (not bare ``--parallel``, which spawns one worker per core)
# because ``make test-affected`` emits this target on its fail-closed and
# hub-module paths -- the bounded-parallelism guarantee has to hold there too.
test-core:
	uv run python manage.py test --tag=core --exclude-tag=visual_regression --exclude-tag=postgres_migration --parallel 4

# Run only the tests the current diff can plausibly break. scripts/affected_tests.py
# maps `git diff --name-only` against origin/main (unioned with staged, unstaged,
# and untracked work) onto Django test labels plus a core-vs-full Playwright
# decision, then runs the emitted commands and forwards the worst exit code.
# This is the per-issue local gate for agents: do NOT run the full Django suite
# locally -- CI runs it on every push to main and blocks the deploy.
# Print the plan without running anything:
#   uv run python scripts/affected_tests.py [--json]
# See _docs/testing-guidelines.md ("Affected-tests selection").
test-affected:
	uv run python scripts/affected_tests.py --run

# Run the live LLM-judge scenario tests (tests/live_judge/). These hit the
# REAL configured provider (LLM_API_KEY must be set) and assert plain-English
# scenario criteria via an LLM judge. On-demand only: NOT referenced by `test`,
# `test-core`, `test-all`, or any CI workflow. Skips cleanly (no live calls)
# when no LLM key is configured. See _docs/testing-guidelines.md.
test-judge:
	uv run pytest -m live_judge tests/live_judge/ -n 4

# Post a real Slack announcement to #integration-tests and delete it.
# Opt-in only: NOT referenced by `test`, `test-core`, `test-all`, Playwright,
# or any CI workflow. Skips cleanly (no Slack call) without
# SLACK_BOT_TOKEN and SLACK_TEST_ANNOUNCEMENTS_CHANNEL_ID.
# See community/tests/live_slack/ and _docs/testing-guidelines.md.
test-live-slack-announcement:
	RUN_LIVE_SLACK_ANNOUNCEMENT=1 uv run pytest -m live_slack_announcement community/tests/live_slack/ -v

# Optional local/exhaustive Django coverage (not the Deploy Dev gate, not the
# agent inner loop). Deploy Dev collects Coverage.py on the sharded `test`
# matrix and combines with --fail-under=85; see _docs/testing-guidelines.md.
coverage:
	uv run coverage erase
	uv run coverage run manage.py test
	uv run coverage report --fail-under=85

# Local Playwright parallelism (#1470). pytest-xdist splits the suite across
# PLAYWRIGHT_XDIST_WORKERS worker processes; each worker is an independent
# pytest session with its own free OS-assigned port (#885) and its own
# test_playwright_db_gwN.sqlite3 file, so workers cannot contend on the DB.
#
# Default 4, deliberately NOT `-n auto` (= one worker per core, 12 here).
# Each worker runs a Chromium process tree plus an in-process Django server,
# and this box routinely runs several agents' Playwright suites at once from
# separate worktrees; `-n auto` would multiply that into the oversubscription
# that already produces spurious timeout reds (see SETTLE_TIMEOUT_MS in
# playwright_tests/conftest.py, #903). 4 matches the CI shard count and the
# existing `make test-judge -n 4`. Tune without editing code:
#   PLAYWRIGHT_XDIST_WORKERS=8 make test-playwright-core   # quiet box
#   PLAYWRIGHT_XDIST_WORKERS=0 make test-playwright-core   # serial, no xdist
#
# --dist loadfile keeps every test from one module on one worker: module-level
# ordering assumptions and per-module setup cost are preserved, and it mirrors
# how deploy-dev.yml already shards the core subset by file. The largest core
# module is ~40 of ~950 tests, so file-granularity imbalance stays bounded.
PLAYWRIGHT_XDIST_WORKERS ?= 4
PLAYWRIGHT_XDIST_FLAGS = -n $(PLAYWRIGHT_XDIST_WORKERS) --dist loadfile

# Run the full active Playwright end-to-end suite.
# The local-server fixture picks a free OS-assigned port per session (#885),
# so concurrent runs from separate worktrees no longer collide on a fixed
# port. A repo-local pytest guard blocks two separate local Playwright
# invocations inside the same worktree; the xdist workers of ONE invocation
# are allowed through because each owns a private SQLite database.
# Set PLAYWRIGHT_DJANGO_PORT only to pin a known port.
test-playwright: css-build
	uv run pytest -m "not visual_regression" playwright_tests/ $(PLAYWRIGHT_XDIST_FLAGS) -v

# Run only the core subset of Playwright tests (auth, access control, payments,
# one happy path each for events/courses/sprints/plans, notifications, and
# minimal Studio operator coverage). Runs on every push to main via Deploy Dev.
# See _docs/testing-guidelines.md ("Core Playwright subset") for the tagging policy.
test-playwright-core: css-build
	uv run pytest -m "core and not visual_regression" playwright_tests/ $(PLAYWRIGHT_XDIST_FLAGS) -v

# Run screenshot-generator/manual-review Playwright tests on demand.
test-playwright-manual-visual: css-build
	uv run pytest -m manual_visual playwright_tests/ -v

# Run only the visual_regression-tagged tests on demand. The scheduled
# Playwright workflow includes these in its default run; push/core CI
# excludes them. See _docs/testing-guidelines.md ("visual_regression").
# Note: pytest exit code 5 ("no tests collected") is treated as success on
# the Playwright leg while the Playwright visual_regression suite is empty
# (only the Django side has migrated tests so far). When the Playwright
# suite picks up its first ``visual_regression`` test, that test's
# pass/fail will surface normally.
test-visual-regression: css-build
	uv run python manage.py test --tag=visual_regression --parallel
	@uv run pytest -m visual_regression playwright_tests/ -v; \
	status=$$?; \
	if [ $$status -eq 5 ]; then \
		echo "No Playwright visual_regression tests collected yet; treating as success."; \
		exit 0; \
	fi; \
	exit $$status

# Backwards-compat alias for older muscle-memory: `make playwright` runs the
# full Playwright suite (same as `make test-playwright`).
playwright: test-playwright

# Run all tests (Django + Playwright)
test-all: test test-playwright

# Initial setup: .env, content repo, deps, migrate, sync
setup:
	bash scripts/setup.sh

# Run ruff linter
lint:
	uv run ruff check .

# Run ruff linter with auto-fix
lint-fix:
	uv run ruff check --fix .

# Run expanded Ruff checks and trend metrics without failing the build
lint-advisory:
	uv run ruff check --config ruff-advisory.toml --statistics --exit-zero .
	uv run python scripts/lint_advisory_metrics.py

# Drift check for the committed OpenAPI spec (_docs/openapi.json).
# Wired into CI in .github/workflows/deploy-dev.yml so a forgotten
# regenerate fails the build instead of shipping a stale spec. Run
# locally with ``make check-openapi-drift`` after changing any
# @openapi_spec decorator; regenerate with
# ``uv run python manage.py generate_openapi``.
check-openapi-drift:
	uv run python manage.py generate_openapi --check

# Local Docker boot-profiling harness (issue #1143). Reproduces the Fargate-dev
# cold-start under --cpus=0.25 --memory=512m, runs the REAL instrumented boot
# (Dockerfile -> entrypoint.sh -> scripts/entrypoint_init.py) against a
# THROWAWAY isolated Postgres (compose project aisl-bootprofile, torn down with
# down -v), and prints the BOOT_TIMING per-phase min/median plus the Logfire
# off-vs-on django_setup delta. Dev tooling only — no change to production boot.
# See _docs/boot-profiling.md for usage and the faithfulness caveats.
# Knobs:
#   BOOT_PROFILE_ITERATIONS   warm-boot repeats per Logfire mode (default 3)
#   BOOT_PROFILE_LOGFIRE      off | on | both (default both)
#   BOOT_PROFILE_PHASE_A      1 to also capture the cold first-migrate boot
# Example: BOOT_PROFILE_ITERATIONS=5 BOOT_PROFILE_LOGFIRE=both make boot-profile
BOOT_PROFILE_ITERATIONS ?= 3
BOOT_PROFILE_LOGFIRE ?= both
boot-profile:
	BOOT_PROFILE_ITERATIONS=$(BOOT_PROFILE_ITERATIONS) \
	BOOT_PROFILE_LOGFIRE=$(BOOT_PROFILE_LOGFIRE) \
	bash scripts/boot_profile.sh

# Clean generated files
clean:
	rm -f db.sqlite3
	rm -f static/css/tailwind.css
	rm -rf __pycache__ */__pycache__ */*/__pycache__
	rm -rf .coverage htmlcov
	rm -rf /tmp/screenshots_*
