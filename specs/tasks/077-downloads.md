# 077 - Downloadable Resources

**Status:** pending
**Tags:** `content`, `admin`, `frontend`
**GitHub Issue:** [#77](https://github.com/AI-Shipping-Labs/website/issues/77)
**Specs:** 06 (downloads section)
**Depends on:** [071-access-control](071-access-control.md)
**Blocks:** [092-github-content-sync](092-github-content-sync.md)

## Scope

- Download model: slug, title, description, file_url, file_type, file_size_bytes, cover_image_url, required_level, tags, download_count
- `/downloads` listing page: grid of downloads with file type badge and size
- File serving endpoint: streams file if authorized, increments download_count
- Lead magnet flow: if required_level = 0 and user anonymous, show email signup form; after signup deliver download
- `{{download:slug}}` shortcode in markdown renderer: renders inline download CTA card
- Admin CRUD for downloads

## Acceptance Criteria

- [ ] Download model with fields: slug (unique), title, description, file_url, file_type (pdf/zip/slides/etc), file_size_bytes, cover_image_url, required_level, tags (string[]), download_count (default 0), created_at
- [ ] `GET /downloads` shows grid of downloads; each card: title, description, file type badge, human-readable file size
- [ ] Authorized user clicking download triggers file stream from `GET /api/downloads/{slug}/file`; download_count increments by 1
- [ ] Unauthorized user with required_level > 0 sees CTA "Upgrade to {tier_name} to download"
- [ ] Anonymous user on a level-0 download sees email signup form (lead magnet); after signup, receives download link
- [ ] `{{download:slug}}` in markdown body renders as an inline card with title, description, and download/CTA button
- [ ] Download endpoint returns 403 for unauthorized users (never serves the file)
- [ ] Admin can create/edit/delete downloads with all fields
