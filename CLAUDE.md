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
uv run python manage.py test
```

### File Editing on Windows

When using Edit or MultiEdit tools on Windows, use backslashes (`\`) in file paths.

If you get "File has been unexpectedly modified" — re-read the file immediately before editing.

### Formatting Rules for Documents and Issues

- No bold formatting (`**text**`) — use plain text, headings, or backticks for emphasis
- Use `backticks` for code, file paths, commands, field names, and technical terms
- Use headings (`##`, `###`) for structure, not bold text
- Use tables for structured data, not bullet lists of key-value pairs
- Keep lines concise — one idea per bullet point

## Current Work
<!-- What are you working on? What's the current context? -->
