# 06 - Content: Resources

## Overview

Four resource types: event recordings, project showcase, curated links, downloadable resources.

## Data Models

### Event Recordings

```
Recording:
  id: uuid
  slug: string (unique)
  title: string
  description: text               # markdown
  video_url: string               # YouTube/Loom URL
  timestamps: jsonb               # [{time_seconds: int, label: string}, ...]
  materials: jsonb                 # [{title: string, url: string}, ...] — slides, repos, docs used
  event_id: FK -> Event | null    # link to originating event, if any
  tags: string[]
  required_level: int             # 0-3
  published_at: datetime
  created_at: datetime
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
  required_level: int             # 0-3
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
  file_url: string                # S3/storage URL to the file
  file_type: string               # "pdf", "zip", "slides", etc.
  file_size_bytes: int
  cover_image_url: string | null
  required_level: int             # 0-3. 0 = gated behind email signup (lead magnet for free tier)
  tags: string[]
  download_count: int             # incremented on each download
  created_at: datetime
```

## Pages

### `/recordings` — Recordings library

- Grid/list of all published recordings, sorted by `published_at` desc
- Each card: title, thumbnail (auto-generated from video or cover image), date, tags, lock icon if gated
- Filterable by tags
- Clicking a card goes to `/recordings/{slug}`

### `/recordings/{slug}` — Recording detail

- If user has access: video player with clickable timestamps (see spec 08), description, materials list (links to slides/repos/docs)
- If not: title, description visible. Video replaced with blurred placeholder + CTA: "Upgrade to {tier_name} to watch this recording"

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
- Each card: title, description, file type badge, file size
- Clicking initiates download if user has access. If not:
  - If `required_level = 0`: show email signup form (lead magnet flow, see spec 10). After signup, deliver download.
  - If `required_level > 0`: show CTA: "Upgrade to {tier_name} to download"

## Embeddable Download CTA

A reusable component that can be placed inside any article body (via a shortcode or markdown extension):

```markdown
{{download:slug-of-resource}}
```

Renders as: a card with the download title, description, and a button. Button behavior follows the same gating logic as `/downloads`.

## Requirements

- R-RES-1: Create `recordings`, `projects`, `curated_links`, `downloads` tables with schemas above.
- R-RES-2: `GET /api/recordings` returns published recordings paginated (20/page), sorted by `published_at` desc. Each includes `is_locked` flag.
- R-RES-3: `GET /api/recordings/{slug}` returns full recording (video_url, timestamps, materials) if user has access, or title + description + `is_locked + required_tier_name` if not.
- R-RES-4: `GET /api/projects` returns published projects, filterable by `difficulty` and `tags` query params.
- R-RES-5: Community members can submit projects via `POST /api/projects/submit` (requires auth). Creates with `status = "pending_review"`. Admin approves via `PUT /api/admin/projects/{id}/approve`.
- R-RES-6: `GET /api/curated-links` returns all links grouped by `category`. Gated links return `is_locked` instead of `url`.
- R-RES-7: `GET /api/downloads/{slug}/file` streams the file if user has access. Returns 403 otherwise. Increments `download_count` on success.
- R-RES-8: For lead magnet downloads (`required_level = 0`): if user is anonymous, return 401 with `requires_email: true`. Frontend shows email signup form. After signup, redirect to download.
- R-RES-9: Implement `{{download:slug}}` shortcode in the markdown renderer. When encountered, render an inline download CTA card.
- R-RES-10: Admin CRUD endpoints for all four resource types.
