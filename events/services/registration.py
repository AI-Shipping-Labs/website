"""Conflict-safe EventRegistration / SeriesRegistration inserts (issue #1518).

Member-facing register paths and series fan-out used to check-then-create.
Two overlapping requests can both observe "not registered" and both attempt
the unique insert; the loser then raised ``IntegrityError`` as HTTP 500.

These helpers always attempt the insert inside a savepoint, treat a unique
collision as ``created=False``, and never propagate ``IntegrityError``.
``record_event_register`` runs only when the database confirms a new
``EventRegistration`` row.
"""

from django.db import IntegrityError, transaction

from events.models import EventRegistration, SeriesRegistration


def has_event_registration(event, user):
    """Return whether ``user`` already has a row for ``event``."""
    return EventRegistration.objects.filter(event=event, user=user).exists()


def get_event_registration(event, user):
    """Return the existing ``EventRegistration``, or ``None``."""
    return EventRegistration.objects.filter(event=event, user=user).first()


def has_series_registration(series, user):
    """Return whether ``user`` already holds the standing series flag."""
    return SeriesRegistration.objects.filter(series=series, user=user).exists()


def get_or_create_event_registration(event, user):
    """Insert an ``EventRegistration`` or return the winning row.

    Returns ``(registration, created)``. A unique ``(event, user)``
    collision is ``created=False``, not an error. CRM activity is recorded
    only for ``created=True``.
    """
    try:
        with transaction.atomic():
            registration = EventRegistration.objects.create(
                event=event, user=user,
            )
    except IntegrityError:
        try:
            return EventRegistration.objects.get(event=event, user=user), False
        except EventRegistration.DoesNotExist:
            raise
    from analytics.activity import record_event_register
    record_event_register(user, event)
    return registration, True


def get_or_create_series_registration(series, user):
    """Insert a ``SeriesRegistration`` or return the winning row.

    Returns ``(registration, created)``. A unique ``(series, user)``
    collision is ``created=False``, not an error.
    """
    try:
        with transaction.atomic():
            registration = SeriesRegistration.objects.create(
                series=series, user=user,
            )
    except IntegrityError:
        try:
            return SeriesRegistration.objects.get(series=series, user=user), False
        except SeriesRegistration.DoesNotExist:
            raise
    return registration, True
