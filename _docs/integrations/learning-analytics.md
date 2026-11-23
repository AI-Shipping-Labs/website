# Stored course learning activity

`GET /api/courses/{slug}/learning-activity` exposes retained server-side
`analytics.UserActivity` lesson-opening rows to staff tokens. The existing CRM
timeline remains unchanged. The new endpoint has bounded pages and a signed
`next_cursor` so operators can retrieve the entire retained history.

```bash
asl analytics course-activity ai-buildcamp --cohort 4 --all \
  --since 2026-09-20T22:00:00Z --until 2026-11-22T23:00:00Z

asl analytics course-activity ai-buildcamp --cohort 4 --all -f csv > openings.csv
```

## Filters and pagination

| Parameter | Meaning |
|---|---|
| `cohort` | Current enrollment external key, such as `4` |
| `module` | Current module id, including its immediate children |
| `unit` | Current unit id |
| `email` | Primary member email, case-insensitive |
| `since` | Inclusive event time, ISO datetime with timezone |
| `until` | Exclusive event time, ISO datetime with timezone |
| `limit` | Page size, default `100`, maximum `500` |
| `cursor` | Opaque `next_cursor` returned by the preceding page |

Keep all filters unchanged while following the cursor. Rows are ordered by
insertion id descending. The first page fixes the maximum id; subsequent writes
cannot introduce duplicate or skipped rows in that traversal. `occurred_at` is
the event timestamp and can differ from insertion order for backfilled events.
`--all` follows pages sequentially, respecting the normal API client rate-limit
handling. JSON keeps measurement and retention metadata. CSV/table flatten the
event's member, unit, module, current bonus flags and recorded URL.

## What is stored and what is measured

- A `lesson_open` means an authenticated member opened an accessible unit.
  Reopening the same unit within 30 minutes is deduplicated.
- It does not prove that a video started, how long it played, or that the member
  completed the lesson. There is no reconstruction of unrecorded playback.
- Explicit completion remains available separately through
  `asl users course-progress EMAIL --course COURSE`.
- `USER_ACTIVITY_RETENTION_DAYS` is Studio-editable and defaults to `365`.
  The scheduled daily purge deletes older rows. The response reports the active
  retention window; exporting cannot recover purged events or pre-instrumentation
  openings. This change does not alter the existing retention policy.
- Cohort selection uses current enrollment, not enrollment at the event time.
  Date filters are explicit rather than inferred from cohort dates.
- `unit.optional_module` inherits the current module/parent bonus flags;
  `unit.is_bonus` additionally includes individual bonus units in core modules.
  These are current classifications, not a historical syllabus snapshot.
- Deleted units remain visible through their recorded course URL with `unit:
  null`; their old module/bonus classification cannot be inferred reliably.
- This endpoint is read-only and does not record an operator's export as a
  student's learning event. Account deletion and retention can remove rows
  between pages; cursor pagination is not a permanent data snapshot.
