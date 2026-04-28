"""Shared completion-tracking service (issue #365).

Two content types currently expose a "Mark as completed" action:

- :class:`content.models.Unit` (course unit) — toggle persisted in
  :class:`content.models.UserCourseProgress`. Marking complete also
  triggers auto-enrollment (see ``content.services.enrollment``).
- :class:`content.models.WorkshopPage` — toggle persisted in
  :class:`content.models.UserContentCompletion` with
  ``content_type='workshop_page'``.

The service hides those two storage paths behind a single dispatch so
views (course unit endpoint, workshop page endpoint) and the dashboard
widget query share the same primitives. New content types are added by:

1. Extending :data:`content.models.completion.CONTENT_TYPE_CHOICES`.
2. Adding a new branch in :func:`mark_completed`,
   :func:`unmark_completed`, :func:`is_completed`, and
   :func:`completed_ids_for`.
3. Wiring the API endpoint + template button + dashboard query.

The service raises :class:`TypeError` for any unrecognised class —
we never want to silently no-op when a caller hands us a new model
without the read path also being implemented.
"""

from __future__ import annotations

from django.utils import timezone

from content.models import (
    Unit,
    UserContentCompletion,
    UserCourseProgress,
    WorkshopPage,
)
from content.models.completion import CONTENT_TYPE_WORKSHOP_PAGE
from content.services.enrollment import auto_enroll_on_progress


def _require_supported(item):
    if not isinstance(item, (Unit, WorkshopPage)):
        raise TypeError(
            f'Unsupported completion item type: {type(item).__name__}. '
            'Add a dispatch branch in content/services/completion.py '
            'before tracking this content type.'
        )


def mark_completed(user, item, *, when=None):
    """Persist a completion row for ``user`` against ``item``.

    Idempotent — calling twice returns the existing row with the
    original ``completed_at``. Mirrors the existing course-unit toggle
    behaviour where a second click while already-completed does NOT
    refresh the timestamp.

    For :class:`Unit` we also call
    :func:`content.services.enrollment.auto_enroll_on_progress` so the
    user lands on the dashboard's Continue Learning section even if
    they jumped straight into a unit URL without clicking Enroll.
    Workshops have no equivalent enrollment row — the
    :class:`UserContentCompletion` row itself is the implicit "I am
    taking this workshop" signal.
    """
    _require_supported(item)
    when = when or timezone.now()

    if isinstance(item, Unit):
        progress, created = UserCourseProgress.objects.get_or_create(
            user=user, unit=item,
            defaults={'completed_at': when},
        )
        if created:
            # Brand-new row created above with the supplied timestamp;
            # nothing more to do.
            pass
        elif progress.completed_at is None:
            # Row existed (legacy code path) but was not yet marked
            # completed — fill it in.
            progress.completed_at = when
            progress.save(update_fields=['completed_at'])
        # Auto-enroll regardless of whether the row was just created or
        # already existed — ensure_enrollment is itself idempotent.
        auto_enroll_on_progress(user, item.module.course)
        return progress

    # WorkshopPage path.
    completion, _created = UserContentCompletion.objects.get_or_create(
        user=user,
        content_type=CONTENT_TYPE_WORKSHOP_PAGE,
        object_id=item.pk,
        defaults={'completed_at': when},
    )
    return completion


def unmark_completed(user, item) -> bool:
    """Remove the completion row for ``user`` / ``item``.

    Returns True if a row was deleted, False if there was nothing to
    delete. Matches the existing course-unit toggle behaviour: we
    delete rather than null ``completed_at`` so callers can
    round-trip toggle without leaving stale rows.
    """
    _require_supported(item)

    if isinstance(item, Unit):
        deleted, _ = UserCourseProgress.objects.filter(
            user=user, unit=item,
        ).delete()
        return bool(deleted)

    deleted, _ = UserContentCompletion.objects.filter(
        user=user,
        content_type=CONTENT_TYPE_WORKSHOP_PAGE,
        object_id=item.pk,
    ).delete()
    return bool(deleted)


def is_completed(user, item) -> bool:
    """Return True when ``user`` has a completion row for ``item``.

    Returns False for anonymous / None users without hitting the DB.
    """
    if user is None or not getattr(user, 'is_authenticated', False):
        return False
    _require_supported(item)

    if isinstance(item, Unit):
        return UserCourseProgress.objects.filter(
            user=user, unit=item, completed_at__isnull=False,
        ).exists()

    return UserContentCompletion.objects.filter(
        user=user,
        content_type=CONTENT_TYPE_WORKSHOP_PAGE,
        object_id=item.pk,
    ).exists()


def completed_ids_for(user, items) -> set[int]:
    """Batched read: which of ``items`` has the user already completed.

    All items must be of the same supported class — mixing kinds is a
    programming error and raises :class:`TypeError`. Returns the set of
    PKs that have a completion row. Empty set for anonymous users or an
    empty input list.
    """
    if user is None or not getattr(user, 'is_authenticated', False):
        return set()
    items = list(items)
    if not items:
        return set()

    first = items[0]
    _require_supported(first)
    item_class = type(first)
    for item in items[1:]:
        if type(item) is not item_class:
            raise TypeError(
                'completed_ids_for received a mixed list of item types; '
                'call once per kind so the read path stays single-table.'
            )

    ids = [it.pk for it in items]
    if isinstance(first, Unit):
        return set(
            UserCourseProgress.objects.filter(
                user=user, unit_id__in=ids, completed_at__isnull=False,
            ).values_list('unit_id', flat=True)
        )

    return set(
        UserContentCompletion.objects.filter(
            user=user,
            content_type=CONTENT_TYPE_WORKSHOP_PAGE,
            object_id__in=ids,
        ).values_list('object_id', flat=True)
    )
