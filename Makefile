.PHONY: run run2 worker dev migrate qcache sync seed test test-core test-judge coverage playwright test-playwright test-playwright-core test-playwright-manual-visual test-visual-regression lint lint-fix lint-advisory check-openapi-drift boot-profile clean

# Default SITE_BASE_URL for local dev so generated links (unsubscribe,
# calendar invites, password resets, share URLs) point at the running
# dev server instead of the production hostname. Override with:
#   SITE_BASE_URL=http://localhost:8001 make run2
SITE_BASE_URL ?= http://localhost:8000

# Start dev server
run: migrate
	SITE_BASE_URL=$(SITE_BASE_URL) uv run python manage.py runserver

# Start dev server on port 8001
run2: migrate
	SITE_BASE_URL=http://localhost:8001 uv run python manage.py runserver 8001

# Start django-q worker
worker: migrate
	SITE_BASE_URL=$(SITE_BASE_URL) uv run python manage.py qcluster

# Start dev server + django-q worker together (Ctrl-C kills both).
# Procfile.dev sets SITE_BASE_URL=http://localhost:8000 on each line.
dev: migrate
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
test-core:
	uv run python manage.py test --tag=core --exclude-tag=visual_regression --exclude-tag=postgres_migration --parallel

# Run the live LLM-judge scenario tests (tests/live_judge/). These hit the
# REAL configured provider (LLM_API_KEY must be set) and assert plain-English
# scenario criteria via an LLM judge. On-demand only: NOT referenced by `test`,
# `test-core`, `test-all`, or any CI workflow. Skips cleanly (no live calls)
# when no LLM key is configured. See _docs/testing-guidelines.md.
test-judge:
	uv run pytest -m live_judge tests/live_judge/ -n 4

# Run tests with coverage
coverage:
	uv run coverage erase
	uv run coverage run manage.py test
	uv run coverage report --fail-under=85

# Run the full active Playwright end-to-end suite.
# The local-server fixture picks a free OS-assigned port per session (#885),
# so concurrent runs from separate worktrees no longer collide on a fixed
# port. A repo-local pytest guard blocks two local Playwright sessions inside
# the same worktree because they would share test_playwright_db.sqlite3.
# Set PLAYWRIGHT_DJANGO_PORT only to pin a known port.
test-playwright:
	uv run pytest -m "not visual_regression" playwright_tests/ -v

# Run only the core subset of Playwright tests (auth, access control, payments,
# one happy path each for events/courses/sprints/plans, notifications, and
# minimal Studio operator coverage). Runs on every push to main via Deploy Dev.
# See _docs/testing-guidelines.md ("Core Playwright subset") for the tagging policy.
test-playwright-core:
	uv run pytest -m "core and not visual_regression" playwright_tests/ -v

# Run screenshot-generator/manual-review Playwright tests on demand.
test-playwright-manual-visual:
	uv run pytest -m manual_visual playwright_tests/ -v

# Run only the visual_regression-tagged tests on demand. The scheduled
# Playwright workflow includes these in its default run; push/core CI
# excludes them. See _docs/testing-guidelines.md ("visual_regression").
# Note: pytest exit code 5 ("no tests collected") is treated as success on
# the Playwright leg while the Playwright visual_regression suite is empty
# (only the Django side has migrated tests so far). When the Playwright
# suite picks up its first ``visual_regression`` test, that test's
# pass/fail will surface normally.
test-visual-regression:
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
	rm -rf __pycache__ */__pycache__ */*/__pycache__
	rm -rf .coverage htmlcov
	rm -rf /tmp/screenshots_*
