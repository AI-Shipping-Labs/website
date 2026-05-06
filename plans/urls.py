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
    update_plan_visibility,
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
    path(
        'sprints/<slug:sprint_slug>/plans/<int:plan_id>',
        member_plan_detail,
        name='member_plan_detail',
    ),
    path(
        'account/plan/<int:plan_id>',
        my_plan_detail,
        name='my_plan_detail',
    ),
    path(
        'account/plan/<int:plan_id>/visibility',
        update_plan_visibility,
        name='update_plan_visibility',
    ),
]
