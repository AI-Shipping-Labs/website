"""Shared event calendar lifecycle trigger helpers."""

from datetime import timedelta

from django.utils import timezone

from events.models import EventRegistration


def user_has_permanent_bounce(user):
    """Return whether lifecycle email should be suppressed for bounce state."""
    bounce_state = getattr(user, 'bounce_state', 'none')
    permanent = getattr(getattr(user, 'BounceState', None), 'PERMANENT', 'permanent')
    return bounce_state == permanent


def _effective_end(start_datetime, end_datetime):
    if end_datetime is not None:
        return end_datetime
    if start_datetime is None:
        return None
    return start_datetime + timedelta(hours=1)


def should_notify_schedule_update(event, old_start, old_end=None):
    """Return True for meaningful future DTSTART/DTEND edits."""
    if old_start is None or event.start_datetime is None:
        return False

    now = timezone.now()
    if old_start <= now or event.start_datetime <= now:
        return False

    old_effective_end = _effective_end(old_start, old_end)
    new_effective_end = event.effective_end_datetime

    start_changed = abs((event.start_datetime - old_start).total_seconds()) >= 60
    if old_effective_end is None or new_effective_end is None:
        end_changed = old_effective_end != new_effective_end
    else:
        end_changed = (
            abs((new_effective_end - old_effective_end).total_seconds()) >= 60
        )
    return start_changed or end_changed


def enqueue_schedule_update(event, old_start, old_end=None):
    """Enqueue attendee and series calendar updates for a schedule edit."""
    if not should_notify_schedule_update(event, old_start, old_end):
        return None

    count = EventRegistration.objects.filter(event=event).count()
    if count > 0:
        from events.tasks.notify_reschedule import enqueue_reschedule_notice
        enqueue_reschedule_notice(event.pk, old_start.isoformat())

    if event.event_series_id:
        from events.tasks.notify_series_invite import enqueue_series_update
        enqueue_series_update(event.pk, old_start_iso=old_start.isoformat())

    return count


def should_notify_cancellation(event, old_status):
    """Return True when an upcoming event has just transitioned to cancelled."""
    if event.status != 'cancelled' or old_status == 'cancelled':
        return False
    if event.start_datetime is None:
        return False
    return event.start_datetime > timezone.now()


def bump_sequence_for_cancellation(event):
    """Bump ``SEQUENCE`` so calendar clients accept a cancellation."""
    event.ics_sequence = (event.ics_sequence or 0) + 1
    event.save(update_fields=['ics_sequence'])


def enqueue_cancellation_update(event, old_status):
    """Enqueue attendee and series cancellation fan-outs for a cancel edit."""
    if not should_notify_cancellation(event, old_status):
        return 0

    bump_sequence_for_cancellation(event)

    count = EventRegistration.objects.filter(event=event).count()
    if count > 0:
        from events.tasks.notify_cancellation import enqueue_cancellation_notice
        enqueue_cancellation_notice(event.pk)

    if event.event_series_id:
        from events.tasks.notify_series_invite import enqueue_series_cancellation
        enqueue_series_cancellation(event.pk)

    return count
