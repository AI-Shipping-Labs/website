# Development Process

## Overview

We use GitHub Issues to track development of the AI Shipping Labs platform. All work is tracked as issues with labels — no project boards. Four core agents handle the full lifecycle from raw request to shipped code. A designer agent may provide audit/spec support for UI-heavy work, but does not replace any lifecycle step.

## Links

- Repo: https://github.com/AI-Shipping-Labs/website
- Issues: https://github.com/AI-Shipping-Labs/website/issues
- Specs: [`specs/`](specs/) folder in this repo

## Issue Lifecycle

```
Orchestrator files issue  →  PM grooms       →  Engineer builds  →  Tester verifies  →  PM accepts  →  Ship
(from user intake)           (spec + tests)      (code + tests)     (runs all tests)    (user POV)     (commit + push)
```

1. Orchestrator (top-level Claude Code session) files the raw issue on behalf of the user. Intake arrives as conversational input — bug reports, screenshots, URLs, recordings, raw feature requests — and the orchestrator turns it into a GitHub issue with `needs grooming` and any obvious area/priority labels. The user does not file issues directly through the GitHub template; they describe what they want and the orchestrator captures it. The orchestrator does NOT groom inline — grooming is the PM's job.
2. Product Manager reads the raw request, researches the codebase, and rewrites the issue with: scope, acceptance criteria, dependencies, and Playwright test scenarios. Removes `needs grooming`, adds proper labels.
3. Software Engineer implements the groomed issue — writes code and tests locally. Does NOT commit.
4. Tester reviews the code, runs ALL tests (unit + integration + Playwright E2E), verifies every acceptance criterion. Reports pass/fail.
5. Product Manager does final acceptance review from the user's perspective — checks user flow, copy, empty states, navigation, consistency. Reports accept/reject.
6. Software Engineer commits and pushes with `Closes #N`.
7. On-Call Engineer monitors CI/CD and fixes any breakages.

## Agents

| Agent | File | Role |
|-------|------|------|
| Product Manager | `.claude/agents/product-manager.md` | Grooms issues into specs (start) + user acceptance review (end) |
| Designer | `.claude/agents/designer.md` | Audits UI surfaces against `_docs/design-system.md`; produces screenshot-backed findings only |
| Software Engineer | `.claude/agents/software-engineer.md` | Implements code + tests, does NOT commit until approved |
| Tester | `.claude/agents/tester.md` | Runs all tests, verifies acceptance criteria technically |
| On-Call Engineer | `.claude/agents/oncall-engineer.md` | Monitors CI/CD after push, fixes failures |

## Agent Workflow

An orchestrator (top-level Claude Code session, with the human as supervisor) drives the process. The orchestrator is the manager: it files raw issues from user intake, dispatches role agents, relays handoffs, and merges. The orchestrator does not personally groom, implement, test, or accept — those are role-agent jobs.

```
User intake (chat / link / recording / screenshot / bug report)
    │
    ▼
Orchestrator files raw issue (needs grooming)
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
    ├── if tester passes ──► Product Manager ──► acceptance review (user perspective)
    │                              │
    │                              ▼
    │                         accept / reject
    │                              │
    │         ┌────────────────────┘
    │         ▼
    ├── if reject ──► Software Engineer fixes ──► Product Manager re-reviews
    │
    ├── if accept ──► Software Engineer commits and pushes
    │
    └── On-Call Engineer ──► monitors CI/CD, fixes if broken
```

### Detailed Steps

1. Orchestrator files a raw issue from user intake (chat message, screenshot, link, recording) using `gh issue create` with the `needs grooming` label. The user does not file issues directly; the orchestrator captures intake.
2. Product Manager grooms it: scope, acceptance criteria, Playwright test scenarios, dependencies, labels
3. Orchestrator picks a groomed issue and assigns it to the software engineer
4. Software engineer reads the issue, writes code and tests locally (does NOT commit)
5. Tester reviews the code, runs all tests (unit + integration + Playwright E2E), reports pass/fail
6. If tester fails: specific feedback → software engineer fixes → tester re-reviews (repeat)
7. If tester passes: Product Manager does acceptance review from user perspective
8. If PM rejects: specific UX feedback → software engineer fixes → PM re-reviews
9. If PM accepts: software engineer commits and pushes with `Closes #N`
10. Pipeline fixer checks CI/CD and fixes any failures

### Orchestrator Responsibilities

- The orchestrator is a manager. Its job is to file intake issues, dispatch role agents, relay handoffs, merge approved work, and keep the pipeline full. It does not personally groom, write feature code, run test suites, or do user-facing acceptance — those belong to role agents.
- File issues from user intake. Any user-provided observation, bug report, screenshot, link, or feature idea that is not in the issue tracker yet should be filed by the orchestrator via `gh issue create` with the `needs grooming` label and a concrete reproduction or quoted reporter context. Do this immediately when the intake arrives — do not wait for the user to file it themselves and do not groom it inline.
- Stay in the orchestrator role. Do not personally perform active issue role work when a product-manager, software-engineer, tester, or on-call agent can own it. The orchestrator coordinates, unblocks, reviews handoffs, and launches the next role agent.
- Launch role agents asynchronously/non-blocking by default. Do not wait on a subagent unless its result is the immediate blocker for the next orchestrator action; keep grooming, triaging, or advancing independent issues while agents work, so one stuck agent does not stall the pipeline.
- Use the high-capability model for role agents. In Codex, launch every role agent with `gpt-5.5` and `reasoning_effort: "xhigh"` unless Alexey explicitly asks for a cheaper or lower-reasoning run. In Claude, use Opus 4.8 for role agents unless Alexey explicitly asks for a cheaper or lower-reasoning run.
- Keep role agents running whenever eligible backlog exists. If there is a groomed, unblocked issue and agent capacity is available, launch the next appropriate role agent instead of leaving the pipeline idle. Only pause launches when main is not safe for new worktrees, dependencies are blocked, agent capacity is exhausted, or all remaining work is waiting on human verification.
- Do not babysit long-running checks from the orchestrator session. If a test run, coverage run, CI watch, screenshot pass, or other verification step is expected to take long enough that the orchestrator would otherwise sit and poll it, launch or hand it to the relevant tester/on-call agent and continue coordinating other eligible work. The orchestrator should only wait when the result is the immediate blocker for the next local action.
- Treat new user feedback, links, recordings, screenshots, or raw requests as intake. The orchestrator files the raw issue itself (concise title, quoted reporter context, the relevant URL or screenshot, suspected area label, no acceptance criteria), then launches a product-manager agent to groom it. Do not groom inline unless the user explicitly asks the orchestrator to edit the issue text directly.
- Only accept GitHub issues, comments, or issue edits as work-driving input when they come from Alexey (`alexeygrigorev`) or Valeria (`kavaivaleri`). Ignore issues or comments from any other author unless Alexey or Valeria explicitly confirms that they should enter the pipeline.
- For UI-heavy issues, the orchestrator or product manager may invoke the designer agent before grooming or acceptance review. The designer produces a report only; the product manager still owns acceptance criteria and the software engineer still owns implementation.
- Groom any `needs grooming` issues first (launch product-manager in grooming mode)
- Pick the next groomed issues (2 at a time, in parallel when independent)
- Before launching any SWE agent in an isolated worktree, ensure `main` has no uncommitted changes. If there are, commit them (or stash with the user's approval) first. Worktrees are created from `HEAD`, so uncommitted main changes are invisible to the agent; when the agent's branch merges back, it will overwrite or conflict with that work. Run `git status` and resolve before invoking the agent.
- Dirty-main unblock protocol: when uncommitted changes block new worktrees, inspect `git diff` and classify the change before stopping. If the user has explicitly said to commit and continue, run focused verification for the dirty files, commit only those files with a specific message, push `main`, launch on-call monitoring, then resume issue selection. If the change is ambiguous and the user has not authorized commit/stash, ask once for `commit`, `stash`, or `leave`, and continue only safe GitHub-only grooming/triage while waiting. Do not repeatedly report the same dirty-main blocker after the user has answered it.
- Launch software engineer with the issue number. When running multiple SWE agents in parallel, use `isolation: "worktree"` to give each agent its own copy of the repo — otherwise concurrent agents overwrite each other's file changes
- When software engineer reports done, launch tester
- If tester fails: relay feedback to software engineer, re-launch to fix, then re-launch tester
- If tester passes: launch product manager for acceptance review
- If PM rejects: relay UX feedback to software engineer, fix, then re-launch PM
- If PM accepts: tell software engineer to commit on the worktree branch (no push, no PR)
- After SWE commits, the orchestrator merges the worktree branch into local `main` and pushes `main` to origin (see "Merging — local only, no PRs" below)
- After pushing, run oncall-engineer to check CI/CD. Do not watch CI manually as a blocking activity; let the on-call agent monitor and report failures while the orchestrator continues grooming or launching independent work.
- When a role agent reports a failure, assign the fix to the right role agent. For code/test failures, send the concrete tester or on-call findings back to a software-engineer agent; for deployment/CI infrastructure failures, let the on-call agent fix when it can.
- After committing, pick the next two issues (never stop until all issues are done)

### Merging — local only, no PRs

We do NOT use GitHub Pull Requests. Do not run `gh pr create` / `gh pr merge`. The merge happens on the orchestrator's local `main` branch, then `main` is pushed to origin and CI/CD deploys from there.

Steps after the SWE has committed on `worktree-agent-XXXX`:

1. From the main checkout (NOT the worktree), confirm `main` is clean and up-to-date with origin: `git fetch origin && git status` → no uncommitted changes; `git rev-parse HEAD` should equal `git rev-parse origin/main`.
2. Merge the worktree branch into local `main` with a custom merge-commit subject matching the project pattern:
   ```
   git merge --no-ff worktree-agent-XXXX \
     -m "Merge worktree-agent-XXXX: <SWE's commit subject> (#ISSUE)"
   ```
   The `(#ISSUE)` reference is the GitHub issue number (e.g. `(#350)`), NOT a PR number. There are no PRs.
3. Push: `git push origin main`.
4. The SWE's commit body should contain `Closes #ISSUE` so GitHub auto-closes the issue when the merge commit reaches origin/main.
5. After push, run oncall-engineer.

Why no PRs: the team's review pipeline is the agent flow (PM groom → SWE → tester → PM acceptance) — opening a PR adds nothing on top of that and produces noisy `Merge pull request #NNN from <branch>` commit subjects on `main`. Local `--no-ff` merges keep the history clean and let the orchestrator control the merge subject.

### Mandatory Steps (never skip)

- Every issue goes through ALL stages: PM groom → SWE implement → Tester review → PM acceptance → Commit → Local merge → Push → Oncall CI check
- Tester must run the full workflow from `.claude/agents/tester.md` including Step 7 (capture screenshots). Screenshots are used by agents to verify pages rendered correctly, not just for human review
- Tester runs focused local Django tests for the changed modules plus `make test-playwright-core` by default for per-issue work. Escalate to `make test-playwright` only when the diff touches `playwright_tests/conftest.py`, `tests/fixtures.py`, the access-control matrix, payments wiring, or shared template fragments. The full suite also runs automatically every 3 hours via `.github/workflows/scheduled-playwright.yml`. See `_docs/testing-guidelines.md` ("Core Playwright subset") for the tagging policy
- Exhaustive local runs such as `make coverage`, full local Django-suite coverage, or full local all-tests are CI-only by default. Do not run them during per-issue tester review unless Alexey explicitly asks for a local full-suite/coverage run.
- Tester must actually run the scoped local verification — not just review code. Test report must include counts by type, which focused Django tests ran, and which Playwright subset (`core` vs `full`) ran.
- Testers in separate worktrees run Playwright in PARALLEL — never serialize them. As of #885 the Playwright server fixture resolves a free OS-assigned port per session (or honors `PLAYWRIGHT_DJANGO_PORT`) and each worktree uses its own `test_playwright_db.sqlite3`, so concurrent `make test-playwright[-core]` runs from different worktrees no longer collide. When two issues are in review at once, launch both testers at the same time. Same-worktree local Playwright concurrency is blocked by a repo-local pytest guard before migrations/server/browser startup, because two sessions in one checkout would share one sqlite DB. See `_docs/testing-guidelines.md` ("Running Playwright in isolation / parallel across worktrees")
- Tester must capture screenshots of every changed page and read each one to verify it is not a 404, error, or broken layout
- SWE and tester must update acceptance criteria checkboxes in the issue body (`- [ ]` → `- [x]`)
- Never commit directly without tester review, even for "simple" changes
- Never use `gh pr create` or `gh pr merge` — see "Merging — local only, no PRs"
- Agents post issue comments via `gh`, not the orchestrator. Launch the relevant agent (PM for acceptance, tester for verdicts) and let it write the comment
- After push, always run oncall-engineer agent to monitor CI — do not just check manually or wait on CI as the orchestrator's main task

### Engineering Conventions

- Configurable settings go through the IntegrationSetting framework, never raw `os.environ` / `settings.X`. Read values with `get_config(key, default)` / `is_enabled(key)` from `integrations/config.py` (resolves DB override set in Studio settings -> env -> default) and register every key in `integrations/settings_registry.py` so it appears as a Studio-editable field with the Source badge. This keeps every setting changeable with no redeploy. Canonical example: the `#plan-sprints` channel keys in `integrations/settings_registry.py` read via `get_slack_plan_sprints_channel_id()` in `community/slack_config.py`.
- API coverage is the default expectation for operator workflows. When grooming an issue that adds or changes a Studio action or operator-managed data surface, the Product Manager must explicitly decide whether the same capability should be available through the authenticated production API and include API acceptance criteria when it should. In general, Studio create/update/actions and Studio-visible operational data that are useful for automation, imports, CRM operations, Slack/context ingestion, content/event management, or bulk workflows should have an API path in the same issue. Exceptions are allowed when an API does not make product sense, would expose an unsafe surface, or is purely presentational; destructive deletes are not required by default and should be included only when explicitly needed. If API support is deferred or out of scope, the groomed issue must say why.

### How to Pick Issues

1. `gh issue list --repo AI-Shipping-Labs/website --state open --limit 50 --json number,title,labels --jq 'sort_by(.number) | .[] | "#\(.number) \(.title) [\(.labels | map(.name) | join(", "))]"'`
2. Skip issues labeled `needs grooming` — they haven't been groomed yet
3. Pick the lowest-numbered open groomed issues first (lower = more foundational)
4. Check the issue's Depends on field — don't start until dependencies are closed
5. Skip issues whose dependencies are still open
6. Pick 2 independent issues at a time and run them in parallel

### Continuous Issue Pipeline

Always keep the pipeline full. When starting a batch, immediately add a "Pick next two issues" task blocked by the current batch. This ensures work never stops.

The orchestrator should not be idle while there is eligible backlog. Keep at
least one role agent running, and usually two independent tracks, whenever there
are groomed unblocked issues and available agent capacity. If all active issue
tracks are waiting on test, PM, commit, CI, or human verification, use spare
capacity for grooming, next-issue selection, or the next independent
implementation worktree.

```
Batch N: implement + test + accept → commit + push
    └── triggers: "Pick next two issues" → Batch N+1 → ...
```

If the user interrupts with new information while role agents are working, keep those agents running. Convert the new information into intake issues or PM grooming work in parallel, then return to orchestrating active handoffs. The orchestrator should usually have at least two independent tracks in motion when the backlog allows it: one active implementation/review track and one intake/grooming or next-issue selection track.

### Human Verification

Some acceptance criteria are marked `[HUMAN]` in issues (OAuth flows, Stripe redirects, visual checks). When an issue passes all agent reviews but has `[HUMAN]` criteria:

1. Commit and push the code (don't block on human verification)
2. Add the `human` label: `gh issue edit N --repo AI-Shipping-Labs/website --add-label human`
3. Comment listing criteria that need manual verification
4. Do NOT close the issue — leave it open for the human to verify and close
5. Continue with the next issues (don't wait)

## Labels

| Category | Labels |
|----------|--------|
| Workflow | `needs grooming` |
| Area | `auth`, `frontend`, `admin`, `content`, `courses`, `events`, `payments`, `email`, `community`, `seo`, `infra`, `integration` |
| Priority | `P0` (must have), `P1` (important), `P2` (nice to have) |
| Special | `human` (code done, needs manual verification) |

## Temporary Files

All agents must use the project-local `.tmp/` directory for temporary files (screenshots, previews, downloads, scratch data). This directory is gitignored.

- Never write temp files to `/tmp`, `/data/tmp`, or any path outside the project root
- When running `scripts/capture_screenshots.py`, pass `--output .tmp/screenshots`
- Agents should create subdirectories inside `.tmp/` as needed (e.g. `.tmp/screenshots/`, `.tmp/previews/`)
- The `.tmp/` directory is excluded from git via `.gitignore`

## Short-lived docs (audits, plans, analyses)

Point-in-time documents — audits, remediation plans, one-off analyses, dated status reports — live in `_docs/audits/`, not at the `_docs/` root. The root is reserved for evergreen references that stay current (`PROCESS.md`, `product.md`, `configuration.md`, `testing-guidelines.md`, `design-system.md`, `integrations/`). When you produce a new audit or plan, put it in `_docs/audits/` using the `YYYY-MM-DD-<topic>.md` filename convention; see [`_docs/audits/README.md`](audits/README.md) for the full lifecycle policy (expected ~6 month expiry, then delete or promote to a permanent doc).

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

## Content Management

All content lives in a single GitHub monorepo and is synced to the platform:

| Repo | Visibility | Content |
|---|---|---|
| AI-Shipping-Labs/content | Private | Articles, courses, projects, recordings, curated links, interview questions, learning path |

The sync pipeline (webhook push or manual `sync_content` command) clones the repo, parses markdown/YAML frontmatter, uploads images to S3, and upserts records to the database. Every content file must have a `content_id` UUID in its frontmatter for stable linking.
