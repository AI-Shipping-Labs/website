# Agent Notes

## Development Process

- Before continuing development work, read `_docs/PROCESS.md` and follow the issue pipeline documented there.
- Treat feature requests for this repo as permission to launch the role subagents required by `_docs/PROCESS.md` (PM, software engineer, tester, PM acceptance, on-call) unless the user explicitly asks not to.
- Treat "continue where we stopped" as a prompt to check `_docs/PROCESS.md`, inspect the current issue/worktree/process state, and resume the next pipeline step.
- When launching Codex subagents for this workflow, use `gpt-5.6` with `reasoning_effort: "high"` and `service_tier: "priority"` by default unless the user explicitly asks for a cheaper or lower-reasoning run. Do not fall back to `gpt-5.4` or `gpt-5.5`; retry `gpt-5.6` later or keep the work local if `gpt-5.6` is unavailable. When launching Claude subagents, use Opus 4.8 by default.
- Run at most three active role subagents by default. Count PM, software engineer, tester, PM acceptance, and on-call agents toward the cap; exceed it only when explicitly requested.

## Production Data Access

- Production URL: `https://aishippinglabs.com`.
- Do not assume local files, SQLite, or a remote database tunnel represent production data.
- Agents cannot access production data directly. Use the authenticated production API when checking production users, email logs, SES events, or other live records.
- Do not print API tokens or other secrets in logs, comments, or final responses.

## Project Overview

AI Shipping Labs community platform — a Django-based website replacing the current Next.js static site.

- Product: [`_docs/product.md`](_docs/product.md) — what the site is, user personas, tiers, feature inventory, terminology
- Process: [`_docs/PROCESS.md`](_docs/PROCESS.md) — development workflow, agent definitions, issue lifecycle, how to pick issues
- Configuration: [`_docs/configuration.md`](_docs/configuration.md) — operator setup guide for OAuth login + every integration on a fresh environment
- Specs: `specs/` folder (14 requirement specs with data models, APIs, acceptance criteria)
- Issues: GitHub Issues on [AI-Shipping-Labs/website](https://github.com/AI-Shipping-Labs/website/issues)
- Agents: `.claude/agents/` (product-manager, software-engineer, tester, oncall-engineer)

## Working Process

Read [`_docs/PROCESS.md`](_docs/PROCESS.md) at the start of every session, before acting on any issue. It defines the development workflow, the agent roles (PM, software-engineer, tester, oncall-engineer), the issue lifecycle, and the orchestrator's responsibilities. Do not groom, implement, test, or ship without first reading it. This applies to local and cloud/scheduled runs alike.

## Repositories

The Django app lives here. AWS infrastructure lives in a separate repo. Knowing the split prevents agents from searching for SES/RDS/Lambda config in this repo when it's not here.

- `AI-Shipping-Labs/website` — this repo. Django app, templates, tests, GitHub Actions for build/deploy. All product code.
- `DataTalksClub/aws-infra` — Terraform for AWS (AISL resources under `main/aisl/`). SES (domain identity, DKIM/SPF/DMARC, configuration sets), SNS topics (`ses-bounces`, `ses-complaints`), RDS, ECS clusters/services, S3 buckets, Route53 DNS, IAM users/roles (including the `ECS-deploy` user used by CI), the inbound `email-forwarder.py` Lambda for `@aishippinglabs.com` mail forwarding. Key files: `main/aisl/email.tf`, `main/aisl/db.tf`, `main/aisl/ecs.tf`, `main/aisl/dns.tf`, `main/aisl/iam_certificates.tf`, `main/aisl/iam_ecs_deploy.tf` (the `ECS-deploy` user lives in `iam_ecs_deploy.tf`). Operator notes in `main/aisl/docs/email-best-practices.md`.
- `AI-Shipping-Labs/content` — markdown + YAML content (articles, courses, projects, recordings, links, interview questions, tier data). Synced into the Django DB by the content-sync pipeline. The Django repo never edits content here directly.
- `AI-Shipping-Labs/workshops-content` — workshop markdown source. Same sync pipeline.

When the user asks "is X wired in AWS?" or "where do we configure SES/SNS/RDS/ECS/DNS?", check the infra repo via `gh api repos/DataTalksClub/aws-infra/contents/main/aisl/<file>`. If something is missing in the infra repo, file an issue there (`gh issue create -R DataTalksClub/aws-infra`) — don't try to provision AWS resources from this repo.

When the user asks about Django code, templates, or the test suite, it's always this repo.

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
uv run python manage.py test {app} --parallel 4
```

### Configurable Settings Go Through the IntegrationSetting Framework

Every new configurable setting must go through the IntegrationSetting framework so it is editable from Studio settings with no redeploy. Do not read raw `os.environ` or `settings.X` for runtime-configurable values.

- Read values with `get_config(key, default)` / `is_enabled(key)` from `integrations/config.py`. These resolve in order: DB override (set in Studio settings) -> environment variable -> default.
- Register every key in `integrations/settings_registry.py` (with a `description` and `docs_url`) so it appears as an editable field in Studio settings with the Source badge (DB override / env / default).
- Canonical example: the `#plan-sprints` channel — keys registered in `integrations/settings_registry.py` (`slack` group) and read via `get_slack_plan_sprints_channel_id()` in `community/slack_config.py`, which calls `get_config(...)`. Environment variables remain an optional fallback, never the primary source.

### File Editing on Windows

When using Edit or MultiEdit tools on Windows, use backslashes (`\`) in file paths.

If you get "File has been unexpectedly modified" — re-read the file immediately before editing.

### Testing Rules

Follow [`_docs/testing-guidelines.md`](_docs/testing-guidelines.md) when writing or reviewing tests. Key rules:

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
uv run python manage.py migrate            # creates DB, seeds tiers, creates django_q_cache table
uv run python manage.py seed_content_sources  # register content sources
```

### Content sync

All content (articles, courses, projects, recordings, links, interview questions, tier data) lives in the GitHub repo `AI-Shipping-Labs/content`. To populate locally:

Option A — sync from a local clone:
```bash
git clone git@github.com:AI-Shipping-Labs/content.git ~/git/ai-shipping-labs-content
uv run python manage.py sync_content --from-disk ~/git/ai-shipping-labs-content
```

Option B — sync via GitHub App (requires credentials in `.env`):
```bash
uv run python manage.py sync_content
```

### Dev seed data (optional)

For fake users, events, polls, and notifications (useful for testing):

```bash
uv run python manage.py seed_data
```

This does NOT create content — content only comes from GitHub sync.

### Run tests

Local test scope comes from the diff, not from habit. `scripts/affected_tests.py` maps the changed files (including uncommitted and untracked work) to Django test labels plus a core-vs-full Playwright decision:

```bash
uv run python scripts/affected_tests.py     # print the plan, run nothing
make test-affected                          # run exactly what the plan emitted
```

Inner loop while editing: `uv run python manage.py test {touched_app} --parallel 4`, plus `make test-core` for cross-cutting changes.

Do NOT run the full Django suite locally (`make test`, `make test-all`, `make coverage`, or `manage.py test` with no labels). It is ~14,800 tests and starves everything else on the box, for no coverage gain: CI runs the full Django suite on every push to main and blocks the deploy on failure, and the full Playwright suite runs every 3 hours. Run it locally only if Alexey explicitly asks.

See [`_docs/testing-guidelines.md`](_docs/testing-guidelines.md) ("Affected-tests selection") for the rule chain, the authoritative escalation table, and what to do when the plan looks wrong (fix the map — never widen the run by hand).

## Content Architecture

- Content repo: `AI-Shipping-Labs/content` (private, GitHub App auth)
- Sync pipeline: webhook push → clone → parse markdown/YAML → upload images to S3 → upsert to DB
- Image CDN: `https://cdn.aishippinglabs.com` (S3 + CloudFront)
- Content types: `article`, `course`, `resource`, `project`, `interview_question`, `learning_path`
- Manage sync from Studio: `/studio/sync/`

## Current Work
<!-- What are you working on? What's the current context? -->
