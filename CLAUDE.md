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
    → Implementer commits and pushes with "Closes #N"
    → done, next issue
```

### Agents

- **Implementer** (`.claude/agents/implementer.md`) — receives issue number, reads spec, writes code + tests, does NOT commit until QA approves
- **QA** (`.claude/agents/qa.md`) — reviews uncommitted code against spec + acceptance criteria, runs all tests, reports pass/fail with specifics

### How to Pick Issues

1. List open issues: `gh issue list --repo AI-Shipping-Labs/website --state open --limit 50`
2. Pick the lowest-numbered open issues first (lower number = earlier/more foundational)
3. Check the issue's **Depends on** field — don't start an issue until its dependencies are done
4. Dependency chain for the first batch: #1 (scaffold) → #68 (tiers) → #67 (auth) → #69 (payments), #71 (access control) → #72 (blog), #70 (account page)
5. Content issues (#73-#77) can often be worked in parallel once the scaffold is done

### Orchestrator responsibilities

- Pick the next issue using the process above
- Launch implementer with the issue number
- When implementer reports done, launch QA with the issue number + summary
- If QA fails: relay specific feedback to implementer, re-launch implementer to fix, then re-launch QA
- If QA passes: tell implementer to commit and push
- QA must actually run all tests — not just review code. Test report must include counts by type (unit, integration, Playwright E2E)
- QA must run Playwright visual regression tests, not just verify they exist

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
