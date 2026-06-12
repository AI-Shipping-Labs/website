"""Background task: create Zoom meetings for every eligible occurrence in a
series in one click (issue #859).

Two-function module modelled on ``events.tasks.send_post_event_followup``:

1. ``enqueue_create_series_zoom_meetings(series_id)`` — enqueues the worker
   with a descriptive ``build_task_name``. Called from the Studio
   "Create Zoom meetings for all events" button.

2. ``create_series_zoom_meetings(series_id)`` — loads the series, selects
   the eligible occurrences (future, ``platform == 'zoom'``, no existing
   ``zoom_meeting_id``), and for each calls the existing
   ``integrations.services.zoom.create_meeting`` (no second Zoom code path).
   On success it writes ``zoom_meeting_id`` + ``zoom_join_url`` exactly like
   ``studio.views.events.event_create_zoom``.

Idempotency: occurrences that already carry a ``zoom_meeting_id`` are
SKIPPED, never recreated — recreating would orphan a live meeting link
already mailed to attendees.

Partial-failure handling: each per-occurrence ``create_meeting`` call is
wrapped in ``try / except`` so a single Zoom error (including a 429 rate
limit) records that occurrence as failed and the batch continues. The run
returns and persists a structured summary so the Studio detail page can
surface created / skipped / failed counts after a reload.
"""

import logging

from django.utils import timezone as dj_timezone

from events.models import EventSeries
from integrations.services.zoom import create_meeting

logger = logging.getLogger(__name__)


def eligible_occurrences(series):
    """Return the member events that should get a Zoom meeting created.

    Eligible = future (``is_upcoming``), Zoom platform, and no existing
    ``zoom_meeting_id``. Past / cancelled / draft and ``custom``-platform
    occurrences are excluded. ``is_upcoming`` is a time-derived property
    (not a DB field), so we evaluate it in Python rather than in the ORM.
    """
    eligible = []
    for event in series.events.filter(platform='zoom'):
        if event.zoom_meeting_id:
            continue
        if not event.is_upcoming:
            continue
        eligible.append(event)
    return eligible


def eligible_occurrence_count(series):
    """Count occurrences that still need a Zoom meeting (for the button)."""
    return len(eligible_occurrences(series))


def enqueue_create_series_zoom_meetings(series_id):
    """Enqueue the background job that creates Zoom meetings for a series.

    The Studio view calls this and returns immediately so the request does
    not block on N Zoom API round-trips.
    """
    from jobs.tasks import async_task, build_task_name

    return async_task(
        'events.tasks.create_series_zoom_meetings.create_series_zoom_meetings',
        series_id,
        task_name=build_task_name(
            'Create Zoom meetings',
            f'series #{series_id}',
            'series one-click Zoom creation',
        ),
    )


def create_series_zoom_meetings(series_id):
    """Create a Zoom meeting for every eligible occurrence in the series.

    Returns (and persists on ``series.zoom_meetings_last_run``) a structured
    summary::

        {
            'finished_at': ISO8601 str,
            'created': [event_id, ...],
            'skipped_existing': int,
            'skipped_ineligible': int,
            'failed': [{'event_id': int, 'title': str, 'error': str}, ...],
        }

    A Zoom failure on one occurrence (including a 429) does NOT abort the
    batch — it is recorded under ``failed`` and the loop continues.
    """
    try:
        series = EventSeries.objects.get(pk=series_id)
    except EventSeries.DoesNotExist:
        logger.warning(
            'create_series_zoom_meetings: series %s no longer exists',
            series_id,
        )
        return {
            'status': 'skipped',
            'reason': 'missing_series',
            'series_id': series_id,
        }

    all_events = list(series.events.all())
    eligible = eligible_occurrences(series)
    eligible_ids = {event.pk for event in eligible}

    # Anything not eligible (and not an already-has-meeting Zoom occurrence)
    # counts as ineligible: past/cancelled/draft or custom-platform.
    skipped_existing = sum(
        1 for event in all_events
        if event.platform == 'zoom' and event.zoom_meeting_id
    )
    skipped_ineligible = sum(
        1 for event in all_events
        if event.pk not in eligible_ids and not (
            event.platform == 'zoom' and event.zoom_meeting_id
        )
    )

    created = []
    failed = []

    for event in eligible:
        try:
            result = create_meeting(event)
        except Exception as exc:  # noqa: BLE001 - resilient batch
            # One failure (incl. a Zoom 429, a ``ZoomAPIError`` subclass) must
            # not crash the batch; record the per-occurrence error and keep
            # going. We catch broadly because a transport-level error (network,
            # JSON decode) is just as fatal to one occurrence as a Zoom 4xx.
            logger.exception(
                'create_series_zoom_meetings: failed to create Zoom meeting '
                'for event %s in series %s',
                event.pk, series_id,
            )
            failed.append({
                'event_id': event.pk,
                'title': event.title,
                'error': str(exc),
            })
            continue

        event.zoom_meeting_id = result['meeting_id']
        event.zoom_join_url = result['join_url']
        event.save(update_fields=['zoom_meeting_id', 'zoom_join_url'])
        created.append(event.pk)

    summary = {
        'finished_at': dj_timezone.now().isoformat(),
        'created': created,
        'skipped_existing': skipped_existing,
        'skipped_ineligible': skipped_ineligible,
        'failed': failed,
    }

    # Persist on the series so the Studio detail page can show the last-run
    # result after a reload. update_fields keeps this write narrow.
    series.zoom_meetings_last_run = summary
    series.save(update_fields=['zoom_meetings_last_run'])

    logger.info(
        'create_series_zoom_meetings: series %s — created %d, '
        'skipped_existing %d, skipped_ineligible %d, failed %d',
        series_id, len(created), skipped_existing, skipped_ineligible,
        len(failed),
    )
    return summary
