# AI Shipping Labs

Django-based community platform for [aishippinglabs.com](https://aishippinglabs.com).

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) for package management

## Setup

```bash
make setup
```

This installs dependencies, Playwright browsers, runs migrations, and loads content.

## Running

```bash
make run
```

Visit http://localhost:8000

## Tests

```bash
# Django unit/integration tests
make test

# Tests with coverage report
make coverage

# Playwright visual regression tests
make playwright

# All tests (Django + Playwright)
make test-all
```

## Project Structure

```
website/              # Django project config (settings, urls, wsgi)
accounts/             # User, Tier, auth, notifications
payments/             # Stripe, subscriptions
content/              # Articles, recordings, projects, curated links, courses, events
integrations/         # Slack, Telegram, Zoom, external service hooks
email_app/            # SES, campaigns, newsletter
templates/            # Django templates
static/               # Static files (CSS, images)
reference/            # Original Next.js site (for reference only)
specs/                # Requirement specs and task definitions
playwright_tests/     # E2E visual regression tests
```

## Docs

- [Specs](specs/README.md) — requirement specifications
- [Tasks](specs/tasks/tasks.md) — task index with dependency graph
- [Process](PROCESS.md) — development workflow
