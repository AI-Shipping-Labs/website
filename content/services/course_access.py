"""Individual course access grants: the single owner for grant and revoke.

Studio's course access page and ``/api/courses/<slug>/access`` both run
through these helpers, so a grant always records ``granted_by`` (the
audit trail on the ``CourseAccess`` row) and a revoke always removes only
``granted`` rows and records the resulting access loss.

Granting sends no email. Grants are bulk (one read, one insert) so a
whole cohort is granted in a handful of queries.
"""

from __future__ import annotations

from django.db import transaction

from content.models import CourseAccess
from content.services.enrollment import record_course_access_loss

ACCESS_TYPE_GRANTED = 'granted'

GRANT_GRANTED = 'granted'
GRANT_WOULD_GRANT = 'would_grant'
GRANT_ALREADY_HAS_ACCESS = 'already_has_access'

REVOKE_REVOKED = 'revoked'
REVOKE_PURCHASED = 'purchased'
REVOKE_NO_ACCESS = 'no_access'


def grant_course_access(course, users, *, actor=None, dry_run=False):
    """Grant ``granted`` access to ``course`` for every user in ``users``.

    Idempotent per user: anyone who already holds a ``CourseAccess`` row
    (granted or purchased) is left untouched. Returns a dict mapping
    ``user.pk`` to ``(status, access_type)`` where status is ``granted``
    (``would_grant`` on a dry run) or ``already_has_access``, and
    ``access_type`` is the row's type after the call.
    """
    users = {user.pk: user for user in users}
    if not users:
        return {}

    existing = dict(
        CourseAccess.objects.filter(
            course=course, user_id__in=list(users),
        ).values_list('user_id', 'access_type')
    )
    outcome = {
        user_id: (GRANT_ALREADY_HAS_ACCESS, access_type)
        for user_id, access_type in existing.items()
    }
    missing = [user_id for user_id in users if user_id not in existing]
    new_status = GRANT_WOULD_GRANT if dry_run else GRANT_GRANTED
    for user_id in missing:
        outcome[user_id] = (new_status, ACCESS_TYPE_GRANTED)

    if missing and not dry_run:
        # ``ignore_conflicts`` keeps a concurrent grant (Maven webhook,
        # another operator) from failing the whole batch on the
        # ``(user, course)`` unique constraint.
        CourseAccess.objects.bulk_create(
            [
                CourseAccess(
                    user_id=user_id,
                    course=course,
                    access_type=ACCESS_TYPE_GRANTED,
                    granted_by=actor,
                )
                for user_id in missing
            ],
            ignore_conflicts=True,
        )
    return outcome


def revoke_granted_course_access(course, user, *, actor=None):
    """Remove ``user``'s ``granted`` access to ``course``.

    Purchased access is never touched. Returns ``revoked``, ``purchased``
    (a purchased row exists and was kept) or ``no_access``.
    """
    with transaction.atomic():
        access = (
            CourseAccess.objects.select_for_update()
            .filter(course=course, user=user)
            .first()
        )
        if access is None:
            return REVOKE_NO_ACCESS
        if access.access_type != ACCESS_TYPE_GRANTED:
            return REVOKE_PURCHASED
        access.delete()
    record_course_access_loss(user, course, actor=actor)
    return REVOKE_REVOKED
