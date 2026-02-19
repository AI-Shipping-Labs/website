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

## Background Job Worker

The project uses [Django-Q2](https://django-q2.readthedocs.io/) for background task processing. It uses the ORM broker (SQLite/PostgreSQL) so no extra services (Redis, RabbitMQ) are needed.

**Start the worker process** (in a separate terminal):

```bash
uv run python manage.py qcluster
```

The worker must be running to process queued async tasks and execute recurring schedules.

**Register default recurring schedules:**

```bash
uv run python manage.py setup_schedules
```

This creates:
- `health-check` -- runs every 15 minutes
- `cleanup-webhook-logs` -- runs daily at 3 AM, deletes processed webhook logs older than 30 days

**Enqueue a job from application code:**

```python
from jobs.tasks import async_task

async_task('myapp.tasks.send_email', user_id=42, max_retries=3)
```

**Schedule a recurring job:**

```python
from jobs.tasks import schedule

schedule('myapp.tasks.cleanup', cron='0 * * * *', name='hourly-cleanup')
```

**Monitor jobs** in Django admin at `/admin/django_q/` (queued, successful, failed tasks, and schedules).

## Project Structure

```
website/              # Django project config (settings, urls, wsgi)
accounts/             # User, Tier, auth, notifications
payments/             # Stripe, subscriptions
content/              # Articles, recordings, projects, curated links, courses, events
integrations/         # Slack, Telegram, Zoom, external service hooks
email_app/            # SES, campaigns, newsletter
jobs/                 # Background job infrastructure (Django-Q2 helpers, tasks)
templates/            # Django templates
static/               # Static files (CSS, images)
specs/                # Requirement specs and task definitions
playwright_tests/     # E2E visual regression tests
```

## Docs

- [Specs](specs/README.md) — requirement specifications
- [Issues](https://github.com/AI-Shipping-Labs/website/issues) — task tracking
- [Process](PROCESS.md) — development workflow
