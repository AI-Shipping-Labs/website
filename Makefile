.PHONY: run dev migrate sync seed test test-core coverage playwright lint lint-fix clean

# Start dev server
run: migrate
	uv run python manage.py runserver

# Start dev server on port 8001
run2: migrate
	uv run python manage.py runserver 8001

# Start dev server + django-q worker together (Ctrl-C kills both)
dev: migrate
	uv run honcho -f Procfile.dev start

# Run migrations
migrate:
	uv run python manage.py migrate

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
	uv run coverage run manage.py test
	uv run coverage report

# Run Playwright end-to-end tests
playwright:
	uv run pytest playwright_tests/ -v

# Run all tests (Django + Playwright)
test-all: test playwright

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
