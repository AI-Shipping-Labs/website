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
- Agents: See `.claude/agents/` for implementer and QA agent definitions

## Agent Orchestration

An orchestrator (human or top-level Claude Code session) drives the implementer-QA loop:

```
Orchestrator assigns issue
    → Implementer: reads spec + issue, writes code + tests locally (no commit)
    → QA: reviews uncommitted code, runs ALL tests (unit + integration + Playwright E2E), reports pass/fail
    → if fail: Orchestrator sends QA feedback to Implementer → Implementer fixes → QA re-reviews
    → repeat until QA passes
    → Implementer commits and pushes with "Closes #N" (or "Refs #N" for human-verified issues)
    → Pipeline Fixer: checks CI/CD, fixes if broken, reopens/closes related issue
    → done, next issue
```

### Agents

- **Implementer** (`.claude/agents/implementer.md`) — receives issue number, reads spec, writes code + tests, does NOT commit until QA approves
- **QA** (`.claude/agents/qa.md`) — reviews uncommitted code against spec + acceptance criteria, runs all tests, reports pass/fail with specifics
- **Pipeline Fixer** (`.claude/agents/pipeline-fixer.md`) — runs after push, checks CI/CD status, identifies related issue from commit messages, reopens issue if broken, fixes code, pushes fix, closes issue

### How to Pick Issues

1. List open issues sorted by ID ascending: `gh issue list --repo AI-Shipping-Labs/website --state open --limit 50 --json number,title,labels --jq 'sort_by(.number) | .[] | "#\(.number) \(.title) [\(.labels | map(.name) | join(", "))]"'`
2. Pick the lowest-numbered open issues first (lower number = earlier/more foundational)
3. Check the issue's **Depends on** field — don't start an issue until its dependencies are closed
4. Skip issues whose dependencies are still open — move to the next lowest available
5. Pick 2 independent issues at a time and run them in parallel
6. Dependency chain: #1 (scaffold) → #68 (tiers) → #67 (auth) → #69 (payments), #71 (access control) → #72 (blog), #70 (account page)

### Continuous Issue Pipeline

**Always keep the pipeline full.** When starting a batch of issues, immediately add a "Pick next two issues from GitHub" task blocked by the current batch's QA tasks. This ensures work never stops — as soon as the current batch is committed, the orchestrator checks GitHub for the next unblocked issues and starts a new batch. Repeat until all open issues are done.

```
Batch N: implement + QA → commit + push
    └── triggers: "Pick next two issues" → Batch N+1: implement + QA → commit + push
                                               └── triggers: "Pick next two issues" → ...
```

### Orchestrator responsibilities

- Pick the next issues using the process above (2 at a time, in parallel when independent)
- Launch implementer with the issue number
- When implementer reports done, launch QA with the issue number + summary
- If QA fails: relay specific feedback to implementer, re-launch implementer to fix, then re-launch QA
- If QA passes: tell implementer to commit and push
- After pushing, run pipeline-fixer to check CI/CD — if it fails, check whether an implementer is currently working on related code. If so, defer the fix until the implementer finishes (the in-progress work may resolve the issue). Only run the pipeline fixer immediately if no relevant work is in progress.
- After committing, pick the next two issues (never stop until all issues are done)
- QA must actually run all tests — not just review code. Test report must include counts by type (unit, integration, Playwright E2E)
- QA must run Playwright visual regression tests, not just verify they exist

### Human Verification

Some acceptance criteria are marked `[HUMAN]` in issues (e.g. OAuth flows, Stripe redirects, visual checks). These cannot be verified by automated tests or QA agents. When an issue passes QA but has `[HUMAN]` criteria remaining:

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
