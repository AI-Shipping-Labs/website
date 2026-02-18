# 079 - Course Unit Pages and Progress Tracking

**Status:** pending
**Tags:** `courses`, `frontend`
**GitHub Issue:** [#79](https://github.com/AI-Shipping-Labs/website/issues/79)
**Specs:** 05 (unit page section), 08
**Depends on:** [078-course-models-catalog](078-course-models-catalog.md), [073-video-player](073-video-player.md)
**Blocks:** [081-course-cohorts](081-course-cohorts.md)

## Scope

- `/courses/{slug}/{module_sort}/{unit_sort}` unit page
- Gated: requires user.tier.level >= course.required_level (except is_preview units, which are open to all)
- Layout: VideoPlayer at top (if video_url set, with clickable timestamps), lesson text (markdown) below, homework section at bottom
- Sidebar: module/unit navigation tree with checkmarks for completed units
- "Mark as completed" toggle button: creates/deletes UserCourseProgress record
- "Next unit" button navigating to next unit in sort order
- API: `GET /api/courses/{slug}/units/{unit_id}` (full content if authorized or is_preview, 403 otherwise), `POST /api/courses/{slug}/units/{unit_id}/complete` (toggle)

## Acceptance Criteria

- [ ] `GET /courses/{slug}/{module_sort}/{unit_sort}` renders unit page with: video player at top (if video_url set), lesson text (markdown) below, homework section at bottom
- [ ] Sidebar shows module/unit navigation tree with checkmarks for completed units
- [ ] Units with is_preview = true accessible to all users regardless of tier
- [ ] Non-preview units return 403 for users with tier.level < course.required_level
- [ ] "Mark as completed" toggle creates UserCourseProgress record; toggling again deletes it
- [ ] "Next unit" button navigates to next unit in sort order (across module boundaries)
- [ ] API `GET /api/courses/{slug}/units/{unit_id}` returns full content if authorized or is_preview; 403 otherwise
- [ ] API `POST /api/courses/{slug}/units/{unit_id}/complete` toggles completion status
