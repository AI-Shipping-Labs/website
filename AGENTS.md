# Agent Notes

Mandatory repository rules for every agent. [`_docs/README.md`](_docs/README.md) maps the rest of the documentation.

## Development Process

- Read [`_docs/PROCESS.md`](_docs/PROCESS.md) at the start of every session, before acting on any issue. It defines the workflow, the agent roles (PM, software-engineer, tester, oncall-engineer, designer), the issue lifecycle, and the orchestrator's responsibilities. Do not groom, implement, test, or ship without first reading it. This applies to local and cloud/scheduled runs alike.
- Before writing or changing code, read [`coding-standard.md`](coding-standard.md).
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

AI Shipping Labs community platform — a Django-based website replacing the earlier Next.js static site.

- Product: [`_docs/product.md`](_docs/product.md) — what the site is, user personas, tiers, feature inventory, terminology
- Process: [`_docs/PROCESS.md`](_docs/PROCESS.md) — development workflow, agent definitions, issue lifecycle, how to pick issues
- Configuration: [`_docs/configuration.md`](_docs/configuration.md) — operator setup guide for OAuth login + every integration on a fresh environment
- Specs: [`specs/`](specs/README.md) — product decision records (original requirements; code and `_docs/product.md` describe current behavior)
- Issues: GitHub Issues on [AI-Shipping-Labs/website](https://github.com/AI-Shipping-Labs/website/issues)
- Agents: `.claude/agents/` (product-manager, software-engineer, tester, oncall-engineer, designer); skills: `.agents/skills/` (`.claude/skills` is a symlink)

## Repositories

The Django app lives here. AWS infrastructure lives in a separate repo. Knowing the split prevents agents from searching for SES/RDS/Lambda config in this repo when it's not here.

- `AI-Shipping-Labs/website` — this repo. Django app, templates, tests, GitHub Actions for build/deploy. All product code.
- `DataTalksClub/aws-infra` — Terraform for AWS (AISL resources under `main/aisl/`). SES (domain identity, DKIM/SPF/DMARC, configuration sets), SNS topics (`ses-bounces`, `ses-complaints`), Relay's dedicated AISL SES-event SQS queue, RDS, ECS clusters/services, S3 buckets, Route53 DNS, and IAM users/roles (including the `ECS-deploy` user used by CI). Key files: `main/aisl/email.tf`, `main/aisl/db.tf`, `main/aisl/ecs.tf`, `main/aisl/dns.tf`, `main/aisl/iam_certificates.tf`, `main/aisl/iam_ecs_deploy.tf` (the `ECS-deploy` user lives in `iam_ecs_deploy.tf`). Operator notes in `main/aisl/docs/email-best-practices.md`.
- Content repositories — markdown + YAML synced into the Django DB by the content-sync pipeline. The Django repo never edits content there directly. The registered sources are declared in `integrations/management/commands/seed_content_sources.py`: `AI-Shipping-Labs/content` (articles, projects, recordings, curated links, interview questions, tier data), `AI-Shipping-Labs/workshops-content` (workshops), `AI-Shipping-Labs/python-course` and `AI-Shipping-Labs/ai-buildcamp-course` (courses), and `AI-Shipping-Labs/wiki` (member wiki).
- `DataTalksClub/community-base` — shared Django apps used by this site and by `DataTalksClub/website`. Local checkout at `~/git/community-base`. Its own process lives in its `AGENTS.md` and `docs/PROCESS.md`; work there is plan-driven via `docs/plan/`.
- `DataTalksClub/website` — the DataTalks.Club site. The other consumer of `community-base`. Local checkout at `~/git/dtc-website`.

### Changing `community-base` means testing both sites

`community-base` is shared, so a change there can break either consumer. Whenever you change `community-base`:

- Run its own test suite plus the quality gates in its `docs/04-quality-gates.md`.
- Then run the tests in BOTH consuming sites against the change: this repo and `DataTalksClub/website`. A green package suite is not evidence that either site still works.
- Report the three results separately. Do not merge on the package suite alone.
- If a site cannot be tested against the change (for example it is pinned to an older release and cannot take the new code yet), say so explicitly rather than silently skipping it.

When the user asks "is X wired in AWS?" or "where do we configure SES/SNS/RDS/ECS/DNS?", check the infra repo via `gh api repos/DataTalksClub/aws-infra/contents/main/aisl/<file>`. If something is missing in the infra repo, file an issue there (`gh issue create -R DataTalksClub/aws-infra`) — don't try to provision AWS resources from this repo.

When the user asks about Django code, templates, or the test suite, it's always this repo.

## Technology Stack

- Backend: Django (Python 3.13+, see `pyproject.toml`), managed with uv
- Frontend: pinned Tailwind CSS 3.4.17 (`package.json`, `package-lock.json`) compiled and minified at build time. `make css-build` is canonical, `make dev` watches sources, and the generated `static/css/tailwind.css` is gitignored and never edited or committed. Rebuild it after adding new classes locally; CI rebuilds at deploy.
- Testing: Playwright for E2E, Django TestCase for unit/integration
- Payments: Stripe
- Community: Slack
- Email: Relay for outbound delivery; Amazon SES events ingested through Relay
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

- Read values with `get_config(key, default)` / `is_enabled(key)` from `integrations/config.py`. These resolve in order: DB override (set in Studio settings) -> Django setting -> environment variable -> default.
- Declare every key in `integrations/settings_keys.py`: a `declare(...)` entry with a `description` and `docs_url`, plus its group's `_KEY_ORDER` entry. `integrations/settings_registry.py` derives the Studio view from those declarations, so the key appears as an editable field in Studio settings with the Source badge (DB override / env / default).
- Canonical example: the `#plan-sprints` channel — `SLACK_PLAN_SPRINTS_CHANNEL_ID` and its environment variants are declared in `integrations/settings_keys.py` (`slack` group) and read via `get_slack_plan_sprints_channel_id()` in `community/slack_config.py`, which calls `get_config(...)`. Environment variables remain an optional fallback, never the primary source.

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

Local test scope comes from the diff, not from habit. `scripts/affected_tests.py` maps the changed files (including uncommitted and untracked work) to Django test labels plus a core-vs-full Playwright decision:

```bash
uv run python scripts/affected_tests.py     # print the plan, run nothing
make test-affected                          # run exactly what the plan emitted
```

Inner loop while editing: `uv run python manage.py test {touched_app} --parallel 4`, plus `make test-core` for cross-cutting changes.

Do NOT run the full Django suite locally (`make test`, `make test-all`, `make coverage`, or `manage.py test` with no labels). It is ~17,200 tests and starves everything else on the box, for no coverage gain: CI runs the full Django suite on every push to main and blocks the deploy on failure, and the full Playwright suite runs every 3 hours. Run it locally only if Alexey explicitly asks.

See [`_docs/testing-guidelines.md`](_docs/testing-guidelines.md) ("Affected-tests selection") for the authoritative escalation table and what to do when the plan looks wrong (fix the map — never widen the run by hand), and [`_docs/testing-operations.md`](_docs/testing-operations.md) ("Affected-tests rule chain and maps") for the rule chain.

### Formatting Rules for Documents and Issues

- No bold formatting (`**text**`) — use plain text, headings, or backticks for emphasis
- Use `backticks` for code, file paths, commands, field names, and technical terms
- Use headings (`##`, `###`) for structure, not bold text
- Use tables for structured data, not bullet lists of key-value pairs
- Keep lines concise — one idea per bullet point

## Local Development Setup

[`README.md`](README.md) owns the contributor quick start (`make setup`, `make run`, `make dev`, test users). The individual steps, for an agent working without `make setup`:

```bash
uv sync                                    # install dependencies
uv run python manage.py migrate            # creates DB, seeds tiers, creates django_q_cache table
uv run python manage.py seed_content_sources  # register content sources
```

Content comes only from content sync, never from `seed_data`. Sync from a local clone:

```bash
git clone git@github.com:AI-Shipping-Labs/content.git ~/git/ai-shipping-labs-content
uv run python manage.py sync_content --from-disk ~/git/ai-shipping-labs-content
```

or via the GitHub App (requires credentials in `.env`): `uv run python manage.py sync_content`. For fake users, cohorts, polls, and notifications, run `uv run python manage.py seed_data`.

## Content Architecture

- Sync pipeline: webhook push → clone → parse markdown/YAML → upload images to S3 → upsert to DB. [`_docs/content.md`](_docs/content.md) owns content types, frontmatter, and sync behavior.
- Content repos are private and read through the GitHub App ([`_docs/integrations/github.md`](_docs/integrations/github.md)).
- Image CDN: `https://cdn.aishippinglabs.com` (S3 + CloudFront)
- Manage sync from Studio: `/studio/sync/`
