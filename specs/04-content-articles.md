# 04 - Content: Articles

## Overview

Blog posts and articles. Each article has per-article access control.

## Data Model

```
Article:
  id: uuid
  slug: string (unique)           # URL-friendly, e.g. "building-ai-agents-with-mcp"
  title: string
  body: text                      # markdown
  excerpt: string                 # first 200 chars or manually set, used for teasers and meta descriptions
  cover_image_url: string | null
  author_id: FK -> User
  tags: string[]                  # e.g. ["ai-engineering", "tutorial", "mcp"]
  required_level: int             # 0-3, see spec 03
  status: enum                    # "draft", "published"
  published_at: datetime | null
  created_at: datetime
  updated_at: datetime
```

## Pages

### `/blog` — Article listing

- Lists all published articles, sorted by `published_at` descending
- Each card shows: title, excerpt, cover image, author name, date, tags
- Gated articles show a lock icon and the required tier name (e.g. "Basic+")
- Filterable by tag (clicking a tag filters the list)

### `/blog/{slug}` — Article detail

- If `user.tier.level >= article.required_level`: render full markdown body with embedded media
- If not: render `excerpt` + blurred placeholder + CTA banner: "Upgrade to {tier_name} to read this article" with link to `/pricing`
- Sidebar or bottom: related articles (same tags)
- Embedded media supported: images (markdown `![](url)`), YouTube/Loom embeds (see spec 08), code blocks with syntax highlighting

## Admin

### `/admin/articles` — Article list

- Table of all articles (draft + published), sortable by date, filterable by status and tags
- Actions: Edit, Delete, Publish/Unpublish

### `/admin/articles/new` and `/admin/articles/{id}/edit`

- Form fields: title, slug (auto-generated from title, editable), body (markdown editor), excerpt (auto-generated, editable), cover image upload, tags (multi-select or free-text), visibility dropdown (spec 03), status (draft/published)
- "Publish" button sets `status = "published"` and `published_at = now()`

## Requirements

- R-ART-1: Create `articles` table with the schema above.
- R-ART-2: `GET /api/articles` returns published articles, paginated (20 per page), sorted by `published_at` desc. Each article includes `is_locked: bool` based on the requesting user's tier.
- R-ART-3: `GET /api/articles/{slug}` returns full article if user has access. If not, returns `excerpt` + `is_locked: true` + `required_tier_name`.
- R-ART-4: Markdown body supports: headings, bold/italic, links, images, code blocks with language hints, YouTube/Loom embeds via URL on its own line.
- R-ART-5: The `/blog` page renders server-side with proper `<title>`, `<meta description>`, and OpenGraph tags for each article (see spec 13).
- R-ART-6: Admin CRUD endpoints: `POST /api/admin/articles`, `PUT /api/admin/articles/{id}`, `DELETE /api/admin/articles/{id}`. All require admin auth.

### Nice to Have

- R-ART-7: Community article submission: authenticated non-admin users can `POST /api/articles/submit` with title, body, tags. Creates article with `status = "pending_review"`. Admin sees pending submissions and can approve (sets `status = "published"`) or reject.
