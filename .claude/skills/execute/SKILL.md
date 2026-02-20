---
name: execute
description: Run the full development loop - pick issues, implement, QA, PM review, commit, push, repeat. Works for both new features (open issues) and Playwright test implementation (needs-testing issues).
disable-model-invocation: true
argument-hint: [number-of-issues]
---

# Execute Development Loop

Run the full issue pipeline. Number of issues per batch: $ARGUMENTS (default: 2)

## Step 1: Pick Issues

Check both open issues (new features) and closed `needs-testing` issues (Playwright tests to write):

```bash
# Open issues (new features)
gh issue list --repo AI-Shipping-Labs/website --state open --limit 50 --json number,title,labels --jq 'sort_by(.number) | .[] | "#\(.number) \(.title) [\(.labels | map(.name) | join(", "))]"'

# Closed issues needing Playwright tests
gh issue list --repo AI-Shipping-Labs/website --state closed --label "needs-testing" --limit 50 --json number,title --jq 'sort_by(.number) | .[] | "#\(.number) \(.title)"'
```

Priority order:
1. Open issues without `needs grooming` or `human` labels (new features to implement)
2. If no open issues available: closed `needs-testing` issues (Playwright tests to write)

Rules:
- Skip issues labeled `needs grooming` (groom them first with PM agent)
- Skip issues labeled `human` (waiting for manual verification)
- Pick the lowest-numbered issues first (lower = more foundational)
- Check `Depends on` field -- don't start until dependencies are closed
- If no actionable issues remain, report "No actionable issues" and stop

## Step 1b: Create Todo List

After picking issues, create a todo list so the user can track progress. For each batch, create tasks with dependencies:

1. "Implement Playwright tests for #N (Title)" -- one per issue, status: in_progress
2. "QA Playwright tests for #N (Title)" -- blocked by the implement task
3. "Commit and push batch" -- blocked by all QA tasks
4. "Pick next batch" -- blocked by the commit task

For feature issues, add a PM review task between QA and commit.

Update task status as work progresses: pending -> in_progress -> completed.

## Step 2: Implement (parallel)

Launch engineers in parallel for each picked issue.

### For new features (open issues):

```
Task(subagent_type="implementer", model="opus", prompt="Implement issue #N. Read the issue with gh issue view N --repo AI-Shipping-Labs/website. Read _docs/PRODUCT.md and _docs/PROCESS.md first. Follow the spec and acceptance criteria. Write code and tests. Do NOT commit.")
```

### For Playwright tests (needs-testing issues):

```
Task(subagent_type="implementer", model="opus", prompt="Implement Playwright E2E tests for issue #N. Read _docs/PRODUCT.md and _docs/PROCESS.md first. Read the issue (gh issue view N --repo AI-Shipping-Labs/website) for BDD scenarios. Read existing tests in playwright_tests/ for patterns and conventions. Write Playwright tests matching each BDD scenario. Run them: uv run pytest playwright_tests/test_{feature}.py -v. Do NOT commit.")
```

Wait for all engineers to complete. If an engineer reports a blocker, skip that issue and note it.

## Step 3: QA (parallel)

For each completed implementation, launch a tester agent:

```
Task(subagent_type="qa", model="opus", prompt="QA issue #N. Read _docs/PRODUCT.md and _docs/PROCESS.md first. The engineer wrote {description}. Review the code, run ALL tests (uv run python manage.py test AND uv run pytest playwright_tests/ -v). After tests pass, capture screenshots: uv run python scripts/capture_screenshots.py --urls {relevant URLs} --issue N. Report pass/fail with specifics.")
```

## Step 4: Handle QA Results

For each issue:
- If QA PASSES: proceed to PM review (for features) or commit (for Playwright tests)
- If QA FAILS: relay specific feedback back to the engineer, re-implement, re-QA (max 2 retries)
- If QA fails after 2 retries: skip the issue, report it, continue with others

## Step 5: PM Acceptance Review (parallel, features only)

Skip this step for Playwright test issues. For new features only:

- User-facing features (labels: `frontend`, `content`, `courses`, `events`, `payments`, `auth`, `community`, `email`, `seo`, `admin`): UX review
- Infrastructure tasks (labels: `infra`, `integration` without `frontend`): DX review

```
Task(subagent_type="general-purpose", model="opus", prompt="You are the Product Manager agent doing acceptance review for issue #N. Read _docs/PRODUCT.md first. Read .claude/agents/product-manager.md for your review checklist. Read the templates, views, and copy. Report ACCEPT or REJECT with specifics.")
```

## Step 6: Handle PM Results

For each issue:
- If PM ACCEPTS: proceed to commit
- If PM REJECTS: relay UX/DX feedback to engineer, fix, re-run PM review (max 2 retries)

## Step 7: Commit and Push

For each accepted issue, commit with specific files (not `git add -A`):

```bash
git add {specific files}
git commit -m "$(cat <<'EOF'
Short description

Closes #N
EOF
)"
git push origin main
```

For Playwright test commits:
- Use `Refs #N` (not `Closes #N`) since the issue is already closed
- Remove the `needs-testing` label after push: `gh issue edit N --repo AI-Shipping-Labs/website --remove-label "needs-testing"`

For feature commits with `[HUMAN]` criteria:
- Use `Refs #N` instead of `Closes #N`
- Add the `human` label: `gh issue edit N --repo AI-Shipping-Labs/website --add-label human`
- Comment listing criteria needing manual verification
- Do NOT close the issue

## Step 8: Pipeline Check

After pushing, check CI/CD:

```bash
sleep 10
gh run list --repo AI-Shipping-Labs/website --limit 3
```

If a run fails, launch the oncall-engineer agent (pipeline-fixer) to fix it.

## Step 9: Repeat

Go back to Step 1 and pick the next batch. Never stop until all open issues are done and all needs-testing issues have Playwright tests, or no actionable issues remain.

## Summary Format

After each batch, report:

```
## Batch N Complete

| Issue | Type | Engineer | QA | PM | Status |
|-------|------|----------|----|----|--------|
| #X Title | Feature | DONE | PASS | ACCEPT | Committed (abc1234) |
| #Y Title | Playwright | DONE | PASS | -- | Committed (def5678) |

Tests: XXXX Django + XX Playwright, all green
Next: picking issues for batch N+1...
```
