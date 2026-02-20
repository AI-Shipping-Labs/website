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

## Seed Data and Test Users

Load sample data for local development:

```bash
uv run python manage.py seed_data
```

This creates tiers, users, articles, courses, events, recordings, projects, polls, notifications, and newsletter subscribers. The command is idempotent -- running it twice won't create duplicates. Use `--flush` to wipe and recreate everything.

### Test Users

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

Log in at http://localhost:8000/accounts/login/ with any of these emails. The admin panel is at http://localhost:8000/admin/ (use the admin account).

### Creating Users Manually

Via Django shell:

```bash
uv run python manage.py shell
```

```python
from accounts.models import User
from payments.models import Tier

tier = Tier.objects.get(slug='main')  # free, basic, main, or premium
user = User.objects.create_user(email='you@example.com', password='yourpass')
user.tier = tier
user.email_verified = True
user.save()
```

Or create a superuser:

```bash
uv run python manage.py createsuperuser
```

## Tests

```bash
# Django unit/integration tests
make test

# Tests with coverage report
make coverage

# Playwright E2E tests
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
playwright_tests/     # Playwright E2E tests
```

## Docs

- [Specs](specs/README.md) — requirement specifications
- [Issues](https://github.com/AI-Shipping-Labs/website/issues) — task tracking
- [Process](PROCESS.md) — development workflow
