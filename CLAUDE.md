# Project Context

## Project Overview

AI Shipping Labs community platform — a Django-based website replacing the current Next.js static site.

- Product: [`_docs/PRODUCT.md`](_docs/PRODUCT.md) — what the site is, user personas, tiers, feature inventory, terminology
- Process: [`_docs/PROCESS.md`](_docs/PROCESS.md) — development workflow, agent definitions, issue lifecycle, how to pick issues
- Specs: `specs/` folder (14 requirement specs with data models, APIs, acceptance criteria)
- Issues: GitHub Issues on [AI-Shipping-Labs/website](https://github.com/AI-Shipping-Labs/website/issues)
- Agents: `.claude/agents/` (product-manager, software-engineer, tester, oncall-engineer)

## Technology Stack

- Backend: Django (Python), managed with uv
- Frontend: Tailwind CSS via CDN (no build step)
- Testing: Playwright for E2E, Django TestCase for unit/integration
- Payments: Stripe
- Community: Slack
- Email: Amazon SES
- Video: YouTube / Loom embeds
- Live events: Zoom API
- Content source: GitHub repos (markdown + YAML)

## Development Rules

### Use UV for Python Package Management

Always use `uv` instead of `pip`:

```bash
uv add djangorestframework
uv run python manage.py makemigrations
uv run python manage.py migrate
uv run python manage.py test --parallel
```

### File Editing on Windows

When using Edit or MultiEdit tools on Windows, use backslashes (`\`) in file paths.

If you get "File has been unexpectedly modified" — re-read the file immediately before editing.

### Testing Rules

Follow [`_docs/TESTING_GUIDELINES.md`](_docs/TESTING_GUIDELINES.md) when writing or reviewing tests. Key rules:

- Every assertion must fail if the feature is broken (no false positives)
- Assert on specific elements, not full HTML body strings
- Do not test Django framework behavior (ORM round-trips, CASCADE, field defaults)
- Do not test JavaScript by string-matching HTML — use Playwright E2E instead
- Do not test URL resolution separately — view tests already cover it
- Use `setUpTestData` for read-only fixtures, not `setUp`
- Playwright tests test user flows, not implementation details
- One authoritative test per behavior — pick the right layer

### Formatting Rules for Documents and Issues

- No bold formatting (`**text**`) — use plain text, headings, or backticks for emphasis
- Use `backticks` for code, file paths, commands, field names, and technical terms
- Use headings (`##`, `###`) for structure, not bold text
- Use tables for structured data, not bullet lists of key-value pairs
- Keep lines concise — one idea per bullet point

## Local Development Setup

### First-time setup

```bash
uv sync                                    # install dependencies
uv run python manage.py migrate            # creates DB, seeds tiers
uv run python manage.py seed_content_sources  # register content sources
```

### Content sync

All content (articles, courses, projects, recordings, links, interview questions, learning path) lives in the GitHub repo `AI-Shipping-Labs/content`. To populate locally:

Option A — clone and sync from disk:
```bash
git clone git@github.com:AI-Shipping-Labs/content.git ~/git/ai-shipping-labs-content
uv run python manage.py shell -c "
from integrations.models import ContentSource
from integrations.services.github import sync_content_source
for source in ContentSource.objects.all():
    print(f'Syncing {source.content_type}...')
    sync_content_source(source, repo_dir='$HOME/git/ai-shipping-labs-content')
"
```

Option B — sync via GitHub App (requires credentials in `.env`):
```bash
uv run python manage.py shell -c "
from integrations.models import ContentSource
from integrations.services.github import sync_content_source
for source in ContentSource.objects.all():
    sync_content_source(source)
"
```

The interview questions and learning path pages also read from disk as fallback (`CONTENT_REPO_DIR` setting), so Option A works without running a sync.

### Dev seed data (optional)

For fake users, events, polls, and notifications (useful for testing):

```bash
uv run python manage.py seed_data
```

This does NOT create content — content only comes from GitHub sync.

### Run tests

```bash
uv run python manage.py test --parallel     # Django tests (~1 min)
uv run python -m pytest playwright_tests/   # E2E tests (requires running server)
```

## Content Architecture

- Content repo: `AI-Shipping-Labs/content` (private, GitHub App auth)
- Sync pipeline: webhook push → clone → parse markdown/YAML → upload images to S3 → upsert to DB
- Image CDN: `https://cdn.aishippinglabs.com` (S3 + CloudFront)
- Content types: `article`, `course`, `resource`, `project`, `interview_question`, `learning_path`
- Manage sync from Studio: `/studio/sync/`

## Current Work
<!-- What are you working on? What's the current context? -->
