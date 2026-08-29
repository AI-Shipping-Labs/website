---
name: ai-shipping-labs-plan-import
description: Use when an AI Shipping Labs sprint member is missing a production sprint plan, or when a local markdown plan from telegram-writing-assistant or zoom-calls must be imported into the production plans API.
metadata:
  short-description: Import AI Shipping Labs sprint plans
---

# AI Shipping Labs Plan Import

Use the production API at `https://aishippinglabs.com`. The API token is in the repo `.env` as `API_SHIPPING_LABS_API_TOKEN`; never print the token.

## Source Locations

Check these local sources for markdown plans:

- `~/git/telegram-writing-assistant/articles/ai-shipping-labs/plans`
- `~/git/zoom-calls`

Prefer the explicit markdown plan file when it exists. Interview files are supporting context, not the plan source.

## Reusable Scripts

Use the checked-in scripts instead of rewriting API/parsing code:

```bash
python scripts/find_missing_sprint_plans.py --sprint may-2026 --query juan
```

This lists sprint enrollments without plans and can include a user search result for disambiguation.

```bash
python scripts/import_sprint_plan_markdown.py \
  --sprint may-2026 \
  --email member@example.com \
  --source ~/git/telegram-writing-assistant/articles/ai-shipping-labs/plans/YYYYMMDD_name.md \
  --create-if-missing \
  --no-ready-email \
  --dry-run
```

Review the dry-run shape, then repeat without `--dry-run` to create/update production:

```bash
python scripts/import_sprint_plan_markdown.py \
  --sprint may-2026 \
  --email member@example.com \
  --source ~/git/telegram-writing-assistant/articles/ai-shipping-labs/plans/YYYYMMDD_name.md \
  --create-if-missing \
  --send-ready-email
```

If the plan needs a concise top-level headline, pass `--goal "..."`. Without `--goal`, the importer preserves an existing API goal; if there is no existing goal, it uses the markdown Summary goal.

## Draft Versus Ready

Importing content does not make a plan visible to its member. `Plan.shared_at`
is the readiness gate: until it is set, the member dashboard keeps showing
"Your plan is being prepared" no matter how complete the content is.

Every importer invocation, dry run included, must pick exactly one intent:

| Flag | Effect |
|---|---|
| `--send-ready-email` | After the content PATCH succeeds, runs the idempotent one-plan ready delivery: sets `shared_at`, fires the bell notification, sends the transactional email, and records the durable ready log. |
| `--no-ready-email` | Persists content only. Readiness and any existing `shared_at` are untouched and the member is not notified. |

Ready delivery never happens during a dry run, during plan creation, before the
content PATCH, or after a failed PATCH. Outcomes:

- `sent` — delivered on this run.
- `already_sent` / `already_shared` — safe; nothing was sent again.
- `failed_retryable` — content is saved, the plan is still unshared, the command exits non-zero, and re-running the same command retries safely.

The three ways to make a plan ready, in order of preference:

1. Studio: open `/studio/plans/<id>/` and use the visible `Share with member` action. This is the primary human workflow and does not require opening Edit.
2. CLI: `uv run asl plans send-ready <id> [--dry-run]` for one plan.
3. Importer: `--send-ready-email` as part of the content import.

All three run the same idempotent service, so none of them can notify a member
twice. Sending a member a deliberate second notification is only possible
through the confirmed Studio `Re-share with member` action, which warns that
another bell notification and email will go out.

## Workflow

1. Identify the active sprint:
   ```bash
   python scripts/find_missing_sprint_plans.py --sprint may-2026 --query <name>
   ```
2. Confirm the exact member email from the missing-enrollment output and user search.
3. Find the source markdown plan in the source locations above.
4. Run `import_sprint_plan_markdown.py --no-ready-email --dry-run` and check counts/sections.
5. Run the importer without `--dry-run`, choosing `--send-ready-email` or `--no-ready-email`.
6. Verify with `GET /api/plans/<id>` or by re-running the importer dry-run and checking the API response counts.
7. Verify readiness: `shared_at` is set and the reported ready status is `sent`/`already_sent`/`already_shared`. If it is still null and the member should see the plan, share it from Studio or with `uv run asl plans send-ready <id>`.

## Mapping

The importer maps:

- `Summary` to `summary.*`
- `Focus` to `focus.main` and `focus.supporting`
- `Timeline` week bullets to `weeks[].checkpoints`
- `Resources`, `Deliverables`, `Accountability`, and `Next Steps` to matching API fields
- `Persona`, `Background`, `Intake`, `Meeting Notes`, `Internal Recommendations`, `Internal Action Items`, and `Sources` to internal `interview_notes`

Keep member-facing plan content in the shareable sections. Internal sections should remain internal notes.
