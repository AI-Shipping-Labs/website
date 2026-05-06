"""Member-facing sprint detail + self-join / leave views (issue #443).

Three URLs live here:

- ``GET /sprints/<slug>``: public detail page with one of four CTAs
  (login-to-join, upgrade, join, leave) decided by viewer state.
- ``POST /sprints/<slug>/join``: tier-gated, idempotent self-enrollment.
- ``POST /sprints/<slug>/leave``: idempotent unenrollment that
  auto-privates an existing plan but leaves the plan row intact.

Studio bulk-enroll lives in ``studio/views/sprints_enroll.py``;
the JSON API mirror lives in ``api/views/enrollments.py``.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from content.access import LEVEL_TO_TIER_NAME, get_user_level
from plans.models import Plan, Sprint, SprintEnrollment


def _viewer_plan(sprint, user):
    """Return the viewer's plan in this sprint or None.

    Anonymous viewers always get None (anonymous users never own a plan).
    """
    if user is None or not user.is_authenticated:
        return None
    return Plan.objects.filter(sprint=sprint, member=user).first()


def _is_enrolled(sprint, user):
    """Whether the user has a SprintEnrollment row for this sprint."""
    if user is None or not user.is_authenticated:
        return False
    return SprintEnrollment.objects.filter(sprint=sprint, user=user).exists()


def _resolve_sprint_or_404(slug, user):
    """Look up a sprint by slug, hiding draft sprints from non-staff.

    Mirrors the events surface: a sprint with status=draft is invisible
    to anonymous and non-staff users. Staff can preview the page.
    """
    sprint = get_object_or_404(Sprint, slug=slug)
    if sprint.status == 'draft':
        if not user.is_authenticated or not user.is_staff:
            raise Http404('Sprint not found')
    return sprint


def sprint_detail(request, sprint_slug):
    """Public detail page for a sprint with a tier-aware Join CTA."""
    sprint = _resolve_sprint_or_404(sprint_slug, request.user)

    user = request.user
    is_authenticated = user.is_authenticated
    user_level = get_user_level(user) if is_authenticated else 0
    eligible = user_level >= sprint.min_tier_level
    enrolled = _is_enrolled(sprint, user)
    viewer_plan = _viewer_plan(sprint, user)
    required_tier_name = LEVEL_TO_TIER_NAME.get(sprint.min_tier_level, 'Premium')

    return render(
        request,
        'plans/sprint_detail.html',
        {
            'sprint': sprint,
            'is_authenticated': is_authenticated,
            'enrolled': enrolled,
            'eligible': eligible,
            'viewer_plan': viewer_plan,
            'required_tier_name': required_tier_name,
        },
    )


@login_required
@require_POST
def sprint_join(request, sprint_slug):
    """Self-join a sprint. Tier-gated and idempotent."""
    sprint = _resolve_sprint_or_404(sprint_slug, request.user)

    user_level = get_user_level(request.user)
    required_tier_name = LEVEL_TO_TIER_NAME.get(sprint.min_tier_level, 'Premium')
    if user_level < sprint.min_tier_level:
        messages.error(
            request,
            f'Upgrade to {required_tier_name} to join this sprint.',
        )
        return redirect('/pricing')

    _enrollment, created = SprintEnrollment.objects.get_or_create(
        sprint=sprint,
        user=request.user,
        defaults={'enrolled_by': None},
    )
    if created:
        messages.success(request, f'Welcome to {sprint.name}.')
    else:
        messages.info(request, "You're already enrolled in this sprint.")

    plan = _viewer_plan(sprint, request.user)
    if plan is not None:
        return redirect('my_plan_detail', plan_id=plan.pk)
    return redirect('cohort_board', sprint_slug=sprint.slug)


@login_required
@require_POST
def sprint_leave(request, sprint_slug):
    """Leave a sprint. Auto-privates the user's plan if one exists.

    Idempotent: re-leaving emits an info message and returns the same
    redirect; never 404 / 409.
    """
    sprint = _resolve_sprint_or_404(sprint_slug, request.user)

    with transaction.atomic():
        deleted_count, _ = SprintEnrollment.objects.filter(
            sprint=sprint, user=request.user,
        ).delete()

        if deleted_count:
            plan = _viewer_plan(sprint, request.user)
            if plan is not None and plan.visibility != 'private':
                plan.visibility = 'private'
                plan.save(update_fields=['visibility', 'updated_at'])
            messages.success(request, f'You left {sprint.name}.')
        else:
            messages.info(request, "You weren't enrolled.")

    return redirect('sprint_detail', sprint_slug=sprint.slug)
