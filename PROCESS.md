# Development Process

## Overview

We use GitHub Issues to track development of the AI Shipping Labs platform. All work is tracked as issues with labels and milestones — no project boards. Two agents (implementer + QA) iterate on each issue until it's done.

## Links

- Repo: https://github.com/AI-Shipping-Labs/website
- Issues: https://github.com/AI-Shipping-Labs/website/issues
- Specs: [`specs/`](specs/) folder in this repo
- Issue backlog: [`specs/issues.md`](specs/issues.md)

## Specs → Issues → Code

```
specs/*.md          →    GitHub Issues        →    Commits to main
(requirements)           (tracked work)            (implementation)
```

1. Specs define what to build. Each spec has numbered requirements (R-PAY-1, R-ACL-2, etc.) and acceptance criteria.
2. Issues are concrete tasks derived from specs. Each issue references its spec and has clear acceptance criteria.
3. Code is committed directly to `main` with `Closes #N` to auto-close issues.

## Milestones

| # | Milestone | What it delivers |
|---|---|---|
| M1 | Django scaffold + existing content | Django project, models, migrate existing content, pages |
| M2 | Auth + tiers + payments | Registration, login, tiers, Stripe checkout, webhooks |
| M3 | Access control + gating | Per-item visibility, gated teasers, upgrade CTAs |
| M4 | Courses | Course/module/unit structure, catalog, progress, GitHub sync |
| M5 | Community automation | Slack invite/remove/reactivate on tier change |
| M6 | Events + calendar | Event CRUD, Zoom integration, calendar, registration |
| M7 | Email | SES, newsletter signup, campaigns, lead magnets |
| M8 | Video + recordings | Video player, timestamps, downloads |
| M9 | Notifications + voting | Slack announcements, on-platform notifications, polls |

Complete milestones in order.

## Agent Workflow

An orchestrator (human or top-level agent) drives the process:

```
Orchestrator
    │
    ├── assigns issue ──► Implementer ──► writes code, commits to main
    │                          │
    │                          ▼
    ├── sends to review ──► QA ──► reviews code, checks acceptance criteria
    │                          │
    │                          ▼
    │                     feedback (pass / fail with specifics)
    │                          │
    │         ┌────────────────┘
    │         ▼
    ├── if fail ──► Implementer fixes ──► QA re-reviews
    │                    (repeat until pass)
    │
    └── if pass ──► issue is done, pick next issue
```

### Steps

1. Orchestrator picks an issue from the current milestone and assigns it to the implementer
2. Implementer reads the issue + spec, writes code, commits to main, reports what was done
3. QA reviews the code against the spec and acceptance criteria, reports pass/fail
4. If fail: QA gives specific feedback → implementer fixes → QA re-reviews
5. If pass: issue is done, orchestrator picks the next issue

### Agents

- Implementer (`.claude/agents/implementer.md`) — receives an issue number, reads spec, writes code, commits to main, handles QA feedback
- QA (`.claude/agents/qa.md`) — receives an issue number + implementer summary, reviews code against spec, checks acceptance criteria, gives concrete feedback

## Labels

### Spec labels
`spec:01-tiers`, `spec:02-payments`, ..., `spec:14-github`

### Priority labels
- `P0` — Must have for launch
- `P1` — Important
- `P2` — Nice to have

## Content Management

Content is stored in GitHub repos and synced to the platform:

| Repo | Visibility | Content |
|---|---|---|
| AI-Shipping-Labs/blog | Public | Blog articles |
| AI-Shipping-Labs/courses | Private | Course modules and units |
| AI-Shipping-Labs/resources | Public | Recordings metadata, curated links |
| AI-Shipping-Labs/projects | Public | Community project showcases |

See [spec 14](specs/14-github-content.md) for details.

## Technology Stack

- Backend: Django (Python), managed with uv
- Payments: Stripe
- Community: Slack
- Email: Amazon SES
- Video: YouTube / Loom embeds
- Live events: Zoom API
- Content source: GitHub repos (markdown + YAML)
