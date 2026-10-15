# AI Shipping Labs

Django-based community platform for [aishippinglabs.com](https://aishippinglabs.com).

This README is the contributor quick start. [`_docs/README.md`](_docs/README.md) maps every canonical document; agents must also follow [`AGENTS.md`](AGENTS.md).

## Prerequisites

- Python 3.13+ (`requires-python` in `pyproject.toml`)
- [uv](https://docs.astral.sh/uv/) for package management
- Node.js 24 and npm (the pinned Tailwind 3.4.17 build is lockfile-driven)

## Setup

```bash
make setup
```

This runs `scripts/setup.sh` which:
1. Creates `.env` from `.env.example` (edit it with your credentials)
2. Installs Python dependencies (`uv sync`)
3. Clones `AI-Shipping-Labs/content` into `_content-repo/`
4. Runs database migrations
5. Seeds local sample data and test users (`seed_data`)
6. Registers content sources and syncs content from the local clone

## Running

```bash
make run
```

Visit http://localhost:8000

`make run` and `make run2` compile the minified Tailwind bundle once before
starting Django. For active template/Python/JavaScript styling work, use
`make dev`: its `Procfile.dev` runs `npm run css:watch` beside Django and the
worker. The generated `static/css/tailwind.css` is gitignored and must never be
edited or committed. Use `make css-build` for an explicit production build.

## Seed Data and Test Users

`uv run python manage.py seed_data` loads idempotent local sample data (`content/management/commands/seed_data.py`: test users, course cohorts, polls, notifications, newsletter subscribers, and social-login app rows); `--flush` clears that dev data first. Tiers come from migrations and content comes from content sync, not from `seed_data`.

All test users have the password `testpass123`:

| Email | Tier | Role |
|-------|------|------|
| `admin@aishippinglabs.com` | Premium | Superuser/staff (password: `admin123`) |
| `free@test.com` | Free | Regular user |
| `basic@test.com` | Basic | Regular user |
| `main@test.com` | Main | Regular user |
| `premium@test.com` | Premium | Regular user |
| `alice@test.com` | Main | Regular user |
| `charlie@test.com` | Basic | Regular user |
| `diana@test.com` | Free | Regular user |

Log in at http://localhost:8000/accounts/login/ with any of these emails. Routine staff work is under `/studio/`; the unlinked low-level Django admin is at `http://localhost:8000/admin/` (use the admin account).

Create your own superuser with `uv run python manage.py createsuperuser`.

## Tests

Local runs come from the diff, not from habit:

```bash
# Print the plan (Django labels + core-vs-full Playwright), run nothing
uv run python scripts/affected_tests.py

# Run exactly what the plan emitted
make test-affected

# Fast core subset for the inner dev loop
make test-core

# Core Playwright subset
make test-playwright-core
```

Do not run the full Django suite locally (`make test`, `make test-all`, `make coverage`); those are CI gates. Deploy Dev runs the sharded Django suite and its 85% coverage gate on every push to `main`, and the full Playwright suite runs every 3 hours. [`_docs/testing-guidelines.md`](_docs/testing-guidelines.md) owns the test-design rules, the affected-tests overview and escalation table, and the core-test policy; [`_docs/testing-operations.md`](_docs/testing-operations.md) owns test-database isolation, coverage, ratchets, the affected-tests rule chain, and Playwright concurrency.

## Background Job Worker

Background tasks use [Django-Q2](https://django-q2.readthedocs.io/) with the ORM broker, so no Redis or RabbitMQ is needed.

```bash
uv run python manage.py qcluster          # start the worker (separate terminal)
uv run python manage.py setup_schedules   # register the default recurring schedules
```

The worker must run to process queued tasks and recurring schedules. The schedule list lives in `jobs/schedule_reconciliation.py`; enqueue and schedule helpers are `async_task` and `schedule` in `jobs/tasks/helpers.py` (exported from `jobs.tasks`). Monitor jobs in Studio at `/studio/worker/`.

## Configuration and Deployment

- [`_docs/configuration.md`](_docs/configuration.md) — operator bring-up: platform env vars that cannot live in Studio, every `Studio > Settings` integration group, webhook URLs, and the end-to-end smoke test.
- [`_docs/setup.md`](_docs/setup.md) — infrastructure, CI/CD, manual deployment, database access, and admin user creation.

## Project Structure

Each top-level Python package with an `apps.py` is a Django app (`accounts/`, `content/`, `events/`, `payments/`, `studio/`, and so on); `website/` holds project settings and URLs.

```
templates/            # Django templates
static/, assets/      # Static files and the Tailwind input stylesheet
playwright_tests/     # Playwright E2E tests
tests/                # Repository-wide guards and tooling tests
scripts/              # Developer and operator tooling
_docs/                # Canonical documentation (see _docs/README.md)
specs/                # Product decision records
```

## Docs

- [Documentation map](_docs/README.md) — canonical owner for each topic
- [Process](_docs/PROCESS.md) — development workflow and issue lifecycle
- [Coding standard](coding-standard.md) — code structure and review rules
- [Specs](specs/README.md) — product decision records
- [Issues](https://github.com/AI-Shipping-Labs/website/issues) — task tracking
