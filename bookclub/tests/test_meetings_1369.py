"""Book-detail meeting rows + derived meets label (issue #1369).

#1461 merged the rows into the week blocks and linked the derived label to
the series page; the assertions here track that shape.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from bookclub.models import Book, Chapter
from content.access import LEVEL_MAIN
from events.models import Event, EventSeries
from payments.models import Tier

User = get_user_model()


class BookMeetingsTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.main_tier = Tier.objects.get(slug='main')
        cls.free_tier = Tier.objects.get(slug='free')
        cls.main_user = User.objects.create_user(
            email='main@test.com', password='pw',
        )
        cls.main_user.tier = cls.main_tier
        cls.main_user.save()

        # Cadence-less collection with one published kickoff occurrence.
        cls.series = EventSeries.objects.create(
            name='Inference Engineering Book Club',
            slug='inference-engineering-book-club', cadence='none',
            day_of_week=None, start_time=None, timezone='Europe/Berlin',
            required_level=0,
        )
        start = timezone.now() + timedelta(days=7)
        cls.kickoff = Event.objects.create(
            title='Book club kickoff', slug='book-club-kickoff',
            start_datetime=start, end_datetime=start + timedelta(hours=1),
            status='upcoming', origin='studio', event_series=cls.series,
        )
        # A draft occurrence must never surface publicly.
        Event.objects.create(
            title='Draft session', slug='draft-session',
            start_datetime=start + timedelta(days=7),
            end_datetime=start + timedelta(days=7, hours=1),
            status='draft', origin='studio', event_series=cls.series,
        )
        cls.book = Book.objects.create(
            title='Inference Engineering', slug='inference-engineering',
            author='Philip Kiely', required_level=LEVEL_MAIN, status='current',
            event_series=cls.series, meeting_cadence='Weekly · 17:00 CET',
        )
        Chapter.objects.create(book=cls.book, number=0, title='Inference')

        # A book with no series at all.
        cls.no_series_book = Book.objects.create(
            title='No Series Book', slug='no-series-book', author='Anon',
            required_level=LEVEL_MAIN, status='current',
            meeting_cadence='Every other Tuesday',
        )
        Chapter.objects.create(book=cls.no_series_book, number=0, title='Ch 0')

        # A book whose series has zero public occurrences.
        cls.empty_series = EventSeries.objects.create(
            name='Empty', slug='empty-series', cadence='none',
            day_of_week=None, start_time=None, timezone='Europe/Berlin',
            required_level=0,
        )
        cls.empty_book = Book.objects.create(
            title='Empty Series Book', slug='empty-series-book', author='Anon',
            required_level=LEVEL_MAIN, status='current',
            event_series=cls.empty_series,
        )
        Chapter.objects.create(book=cls.empty_book, number=0, title='Ch 0')

    def test_member_sees_meeting_rows(self):
        self.client.force_login(self.main_user)
        response = self.client.get('/books/inference-engineering')
        self.assertContains(response, 'book-meeting-row', count=1)
        self.assertContains(response, 'Book club kickoff')
        self.assertContains(response, self.kickoff.get_absolute_url())
        # Draft occurrence must not surface.
        self.assertNotContains(response, 'Draft session')

    def test_when_we_meet_section_is_gone(self):
        # #1461 merged meetings into the week blocks; the separate section and
        # its heading no longer render.
        self.client.force_login(self.main_user)
        response = self.client.get('/books/inference-engineering')
        self.assertNotContains(response, 'book-when-we-meet')
        self.assertNotContains(response, 'When we meet')

    def test_template_comments_do_not_leak(self):
        self.client.force_login(self.main_user)
        response = self.client.get('/books/inference-engineering')
        self.assertNotContains(response, '{#')
        self.assertNotContains(response, '{% comment %}')
        self.assertNotContains(response, 'never repeats the series name')

    def test_no_series_hides_meeting_rows(self):
        self.client.force_login(self.main_user)
        response = self.client.get('/books/no-series-book')
        self.assertContains(response, 'book-participation-body')
        self.assertNotContains(response, 'book-meeting-row')

    def test_zero_public_occurrences_hides_meeting_rows(self):
        self.client.force_login(self.main_user)
        response = self.client.get('/books/empty-series-book')
        self.assertContains(response, 'book-participation-body')
        self.assertNotContains(response, 'book-meeting-row')

    def test_guest_gated_with_no_meeting_rows(self):
        response = self.client.get('/books/inference-engineering')
        self.assertContains(response, 'book-guest-gate')
        self.assertNotContains(response, 'book-meeting-row')
        self.assertNotContains(response, 'Book club kickoff')

    def test_meets_label_prefers_series_schedule_label(self):
        # A cadence-less collection with a single session must read honestly —
        # a neutral single-session summary, never a false weekly claim.
        self.client.force_login(self.main_user)
        response = self.client.get('/books/inference-engineering')
        self.assertContains(response, 'book-meets')
        self.assertContains(response, self.series.schedule_label)
        self.assertNotContains(response, 'Weekly · 17:00 CET')

    def test_meets_label_falls_back_to_meeting_cadence(self):
        self.client.force_login(self.main_user)
        response = self.client.get('/books/no-series-book')
        self.assertContains(response, 'Every other Tuesday')

    def test_meets_label_links_to_the_series(self):
        # #1461: schedule_label counts only public occurrences, so it
        # under-reports while draft weeks exist — the series page is the full
        # schedule and the label now links to it.
        self.client.force_login(self.main_user)
        response = self.client.get('/books/inference-engineering')
        self.assertContains(
            response,
            f'<a href="{self.series.get_absolute_url()}" '
            'class="text-accent underline underline-offset-4 '
            'hover:text-accent/80" data-testid="book-meets-link">',
        )

    def test_meeting_cadence_fallback_stays_plain_text(self):
        self.client.force_login(self.main_user)
        response = self.client.get('/books/no-series-book')
        self.assertNotContains(response, 'book-meets-link')
