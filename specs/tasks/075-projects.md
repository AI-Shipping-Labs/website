# 075 - Project Showcase

**Status:** pending
**Tags:** `content`, `admin`, `frontend`
**GitHub Issue:** [#75](https://github.com/AI-Shipping-Labs/website/issues/75)
**Specs:** 06 (projects section)
**Depends on:** [071-access-control](071-access-control.md)
**Blocks:** [092-github-content-sync](092-github-content-sync.md)

## Scope

- Project model: slug, title, description, author, difficulty, tags, source_code_url, demo_url, cover_image_url, required_level, status (pending_review/published)
- `/projects` listing page: grid filterable by difficulty and tags, cards with cover image, author, difficulty badge, tags
- `/projects/{slug}` detail page: full description, author info, difficulty, source code link, demo link; gated per required_level
- Community submission: authenticated users can submit projects for admin review
- Admin CRUD + approve/reject pending submissions

## Acceptance Criteria

- [ ] Project model with fields: slug (unique), title, description (markdown), author FK, difficulty (beginner/intermediate/advanced), tags (string[]), source_code_url, demo_url, cover_image_url, required_level, status (pending_review/published), published_at, created_at
- [ ] `GET /projects` shows grid of published projects; each card: title, cover image, author name, difficulty badge, tag badges
- [ ] Filtering by difficulty (dropdown or chips) and tags (clickable chips) works
- [ ] `GET /projects/{slug}` for authorized user shows: full description, author info, difficulty, source code link, demo link
- [ ] `GET /projects/{slug}` for unauthorized user shows gated teaser + CTA
- [ ] Authenticated non-admin user can submit a project via `POST /api/projects/submit`; creates with status = "pending_review"
- [ ] Admin sees pending submissions in admin panel and can approve (→ published) or reject
- [ ] Admin can create/edit/delete projects directly
