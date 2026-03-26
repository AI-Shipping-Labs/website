.PHONY: run migrate sync seed test coverage playwright lint clean

# Start dev server
run: migrate
	uv run python manage.py runserver

# Start dev server on port 8001
run2: migrate
	uv run python manage.py runserver 8001

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

# Clean generated files
clean:
	rm -f db.sqlite3
	rm -rf __pycache__ */__pycache__ */*/__pycache__
	rm -rf .coverage htmlcov
	rm -rf /tmp/screenshots_*
