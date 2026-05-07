# Course YAML Editing Guide

How to edit `course.yaml` and the surrounding markdown files for a source-managed course. Companion to `_docs/content.md` (which covers the broader sync pipeline) and `_docs/configuration.md` (which covers integration setup).

## Why this matters

Source-managed courses are the source of truth in the GitHub content repo (`AI-Shipping-Labs/content` by default). Studio shows the row read-only and disables every input. To change content, edit the YAML/markdown in GitHub, push, and re-sync from Studio — the sync upserts changes into the database. Editing the database directly will be overwritten on the next sync.

Local-only courses (no `source_repo` set) remain editable in Studio. This guide covers the source-managed path.

## File layout

A course is a top-level directory under the repo's `content_path` (e.g. `courses/`). Three levels:

- `courses/<slug>/course.yaml` — course metadata
- `courses/<slug>/<module-dir>/module.yaml` — module metadata
- `courses/<slug>/<module-dir>/<NN>-unit.md` — unit content with frontmatter

A numeric prefix on the module or unit filename (`01-intro`, `05-eval.md`) determines `sort_order` if the YAML/frontmatter does not override it.

## `course.yaml` fields

```yaml
content_id: <UUID>                  # stable upsert key, never change
slug: aihero
title: 'AI Hero: 7-Day AI Agents Crash-Course'
description: |
  Markdown description shown on the course detail page.
cover_image_url: https://...        # optional
instructors:                        # ordered list; first is primary
  - name: Alexey Grigorev
    bio: ...
required_level: 0                   # 0=open, 10=basic, 20=main, 30=premium
default_unit_access: registered     # optional default for every unit
discussion_url: https://...         # Slack channel URL or GitHub URL
tags: [ai-agents, rag]
testimonials:
  - quote: ...
    name: ...
    role: ...
    source_url: ...
```

### Access levels

`required_level` controls course-wide gating (catalog tier badge, course detail CTA, individual-purchase eligibility). Use it to mark a course as a paid perk.

`default_unit_access` controls per-lesson gating (the access wall a visitor hits inside a unit). When unset, units inherit `required_level`.

Accepted values for `default_unit_access` and per-unit `access:` (case-insensitive): `open`, `registered`, `basic`, `main`, `premium`. Raw integers (`0`, `5`, `10`, `20`, `30`) are also accepted.

| Use case | `required_level` | `default_unit_access` | Per-unit `access:` |
|---|---|---|---|
| Fully open (everything readable, no sign-in) | `0` | (omit) | (omit) |
| Free with sign-in | `0` | `registered` | (omit) |
| Sign-in walled with one anonymous teaser | `0` | `registered` | `open` on teaser |
| Paid course, no anonymous access | `10` / `20` / `30` | (omit) | (omit) |
| Paid course with one free intro lesson | `10` / `20` / `30` | (omit) | `open` on lesson 1 |

### Source-owned vs Studio-owned fields

Studio writes nothing back to GitHub. Some operational fields therefore live only in the database and are edited via Studio for local-only courses, or via Django admin / management commands for source-managed courses. They do not appear in `course.yaml`.

| Field | Owned by | Notes |
|---|---|---|
| `title`, `slug`, `description`, `cover_image_url`, `tags` | YAML | Edit in GitHub, then re-sync. |
| `required_level`, `default_unit_access` | YAML | Edit in GitHub, then re-sync. |
| `instructors` | YAML | Order matters — first instructor is primary on cards. |
| `discussion_url`, `testimonials` | YAML | Edit in GitHub, then re-sync. |
| `status` | YAML (`published: true/false`) or DB | Source sets initial value; operators may flip it via admin without losing the source link. |
| `individual_price_eur` | DB only | Set in Django admin or via local-only migration. Not in `course.yaml`. |
| `stripe_product_id`, `stripe_price_id` | DB only | Created via "Create Stripe Product" button after a price is set. |
| `peer_review_*` | DB only | Configured per-course in Studio (admin) once. |

When a source-managed course shows `Not configured` next to `Individual price`, `Stripe product`, or `Stripe price`, the field is genuinely empty in the database — it has not been configured yet.

## `module.yaml` fields

```yaml
content_id: <UUID>                  # stable upsert key
sort_order: 5                       # optional; otherwise from filename prefix
title: 'Day 5: Offline Evaluation and Testing'
```

## Unit frontmatter

```markdown
---
content_id: <UUID>
sort_order: 2
title: Logging
video_url: https://www.youtube.com/embed/...   # optional
access: open                                   # optional, per-unit override
is_preview: true                               # legacy alias for `access: open`
---
markdown body
```

Per-unit `access` overrides the course's `default_unit_access`. `is_preview: true` is a legacy alias for `access: open`; if both are set, `access` wins.

## Editing workflow

1. Open the course in Studio: `/studio/courses/<id>/edit`. The "Source-managed course" sticky bar links to the file on GitHub via the `Edit on GitHub` button.
2. Edit `course.yaml`, `module.yaml`, or unit markdown in GitHub. Commit and push to the default branch.
3. Webhooks trigger an automatic sync. To force one immediately, use the `Re-sync source` button in the same sticky bar, or run `uv run python manage.py sync_content`.
4. Reload the Studio page to confirm the new values.

If the sync log shows errors, fix them at the YAML source and re-sync. Do not work around them by editing the database — the next sync will overwrite the fix.

## When to edit in GitHub vs Studio

- Always edit in GitHub: any field listed under "YAML" in the table above. The Studio form is read-only for these fields on source-managed courses, by design.
- Edit in Django admin or via Studio: pricing (`individual_price_eur`), Stripe IDs, peer-review configuration, and one-off access grants. These are operational and intentionally not in source.
- Never edit a synced row in the database for content fields. Edit the YAML.

## Common mistakes

- Renaming `slug` without changing the URL: breaks SEO and any external links. Set up a redirect first.
- Reusing a `content_id` from a different course: the sync upserts on `content_id`, so the wrong row is replaced.
- Setting `individual_price_eur` in `course.yaml`: ignored by the parser, since pricing is DB-owned.
- Editing `course.yaml` and forgetting to re-sync: the live site keeps showing the old version until sync runs.
