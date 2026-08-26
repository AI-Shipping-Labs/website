"""Tests for the seed_book_club management command (issue #1362)."""

from datetime import date, time

from django.core.management import call_command
from django.test import TestCase

from bookclub.models import Book, Chapter
from content.access import LEVEL_MAIN


class SeedBookClubTest(TestCase):
    def test_seeds_inference_engineering_with_eight_chapters(self):
        call_command('seed_book_club')
        book = Book.objects.get(slug='inference-engineering')
        self.assertEqual(book.title, 'Inference Engineering')
        self.assertEqual(book.author, 'Philip Kiely')
        self.assertEqual(book.required_level, LEVEL_MAIN)
        self.assertEqual(book.status, 'current')
        self.assertEqual(book.start_date, date(2026, 8, 10))
        self.assertEqual(book.chapters.count(), 8)
        numbers = list(book.chapters.values_list('number', flat=True))
        self.assertEqual(numbers, [0, 1, 2, 3, 4, 5, 6, 7])
        self.assertEqual(book.chapters.get(number=0).title, 'Inference')
        self.assertEqual(book.chapters.get(number=7).title, 'Production')

    def test_is_idempotent(self):
        call_command('seed_book_club')
        call_command('seed_book_club')
        self.assertEqual(Book.objects.filter(slug='inference-engineering').count(), 1)
        book = Book.objects.get(slug='inference-engineering')
        self.assertEqual(book.chapters.count(), 8)

    def test_does_not_fail_when_event_series_absent(self):
        # No EventSeries seeded — the book is created and left unlinked.
        call_command('seed_book_club')
        book = Book.objects.get(slug='inference-engineering')
        self.assertIsNone(book.event_series)

    def test_links_event_series_when_present(self):
        from events.models.event_series import EventSeries

        series = EventSeries.objects.create(
            slug='inference-engineering-book-club',
            name='Inference Engineering Book Club',
            cadence='weekly', day_of_week=0, start_time=time(17, 0),
            timezone='Europe/Berlin', required_level=0, is_active=True,
        )
        call_command('seed_book_club')
        book = Book.objects.get(slug='inference-engineering')
        self.assertEqual(book.event_series_id, series.id)

    def test_attaches_kickoff_event_to_cadence_less_collection(self):
        from events.models import Event, EventSeries

        series = EventSeries.objects.create(
            slug='inference-engineering-book-club',
            name='Inference Engineering Book Club',
            cadence='none', day_of_week=None, start_time=None,
            timezone='Europe/Berlin', required_level=0, is_active=True,
        )
        call_command('seed_book_club')
        # The kickoff event is created and attached through the series link
        # (#1358's supported path), not a standalone script.
        kickoff = Event.objects.get(
            slug='inference-engineering-book-club-kickoff',
        )
        self.assertEqual(kickoff.event_series_id, series.id)
        # Idempotent: a re-run neither duplicates nor detaches.
        call_command('seed_book_club')
        self.assertEqual(
            Event.objects.filter(
                slug='inference-engineering-book-club-kickoff',
            ).count(),
            1,
        )

    def test_sets_week_number_and_never_writes_week_n_into_week_label(self):
        # #1461: the seed used to write week_label='Week 1'..'Week 8' and never
        # set week_number, which contradicted the field's documented meaning
        # (week_label is a THEME) and hid the roadmap's week grouping locally.
        call_command('seed_book_club')
        chapters = Chapter.objects.filter(
            book__slug='inference-engineering',
        ).order_by('number')
        self.assertEqual(
            [c.week_number for c in chapters], [1, 2, 3, 4, 5, 6, 7, 8],
        )
        for chapter in chapters:
            self.assertNotIn('Week', chapter.week_label)

    def test_rerun_heals_a_chapter_that_drifted(self):
        call_command('seed_book_club')
        Chapter.objects.filter(
            book__slug='inference-engineering', number=0,
        ).update(week_number=None, week_label='Week 1')
        call_command('seed_book_club')
        chapter = Chapter.objects.get(
            book__slug='inference-engineering', number=0,
        )
        self.assertEqual(chapter.week_number, 1)
        self.assertEqual(chapter.week_label, '')

    def test_does_not_duplicate_chapters_on_rerun(self):
        call_command('seed_book_club')
        call_command('seed_book_club')
        self.assertEqual(
            Chapter.objects.filter(book__slug='inference-engineering').count(), 8,
        )
