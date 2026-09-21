"""Series -> book cross-link on the public series page (issue #1772).

The public ``/events/series/<id>/<slug>`` page renders a calm ``Reading:``
line (``data-testid="series-book"``) above the occurrence list when the
series has a visible linked book. Visibility mirrors ``book_detail``:
``cancelled`` never renders, ``draft`` renders for staff preview only, and
``upcoming`` / ``current`` / ``finished`` render for everyone without any
``required_level`` gate.
"""

from datetime import date, time, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from bookclub.models import (
    BOOK_STATUS_CANCELLED,
    BOOK_STATUS_CURRENT,
    BOOK_STATUS_DRAFT,
    BOOK_STATUS_FINISHED,
    BOOK_STATUS_UPCOMING,
    Book,
)
from content.access import LEVEL_PREMIUM
from events.models import Event, EventSeries

User = get_user_model()


class SeriesBookLinkTest(TestCase):
    """Issue #1772: ``event_series_public`` passes ``book`` and the template
    renders the cross-link block per the visibility pick rule."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff1772@test.com', password='pass', is_staff=True,
        )
        cls.member = User.objects.create_user(
            email='member1772@test.com', password='pass',
        )

    @staticmethod
    def _series_with_session(slug):
        series = EventSeries.objects.create(
            name=slug.replace('-', ' ').title(),
            slug=slug,
            start_time=time(18, 0),
        )
        Event.objects.create(
            title=f'{series.name} Session',
            slug=f'{slug}-session-1',
            start_datetime=timezone.now() + timedelta(days=7),
            status='upcoming',
            event_series=series,
            series_position=1,
            origin='studio',
        )
        return series

    @staticmethod
    def _book(series, slug, status, **kwargs):
        params = {
            'title': slug.replace('-', ' ').title(),
            'slug': slug,
            'author': 'Philip Kiely',
            'status': status,
            'event_series': series,
        }
        params.update(kwargs)
        return Book.objects.create(**params)

    def _assert_book_block(self, response, book):
        """The block renders above the occurrence list with the exact copy,
        the cover, and the title link to the book detail page."""
        self.assertEqual(response.context['book'].pk, book.pk)
        self.assertContains(response, 'data-testid="series-book"')
        self.assertContains(response, 'data-testid="series-book-link"')
        self.assertContains(response, f'href="{book.get_absolute_url()}"')
        self.assertContains(response, 'Reading:')
        self.assertContains(
            response, f'>{book.title}</a> by {book.author}',
        )
        html = response.content.decode()
        self.assertLess(
            html.index('data-testid="series-book"'),
            html.index('data-testid="series-events"'),
        )
        self.assertLess(
            html.index('data-testid="series-register-panel"'),
            html.index('data-testid="series-book"'),
        )

    def test_current_book_renders_block_with_cover_for_anonymous(self):
        series = self._series_with_session('reading-line-series')
        book = self._book(
            series,
            'inference-engineering-1772',
            BOOK_STATUS_CURRENT,
            title='Inference Engineering',
            cover_image_url='https://cdn.example.com/covers/inference.jpg',
        )
        response = self.client.get(series.get_absolute_url())
        self._assert_book_block(response, book)
        self.assertContains(
            response,
            '<img src="https://cdn.example.com/covers/inference.jpg"',
            html=False,
        )

    def test_upcoming_and_finished_render_block_for_anonymous(self):
        cases = [
            ('upcoming-book-series', BOOK_STATUS_UPCOMING),
            ('finished-book-series', BOOK_STATUS_FINISHED),
        ]
        for slug, status in cases:
            with self.subTest(status=status):
                series = self._series_with_session(slug)
                book = self._book(series, f'{slug}-book', status)
                response = self.client.get(series.get_absolute_url())
                self._assert_book_block(response, book)

    def test_coverless_book_renders_spine_fallback(self):
        series = self._series_with_session('spine-fallback-series')
        book = self._book(
            series,
            'spine-fallback-book',
            BOOK_STATUS_CURRENT,
            cover_accent='from-blue-500/30',
        )
        response = self.client.get(series.get_absolute_url())
        self._assert_book_block(response, book)
        self.assertContains(response, 'from-blue-500/30')
        html = response.content.decode()
        block = html[
            html.index('data-testid="series-book"'):
            html.index('data-testid="series-events"')
        ]
        self.assertNotIn('<img', block)

    def test_block_visible_regardless_of_required_level(self):
        series = self._series_with_session('premium-book-series')
        book = self._book(
            series,
            'premium-book-1772',
            BOOK_STATUS_CURRENT,
            required_level=LEVEL_PREMIUM,
        )
        anonymous_response = self.client.get(series.get_absolute_url())
        self._assert_book_block(anonymous_response, book)
        self.client.force_login(self.member)
        member_response = self.client.get(series.get_absolute_url())
        self._assert_book_block(member_response, book)

    def test_book_title_link_lands_on_book_detail(self):
        series = self._series_with_session('click-through-series')
        book = self._book(
            series, 'click-through-book', BOOK_STATUS_CURRENT,
        )
        response = self.client.get(book.get_absolute_url())
        self.assertContains(response, book.title)

    def test_draft_hidden_from_public_but_visible_to_staff(self):
        series = self._series_with_session('draft-book-series')
        book = self._book(series, 'draft-book-1772', BOOK_STATUS_DRAFT)
        anonymous_response = self.client.get(series.get_absolute_url())
        self.assertIsNone(anonymous_response.context['book'])
        self.assertNotContains(
            anonymous_response, 'data-testid="series-book"',
        )
        self.client.force_login(self.member)
        member_response = self.client.get(series.get_absolute_url())
        self.assertIsNone(member_response.context['book'])
        self.assertNotContains(member_response, 'data-testid="series-book"')
        self.client.force_login(self.staff)
        staff_response = self.client.get(series.get_absolute_url())
        self._assert_book_block(staff_response, book)

    def test_cancelled_hidden_from_everyone_including_staff(self):
        series = self._series_with_session('cancelled-book-series')
        self._book(series, 'cancelled-book-1772', BOOK_STATUS_CANCELLED)
        anonymous_response = self.client.get(series.get_absolute_url())
        self.assertIsNone(anonymous_response.context['book'])
        self.assertNotContains(
            anonymous_response, 'data-testid="series-book"',
        )
        self.client.force_login(self.staff)
        staff_response = self.client.get(series.get_absolute_url())
        self.assertIsNone(staff_response.context['book'])
        self.assertNotContains(staff_response, 'data-testid="series-book"')

    def test_series_without_book_renders_no_block(self):
        series = self._series_with_session('bookless-series')
        response = self.client.get(series.get_absolute_url())
        self.assertIsNone(response.context['book'])
        self.assertNotContains(response, 'data-testid="series-book"')
        self.assertContains(response, 'data-testid="series-events"')

    def test_multiple_books_resolve_to_first_in_default_ordering(self):
        series = self._series_with_session('multi-book-series')
        older = self._book(
            series,
            'multi-book-older',
            BOOK_STATUS_CURRENT,
            title='Older Multi Book',
            start_date=date(2026, 3, 1),
        )
        newer = self._book(
            series,
            'multi-book-newer',
            BOOK_STATUS_CURRENT,
            title='Newer Multi Book',
            start_date=date(2026, 6, 1),
        )
        response = self.client.get(series.get_absolute_url())
        # Default ordering is -start_date: the newer book wins.
        self.assertEqual(response.context['book'].pk, newer.pk)
        self.assertContains(
            response, f'href="{newer.get_absolute_url()}"',
        )
        self.assertNotContains(response, older.title)
