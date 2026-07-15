# 06 - Content: Resources

## Overview

Self-serve resource surfaces include project showcase, curated links, downloadable resources, and the legacy past-event-recordings discovery surface. The `/resources` route is the curated-links collection, not a catch-all hub for all community activity or recordings.

## Data Models

### Past Event Recordings (shipped model)

The shipped platform does not use an active standalone `Recording` content table or `/recordings` public route. Recordings are stored on `Event` fields and, when the event has a linked `Workshop`, the workshop becomes the canonical learning artifact.

```
Event recording fields:
  recording_url: string                 # YouTube/Loom/external URL
  recording_embed_url: string           # embeddable provider URL
  recording_s3_url: string              # private S3 object URL
  timestamps: jsonb                     # [{time_seconds: int, label: string}, ...]
  materials: jsonb                      # [{title: string, url: string}, ...]
  required_level: int                   # access level for standalone event recordings

Workshop linked recording:
  event_id: FK -> Event | null          # recording source event
  recording_required_level: int         # access level for workshop video page
```

### Project Showcase

```
Project:
  id: uuid
  slug: string (unique)
  title: string
  description: text               # markdown
  author_id: FK -> User           # community member who submitted
  difficulty: enum                # "beginner", "intermediate", "advanced"
  tags: string[]
  source_code_url: string | null  # GitHub link
  demo_url: string | null         # live demo link
  cover_image_url: string | null
  required_level: int             # 0/10/20/30 (Free/Basic/Main/Premium)
  status: enum                    # "pending_review", "published"
  published_at: datetime | null
  created_at: datetime
```

### Curated Links

```
CuratedLink:
  id: uuid
  title: string
  description: string             # short, 1-2 sentences
  url: string                     # external URL
  category: string                # e.g. "github-repo", "model-hub", "tool", "learning"
  tags: string[]
  required_level: int             # 0-3
  sort_order: int
  created_at: datetime
```

### Downloadable Resources

```
Download:
  id: uuid
  slug: string (unique)
  title: string
  description: string
  file_url: string                # legacy source URL; never a public delivery target
  storage_key: string             # private S3 object key; public delivery never exposes it
  file_type: string               # explicit allowlist: pdf/zip/slides/notebook/csv
  asset_mime_type: string         # validated against storage-key extension
  file_size_bytes: int
  delivery_blocked_reason: string # operator-only source/readiness failure marker
  cover_image_url: string | null
  required_level: int             # 0/10/20/30; 0 uses verified-mailbox delivery
  published: bool                 # source failures unpublish an existing row
  tags: string[]
  download_count: int             # incremented on each download
  created_at: datetime
```

## Pages

### `/events?filter=past` — Past event recordings

- Paginated list of published past events that have a recording field populated.
- Each card: title, event date, tags, lock icon/tier cue if gated.
- Filterable by tags.
- Workshop-linked events link to `/workshops/{slug}` and their recording CTA points to `/workshops/{slug}/video`.
- Standalone legacy recordings keep the existing event detail URL (`/events/{id}/{slug}`).

### `/events/{id}/{slug}` — Standalone recording detail

- For completed standalone events with a recording: video player with clickable timestamps (see spec 08), description, and materials list.
- For workshop-linked events: show a clear handoff to the workshop landing page.
- If the user lacks access: preserve the existing upgrade/paywall behavior.

### `/projects` — Project showcase

- Grid of published projects, filterable by difficulty and tags
- Each card: title, cover image, author name, difficulty badge, tag badges
- Clicking goes to `/projects/{slug}`

### `/projects/{slug}` — Project detail

- Full description, author info, difficulty, source code link, demo link
- Gated per `required_level`

### `/resources` — Curated links

- Grid/list grouped by `category`, filterable by tags
- Each item: title, description, external link icon. Clicking opens the URL in a new tab.
- Gated items show lock icon; clicking shows upgrade CTA instead of opening URL

### `/downloads` — Downloadable resources

- Grid of available downloads
- Cards show file type, size, tags, and the required access tier. Downloads
  deliberately render no cover/media treatment.
- Each card: title, description, file type badge, file size
- Cards link to `/downloads/{slug}`; repeated catalog and shortcode cards never embed signup forms.
- The detail page initiates download if the member has access. If not:
  - If `required_level = 0`: show a transactional email-delivery form with separate optional newsletter consent. After mailbox verification, redeem a one-time scoped grant.
  - If `required_level > 0`: show CTA: "Upgrade to {tier_name} to download"

## Embeddable Download CTA

A reusable component that can be placed inside any article body (via a shortcode or markdown extension):

```markdown
{{download:slug-of-resource}}
```

Renders as: a card with the download title, description, access label, and an
access-specific button that hands off to the detail page. Unknown or unpublished
slugs render nothing; shortcodes never embed an email form or leak the slug.

## Requirements

- R-RES-1: Create `projects`, `curated_links`, and `downloads` tables with schemas above. Past event recordings use `Event` and linked `Workshop` fields from the shipped event/workshop model.
- R-RES-2: `/events?filter=past` returns published past events with recording fields populated, paginated 20/page, sorted by event start time descending, and with existing gated tier cues.
- R-RES-3: Workshop-linked event recordings hand off to `/workshops/{slug}` and `/workshops/{slug}/video`; standalone legacy event recordings keep `/events/{id}/{slug}`.
- R-RES-4: `GET /api/projects` returns published projects, filterable by `difficulty` and `tags` query params.
- R-RES-5: Community members can submit projects via `POST /api/projects/submit` (requires auth). Creates with `status = "pending_review"`. Admin approves via `PUT /api/admin/projects/{id}/approve`.
- R-RES-6: `GET /api/curated-links` returns all links grouped by `category`. Gated links return `is_locked` instead of `url`.
- R-RES-7: `GET /api/downloads/{slug}/file` re-checks access and redirects to a fresh private-S3 presigned attachment. It increments `download_count` only after successful authorization and presigning.
- R-RES-8: For Free downloads (`required_level = 0`), anonymous visitors request a generic, enumeration-safe transactional email from the detail page. Verification leads to a durable, one-time, slug-scoped delivery grant; marketing consent is separate and optional.
- R-RES-9: Implement `{{download:slug}}` shortcode in the markdown renderer. When encountered, render an inline download CTA card.
- R-RES-10: `POST /api/downloads/{slug}/request` is enumeration-safe and rate
  limited by trusted client IP, normalized email, and resource. Its optional
  newsletter checkbox is separate consent; when checked, the delivery email
  explicitly states that clicking confirms both subscription and delivery.
- R-RES-11: Source sync validates metadata and HEAD-checks the configured private
  S3 object. A valid row that later fails validation is unpublished and marked
  unready while preserving operator-visible metadata; a later valid sync recovers it.
- R-RES-12: Publishable file types use an explicit extension/MIME allowlist.
  Legacy `.ppt` uses `application/vnd.ms-powerpoint`; `.pptx` uses the OOXML MIME.
  Legacy `other` rows remain stored but are never deliverable until a separately
  reviewed extension/MIME pair is added to the allowlist.
- R-RES-13: Admin CRUD endpoints for all four resource types.
