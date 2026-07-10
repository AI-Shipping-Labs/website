# API DELETE policy (no-deletes-via-API for canonical content)

Issue #864. This document is the authoritative contract for what the JSON API
under `api/views/` may delete, and what it must refuse.

## The policy, stated precisely

Studio-authored canonical content must NOT be hard-deletable through the public
API. For those resources the API accepts the `DELETE` method but returns HTTP
`405` with a structured `*_delete_not_available` error code and a message
pointing the operator to Studio. Cancellation / hiding is done via `PATCH`
(e.g. `status=cancelled`, `is_active=false`).

The policy is NOT "no `DELETE` method may ever delete anything". It specifically
protects the canonical content catalog. Several categories of `DELETE` are
legitimate and stay.

## Categories

### 1. 405-protected — canonical content (refuse with "use Studio")

These return `405` with a `*_delete_not_available` code. Deletion happens in
Studio so ownership and sync rules stay explicit.

| Endpoint (view) | Error code |
| --- | --- |
| `api/views/events.py` (`events_collection`, `event_detail`) | `event_delete_not_available` |
| `api/views/marketing_pages.py` (`marketing_pages_collection`, `marketing_page_detail`) | `marketing_page_delete_not_available` |
| `api/views/event_series.py` (`event_series_collection`, `event_series_detail`) | `series_delete_not_available` |
| `api/views/event_series.py` (`event_series_occurrence_detail`) | `occurrence_delete_not_available` |
| `api/views/sync_sources.py` (`sync_sources_collection`, `sync_source_trigger`) | `sync_source_delete_not_available` |
| `api/views/sprints.py` (`sprint_detail`) | `sprint_delete_not_available` |
| `api/views/course_certificates.py` (`course_certificate_detail`) | `certificate_delete_not_available` |
| `api/views/redirects.py` (`redirect_detail`) | `redirect_delete_not_available` |
| `api/views/interview_notes.py` (`interview_note_detail`) | `interview_note_delete_not_available` |
| `api/views/enrollments.py` (`sprint_enrollment_detail`) | `sprint_enrollment_delete_not_available` |

The last five were added per the human decision (Alexey, 2026-06-13, issue #864)
to block delete on all five previously-pending endpoints. See the rationale
table below.

### 2. Relationship / attribute removal (legitimate — keep)

Removing a join row or a value from a list, where the underlying account or
content survives. Idempotent and audited where applicable.

| Endpoint (view) | What it removes |
| --- | --- |
| `api/views/aliases.py` (`user_aliases_remove`) | one `EmailAlias` mapping; account untouched |
| `api/views/users.py` (`user_tags_remove`) | one tag from the `user.tags` JSON list |
| `api/views/sprints.py` (`sprint_accountability_partners`) | reciprocal `SprintAccountabilityPartner` assignment edges between two enrolled sprint members; sprint and users untouched |

### 3. Soft-delete (legitimate — keep)

Marks a record inactive / unenrolled but preserves history; not a hard delete.

| Endpoint (view) | What it does |
| --- | --- |
| `api/views/course_enrollments.py` (`course_enrollment_detail`) | sets `unenrolled_at` (soft) |

### 4. Member-owned plan structure edits (legitimate — keep)

A member (or staff on their behalf) editing their own learning Plan. The plan
editor (`studio/views/plans.py` docstring) is explicitly designed so every
write, INCLUDING add/delete, goes through the JSON API; blocking `DELETE` here
would break the editor. All are gated by `visible_plans_for(user)`.

| Endpoint (view) | What it deletes |
| --- | --- |
| `api/views/plans.py` (`plan_detail`) | a member's `Plan` (staff-only; 409 if sprint guard applies) |
| `api/views/weeks.py` (`week_detail`) | a `Week`, re-packs siblings |
| `api/views/weeks.py` (`week_note_detail`) | the singleton participant `WeekNote` for a week |
| `api/views/checkpoints.py` (`checkpoint_detail`) | a `Checkpoint`, re-packs |
| `api/views/plan_items.py` (`resource_detail`, `deliverable_detail`, `next_step_detail`) | a plan item, re-packs |

### 5. Previously-pending endpoints — now 405-protected (human decision)

Five endpoints were flagged as borderline during the original audit and left as
hard-deletable pending a per-resource human decision. On 2026-06-13 Alexey
decided to BLOCK delete on all five: each now returns `405` pointing the
operator to Studio (listed in the 405-protected table above). Cancellation /
hiding via `PATCH` stays available where it already existed (e.g. a redirect can
still be deactivated with `PATCH is_active=false`; a sprint's status can still
be changed with `PATCH status=...`); only the hard-`DELETE` is blocked.

| Endpoint (view) | Decision rationale (Alexey, 2026-06-13) |
| --- | --- |
| `api/views/sprints.py` (`sprint_detail`) | forbid — treat sprints like canonical content; delete only in Studio |
| `api/views/course_certificates.py` (`course_certificate_detail`) | forbid — a granted credential must not be hard-revoked via the API; revoke in Studio |
| `api/views/redirects.py` (`redirect_detail`) | forbid — Studio already has a redirect-delete UI; deactivate via PATCH `is_active=false` |
| `api/views/interview_notes.py` (`interview_note_detail`) | forbid — delete notes in Studio, not via the API |
| `api/views/enrollments.py` (`sprint_enrollment_detail`) | forbid — unenroll in Studio, not via the API |

## The guard

`api/tests/test_delete_policy_guard.py` enforces this contract:

1. A parametrised test asserts every FORBIDDEN route returns `405` with the
   correct `*_delete_not_available` code.
2. A meta-test greps `api/views/` for `require_methods(... DELETE ...)` and
   asserts every such handler is either on the legitimate-deleter allow-list
   (`api/delete_policy.py`) OR is 405-protected. Adding a new, unclassified
   `DELETE` handler fails CI until it is classified — so a violation cannot be
   silently reintroduced.

Current classification (issue #864, after the 2026-06-13 human decision, plus
issue #1045's singleton week-note clear route and issue #1123's accountability
partner assignment removal): 12 forbidden (405-protected) + 11 legitimate = 23
`DELETE` handlers in `api/views/`.

When you add or change a `DELETE` handler, update both this document and the
classification in `api/delete_policy.py`.
