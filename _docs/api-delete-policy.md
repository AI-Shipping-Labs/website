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
| `api/views/event_series.py` (`event_series_collection`, `event_series_detail`) | `series_delete_not_available` |
| `api/views/event_series.py` (`event_series_occurrence_detail`) | `occurrence_delete_not_available` |
| `api/views/sync_sources.py` (`sync_sources_collection`, `sync_source_trigger`) | `sync_source_delete_not_available` |

### 2. Relationship / attribute removal (legitimate — keep)

Removing a join row or a value from a list, where the underlying account or
content survives. Idempotent and audited where applicable.

| Endpoint (view) | What it removes |
| --- | --- |
| `api/views/aliases.py` (`user_aliases_remove`) | one `EmailAlias` mapping; account untouched |
| `api/views/users.py` (`user_tags_remove`) | one tag from the `user.tags` JSON list |
| `api/views/enrollments.py` (`sprint_enrollment_detail`) | a `SprintEnrollment` membership row (auto-privates the plan) |

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
| `api/views/checkpoints.py` (`checkpoint_detail`) | a `Checkpoint`, re-packs |
| `api/views/plan_items.py` (`resource_detail`, `deliverable_detail`, `next_step_detail`) | a plan item, re-packs |

### 5. Staff working-data hard deletes (legitimate — keep, per human decision)

These hard-delete persistent records that are also editable in Studio. They are
operational config or mutable staff working data, not member-facing canonical
content, and each has a Studio delete counterpart and/or a guard. Kept as
hard-deletable per the per-resource decision recorded in issue #864.

| Endpoint (view) | Rationale |
| --- | --- |
| `api/views/redirects.py` (`redirect_detail`) | operational config; Studio already has a redirect-delete UI |
| `api/views/interview_notes.py` (`interview_note_detail`) | mutable staff working data; Studio deletes notes too |
| `api/views/sprints.py` (`sprint_detail`) | staff-only; 409-guarded if the sprint has attached plans; API is the only delete path |
| `api/views/course_certificates.py` (`course_certificate_detail`) | staff-only credential management |

## The guard

`api/tests/test_delete_policy_guard.py` enforces this contract:

1. A parametrised test asserts every FORBIDDEN route returns `405` with the
   correct `*_delete_not_available` code.
2. A meta-test greps `api/views/` for `require_methods(... DELETE ...)` and
   asserts every such handler is either on the legitimate-deleter allow-list
   (`api/delete_policy.py`) OR is 405-protected. Adding a new, unclassified
   `DELETE` handler fails CI until it is classified — so a violation cannot be
   silently reintroduced.

When you add or change a `DELETE` handler, update both this document and the
classification in `api/delete_policy.py`.
