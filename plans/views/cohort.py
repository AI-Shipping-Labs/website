"""Member-facing cohort board and individual plan views (issue #440).

Visibility enforcement lives at the :class:`plans.models.PlanQuerySet`
layer, not in this file. The view bodies here MUST NOT branch on
staff status or compare a plan's visibility to literal strings;
instead they call the queryset helpers and trust the result. A
regression test
(``plans/tests/test_view_layer_no_visibility_literals.py``) reads this
file as a string and rejects forbidden patterns -- if a future change
needs a staff bypass it MUST be expressed via a queryset method on
:class:`plans.models.Plan`.

Internal interview notes never render on member-facing pages in this
issue. The interview-note model is deliberately NOT imported here.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from accounts.models import Token
from plans.cohort_rows import build_progress_rows
from plans.models import PLAN_VISIBILITY_CHOICES, Plan, Sprint, SprintEnrollment

# Tuple of valid visibility values posted by the toggle form. We accept
# only the active enum members from ``PLAN_VISIBILITY_CHOICES`` -- a
# ``public`` POST (the future-reserved value) must round-trip as a 400
# until a separate later issue widens the enum.
_VALID_VISIBILITY_VALUES = frozenset(
    value for value, _label in PLAN_VISIBILITY_CHOICES
)


def _annotated_plans(queryset):
    """Annotate plans with checkpoint progress counts for cohort cards.

    ``progress_total`` is the count of all checkpoints across all weeks
    on the plan; ``progress_done`` is the subset where ``done_at`` is
    set. Both are computed in a single SQL query via ``Count`` so the
    cohort board does not N+1 over weeks/checkpoints.
    """
    return queryset.annotate(
        progress_total=Count('weeks__checkpoints', distinct=True),
        progress_done=Count(
            'weeks__checkpoints',
            filter=Q(weeks__checkpoints__done_at__isnull=False),
            distinct=True,
        ),
    )


def _viewer_plan_for_sprint(sprint, user):
    """Return the viewer's own plan in ``sprint`` or ``None``."""
    return (
        Plan.objects.filter(sprint=sprint, member=user)
        .select_related('sprint')
        .first()
    )


@login_required
def cohort_board(request, sprint_slug):
    """Sprint cohort progress board (issue #461).

    Anonymous users hit ``login_required``. An authenticated user who
    is NOT enrolled in the sprint (no ``SprintEnrollment`` row of their
    own) gets 404 rather than 403 -- 404 keeps sprint membership opaque
    to outsiders. Membership is derived from ``SprintEnrollment`` (issue
    #443), not plan-existence; an enrolled viewer with no plan still
    sees the board (the viewer-plan panel renders a "plan being
    prepared" placeholder).

    The board renders one row per enrolled member, classified by
    :func:`plans.cohort_rows.build_progress_rows` into ``cohort``
    (clickable card with focus + weeks), ``private`` (counts-only stub,
    non-clickable), and ``no_plan`` (em-dash, "No plan yet" caption).
    Pagination is a future concern when sprints exceed roughly 50
    members; current cohorts are 5-30 so a single page is fine.
    """
    sprint = get_object_or_404(Sprint, slug=sprint_slug)
    viewer_enrolled = SprintEnrollment.objects.filter(
        sprint=sprint, user=request.user,
    ).exists()
    if not viewer_enrolled:
        # Not enrolled -> 404. Visible only to members of this sprint.
        raise Http404('Not enrolled in this sprint')

    viewer_plan = _viewer_plan_for_sprint(sprint, request.user)

    plans = list(
        Plan.objects.cohort_progress_rows(
            sprint=sprint, viewer=request.user,
        )
        .select_related('member')
        .prefetch_related('weeks'),
    )

    # Enrolled members who have NOT authored a plan in this sprint --
    # rendered as ``no_plan`` rows pinned to the bottom of the list.
    no_plan_members = [
        enrollment.user
        for enrollment in (
            sprint.enrollments
            .exclude(user__plans__sprint=sprint)
            .select_related('user')
        )
    ]

    progress_rows = build_progress_rows(
        plans=plans,
        no_plan_members=no_plan_members,
        viewer=request.user,
    )

    if viewer_plan is not None:
        viewer_plan = _annotated_plans(
            Plan.objects.filter(pk=viewer_plan.pk),
        ).select_related('sprint').first()

    return render(
        request,
        'plans/cohort_board.html',
        {
            'sprint': sprint,
            'progress_rows': progress_rows,
            'viewer_plan': viewer_plan,
        },
    )


@login_required
def member_plan_detail(request, sprint_slug, plan_id):
    """Read-only individual plan view scoped under a sprint.

    The owner is redirected to the editable :func:`my_plan_detail` so
    the visibility toggle is reachable. Any other viewer must satisfy
    :meth:`PlanQuerySet.visible_to_member` -- non-enrolled users and
    private-plan teammates get 404.
    """
    sprint = get_object_or_404(Sprint, slug=sprint_slug)
    plan = get_object_or_404(
        Plan.objects.visible_to_member(
            plan_id=plan_id, viewer=request.user,
        )
        .filter(sprint=sprint)
        .select_related('member', 'sprint')
        .prefetch_related('weeks__checkpoints', 'resources', 'deliverables', 'next_steps'),
    )

    if plan.member_id == request.user.id:
        return redirect('my_plan_detail', plan_id=plan.pk)

    return render(
        request,
        'plans/member_plan_detail.html',
        {
            'sprint': sprint,
            'plan': plan,
        },
    )


@login_required
def my_plan_detail(request, plan_id):
    """The member's own plan, with the visibility toggle UI.

    Owner-only. Any other authenticated user gets 404 (per spec, 404
    not 403 -- avoids leaking the existence of plan IDs).
    """
    plan = get_object_or_404(
        Plan.objects.filter(pk=plan_id, member=request.user)
        .select_related('member', 'sprint')
        .prefetch_related('weeks__checkpoints', 'resources', 'deliverables', 'next_steps'),
    )
    progress = plan.weeks.aggregate(
        total=Count('checkpoints'),
        done=Count('checkpoints', filter=Q(checkpoints__done_at__isnull=False)),
    )
    token, _created = Token.objects.get_or_create(
        user=request.user,
        name='member-plan-editor',
    )

    return render(
        request,
        'plans/my_plan_detail.html',
        {
            'sprint': plan.sprint,
            'plan': plan,
            'api_base': '/api/',
            'api_token': token.key,
            'plan_can_edit': True,
            'plan_progress_done': progress['done'],
            'plan_progress_total': progress['total'],
            'visibility_choices': PLAN_VISIBILITY_CHOICES,
        },
    )


@login_required
@require_POST
def update_plan_visibility(request, plan_id):
    """Owner-only POST endpoint that flips the plan's visibility.

    Accepts only values from ``PLAN_VISIBILITY_CHOICES``. Anything else
    (including the future-reserved ``public`` value) is rejected with
    HTTP 400 and the row is left unchanged. Non-owners get 404 and the
    row is unchanged. Anonymous users are redirected to login by the
    ``login_required`` decorator before any side effect.
    """
    plan = get_object_or_404(
        Plan.objects.filter(pk=plan_id, member=request.user),
    )

    raw = request.POST.get('visibility')
    if raw not in _VALID_VISIBILITY_VALUES:
        return HttpResponseBadRequest(
            'Invalid visibility value.',
        )

    plan.visibility = raw
    plan.save(update_fields=['visibility', 'updated_at'])
    messages.success(
        request,
        'Plan visibility updated.',
    )
    return redirect(reverse('my_plan_detail', kwargs={'plan_id': plan.pk}))
