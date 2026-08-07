"""Idempotent seed for the first Book Club book (issue #1362).

Seeds "Inference Engineering" (Main tier, current) with its 8-chapter
roadmap and illustrative deadlines/week labels, mirroring the prototype
sample data. Safe to re-run: the book is upserted by slug and chapters are
upserted by ``(book, number)``.

Event linkage: if the "Inference Engineering Book Club" event series already
exists (by slug), the book is linked to it; otherwise the link is left null.
The seed never fails when the series or its kickoff event is absent — the
kickoff attaches via that series once #1369/#1358 land.
"""

from datetime import date, timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from bookclub.models import BOOK_STATUS_CURRENT, Book, Chapter
from content.access import LEVEL_MAIN
from events.models import Event
from events.models.event_series import EventSeries

BOOK_SLUG = 'inference-engineering'
EVENT_SERIES_SLUG = 'inference-engineering-book-club'
KICKOFF_EVENT_SLUG = 'inference-engineering-book-club-kickoff'

BOOK_DEFAULTS = {
    'title': 'Inference Engineering',
    'subtitle': 'The technologies behind every AI product in production',
    'author': 'Philip Kiely',
    'description': (
        'A book for engineers who want to understand the technologies behind '
        'every AI product in production — the stack from model architecture '
        'and hardware to serving software, optimization techniques, and '
        'running inference in production.'
    ),
    'cover_accent': 'from-accent/30',
    'required_level': LEVEL_MAIN,
    'status': BOOK_STATUS_CURRENT,
    'start_date': date(2026, 8, 10),
    'meeting_cadence': 'Weekly · 17:00 CET',
    'buy_url': 'https://www.baseten.co/inference-engineering/',
}

# Chapter roadmap: the real table of contents (Ch 0–7). Deadlines and week
# labels are illustrative — the actual cadence is set on the kickoff call.
CHAPTERS = [
    (0, 'Inference', date(2026, 8, 17), 'Week 1'),
    (1, 'Prerequisites', date(2026, 8, 24), 'Week 2'),
    (2, 'Architecture', date(2026, 8, 31), 'Week 3'),
    (3, 'Hardware', date(2026, 9, 7), 'Week 4'),
    (4, 'Software', date(2026, 9, 14), 'Week 5'),
    (5, 'Techniques', date(2026, 9, 21), 'Week 6'),
    (6, 'Modalities', date(2026, 9, 28), 'Week 7'),
    (7, 'Production', date(2026, 10, 5), 'Week 8'),
]


class Command(BaseCommand):
    help = 'Idempotently seed the Inference Engineering book club (issue #1362).'

    def handle(self, *args, **options):
        event_series = self._resolve_event_series()
        if event_series is not None:
            self._ensure_kickoff_event(event_series)

        defaults = dict(BOOK_DEFAULTS)
        if event_series is not None:
            defaults['event_series'] = event_series

        book, created = Book.objects.get_or_create(
            slug=BOOK_SLUG,
            defaults=defaults,
        )
        if not created:
            # Re-sync the fields to the canonical seed values so a re-run
            # heals drift, but never clobber a manually-set event series link
            # with null when the series is absent this run.
            for field, value in defaults.items():
                setattr(book, field, value)
            book.save()

        updated = 0
        for number, title, deadline, week_label in CHAPTERS:
            _chapter, ch_created = Chapter.objects.update_or_create(
                book=book,
                number=number,
                defaults={
                    'title': title,
                    'deadline': deadline,
                    'week_label': week_label,
                },
            )
            if ch_created:
                updated += 1

        if created:
            self.stdout.write(self.style.SUCCESS(
                f'Created book "{book.title}" with {len(CHAPTERS)} chapters.'
            ))
        else:
            self.stdout.write(self.style.SUCCESS(
                f'Book "{book.title}" already exists, synced '
                f'{len(CHAPTERS)} chapters ({updated} newly created).'
            ))
        if event_series is None:
            self.stdout.write(
                'Event series "%s" not found — left unlinked.' % EVENT_SERIES_SLUG
            )
        else:
            self.stdout.write(
                'Linked event series "%s" (id %s).'
                % (event_series.slug, event_series.pk)
            )

    def _resolve_event_series(self):
        """Return the book-club EventSeries by slug, or None if absent."""
        return EventSeries.objects.filter(slug=EVENT_SERIES_SLUG).first()

    def _ensure_kickoff_event(self, event_series):
        """Idempotently attach the kickoff event to the collection (#1369).

        Uses #1358's supported path — a real ``events.Event`` whose
        ``event_series`` points at the (cadence-less) collection — rather than
        the prototype's standalone ``seed_local_event.py`` script. The start is
        derived from ``timezone.now()`` so the seed never hard-codes a date that
        rots. Safe to re-run: the event is upserted by slug and re-attached if a
        pre-existing event drifted off the series.
        """
        start = timezone.now() + timedelta(days=7)
        event, created = Event.objects.get_or_create(
            slug=KICKOFF_EVENT_SLUG,
            defaults={
                'title': 'Inference Engineering book club kickoff',
                'description': (
                    'Kickoff session for the Inference Engineering book club.'
                ),
                'start_datetime': start,
                'end_datetime': start + timedelta(hours=1),
                'timezone': 'Europe/Berlin',
                'required_level': 0,
                'status': 'upcoming',
                'origin': 'studio',
                'event_series': event_series,
            },
        )
        if not created and event.event_series_id != event_series.id:
            event.event_series = event_series
            event.save(update_fields=['event_series'])
        self.stdout.write(
            'Kickoff event "%s" %s and attached to series "%s".'
            % (
                event.slug,
                'created' if created else 'exists',
                event_series.slug,
            )
        )
