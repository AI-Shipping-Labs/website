# Project Context

## Project Overview

AI Shipping Labs community platform — a Django-based website replacing the current Next.js static site.

- **Process & agents:** See [`PROCESS.md`](PROCESS.md) for the full development workflow, agent definitions, issue lifecycle, and how to pick issues
- **Specs:** `specs/` folder (14 requirement specs with data models, APIs, acceptance criteria)
- **Issues:** GitHub Issues on [AI-Shipping-Labs/website](https://github.com/AI-Shipping-Labs/website/issues)
- **Agents:** `.claude/agents/` (product-manager, software-engineer, tester, pipeline-fixer)

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
uv run python manage.py test
```

### File Editing on Windows

When using Edit or MultiEdit tools on Windows, use backslashes (`\`) in file paths.

If you get "File has been unexpectedly modified" — re-read the file immediately before editing.

## Current Work
<!-- What are you working on? What's the current context? -->
