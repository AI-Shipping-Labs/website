# Course YAML Editing Guide

How to edit `course.yaml` and the surrounding markdown files for a source-managed course. Companion to `_docs/content.md` (which covers the broader sync pipeline) and `_docs/configuration.md` (which covers integration setup).

## Why this matters

Source-managed courses are the source of truth in the GitHub content repo (`AI-Shipping-Labs/content` by default). Studio shows the row read-only and disables every input. To change content, edit the YAML/markdown in GitHub, push, and re-sync from Studio — the sync upserts changes into the database. Editing the database directly will be overwritten on the next sync.

Local-only courses (no `source_repo` set) remain editable in Studio. This guide covers the source-managed path.

## File layout

A course is a top-level directory under the repo's `content_path` (e.g. `courses/`). Two shapes are supported (issue #1674):

Two-level (simpler courses — module holds units directly):

- `courses/<slug>/course.yaml` — course metadata
- `courses/<slug>/<module-dir>/module.yaml` — module metadata
- `courses/<slug>/<module-dir>/<NN>-unit.md` — unit content with frontmatter

Three-level (a module directory that itself contains submodule subdirectories, each with their own `module.yaml`):

```
courses/<course-slug>/
  course.yaml
  01-fundamentals/                 # leaf module (two-level shape)
    module.yaml
    README.md                      # module overview
    01-intro.md
    02-setup.md
  02-deployment/                   # parent module (has child module dirs)
    module.yaml
    README.md                      # still the parent module's overview
    01-docker/                     # submodule
      module.yaml
      README.md
      01-lesson.md
      02-lesson.md
    02-kubernetes/                 # submodule
      module.yaml
      01-lesson.md
      02-homework.md
      03-live-qa.md                # kind: event in frontmatter
```

A module directory becomes a **parent module** when it contains one or more subdirectories that themselves carry a `module.yaml` — a submodule directory is parsed exactly like a top-level module directory, one level deeper (own numeric-prefix `sort_order`, own `derive_slug`, own `module.yaml` overrides, own `README.md` overview, own `ignore:` list). Submodule slugs are unique per **parent** (sibling group), not per course — two submodules under *different* parent weeks may share a slug (e.g. a "Homework" submodule repeated under several weeks), matching how real course content is actually organised. Top-level module slugs stay unique per course, as before.

A module directory **must not** mix submodule subdirectories with direct unit `.md` files (other than `README.md`, which always stays the module overview) — a module holds either child modules or units, never both. The sync rejects that course with a `GitHubSyncError` naming the offending directory path and does not partially create either side (neither the submodules nor the units are written).

Maximum nesting is two levels of module — a submodule cannot itself contain a further submodule level.

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
access_mode: entitlement            # optional; 'tier' (default, omit) or 'entitlement'
enroll_url: https://maven.com/alexey-grigorev/from-rag-to-agents  # required when access_mode: entitlement
program_label: Maven                # optional; shown on the "Sold separately" badge/CTA
tags: [ai-agents, rag]
testimonials:
  - quote: ...
    name: ...
    role: ...
    source_url: ...
maven_course_key: from-rag-to-agents   # optional; matches the Maven webhook's course_key
cohorts:                               # optional; upserted into content.Cohort
  - key: cohort-4                      # matches the Maven webhook's cohort_key
    name: Cohort 4
    start_date: 2026-09-21
    end_date: 2026-11-22
  - key: self-paced                    # optional second entry; mode: self_paced
    name: Self-paced
    mode: self_paced                   # optional; 'cohort' (default) or 'self_paced'
peer_review_enabled: true             # required when projects are listed
peer_review_count: 3                  # default reviews per learner
peer_review_criteria: |              # markdown shown during review
  Explain what works and what to improve.
projects:                              # optional dated project attempts
  - slug: attempt-1                    # unique within the course
    title: Attempt 1
    module_path: capstone-project/capstone-project  # leaf syllabus module
    cohort_key: cohort-4               # optional; must match a cohort key above
    submission_due_at: '2030-11-08T23:59:00+01:00'
    review_due_at: '2030-11-15T23:59:00+01:00'
```

### `maven_course_key` and `cohorts:`

Issue #1659: `maven_course_key` links this course to a Maven course so an
inbound Maven enrollment webhook can grant course access automatically.
`cohorts:` declares the cohorts that Maven enrollees are matched against.

Each `cohorts:` entry is upserted into `content.Cohort` keyed on
`(course, external_key=key)`: a new `key` creates a cohort, a known `key`
updates `name`/`start_date`/`end_date` when they changed. A cohort `key`
removed from a later YAML edit is left untouched in the database — cohorts
are never deleted by sync, since they may already have enrollments. `key`,
`name`, `start_date`, and `end_date` are all required on every entry; an
entry missing one fails only that course's sync (its `SyncLog` entry names
the missing field), not the whole sync run.

Issue #1674: each entry accepts an optional `mode:` key, `cohort` (default,
matching the model default) or `self_paced`. A `mode: self_paced` entry
must OMIT `start_date`/`end_date` — sync fails that course, naming the
cohort key, if either is present. A course may define at most one
`mode: self_paced` cohort; a second one fails sync too (surfaces as the
same "which key" error shape the existing cohort-parsing errors already
use). A self-paced cohort has no dates, no `event_series`, and unlimited
capacity by construction — course access alone grants implicit
membership in it (no self-enroll action), which is what makes a
self-paced learner resolve to a real `Cohort` row for drip-lock and
event-slot resolution instead of "no cohort at all".

`maven_course_key` and cohort `key` values must exactly match what the real
Maven webhook sends in `course_key`/`cohort_key` — confirm this with a live
test enrollment before relying on it (see `_docs/integrations/maven.md`,
"Testing live"). A mismatch fails the enrollment `enrollment` step silently
from the enrollee's perspective (they still get the community welcome, just
not the course grant) — visible on `/studio/maven-events/<pk>/`.

### Project attempts

`projects:` defines separate submission and peer review windows within a
curriculum module. Each entry needs a course-unique `slug`, a `title`, a
`module_path` of one or two module slugs, and timezone-aware ISO 8601 values
for `submission_due_at` and `review_due_at`. The review deadline must follow
the submission deadline. Set `cohort_key` to restrict the attempt to learners
enrolled in that cohort; omit it for a course-wide attempt. An optional
`peer_review_count` overrides the course value for that attempt.

`module_path` must identify a leaf module; the attempts appear in that module's
syllabus and overview page. Sync upserts attempts by `(course, slug)` and never deletes an attempt removed
from YAML, because it may already have submissions. Review assignments begin
after the submission deadline and use the attempt's review deadline. Learners
choose an attempt within the curriculum and submit separately to each one.

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

### Programs sold outside the membership plans

`access_mode` (issue #1658) controls whether tier level grants access at all. Omit it (or set `access_mode: tier`) for every normal course — subscription tier / `TierOverride` comparison works exactly as described above. Set `access_mode: entitlement` for a course sold as an independent program (e.g. the Maven buildcamp): the tier comparison is skipped entirely, and only a `CourseAccess` grant or staff opens the course, for any subscription tier the visitor holds — but only once `required_level` and `default_unit_access` are Basic or above. Below Basic (`required_level: 0`/`registered`, or an inherited course default at that level), `can_access()` grants `LEVEL_OPEN`/`LEVEL_REGISTERED` content before the entitlement branch ever runs — that early return is intentional (free/sign-in-walled content stays free), which means an entitlement course must not be left at the free/registered levels. `required_level` and `default_unit_access` — the field that actually governs whether lesson content is readable — are both required to be Basic or above when `access_mode: entitlement`; the sync fails the course otherwise, so a placeholder value like `default_unit_access: registered` cannot silently ship as a paywall-free "Sold separately" page.

`enroll_url` is required when `access_mode: entitlement` — the sync fails the course otherwise. It is the external signup page linked from the "Enroll via {program_label}" CTA. `program_label` is optional short copy for that CTA and the "Sold separately" badge (e.g. `Maven`); when blank, the CTA reads "Enroll" without a program name.

### Source-owned vs Studio-owned fields

Studio writes nothing back to GitHub. Some operational fields therefore live only in the database. Studio owns local-course edits; source-managed operational fields that Studio does not yet expose require an explicit low-level maintenance change until the tracked Studio/API follow-up lands. They do not appear in `course.yaml`.

| Field | Owned by | Notes |
|---|---|---|
| `title`, `slug`, `description`, `cover_image_url`, `tags` | YAML | Edit in GitHub, then re-sync. |
| `required_level`, `default_unit_access` | YAML | Edit in GitHub, then re-sync. |
| `instructors` | YAML | Order matters — first instructor is primary on cards. |
| `discussion_url`, `testimonials` | YAML | Edit in GitHub, then re-sync. |
| `maven_course_key`, `cohorts:` | YAML | Edit in GitHub, then re-sync. Cohorts are upserted, never deleted, by sync. |
| `access_mode`, `enroll_url`, `program_label` | YAML | Edit in GitHub, then re-sync. `access_mode: entitlement` requires `enroll_url`, and requires `required_level` and `default_unit_access` to both be Basic or above; sync fails the course otherwise. |
| `status` | Always `published` (not sourced) | Source-synced courses are always written as `status='published'` on upsert; there is no `published:` source key. Admin status changes are overwritten on the next sync (a non-`published` row is marked dirty and forced back to `published`). |
| `individual_price_eur` | DB only | Not yet editable for source-managed courses in Studio; use an explicit low-level maintenance change. Not in `course.yaml`. |
| `stripe_product_id`, `stripe_price_id` | DB only | Created via "Create Stripe Product" button after a price is set. |
| `peer_review_*`, `projects:` | YAML | Source-managed course review settings and dated attempts. Edit in GitHub, then re-sync. |

When a source-managed course shows `Not configured` next to `Individual price`, `Stripe product`, or `Stripe price`, the field is genuinely empty in the database — it has not been configured yet.

## `module.yaml` fields

```yaml
content_id: <UUID>                  # stable upsert key
sort_order: 5                       # optional; otherwise from filename prefix
title: 'Day 5: Offline Evaluation and Testing'
bonus: true                          # optional; default false (issue #1674)
available_after_days: 21             # optional; default null (issue #1674)
```

`bonus: true` sets `Module.is_bonus` — the whole submodule (or top-level module) is optional enrichment, excluded from the progress denominator but still tracked/shown. `available_after_days: 21` sets `Module.available_after_days` — same key name and meaning as the existing per-unit frontmatter field, set on a top-level ("week") module to drive the derived cohort week date range shown to learners (`Cohort.start_date` + this offset). Both apply to a submodule's own `module.yaml` too, parsed exactly the same way.

## Unit frontmatter

```markdown
---
content_id: <UUID>
sort_order: 2
title: Logging
video_url: https://www.youtube.com/embed/...   # optional
access: open                                   # optional, per-unit override
is_preview: true                               # legacy alias for `access: open`
kind: homework                                 # optional; lesson (default) | homework | event, case-insensitive
session_position: 4                            # required when kind: event; 1-indexed position within the course's live-session series
is_bonus: true                                 # optional; default false
---
markdown body
```

Per-unit `access` overrides the course's `default_unit_access`. `is_preview: true` is a legacy alias for `access: open`; if both are set, `access` wins.

Issue #1674: `kind: homework` routes the body into `Unit.homework` exactly like the existing `is_homework: true` does today — `is_homework: true` keeps working as a legacy alias and, when `kind:` is absent, sets `kind='homework'` too. If both are set and disagree, `kind:` wins and sync records an info-level note. `kind: event` requires `session_position:` (a positive integer, matching `events.Event.series_position`) — sync does NOT look up any `events.Event` row at sync time (curriculum syncs independently of which cohorts/events exist; the actual event is resolved per viewer/cohort at render time). Sync fails that unit, naming the file, if `session_position` is missing, non-numeric, or not a positive integer when `kind: event` is set. `is_bonus: true` sets `Unit.is_bonus` — excluded from the progress denominator, still tracked/shown.

### Homework: `questions:` and `due_date:`

Issue #1683 (tranche 1). Extends the same `kind: homework` unit file with a
submittable, auto-scored question list. `due_date:` is required whenever
`questions:` is present.

```markdown
---
content_id: <UUID>
sort_order: 41
title: 'Module 1 Homework: Document Processing with AI'
kind: homework                        # or is_homework: true, per #1674
due_date: '2026-09-27T21:59:00Z'      # ISO-8601 with an explicit UTC offset -- a naive
                                        # datetime (no offset) is a sync error naming the file
questions:
  - id: q1-lines                       # required, stable across edits -- the re-sync upsert key
    text: 'How many lines are in the extracted content from the "Think Python" book?'
    type: multiple_choice              # multiple_choice | free_form | free_form_long | checkboxes
    options: ['12,268', '14,268', '16,268', '18,268']
    correct: '3'                       # 1-based index (comma-separated for checkboxes)
    score: 1                           # optional, default 1
  - id: q7-reflection
    text: 'What was the hardest part of this homework?'
    type: free_form
    answer_type: any                   # any | float | integer | exact_string | contains_string
---
markdown body (the homework instructions/prose)
```

Sync resolves ONE target cohort per sync pass (`_resolve_homework_cohort`
in `content/sync_parsers/families/homework.py`): the course's currently
in-range `mode='cohort'` cohort; if none is in range and the course has
exactly one cohort overall (including a `mode='self_paced'` cohort), that
cohort; otherwise the homework file is skipped with a logged sync warning
(not a hard failure -- unrelated units in the same course still sync).
`Homework.content_id` is scoped `(cohort, content_id)`, not globally
unique, specifically so a later cohort resolving against the same
curriculum unit gets its own `Homework` row instead of colliding with an
earlier cohort's -- reconcile-never-destroy, same as the rest of the sync
pipeline; an earlier cohort's `Homework` row is never touched by a later
cohort's sync pass. A `questions:` entry removed from a later edit deletes
the corresponding question (and its submitted answers) on next sync.

Render-time resolution is a related but DIFFERENT, per-viewer policy
(`content.services.course_units.resolve_homework_for_unit`, a sibling of
`resolve_session_event`'s policy for `kind: event` units): the viewer's
own enrolled cohort (any mode) first, else the most recent past dated
cohort or any self-paced cohort. A self-paced cohort has no
`start_date`/`end_date` and therefore no deadline to be late against --
`Homework.is_accepting_submissions` never enforces `due_date` for a
`mode='self_paced'` cohort (only `state == OPEN` gates it); the frontmatter
`due_date:` value is still stored on that cohort's `Homework` row (sync
requires it whenever `questions:` is present, dated cohort or not) but is
inert for a self-paced learner.

A `kind: homework` unit with no `questions:`/`due_date:` frontmatter keeps
rendering as prose-only instructions, with no submission form -- adding
`questions:` later is what turns it into a submittable homework.

## Editing workflow

1. Open the course in Studio: `/studio/courses/<id>/edit`. The "Source-managed course" sticky bar links to the file on GitHub via the `Edit on GitHub` button.
2. Edit `course.yaml`, `module.yaml`, or unit markdown in GitHub. Commit and push to the default branch.
3. Webhooks trigger an automatic sync. To force one immediately, use the `Re-sync source` button in the same sticky bar, or run `uv run python manage.py sync_content`.
4. Reload the Studio page to confirm the new values.

If the sync log shows errors, fix them at the YAML source and re-sync. Do not work around them by editing the database — the next sync will overwrite the fix.

## When to edit in GitHub vs Studio

- Always edit in GitHub: any field listed under "YAML" in the table above. The Studio form is read-only for these fields on source-managed courses, by design.
- Edit in Studio: operational fields that the course form exposes, including peer-review configuration and one-off access grants. Source-managed pricing and Stripe identifiers are pending their dedicated Studio/API workflow; use an explicit low-level maintenance change until it lands.
- Never edit a synced row in the database for content fields. Edit the YAML.

## Common mistakes

- Renaming `slug` without changing the URL: breaks SEO and any external links. Set up a redirect first.
- Reusing a `content_id` from a different course: the sync upserts on `content_id`, so the wrong row is replaced.
- Setting `individual_price_eur` in `course.yaml`: ignored by the parser, since pricing is DB-owned.
- Editing `course.yaml` and forgetting to re-sync: the live site keeps showing the old version until sync runs.
