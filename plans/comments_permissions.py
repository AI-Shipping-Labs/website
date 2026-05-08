"""Permission hook that lets the existing comments app safely host plan threads.

Issue #499 reuses ``comments.Comment`` / ``CommentVote`` / the existing
``/api/comments/<uuid:content_id>`` endpoints for plan discussion. The
gating rules differ from course / workshop comments though, so we
expose a single helper here that the comments API consults whenever a
``content_id`` happens to resolve to a ``Plan.comment_content_id``.

Rules implemented (matched 1:1 with the issue ACs):

- Anonymous viewers cannot read or write a plan thread. The existing
  401-on-write behaviour is preserved; reads return 404 (never expose
  whether a private plan exists).
- Reads require the same plan visibility predicate the member-plan
  view already uses: owner OR (cohort visibility AND viewer enrolled
  in the same sprint) OR staff.
- Writes (create top-level / reply / vote) require everything reads
  do, PLUS:
    - on a private plan, only staff can write.
    - on a cohort plan, anyone who can read can write (i.e. the
      owner and any sprint-mate).

The public surface is two functions:

- :func:`resolve_plan_for_content_id` — takes the URL ``content_id``
  and returns ``(plan, kind)`` where ``kind`` is ``'plan'`` if the
  UUID matches a plan and ``None`` otherwise. Non-plan UUIDs (course
  units, workshop pages) fall through to the existing comments
  behaviour unchanged.
- :func:`viewer_can_read_plan_thread`,
  :func:`viewer_can_write_plan_thread` — the boolean predicates the
  comments API calls.

This file deliberately lives in the ``plans`` app rather than the
``comments`` app: comment access policy is a plan domain concern, and
the comments app must stay generic so it can host threads for other
content kinds without learning about plans.
"""

from __future__ import annotations

from typing import Optional

from plans.models import Plan, SprintEnrollment


def resolve_plan_for_content_id(content_id) -> Optional[Plan]:
    """Return the :class:`Plan` whose ``comment_content_id`` is ``content_id``.

    Returns ``None`` when the UUID is not a plan thread (e.g. it
    belongs to a course unit or workshop page). Callers must treat
    ``None`` as "fall through to the default comments behaviour", not
    as an error.
    """
    if content_id is None:
        return None
    return (
        Plan.objects
        .filter(comment_content_id=content_id)
        .select_related('sprint', 'member')
        .first()
    )


def _is_authenticated(user) -> bool:
    return user is not None and getattr(user, 'is_authenticated', False)


def _is_staff(user) -> bool:
    return _is_authenticated(user) and bool(getattr(user, 'is_staff', False))


def _viewer_can_view_plan(plan: Plan, viewer) -> bool:
    """Mirror ``Plan.objects.visible_to_member`` plus a staff bypass.

    Staff have read access to every plan -- their Studio-side surface
    needs to render the same comment thread as the member-facing page.
    Non-staff viewers must satisfy the member visibility predicate
    (owner or sprint-mate on a cohort plan).
    """
    if not _is_authenticated(viewer):
        return False
    if _is_staff(viewer):
        return True
    if plan.member_id == viewer.id:
        return True
    if plan.visibility == 'cohort':
        return SprintEnrollment.objects.filter(
            sprint_id=plan.sprint_id,
            user=viewer,
        ).exists()
    return False


def viewer_can_read_plan_thread(plan: Plan, viewer) -> bool:
    """Whether ``viewer`` may GET ``/api/comments/<plan.comment_content_id>``."""
    return _viewer_can_view_plan(plan, viewer)


def viewer_can_write_plan_thread(plan: Plan, viewer) -> bool:
    """Whether ``viewer`` may POST top-level / reply / vote on this plan thread.

    A plan that is ``private`` accepts comments only from staff. A
    plan that is ``cohort`` accepts comments from anyone who can
    already view it (owner + sprint-mates + staff). Anonymous viewers
    can never write, regardless of plan state.
    """
    if not _is_authenticated(viewer):
        return False
    if not _viewer_can_view_plan(plan, viewer):
        return False
    if plan.visibility == 'private':
        return _is_staff(viewer)
    return True


def composer_state_for_owner_view(plan: Plan, viewer) -> tuple[bool, str]:
    """Return ``(disabled, reason)`` for the owner-page comment composer.

    The owner page renders the comments thread regardless of plan
    visibility; what changes is whether the composer textarea is
    enabled. This helper centralises the decision so the view file
    in :mod:`plans.views.cohort` can stay free of inlined
    visibility/staff branching (the regression test in
    ``plans/tests/test_view_layer_no_visibility_literals.py``
    forbids that). Cohort plans always allow the owner to comment;
    private plans hide the composer for non-staff owners with the
    "open the plan to your cohort" hint copy.
    """
    if viewer_can_write_plan_thread(plan, viewer):
        return False, ''
    return True, (
        'Open this plan to your cohort to discuss it with teammates. '
        'Staff can still post comments while the plan is private.'
    )
