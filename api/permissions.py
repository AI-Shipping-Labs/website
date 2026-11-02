"""Queryset access rules shared by JSON API endpoints."""

from api.safety import error_response
from plans.models import InterviewNote, Plan, Week


def _is_staff(user):
    """Keep direct staff checks inside this permission owner."""
    return bool(user is not None and getattr(user, "is_staff", False))


def bearer_is_admin(user):
    """Return whether the bearer may perform staff-only API actions."""
    return _is_staff(user)


def visible_plans_for(user):
    """Return plans visible to a staff bearer or the plan's member."""
    if user is None or not getattr(user, "is_authenticated", False):
        return Plan.objects.none()
    if _is_staff(user):
        return Plan.objects.all()
    return Plan.objects.filter(member=user)


def writable_plans_for(user):
    """Return plans the bearer can edit; currently the visible plan set."""
    return visible_plans_for(user)


def visible_interview_notes_for(user):
    """Return notes visible to staff or external notes owned by the bearer."""
    if user is None or not getattr(user, "is_authenticated", False):
        return InterviewNote.objects.none()
    if _is_staff(user):
        return InterviewNote.objects.all()
    return InterviewNote.objects.filter(
        member=user,
        visibility="external",
    )


def bearer_sees_internal_notes(user):
    """Return whether the bearer can read internal interview notes."""
    return _is_staff(user)


def plan_for_write(user, plan_id):
    """Return ``(plan, error)`` for a plan visible to this API bearer."""
    plan = visible_plans_for(user).filter(pk=plan_id).first()
    if plan is None:
        return None, error_response(
            "Plan not found",
            "unknown_plan",
            status=404,
        )
    return plan, None


def week_for_write(user, week_id):
    """Return ``(week, error)`` for a week in a plan visible to the bearer."""
    week = (
        Week.objects.select_related("plan", "plan__member")
        .filter(pk=week_id)
        .first()
    )
    if week is None or not visible_plans_for(user).filter(pk=week.plan_id).exists():
        return None, error_response(
            "Week not found",
            "unknown_week",
            status=404,
        )
    return week, None
