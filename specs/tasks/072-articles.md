# 072 - Articles (Blog)

**Status:** pending
**Tags:** `content`, `admin`, `frontend`
**GitHub Issue:** [#72](https://github.com/AI-Shipping-Labs/website/issues/72)
**Specs:** 04, 03
**Depends on:** [071-access-control](071-access-control.md)
**Blocks:** [092-github-content-sync](092-github-content-sync.md)

## Scope

- Article model: slug, title, body (markdown), excerpt, cover_image_url, author, tags, required_level, status, published_at
- `/blog` listing page: published articles sorted by date, cards with title/excerpt/cover/author/date/tags, lock icon for gated articles, tag filtering
- `/blog/{slug}` detail page: full markdown body if user has access, excerpt + blur + CTA if not
- Markdown rendering: headings, bold/italic, links, images, code blocks with syntax highlighting
- Related articles (same tags) on detail page
- Admin CRUD for articles (list, create, edit, delete, publish/unpublish)
- API endpoints for listing and detail

## Acceptance Criteria

- [ ] Article model with fields: slug (unique), title, body (markdown), excerpt, cover_image_url, author FK, tags (string[]), required_level, status (draft/published), published_at, created_at, updated_at
- [ ] `GET /blog` lists published articles sorted by published_at desc; each card shows: title, excerpt, cover image, author name, date, tags
- [ ] Gated articles show a lock icon and required tier name (e.g. "Basic+") on the listing card
- [ ] Clicking a tag on /blog filters the list to articles with that tag (via ?tag=X query param)
- [ ] `GET /blog/{slug}` for authorized user renders full markdown body with: headings, bold/italic, links, images, code blocks with syntax highlighting
- [ ] `GET /blog/{slug}` for unauthorized user renders: excerpt text + blurred placeholder + CTA banner "Upgrade to {tier_name} to read this article" linking to /pricing
- [ ] Related articles (sharing at least one tag) shown at bottom of detail page
- [ ] Admin can create article with all fields, auto-generate slug from title, set status to draft or published
- [ ] Admin can publish (sets published_at = now), unpublish, edit, and delete articles
- [ ] `<title>` tag on detail page follows "{Article Title} | AI Shipping Labs" format
