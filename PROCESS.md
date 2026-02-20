# Development Process

## Overview

We use GitHub Issues to track development of the AI Shipping Labs platform. All work is tracked as issues with labels and milestones — no project boards. Four agents (product-manager, software-engineer, tester, pipeline-fixer) handle the full lifecycle from raw request to shipped code.

## Links

- Repo: https://github.com/AI-Shipping-Labs/website
- Issues: https://github.com/AI-Shipping-Labs/website/issues
- Specs: [`specs/`](specs/) folder in this repo
- Issue backlog: https://github.com/AI-Shipping-Labs/website/issues

## Issue Lifecycle

```
User creates issue        →    Product Manager grooms    →    Software Engineer builds    →    Commits to main
(needs grooming)               (agent-ready spec)             (code + tests)                  (auto-closes issue)
```

1. **User creates an issue** via the GitHub issue template. It gets the `needs grooming` label automatically.
2. **Product Manager** reads the raw request, researches the codebase, and rewrites the issue with: scope, acceptance criteria, dependencies, and Playwright test scenarios. Removes `needs grooming`, adds proper labels.
3. **Software Engineer** implements the groomed issue — writes code and tests.
4. **Tester** reviews the code, runs all tests, verifies acceptance criteria.
5. **Code is committed** to `main` with `Closes #N` to auto-close the issue.
6. **Pipeline Fixer** monitors CI/CD and fixes any breakages.

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
User creates issue (needs grooming)
    │
    ▼
Product Manager ──► grooms into agent-ready spec
    │
    ▼
Orchestrator picks groomed issue
    │
    ├── assigns issue ──► Software Engineer ──► writes code + tests
    │                          │
    │                          ▼
    ├── sends to review ──► Tester ──► reviews code, runs all tests
    │                          │
    │                          ▼
    │                     feedback (pass / fail with specifics)
    │                          │
    │         ┌────────────────┘
    │         ▼
    ├── if fail ──► Software Engineer fixes ──► Tester re-reviews
    │                    (repeat until pass)
    │
    ├── if pass ──► Software Engineer commits and pushes
    │
    └── Pipeline Fixer ──► monitors CI/CD, fixes if broken
```

### Steps

1. User creates a raw issue via the GitHub template (auto-labeled `needs grooming`)
2. Product Manager grooms it into the agent-ready format with scope, acceptance criteria, and Playwright test scenarios
3. Orchestrator picks a groomed issue and assigns it to the software engineer
4. Software engineer reads the issue + spec, writes code and tests locally (does NOT commit)
5. Tester reviews the code, runs all tests (unit + integration + Playwright E2E), reports pass/fail
6. If fail: Tester gives specific feedback → software engineer fixes → tester re-reviews
7. If pass: Software engineer commits and pushes with `Closes #N`
8. Pipeline fixer checks CI/CD and fixes any failures

### Agents

- **Product Manager** (`.claude/agents/product-manager.md`) — grooms raw "needs grooming" issues into structured specs with acceptance criteria and Playwright test scenarios
- **Software Engineer** (`.claude/agents/software-engineer.md`) — receives an issue number, reads spec, writes code + tests, does NOT commit until tester passes
- **Tester** (`.claude/agents/tester.md`) — receives an issue number + software engineer summary, reviews code against spec, runs all tests, gives concrete feedback
- **Pipeline Fixer** (`.claude/agents/pipeline-fixer.md`) — runs after push, checks CI/CD status, identifies related issue from commit messages, fixes code if broken

## Labels

### Workflow labels
- `needs grooming` — Raw user request, not yet agent-ready

### Area labels
`auth`, `frontend`, `admin`, `content`, `courses`, `events`, `payments`, `email`, `community`, `seo`, `infra`, `integration`

### Priority labels
- `P0` — Must have for launch
- `P1` — Important
- `P2` — Nice to have

### Special labels
- `human` — Code done, needs manual verification (OAuth flows, visual checks, etc.)

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
