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
  --dry-run
```

Review the dry-run shape, then repeat without `--dry-run` to create/update production:

```bash
python scripts/import_sprint_plan_markdown.py \
  --sprint may-2026 \
  --email member@example.com \
  --source ~/git/telegram-writing-assistant/articles/ai-shipping-labs/plans/YYYYMMDD_name.md \
  --create-if-missing
```

If the plan needs a concise top-level headline, pass `--goal "..."`. Without `--goal`, the importer preserves an existing API goal; if there is no existing goal, it uses the markdown Summary goal.

## Workflow

1. Identify the active sprint:
   ```bash
   python scripts/find_missing_sprint_plans.py --sprint may-2026 --query <name>
   ```
2. Confirm the exact member email from the missing-enrollment output and user search.
3. Find the source markdown plan in the source locations above.
4. Run `import_sprint_plan_markdown.py --dry-run` and check counts/sections.
5. Run the importer without `--dry-run`.
6. Verify with `GET /api/plans/<id>` or by re-running the importer dry-run and checking the API response counts.

## Mapping

The importer maps:

- `Summary` to `summary.*`
- `Focus` to `focus.main` and `focus.supporting`
- `Timeline` week bullets to `weeks[].checkpoints`
- `Resources`, `Deliverables`, `Accountability`, and `Next Steps` to matching API fields
- `Persona`, `Background`, `Intake`, `Meeting Notes`, `Internal Recommendations`, `Internal Action Items`, and `Sources` to internal `interview_notes`

Keep member-facing plan content in the shareable sections. Internal sections should remain internal notes.
