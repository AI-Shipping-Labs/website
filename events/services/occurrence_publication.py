"""Atomic, idempotent publication lifecycle for event-series occurrences."""

import logging
import threading
import time
from dataclasses import dataclass

from django.db import OperationalError, close_old_connections, connection, transaction
from django.utils import timezone

from events.models import Event, EventSeries
from events.services.host_registration import maybe_register_host_as_attendee
from events.services.series_registration import enroll_series_registrants_in_event

logger = logging.getLogger(__name__)
_sqlite_publish_retry_lock = threading.Lock()


@dataclass(frozen=True)
class PublicationResult:
    """Bounded summary returned to Studio and API callers."""

    series_id: int
    occurrence_ids: tuple[int, ...]

    @property
    def published_count(self):
        return len(self.occurrence_ids)


def run_occurrence_publication_lifecycle(event):
    """Run the existing idempotent enrollment and host lifecycle once."""
    if event.event_series_id:
        enroll_series_registrants_in_event(event)
    maybe_register_host_as_attendee(event)


def _claim_draft_occurrence(event):
    """Atomically claim one still-draft occurrence for this transaction."""
    changed = Event.objects.filter(
        pk=event.pk,
        event_series_id=event.event_series_id,
        status="draft",
    ).update(status="upcoming", updated_at=timezone.now())
    if changed:
        event.status = "upcoming"
    return bool(changed)


def _select_locked_drafts(series, occurrence_ids):
    """Select the ordered draft candidates covered by the series lock."""
    drafts = (
        Event.objects.select_for_update()
        .filter(event_series=series, status="draft")
        .order_by("start_datetime", "id")
    )
    if occurrence_ids is not None:
        drafts = drafts.filter(pk__in=occurrence_ids)
    return list(drafts)


def _publish_series_drafts_once(series_id, occurrence_ids):
    """Run one locked publication attempt and return its bounded counts."""
    with transaction.atomic():
        series = EventSeries.objects.select_for_update().get(pk=series_id)
        events = _select_locked_drafts(series, occurrence_ids)
        selected_count = len(events)

        published_ids = []
        for event in events:
            # The conditional update is a second claim guard for databases
            # where ``select_for_update`` is unavailable (notably SQLite in
            # local/test runs), and protects lifecycle side effects too.
            if not _claim_draft_occurrence(event):
                continue
            run_occurrence_publication_lifecycle(event)
            published_ids.append(event.pk)
    return series.pk, selected_count, tuple(published_ids)


def publish_series_drafts(series_id, *, actor_label, occurrence_ids=None):
    """Lock and publish selected (or all) draft children of one series.

    The status predicate is rechecked while locked, making retries and racing
    requests no-ops after the first accepted publication. Side effects use the
    same lifecycle as the single-occurrence Studio action.
    """
    try:
        resolved_series_id, selected_count, published_ids = (
            _publish_series_drafts_once(series_id, occurrence_ids)
        )
    except OperationalError as exc:
        # SQLite has no row-level ``SELECT FOR UPDATE`` and can deadlock two
        # simultaneous read-then-write transactions. Serialize their bounded
        # retries; production databases retain normal lock/error behavior.
        sqlite_lock = (
            connection.vendor == "sqlite"
            and "locked" in str(exc).lower()
        )
        if not sqlite_lock:
            raise
        close_old_connections()
        with _sqlite_publish_retry_lock:
            for attempt in range(3):
                try:
                    resolved_series_id, selected_count, published_ids = (
                        _publish_series_drafts_once(
                            series_id, occurrence_ids,
                        )
                    )
                    break
                except OperationalError as retry_exc:
                    if (
                        "locked" not in str(retry_exc).lower()
                        or attempt == 2
                    ):
                        raise
                    close_old_connections()
                    time.sleep(0.05 * (attempt + 1))

    result = PublicationResult(resolved_series_id, published_ids)
    logger.info(
        "event_series_publish_drafts actor=%s series_id=%s selected=%s changed=%s",
        actor_label,
        resolved_series_id,
        selected_count,
        result.published_count,
    )
    return result
