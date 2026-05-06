"""Queryset-layer permission gates for the plans API (issue #433).

The plans API is the only file in ``api/views/`` that may inspect
``user.is_staff``. View modules import ``visible_plans_for`` /
``visible_interview_notes_for`` and compose every list and detail
lookup against the returned QuerySet so an attacker cannot trick a
view into reading a row outside the bearer's permission scope.

Why this matters: a templating-layer or serializer-layer ``is_staff``
check is easy to forget on a new endpoint. A queryset-layer gate is
unforgeable -- if a view forgets to use it, the QuerySet still returns
zero rows for the bearer and the leak is impossible.

Tests in ``api/tests/test_interview_notes.py`` scan
``api/views/interview_notes.py`` for the literal string ``is_staff``
and assert ZERO occurrences -- the only file in this app that may
mention the attribute is this one.
"""

from plans.models import InterviewNote, Plan


def _is_staff(user):
    """Internal helper -- the only place outside tests that reads
    ``user.is_staff``. Kept private so callers go through the queryset
    helpers below.
    """
    return bool(user is not None and getattr(user, "is_staff", False))


def bearer_is_admin(user):
    """Public predicate for view-level branches that genuinely need it
    (e.g. ``POST /api/sprints/`` is staff-only at the entry point, not
    just at the queryset boundary).

    Every other gate must compose against the queryset helpers below.
    """
    return _is_staff(user)


def visible_plans_for(user):
    """Plans the token bearer can read.

    Staff: every plan. Non-staff: only plans where they are the member.
    Anonymous / ``None``: an empty queryset (defence in depth -- the
    decorator should already have rejected the request before any view
    composes against this).
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return Plan.objects.none()
    if _is_staff(user):
        return Plan.objects.all()
    return Plan.objects.filter(member=user)


def writable_plans_for(user):
    """Plans the token bearer can mutate.

    For now, identical to ``visible_plans_for``: any plan you can read
    you can also edit. Kept as a separate helper so a future v2 read-only
    member token can tighten this without touching every view.
    """
    return visible_plans_for(user)


def visible_interview_notes_for(user):
    """Interview notes the token bearer can read.

    Staff: every note. Non-staff: only ``external`` notes attached to
    a plan they own (or notes with no plan whose ``member`` is them and
    visibility is ``external`` -- the inbox case for the user's own
    email).
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return InterviewNote.objects.none()
    if _is_staff(user):
        return InterviewNote.objects.all()
    return InterviewNote.objects.filter(
        member=user,
        visibility="external",
    )


def bearer_sees_internal_notes(user):
    """Return True iff the bearer can read internal-visibility notes.

    Equivalent to ``bearer_is_admin(user)`` today, but exposed as a
    separate helper so view modules can ask a high-level question
    (instead of inspecting ``user.is_staff`` themselves) and the answer
    can change shape later (e.g. v2 staff-delegated tokens that grant
    internal-note read access without full staff privileges).
    """
    return _is_staff(user)
