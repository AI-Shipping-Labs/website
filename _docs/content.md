# Content Sync and Authoring

How content is structured, synced, and gated. Companion to `setup.md` (which covers GitHub App credentials and infrastructure).

## Architecture

Content lives in GitHub repos. Each repo is registered as a `ContentSource` row. A push to a registered repo triggers a webhook at `/api/webhooks/github` which enqueues a background sync job. The sync clones (or pulls) the repo, walks the configured `content_path`, parses YAML/markdown frontmatter, uploads images to S3 (rewriting relative paths to the CDN), and upserts rows into the relevant content models.

Manual sync: `uv run python manage.py sync_content` (all sources) or `uv run python manage.py sync_content --from-disk <path>` (local clone, useful for previewing changes before they land in GitHub).

Sync code: `integrations/services/github.py`. Per-type sync functions follow the pattern `_sync_<type>(source, repo_dir, commit_sha, sync_log, known_images)`.

## ContentSource

Database-backed. One row per (repo, content_type, sub-path) combo. Multiple rows per repo are fine — the monorepo today registers one row per content type.

| Field | Purpose |
|-------|---------|
| `repo_name` | e.g. `AI-Shipping-Labs/content` |
| `content_type` | One of: `article`, `course`, `resource`, `project`, `interview_question`, `event` |
| `content_path` | Sub-directory inside the repo (e.g. `courses/`). Empty string = repo root. |
| `is_private` | If true, sync uses the GitHub App token. Public repos clone over HTTPS without auth. |
| `webhook_secret` | For validating incoming GitHub webhook signatures |

Register sources by editing `integrations/management/commands/seed_content_sources.py` and re-running it, or insert via Django admin (`/admin/integrations/contentsource/`).

## Authentication

One GitHub App services all content repos. Credentials and installation scope are documented in `setup.md` under "GitHub App (content sync)". The recommended scope is "All repositories" so any new repo in the org becomes syncable without admin clicks.

Verify the App can reach a specific repo with the snippet in `setup.md`.

## Content types

All content carries a `content_id` (UUID or stable slug) used as the upsert key. Most types also carry `required_level` for tier gating, `tags`, and a date field. Beyond that the schema varies by type.

### Course (3-level: course → module → unit)

Three-level hierarchy. Each course is a top-level directory under `content_path`. Each module is a subdirectory of the course. Each unit is a markdown file inside a module.

```yaml
# courses/<course-slug>/course.yaml
content_id: <UUID>
slug: aihero
title: 'AI Hero: 7-Day AI Agents Crash-Course'
description: ...
instructor_name: Alexey Grigorev
instructor_bio: ...
required_level: 0          # 0=open, 10=basic, 20=main, 30=premium
discussion_url: ...
tags: [ai-agents, rag]
testimonials:
  - quote: ...
    name: ...
    role: ...
    source_url: ...
```

```yaml
# courses/<slug>/<module-dir>/module.yaml
content_id: <UUID>
sort_order: 5
title: 'Day 5: Offline Evaluation and Testing'
```

```markdown
<!-- courses/<slug>/<module>/<NN>-unit.md -->
---
content_id: <UUID>
sort_order: 2
title: Logging
video_url: https://www.youtube.com/embed/...   # optional
is_preview: true                                # optional, free even when course is gated
---
markdown body
```

Course directory naming convention: numeric prefix (e.g. `01-intro`) determines `sort_order` if `module.yaml` doesn't override it.

### Article / Blog post

`blog/<slug>.md`. One file per article.

```markdown
---
content_id: <UUID>
title: AI Engineer Learning Path
author: Alexey Grigorev
date: '2025-12-15'
description: ...
tags: [...]
data: {...}        # optional structured payload for rich pages
---
markdown body
```

### Project

`projects/<slug>.md`. One file per project showcase.

```markdown
---
content_id: <UUID>
title: Habit Builder Agent
author: Vancesca Dinh
date: '2025-12-15'
description: ...
difficulty: advanced
tags: [agents, RAG, ...]
---
markdown body with images
```

### Event

`events/<slug>.yaml`. One file per event. Optional `recap_file:` points at a
markdown file in the same content repo and renders recap content inline on the
event detail page. Recap markdown can include repo-local HTML snippets with
`<!-- include:relative-file.html -->`; those snippets are rendered during sync
and stored as HTML, so page-specific markup stays in the content repo instead
of Django templates. Recap-specific structured data belongs directly in the
recap file frontmatter, so there is a single source for the recap content.
Generic event fields, such as the recording URL, stay on the event. Recap
markdown and include files are trusted content-repo inputs, like other synced
markdown HTML; do not point `recap_file` or include markers at user uploads or
unreviewed external content.

```yaml
content_id: <UUID>
slug: example-event
title: Example Event
status: upcoming
start_datetime: "2026-04-13T16:30:00Z"
end_datetime: "2026-04-13T18:00:00Z"
location: Zoom
required_level: 0
speaker_name: Alexey Grigorev
recording_embed_url: https://www.youtube.com/embed/...
recap_file: example-event/recap.md
description: |
  multi-line markdown
```

Example recap file:

```markdown
---
hero:
  title: Example Event Recap
  subtitle: If you missed the stream, start here.
cta_label: Join now
---

# Event recap

<!-- include:recording.html -->
```

If the event file is `events/example-event.yaml`, the include above is
resolved relative to the recap file directory, for example
`events/example-event/recording.html`.

### Recording

`recordings/<slug>.yaml`. One file per recorded session.

```yaml
content_id: <UUID>
slug: ai-coding-tools-compared
description: ...
materials:
  - title: Workshop Code Repository
    type: code
    url: https://github.com/...
published_at: '2025-07-21'
tags: [...]
```

### Curated Link

`curated-links/<slug>.md`. Lightweight bookmarks for the resources page.

```markdown
---
content_id: <slug-or-UUID>
title: Cursor
url: https://cursor.com
date: '2026-03-15'
required_level: 0
category: tools
published: true
sort_order: 2
tags: []
---
short description
```

### Interview Question

`interview-questions/<topic>.md`. One file per topic (`behavioral.md`, `coding.md`, `home-assignments.md`).

### Repo-level files

| File | Purpose |
|------|---------|
| `tiers.yaml` | Global tier definitions (used by the monorepo only) |
| `STYLING_GUIDE.md` | Author-facing reference, not synced |
| `README.md` | GitHub-facing landing page, currently not synced |

## Tier gating

Single uniform check across all content types: `user.tier.level >= content.required_level`. Anonymous users are treated as level 0.

| `required_level` | Meaning |
|------------------|---------|
| `0` | Open (everyone, including anonymous) |
| `10` | Basic and above |
| `20` | Main and above |
| `30` | Premium only |

Constants in `content/access.py` (`LEVEL_OPEN`, `LEVEL_BASIC`, `LEVEL_MAIN`, `LEVEL_PREMIUM`).

Per-unit override on courses: `is_preview: true` in unit frontmatter makes that unit visible to everyone regardless of the course's `required_level`. Use sparingly for free teasers in paid courses.

## Adding a new content repo

Same steps regardless of which content type the repo holds.

1. Restructure the repo to match the content type's expected layout (see schemas above). Add `content_id` to every item.
2. Confirm the GitHub App has access to the repo (org settings → installation → "All repositories", or add the specific repo). Verify with the snippet in `setup.md`.
3. Add a `ContentSource` row — edit `seed_content_sources.py` and re-run, or insert via Django admin.
4. Configure the GitHub webhook on the repo to point at `/api/webhooks/github`, using the `webhook_secret` from the `ContentSource` row.
5. Trigger the first sync: `uv run python manage.py sync_content` or push a commit to the repo.

### Single-course repos

`_sync_courses` walks the configured directory and treats every child folder as a course (looking for `course.yaml` in each). For a standalone single-course repo (where `course.yaml` lives at the repo root), the sync needs to detect that shape — tracked in issue #197.

## Conventions and gotchas

- Image references in markdown use relative paths (`![alt](images/foo.png)`). The sync uploads images to S3 and rewrites the URLs to point at `https://cdn.aishippinglabs.com`.
- `content_id` must be stable. Changing it creates a new row instead of updating the existing one.
- Validation errors during sync are reported in `SyncLog` rows and surfaced in the Studio sync page (`/studio/sync/`).
- Files without recognizable frontmatter are skipped; files with broken frontmatter raise a sync error.
- Test fixtures for the sync live in `integrations/tests/test_github_sync.py`.

## Future enhancements

Tracked as separate issues, not yet implemented:

- #197 — single-course-at-root mode for `_sync_courses` and onboarding `python-course`
- #199 — Studio dropdown of accessible repos when registering a new `ContentSource`
- #200 — per-course/module `ignore:` globs (e.g. `*.template.md`) and `readme:` placement (hidden / first_unit / course_description)
- #201 — drop redundant `is_free` flag on Course; derive from `required_level`
