# 05 - Content: Courses

## Overview

Structured learning: Course → Modules → Units. Each unit has video, text, and homework.

## Data Model

```
Course:
  id: uuid
  slug: string (unique)
  title: string
  description: text               # markdown, shown on course detail page
  cover_image_url: string | null
  instructor_name: string
  instructor_bio: string | null
  required_level: int             # 0-3, see spec 03. 0 = free course
  status: enum                    # "draft", "published"
  is_free: bool                   # true for lead-magnet courses (required_level = 0)
  discussion_url: string | null   # Slack channel URL for paid courses, GitHub URL for free courses
  tags: string[]
  created_at: datetime
  updated_at: datetime

Module:
  id: uuid
  course_id: FK -> Course
  title: string
  sort_order: int                 # 1, 2, 3... determines display order

Unit:
  id: uuid
  module_id: FK -> Module
  title: string
  sort_order: int
  video_url: string | null        # YouTube/Loom URL or self-hosted URL
  body: text                      # markdown, the lesson text
  homework: text | null           # markdown, homework description
  timestamps: jsonb               # [{time_seconds: 120, label: "Setting up the project"}, ...]
  is_preview: bool                # true = visible to everyone regardless of course access (for SEO/conversion)

UserCourseProgress:
  user_id: FK -> User
  unit_id: FK -> Unit
  completed_at: datetime | null   # null = not completed, set = completed
  UNIQUE(user_id, unit_id)
```

### Cohorts (optional overlay)

```
Cohort:
  id: uuid
  course_id: FK -> Course
  name: string                    # e.g. "March 2026 Cohort"
  start_date: date
  end_date: date
  is_active: bool
  max_participants: int | null

CohortEnrollment:
  cohort_id: FK -> Cohort
  user_id: FK -> User
  enrolled_at: datetime
```

## Pages

### `/courses` — Course catalog

- Grid of all published courses. Each card: title, cover image, instructor, tag badges, "Free" badge if `is_free`, required tier badge otherwise
- Visible to everyone (no gating on the catalog page itself)

### `/courses/{slug}` — Course detail

- Always visible to everyone (for SEO). Shows: title, description, instructor bio, full syllabus (module titles + unit titles), tag badges, discussion link
- If user has access: each unit title in the syllabus is a clickable link to the unit page. Shows progress bar (X of Y units completed).
- If user does not have access: unit titles are visible but not clickable. CTA button: "Unlock with {tier_name} — {price}/year" linking to `/pricing`
- If `is_free` and user is not registered: CTA button "Sign up free to start this course" linking to registration

### `/courses/{slug}/{module_sort}/{unit_sort}` — Unit page

- Gated: requires `user.tier.level >= course.required_level` (exception: units with `is_preview = true` are open to all)
- Layout: video player at top (if video_url set, with clickable timestamps), lesson text below, homework section at bottom
- Sidebar: module/unit navigation with checkmarks for completed units
- "Mark as completed" button at the bottom of the unit. Clicking it creates a `UserCourseProgress` record with `completed_at = now()`. Button changes to "Completed ✓" (togglable).
- "Next unit" button at the bottom navigating to the next unit in sort order

## Admin

### `/admin/courses` — Course list

- Table of all courses, sortable by date, filterable by status

### `/admin/courses/new` and `/admin/courses/{id}/edit`

- Course form: title, slug, description, cover image, instructor name/bio, tags, visibility dropdown, is_free checkbox, discussion_url, status
- Below the course form: module editor. Add/reorder/delete modules. Within each module: add/reorder/delete units.
- Unit form (inline or modal): title, video_url, body (markdown), homework (markdown), timestamps (list of time + label pairs), is_preview checkbox

## Requirements

- R-CRS-1: Create `courses`, `modules`, `units`, `user_course_progress` tables with schemas above.
- R-CRS-2: `GET /api/courses` returns all published courses with `is_locked` flag based on requesting user's tier. No pagination needed initially (expect < 50 courses).
- R-CRS-3: `GET /api/courses/{slug}` always returns course detail + full syllabus (module/unit titles). Does NOT return unit body/video for locked courses. Includes `progress` object if user is authenticated: `{completed: 5, total: 12}`.
- R-CRS-4: `GET /api/courses/{slug}/units/{unit_id}` returns full unit content (video_url, body, homework, timestamps) if user has access or unit `is_preview = true`. Returns 403 with `required_tier_name` otherwise.
- R-CRS-5: `POST /api/courses/{slug}/units/{unit_id}/complete` toggles completion. If no `UserCourseProgress` record exists, create one with `completed_at = now()`. If one exists with `completed_at` set, set it to null (uncomplete).
- R-CRS-6: Course detail page renders server-side with `<title>`, `<meta description>`, syllabus visible in HTML (for SEO). Unit content is NOT in the initial HTML for gated units.
- R-CRS-7: Admin CRUD endpoints for courses, modules, and units. Module and unit endpoints support reordering (`PUT /api/admin/modules/reorder` with `[{id, sort_order}]`).

### Cohorts

- R-CRS-8: Create `cohorts` and `cohort_enrollments` tables. A cohort belongs to a course and has start/end dates.
- R-CRS-9: If a course has active cohorts, show them on the course detail page: "Next cohort: {name}, starts {date}". Users with access can enroll in a cohort.
- R-CRS-10: Cohort enrollment is separate from course access. A user must have the required tier to enroll. Enrollment is capped at `max_participants` (if set).

### Nice to Have

- R-CRS-11: Drip schedule: add optional `available_after_days: int` to `Unit`. For cohort enrollees, the unit becomes available `cohort.start_date + available_after_days`. For on-demand, all units are available immediately.
