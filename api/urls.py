"""URL routes for the JSON API.

Routes mounted under ``/api/`` from ``website/urls.py``. This module
hosts the contacts endpoints (issue #431) and the plans-API surface
(issue #433). Every route is JSON-in / JSON-out and gated by
``token_required``.

Path style: no trailing slash. The site-wide
``RemoveTrailingSlashMiddleware`` strips trailing slashes from any
request that doesn't go to admin/accounts/studio, so ``/api/sprints/``
gets 301-redirected to ``/api/sprints``. We register the slashless form
to match the contacts endpoints and skip that redirect on every API
call.
"""

from django.urls import path

from api.views.checkpoints import (
    checkpoint_detail,
    checkpoint_move,
    week_checkpoints_create,
)
from api.views.contacts import (
    contacts_export,
    contacts_import,
    contacts_set_tags,
)
from api.views.course_certificates import (
    course_certificate_detail,
    course_certificates_collection,
)
from api.views.course_enrollments import (
    course_enrollment_detail,
    course_enrollments_collection,
)
from api.views.enrollments import (
    sprint_enrollment_detail,
    sprint_enrollments_collection,
)
from api.views.interview_notes import (
    interview_note_detail,
    interview_notes_create,
    plan_interview_notes,
    user_interview_notes,
)
from api.views.plan_items import (
    deliverable_detail,
    next_step_detail,
    plan_deliverables,
    plan_next_steps,
    plan_resources,
    resource_detail,
)
from api.views.plans import (
    plan_detail,
    sprint_plans_bulk_import,
    sprint_plans_collection,
)
from api.views.ses_events import ses_events
from api.views.sprints import sprint_detail, sprints_collection
from api.views.weeks import plan_weeks_collection, week_detail

urlpatterns = [
    # ---- Contacts (issue #431) ----------------------------------------
    path(
        "contacts/import",
        contacts_import,
        name="api_contacts_import",
    ),
    path(
        "contacts/export",
        contacts_export,
        name="api_contacts_export",
    ),
    # Email contains '@' and '.' which the slug converter doesn't match;
    # use the path converter so the address is captured intact.
    path(
        "contacts/<path:email>/tags",
        contacts_set_tags,
        name="api_contacts_set_tags",
    ),
    # ---- Sprints (issue #433) -----------------------------------------
    path(
        "sprints",
        sprints_collection,
        name="api_sprints_collection",
    ),
    path(
        "sprints/<slug:slug>",
        sprint_detail,
        name="api_sprint_detail",
    ),
    # ---- Sprint enrollments (issue #443) ------------------------------
    # Register the collection BEFORE the per-email detail so the
    # ``enrollments`` literal isn't swallowed by the path converter
    # capturing an email like ``foo@bar.com/extra``.
    path(
        "sprints/<slug:slug>/enrollments",
        sprint_enrollments_collection,
        name="api_sprint_enrollments_collection",
    ),
    path(
        "sprints/<slug:slug>/enrollments/<path:email>",
        sprint_enrollment_detail,
        name="api_sprint_enrollment_detail",
    ),
    # ---- Course enrollments (issue #445) ------------------------------
    # Register the collection BEFORE the per-email detail so the
    # ``enrollments`` literal isn't swallowed by the path converter
    # capturing an email like ``alice@example.com/extra``.
    path(
        "courses/<slug:slug>/enrollments",
        course_enrollments_collection,
        name="api_course_enrollments_collection",
    ),
    path(
        "courses/<slug:slug>/enrollments/<path:email>",
        course_enrollment_detail,
        name="api_course_enrollment_detail",
    ),
    # ---- Course certificates (issue #445) -----------------------------
    path(
        "courses/<slug:slug>/certificates",
        course_certificates_collection,
        name="api_course_certificates_collection",
    ),
    path(
        "courses/<slug:slug>/certificates/<path:email>",
        course_certificate_detail,
        name="api_course_certificate_detail",
    ),
    # ---- Plans (issue #433) -------------------------------------------
    # Bulk-import comes BEFORE the generic plans collection so the
    # ``bulk-import`` literal does not collide with the slug captures.
    path(
        "sprints/<slug:slug>/plans/bulk-import",
        sprint_plans_bulk_import,
        name="api_sprint_plans_bulk_import",
    ),
    path(
        "sprints/<slug:slug>/plans",
        sprint_plans_collection,
        name="api_sprint_plans_collection",
    ),
    path(
        "plans/<int:plan_id>",
        plan_detail,
        name="api_plan_detail",
    ),
    # ---- Weeks (issue #433) -------------------------------------------
    path(
        "plans/<int:plan_id>/weeks",
        plan_weeks_collection,
        name="api_plan_weeks_collection",
    ),
    path(
        "weeks/<int:week_id>",
        week_detail,
        name="api_week_detail",
    ),
    # ---- Checkpoints (issue #433) -------------------------------------
    path(
        "weeks/<int:week_id>/checkpoints",
        week_checkpoints_create,
        name="api_week_checkpoints_create",
    ),
    path(
        "checkpoints/<int:checkpoint_id>/move",
        checkpoint_move,
        name="api_checkpoint_move",
    ),
    path(
        "checkpoints/<int:checkpoint_id>",
        checkpoint_detail,
        name="api_checkpoint_detail",
    ),
    # ---- Resources / Deliverables / NextSteps (issue #433) ------------
    path(
        "plans/<int:plan_id>/resources",
        plan_resources,
        name="api_plan_resources",
    ),
    path(
        "resources/<int:item_id>",
        resource_detail,
        name="api_resource_detail",
    ),
    path(
        "plans/<int:plan_id>/deliverables",
        plan_deliverables,
        name="api_plan_deliverables",
    ),
    path(
        "deliverables/<int:item_id>",
        deliverable_detail,
        name="api_deliverable_detail",
    ),
    path(
        "plans/<int:plan_id>/next-steps",
        plan_next_steps,
        name="api_plan_next_steps",
    ),
    path(
        "next-steps/<int:item_id>",
        next_step_detail,
        name="api_next_step_detail",
    ),
    # ---- Interview notes (issue #433) ---------------------------------
    path(
        "plans/<int:plan_id>/interview-notes",
        plan_interview_notes,
        name="api_plan_interview_notes",
    ),
    path(
        "users/<path:email>/interview-notes",
        user_interview_notes,
        name="api_user_interview_notes",
    ),
    path(
        "users/<path:email>/notes",
        user_interview_notes,
        name="api_user_member_notes",
    ),
    path(
        "users/<path:email>/notes/",
        user_interview_notes,
    ),
    path(
        "interview-notes",
        interview_notes_create,
        name="api_interview_notes_create",
    ),
    path(
        "member-notes",
        interview_notes_create,
        name="api_member_notes_create",
    ),
    path(
        "member-notes/",
        interview_notes_create,
    ),
    path(
        "interview-notes/<int:note_id>",
        interview_note_detail,
        name="api_interview_note_detail",
    ),
    path(
        "member-notes/<int:note_id>",
        interview_note_detail,
        name="api_member_note_detail",
    ),
    path(
        "member-notes/<int:note_id>/",
        interview_note_detail,
    ),
    # ---- SES bounce / complaint webhook (issue #453) ------------------
    # SNS POSTs notifications here. Auth is the SNS signature, not a
    # token (SNS doesn't carry one). The slashless form is canonical so
    # the trailing-slash middleware doesn't 301 the POST.
    path(
        "ses-events",
        ses_events,
        name="api_ses_events",
    ),
]
