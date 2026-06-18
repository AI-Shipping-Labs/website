"""Classification of API DELETE handlers (issue #864).

Single source of truth for the no-deletes-via-API policy. Every view function
decorated with ``require_methods(... "DELETE" ...)`` in ``api/views/`` must be
classified here as either:

- ``FORBIDDEN_DELETE_HANDLERS`` — canonical content; the handler returns HTTP
  405 with a ``*_delete_not_available`` code and a "use Studio" message, or
- ``LEGITIMATE_DELETE_HANDLERS`` — a relationship/attribute removal, a
  soft-delete, a member-owned plan-structure edit, or a sanctioned staff
  working-data hard delete (see ``_docs/api-delete-policy.md``).

``api/tests/test_delete_policy_guard.py`` greps the source and fails CI if a new
DELETE handler appears that is not in either set. This prevents a future change
from silently reintroducing a canonical-content delete via the API.

The values are ``"module.function"`` keys (module is the ``api/views/`` file
stem) so the guard can match the grep output without importing every view.
"""

# Canonical content: DELETE returns 405 "use Studio".
# Maps "module.function" -> expected *_delete_not_available error code.
FORBIDDEN_DELETE_HANDLERS = {
    "events.events_collection": "event_delete_not_available",
    "events.event_detail": "event_delete_not_available",
    "event_series.event_series_collection": "series_delete_not_available",
    "event_series.event_series_detail": "series_delete_not_available",
    "event_series.event_series_occurrence_detail": "occurrence_delete_not_available",
    "sync_sources.sync_sources_collection": "sync_source_delete_not_available",
    "sync_sources.sync_source_trigger": "sync_source_delete_not_available",
    # Human decision (Alexey, 2026-06-13, issue #864): BLOCK delete on all
    # five previously-pending endpoints. Each now returns 405 pointing the
    # operator to Studio. Cancellation/hiding via PATCH stays where it exists.
    "sprints.sprint_detail": "sprint_delete_not_available",
    "course_certificates.course_certificate_detail": "certificate_delete_not_available",
    "redirects.redirect_detail": "redirect_delete_not_available",
    "interview_notes.interview_note_detail": "interview_note_delete_not_available",
    "enrollments.sprint_enrollment_detail": "sprint_enrollment_delete_not_available",
}

# Legitimate deleters. Each entry documents the category (see the doc) so the
# classification is auditable in code review, not just "trust me".
LEGITIMATE_DELETE_HANDLERS = {
    # 2. Relationship / attribute removal
    "aliases.user_aliases_remove": "relationship/attribute removal: EmailAlias mapping",
    "users.user_tags_remove": "relationship/attribute removal: tag from user.tags",
    # 3. Soft-delete
    "course_enrollments.course_enrollment_detail": "soft-delete: sets unenrolled_at",
    # 4. Member-owned plan structure edits
    "plans.plan_detail": "member-owned plan edit: deletes a Plan (staff-only, sprint-guarded)",
    "weeks.week_detail": "member-owned plan edit: deletes a Week, re-packs",
    "weeks.week_note_detail": "member-owned plan edit: clears a singleton WeekNote",
    "checkpoints.checkpoint_detail": "member-owned plan edit: deletes a Checkpoint, re-packs",
    "plan_items.resource_detail": "member-owned plan edit: deletes a plan resource, re-packs",
    "plan_items.deliverable_detail": "member-owned plan edit: deletes a plan deliverable, re-packs",
    "plan_items.next_step_detail": "member-owned plan edit: deletes a plan next-step, re-packs",
}
