# Project Context

<!-- Add project-specific context here for Claude Code -->

## CRITICAL: File Editing on Windows

### MANDATORY: Always Use Backslashes on Windows for File Paths

When using Edit or MultiEdit tools on Windows, you MUST use backslashes (`\`) in file paths, NOT forward slashes (`/`).

### "File has been unexpectedly modified" Error

If you get this error: **"File has been unexpectedly modified. Read it again before attempting to write it"**

**Root cause:** The file was modified after you last read it (by linter, formatter, git, or external process).

**Solution: Re-read the file immediately before editing:**

```bash
# 1. Read the file again
Read(file_path: "path\to\file.txt")

# 2. Then immediately edit
Edit(file_path: "path\to\file.txt", old_string="...", new_string="...")
```

**Tool requirements:**

- Edit - Must `Read` immediately before - `old_string` must match current content
- Write - Must `Read` once per conversation before first write

### Use UV for Python Package Management

When installing Python packages, use `uv` instead of `pip`.

```bash
uv add djangorestframework
uv run python manage.py makemigrations
uv run python manage.py migrate
uv run python manage.py test
```

## Project Overview

AI Shipping Labs community platform — a Django-based website replacing the current Next.js static site.

- Specs: `specs/` folder (14 requirement specs with data models, APIs, acceptance criteria)
- Issues: GitHub Issues on AI-Shipping-Labs/website (labels, no project board)
- Process: See `PROCESS.md` for workflow details
- Agents: See `.claude/agents/` for agent definitions

## Agent Orchestration

An orchestrator (human or top-level Claude Code session) drives the full pipeline:

```
User creates issue (needs grooming)
    → Product Manager: grooms into agent-ready spec with acceptance criteria + Playwright test scenarios
    → Orchestrator assigns groomed issue
    → Software Engineer: reads spec + issue, writes code + tests locally (no commit)
    → Tester: reviews uncommitted code, runs ALL tests (unit + integration + Playwright E2E), reports pass/fail
    → if fail: Orchestrator sends Tester feedback to Software Engineer → fixes → Tester re-reviews
    → repeat until Tester passes
    → Software Engineer commits and pushes with "Closes #N" (or "Refs #N" for human-verified issues)
    → Pipeline Fixer: checks CI/CD, fixes if broken, reopens/closes related issue
    → done, next issue
```

### Agents

- **Product Manager** (`.claude/agents/product-manager.md`) — grooms raw "needs grooming" issues into structured specs with scope, acceptance criteria, dependencies, and Playwright test scenarios
- **Software Engineer** (`.claude/agents/software-engineer.md`) — receives issue number, reads spec, writes code + tests, does NOT commit until tester approves
- **Tester** (`.claude/agents/tester.md`) — reviews uncommitted code against spec + acceptance criteria, runs all tests, reports pass/fail with specifics
- **Pipeline Fixer** (`.claude/agents/pipeline-fixer.md`) — runs after push, checks CI/CD status, identifies related issue from commit messages, reopens issue if broken, fixes code, pushes fix, closes issue

### How to Pick Issues

1. List open issues sorted by ID ascending: `gh issue list --repo AI-Shipping-Labs/website --state open --limit 50 --json number,title,labels --jq 'sort_by(.number) | .[] | "#\(.number) \(.title) [\(.labels | map(.name) | join(", "))]"'`
2. Skip issues labeled `needs grooming` — they haven't been groomed yet
3. Pick the lowest-numbered open groomed issues first (lower number = earlier/more foundational)
4. Check the issue's **Depends on** field — don't start an issue until its dependencies are closed
5. Skip issues whose dependencies are still open — move to the next lowest available
6. Pick 2 independent issues at a time and run them in parallel

### Continuous Issue Pipeline

**Always keep the pipeline full.** When starting a batch of issues, immediately add a "Pick next two issues from GitHub" task blocked by the current batch's tester tasks. This ensures work never stops — as soon as the current batch is committed, the orchestrator checks GitHub for the next unblocked issues and starts a new batch. Repeat until all open issues are done.

```
Batch N: implement + test → commit + push
    └── triggers: "Pick next two issues" → Batch N+1: implement + test → commit + push
                                               └── triggers: "Pick next two issues" → ...
```

### Orchestrator responsibilities

- Groom any `needs grooming` issues first (launch product-manager agent)
- Pick the next groomed issues using the process above (2 at a time, in parallel when independent)
- Launch software engineer with the issue number
- When software engineer reports done, launch tester with the issue number + summary
- If tester fails: relay specific feedback to software engineer, re-launch to fix, then re-launch tester
- If tester passes: tell software engineer to commit and push
- After pushing, run pipeline-fixer to check CI/CD — if it fails, check whether a software engineer is currently working on related code. If so, defer the fix until they finish. Only run the pipeline fixer immediately if no relevant work is in progress.
- After committing, pick the next two issues (never stop until all issues are done)
- Tester must actually run all tests — not just review code. Test report must include counts by type (unit, integration, Playwright E2E)
- Tester must run Playwright visual regression tests, not just verify they exist

### Human Verification

Some acceptance criteria are marked `[HUMAN]` in issues (e.g. OAuth flows, Stripe redirects, visual checks). These cannot be verified by automated tests or tester agents. When an issue passes testing but has `[HUMAN]` criteria remaining:

1. Commit and push the code (don't block on human verification)
2. Add the `human` label to the issue: `gh issue edit N --repo AI-Shipping-Labs/website --add-label human`
3. Leave a comment listing the criteria that need manual verification: `gh issue comment N --repo AI-Shipping-Labs/website --body "..."`
4. Do NOT close the issue — leave it open for the human to verify and close
5. Continue with the next issues (don't wait)

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

## Current Work
<!-- What are you working on? What's the current context? -->
