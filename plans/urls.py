"""Member-facing URL routes for the plans app (issue #440).

Staff Studio routes from #432 stay in ``studio/urls.py``; this module
only exposes the cohort board and the member's own plan views. Mounted
at the project root in ``website/urls.py``.
"""

from django.urls import path

from plans.views.cohort import (
    cohort_board,
    member_plan_detail,
    my_plan_detail,
    my_plan_edit_redirect,
    update_plan_visibility,
)
from plans.views.notes import (
    week_note_create,
    week_note_delete,
    week_note_update,
)
from plans.views.sprints import (
    sprint_ask_team,
    sprint_detail,
    sprint_join,
    sprint_leave,
)

urlpatterns = [
    # ``/sprints/...`` paths are NOT in
    # ``RemoveTrailingSlashMiddleware.SKIP_PREFIXES`` -- defining them
    # without a trailing slash matches the project convention. The
    # ``/account/...`` paths ARE in SKIP_PREFIXES, so trailing slashes
    # are preserved there to match the existing ``/account/profile``
    # style used by ``accounts.urls``.
    path(
        'sprints/<slug:sprint_slug>/board',
        cohort_board,
        name='cohort_board',
    ),
    # Sprint join / leave (issue #443). Registered BEFORE the generic
    # ``<slug:sprint_slug>`` detail route so the literal ``join`` /
    # ``leave`` segments are not swallowed by the slug capture.
    path(
        'sprints/<slug:sprint_slug>/join',
        sprint_join,
        name='sprint_join',
    ),
    path(
        'sprints/<slug:sprint_slug>/leave',
        sprint_leave,
        name='sprint_leave',
    ),
    # "Ask the team to plan with me" ping (issue #585). Same ordering
    # rule as ``join`` / ``leave`` -- registered BEFORE the generic
    # ``<slug:sprint_slug>`` detail route so the literal ``ask-team``
    # segment is not swallowed by the slug capture.
    path(
        'sprints/<slug:sprint_slug>/ask-team',
        sprint_ask_team,
        name='sprint_ask_team',
    ),
    path(
        'sprints/<slug:sprint_slug>/plans/<int:plan_id>',
        member_plan_detail,
        name='member_plan_detail',
    ),
    path(
        'sprints/<slug:sprint_slug>/plan/<int:plan_id>',
        my_plan_detail,
        name='my_plan_detail',
    ),
    # Issue #583: the unified workspace at my_plan_detail handles both
    # view and inline edit, so the dedicated /edit URL now permanently
    # redirects to the workspace. Keep the route + name so old bookmarks
    # and template ``{% url 'my_plan_edit' %}`` references still resolve.
    path(
        'sprints/<slug:sprint_slug>/plan/<int:plan_id>/edit',
        my_plan_edit_redirect,
        name='my_plan_edit',
    ),
    path(
        'sprints/<slug:sprint_slug>/plan/<int:plan_id>/visibility',
        update_plan_visibility,
        name='update_plan_visibility',
    ),
    # Participant week notes (issue #499), now scoped to the sprint
    # workspace URL so form submits keep members in sprint context.
    path(
        'sprints/<slug:sprint_slug>/plan/<int:plan_id>/weeks/<int:week_id>/notes',
        week_note_create,
        name='week_note_create',
    ),
    path(
        'sprints/<slug:sprint_slug>/plan/<int:plan_id>/week-notes/<int:note_id>',
        week_note_update,
        name='week_note_update',
    ),
    path(
        'sprints/<slug:sprint_slug>/plan/<int:plan_id>/week-notes/<int:note_id>/delete',
        week_note_delete,
        name='week_note_delete',
    ),
    path(
        'sprints/<slug:sprint_slug>',
        sprint_detail,
        name='sprint_detail',
    ),
]
