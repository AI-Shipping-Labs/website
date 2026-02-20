.PHONY: run migrate load test coverage playwright lint clean

# Start dev server
run: migrate
	uv run python manage.py runserver

# Run migrations
migrate:
	uv run python manage.py migrate

# Load content from reference/ markdown files
load:
	uv run python manage.py load_content

# Run all Django tests
test:
	uv run python manage.py test

# Run tests with coverage
coverage:
	uv run coverage run manage.py test
	uv run coverage report

# Run Playwright end-to-end tests
playwright:
	uv run pytest playwright_tests/ -v

# Run all tests (Django + Playwright)
test-all: test playwright

# Initial setup
setup:
	uv sync
	uv run playwright install chromium
	$(MAKE) migrate
	$(MAKE) load

# Clean generated files
clean:
	rm -f db.sqlite3
	rm -rf __pycache__ */__pycache__ */*/__pycache__
	rm -rf .coverage htmlcov
	rm -rf /tmp/screenshots_*
