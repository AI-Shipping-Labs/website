# 080 - Course Admin CRUD

**Status:** pending
**Tags:** `courses`, `admin`
**GitHub Issue:** [#80](https://github.com/AI-Shipping-Labs/website/issues/80)
**Specs:** 05 (admin section)
**Depends on:** [078-course-models-catalog](078-course-models-catalog.md)
**Blocks:** —

## Scope

- `/admin/courses` list: table of all courses, sortable by date, filterable by status
- `/admin/courses/new` and `/admin/courses/{id}/edit`: course form with title, slug, description, cover image, instructor name/bio, tags, visibility dropdown, is_free checkbox, discussion_url, status
- Nested module editor below course form: add/reorder/delete modules
- Nested unit editor within each module: add/reorder/delete units
- Unit form (inline or modal): title, video_url, body (markdown), homework (markdown), timestamps (list editor with time + label), is_preview checkbox
- Reordering API: `PUT /api/admin/modules/reorder` and `PUT /api/admin/units/reorder` with [{id, sort_order}]
- Admin CRUD endpoints: POST/PUT/DELETE for courses, modules, units

## Acceptance Criteria

- [ ] Admin `/admin/courses` lists all courses, sortable by date, filterable by status (draft/published)
- [ ] Admin can create a course with all fields: title, slug, description, cover image, instructor name/bio, tags, required_level, is_free, discussion_url, status
- [ ] Nested module editor: add, reorder, and delete modules within the course form
- [ ] Nested unit editor within each module: add, reorder, and delete units
- [ ] Unit form includes all fields: title, video_url, body (markdown), homework (markdown), timestamps (list editor with time + label), is_preview checkbox
- [ ] `PUT /api/admin/modules/reorder` and `PUT /api/admin/units/reorder` accept [{id, sort_order}] and persist correctly
- [ ] Admin can change course status (draft → published and vice versa)
- [ ] Deleting a course cascade-deletes its modules and units
