.PHONY: run run2 worker dev migrate qcache sync seed test test-core coverage playwright test-playwright test-playwright-core test-playwright-manual-visual lint lint-fix clean

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
	uv run python manage.py test --parallel

# Run only the core subset of Django tests (auth, access control, payments,
# sync happy paths, critical model invariants). Targeted at <45s wall time.
# See _docs/testing-guidelines.md ("Core test subset") for the tagging policy.
test-core:
	uv run python manage.py test --tag=core --parallel

# Run tests with coverage
coverage:
	uv run coverage erase
	uv run coverage run manage.py test
	uv run coverage report --fail-under=85

# Run the full Playwright end-to-end suite.
test-playwright:
	uv run pytest playwright_tests/ -v

# Run only the core subset of Playwright tests (auth, access control, payments,
# one happy path each for events/courses/sprints/plans, notifications, and
# minimal Studio operator coverage). Targeted at <8 min locally and <15 min on
# CI; runs on every push to main via Deploy Dev.
# See _docs/testing-guidelines.md ("Core Playwright subset") for the tagging policy.
test-playwright-core:
	uv run pytest -m core playwright_tests/ -v

# Run screenshot-generator/manual-review Playwright tests on demand.
test-playwright-manual-visual:
	uv run pytest -m manual_visual playwright_tests/ -v

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

# Clean generated files
clean:
	rm -f db.sqlite3
	rm -rf __pycache__ */__pycache__ */*/__pycache__
	rm -rf .coverage htmlcov
	rm -rf /tmp/screenshots_*
