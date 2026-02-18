# 078 - Course Models and Catalog

**Status:** pending
**Tags:** `courses`, `frontend`
**GitHub Issue:** [#78](https://github.com/AI-Shipping-Labs/website/issues/78)
**Specs:** 05 (data model, catalog, detail page), 03
**Depends on:** [071-access-control](071-access-control.md)
**Blocks:** [079-course-unit-pages](079-course-unit-pages.md), [080-course-admin](080-course-admin.md)

## Scope

- Course model: slug, title, description (markdown), cover_image_url, instructor_name, instructor_bio, required_level, status (draft/published), is_free, discussion_url, tags
- Module model: course FK, title, sort_order
- Unit model: module FK, title, sort_order, video_url, body (markdown), homework (markdown), timestamps (JSON), is_preview
- UserCourseProgress model: user FK, unit FK, completed_at (unique together)
- `/courses` catalog page: grid of all published courses; each card shows title, cover image, instructor, tag badges, "Free" badge if is_free, required tier badge otherwise
- `/courses/{slug}` detail page: always visible (SEO); shows title, description, instructor bio, full syllabus (module + unit titles), tags, discussion link
  - If user has access: unit titles are clickable links, progress bar (X of Y completed)
  - If not: unit titles visible but not clickable, CTA "Unlock with {tier_name} — {price}/year"
  - If is_free and not registered: CTA "Sign up free to start this course"
- API: `GET /api/courses` (list with is_locked), `GET /api/courses/{slug}` (detail + syllabus + progress)

## Acceptance Criteria

- [ ] Course model with fields: slug (unique), title, description (markdown), cover_image_url, instructor_name, instructor_bio, required_level, status (draft/published), is_free, discussion_url, tags (string[]), created_at
- [ ] Module model with fields: course FK, title, sort_order
- [ ] Unit model with fields: module FK, title, sort_order, video_url, body (markdown), homework (markdown), timestamps (JSON), is_preview
- [ ] UserCourseProgress model with fields: user FK, unit FK, completed_at; unique together (user, unit)
- [ ] `GET /courses` shows grid of published courses; each card: title, cover image, instructor name, tag badges, "Free" badge if is_free, required tier badge otherwise
- [ ] `GET /courses/{slug}` always renders full syllabus (module + unit titles) in HTML — accessible without login (for SEO)
- [ ] Authorized user on course detail sees clickable unit links and progress bar (X of Y completed)
- [ ] Unauthorized user sees unit titles (not clickable) + CTA "Unlock with {tier_name}"
- [ ] is_free course + unauthenticated user sees CTA "Sign up free to start this course"
- [ ] API `GET /api/courses` returns list with is_locked flag per course
- [ ] API `GET /api/courses/{slug}` returns detail + syllabus + progress for authenticated user
