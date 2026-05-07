# Development Process

## Overview

We use GitHub Issues to track development of the AI Shipping Labs platform. All work is tracked as issues with labels — no project boards. Four agents handle the full lifecycle from raw request to shipped code.

## Links

- Repo: https://github.com/AI-Shipping-Labs/website
- Issues: https://github.com/AI-Shipping-Labs/website/issues
- Specs: [`specs/`](specs/) folder in this repo

## Issue Lifecycle

```
User creates issue     →  PM grooms        →  Engineer builds  →  Tester verifies  →  PM accepts  →  Ship
(needs grooming)          (spec + tests)       (code + tests)     (runs all tests)    (user POV)     (commit + push)
```

1. User creates an issue via the GitHub issue template. It gets the `needs grooming` label automatically.
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
| Software Engineer | `.claude/agents/software-engineer.md` | Implements code + tests, does NOT commit until approved |
| Tester | `.claude/agents/tester.md` | Runs all tests, verifies acceptance criteria technically |
| On-Call Engineer | `.claude/agents/oncall-engineer.md` | Monitors CI/CD after push, fixes failures |

## Agent Workflow

An orchestrator (human or top-level Claude Code session) drives the process:

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

1. User creates a raw issue via the GitHub template (auto-labeled `needs grooming`)
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

- Stay in the orchestrator role. Do not personally perform active issue role work when a product-manager, software-engineer, tester, or on-call agent can own it. The orchestrator coordinates, unblocks, reviews handoffs, and launches the next role agent.
- Treat new user feedback, links, recordings, screenshots, or raw requests as intake. Create raw issues when needed, then launch a product-manager agent to groom them instead of grooming them inline, unless the user explicitly asks the orchestrator to edit the issue text directly.
- Groom any `needs grooming` issues first (launch product-manager in grooming mode)
- Pick the next groomed issues (2 at a time, in parallel when independent)
- Before launching any SWE agent in an isolated worktree, ensure `main` has no uncommitted changes. If there are, commit them (or stash with the user's approval) first. Worktrees are created from `HEAD`, so uncommitted main changes are invisible to the agent; when the agent's branch merges back, it will overwrite or conflict with that work. Run `git status` and resolve before invoking the agent.
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
- Tester must actually run all tests — not just review code. Test report must include counts by type
- Tester must capture screenshots of every changed page and read each one to verify it is not a 404, error, or broken layout
- SWE and tester must update acceptance criteria checkboxes in the issue body (`- [ ]` → `- [x]`)
- Never commit directly without tester review, even for "simple" changes
- Never use `gh pr create` or `gh pr merge` — see "Merging — local only, no PRs"
- After push, always run oncall-engineer agent to monitor CI — do not just check manually or wait on CI as the orchestrator's main task

### How to Pick Issues

1. `gh issue list --repo AI-Shipping-Labs/website --state open --limit 50 --json number,title,labels --jq 'sort_by(.number) | .[] | "#\(.number) \(.title) [\(.labels | map(.name) | join(", "))]"'`
2. Skip issues labeled `needs grooming` — they haven't been groomed yet
3. Pick the lowest-numbered open groomed issues first (lower = more foundational)
4. Check the issue's Depends on field — don't start until dependencies are closed
5. Skip issues whose dependencies are still open
6. Pick 2 independent issues at a time and run them in parallel

### Continuous Issue Pipeline

Always keep the pipeline full. When starting a batch, immediately add a "Pick next two issues" task blocked by the current batch. This ensures work never stops.

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
