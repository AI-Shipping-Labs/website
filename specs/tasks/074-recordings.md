# 074 - Event Recordings

**Status:** pending
**Tags:** `content`, `admin`, `frontend`
**GitHub Issue:** [#74](https://github.com/AI-Shipping-Labs/website/issues/74)
**Specs:** 06 (recordings section), 08
**Depends on:** [071-access-control](071-access-control.md), [073-video-player](073-video-player.md)
**Blocks:** [092-github-content-sync](092-github-content-sync.md)

## Scope

- Recording model: slug, title, description, video_url, timestamps (JSON), materials (JSON), event_id FK, tags, required_level, published_at
- `/recordings` listing page: grid of recordings with thumbnails, dates, tags, lock icons
- `/recordings/{slug}` detail page: VideoPlayer with timestamps if authorized, materials list, gated response if not
- Tag filtering on listing page
- Admin CRUD for recordings

## Acceptance Criteria

- [ ] Recording model with fields: slug (unique), title, description (markdown), video_url, timestamps (JSON array), materials (JSON array), event_id FK (nullable), tags (string[]), required_level, published_at, created_at
- [ ] `GET /recordings` shows grid of recordings sorted by published_at desc; each card: title, thumbnail, date, tags, lock icon if gated
- [ ] Clicking a tag filters the recordings list
- [ ] `GET /recordings/{slug}` for authorized user: VideoPlayer with video, clickable timestamps below, materials listed as links (title + URL)
- [ ] `GET /recordings/{slug}` for unauthorized user: title + description visible, video replaced with blurred placeholder + CTA "Upgrade to {tier_name} to watch this recording"
- [ ] Admin can create/edit/delete recordings with all fields including timestamps and materials
- [ ] Pagination: 20 recordings per page
