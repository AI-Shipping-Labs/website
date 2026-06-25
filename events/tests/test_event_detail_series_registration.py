"""Issue #1077: the individual event detail page is series-aware.

A viewer holding a standing ``SeriesRegistration`` for the series an
occurrence belongs to is "effectively registered" on that occurrence's
detail page, even without a per-occurrence ``EventRegistration`` row.

These tests cover the ``registration_source`` resolution
(``event`` / ``series`` / ``none`` and the both-present precedence), the
downstream gates the combined flag drives (join link, feedback), and the
template variant the source selects.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, tag
from django.utils import timezone

from content.access import LEVEL_MAIN, LEVEL_OPEN
from events.models import (
    Event,
    EventRegistration,
    EventSeries,
    SeriesRegistration,
)
from tests.fixtures import TierSetupMixin

User = get_user_model()


def _make_series(**kwargs):
    defaults = {
        'name': 'Weekly Office Hours',
        'slug': 'weekly-office-hours',
        'start_time': timezone.now().time(),
        'timezone': 'Europe/Berlin',
    }
    defaults.update(kwargs)
    return EventSeries.objects.create(**defaults)


def _make_occurrence(series=None, *, offset_days=7, position=1,
                     status='upcoming', required_level=LEVEL_OPEN, slug=None,
                     zoom_join_url=''):
    start = timezone.now() + timedelta(days=offset_days)
    base = slug or 'occ'
    return Event.objects.create(
        title=f'Session {position}',
        slug=slug or f'{base}-{position}',
        start_datetime=start,
        end_datetime=start + timedelta(hours=1),
        status=status,
        required_level=required_level,
        event_series=series,
        series_position=position if series else None,
        zoom_join_url=zoom_join_url,
    )


@tag('core')
class EventDetailRegistrationSourceTest(TierSetupMixin, TestCase):
    """``registration_source`` resolves event / series / none correctly."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.user = User.objects.create_user(
            email='main@test.com', password='pass', email_verified=True,
        )
        cls.user.tier = cls.main_tier
        cls.user.save()
        cls.series = _make_series()
        cls.event = _make_occurrence(cls.series, slug='covered')

    def setUp(self):
        self.client = Client()

    def _get(self):
        self.client.login(email='main@test.com', password='pass')
        return self.client.get(self.event.get_absolute_url())

    def test_series_only_resolves_source_series(self):
        SeriesRegistration.objects.create(series=self.series, user=self.user)
        resp = self._get()
        self.assertEqual(resp.context['registration_source'], 'series')
        self.assertTrue(resp.context['is_effectively_registered'])
        self.assertFalse(resp.context['is_registered'])
        self.assertTrue(resp.context['is_series_registered'])

    def test_per_occurrence_only_resolves_source_event(self):
        EventRegistration.objects.create(event=self.event, user=self.user)
        resp = self._get()
        self.assertEqual(resp.context['registration_source'], 'event')
        self.assertTrue(resp.context['is_effectively_registered'])
        self.assertTrue(resp.context['is_registered'])
        self.assertFalse(resp.context['is_series_registered'])

    def test_both_present_resolves_source_event(self):
        SeriesRegistration.objects.create(series=self.series, user=self.user)
        EventRegistration.objects.create(event=self.event, user=self.user)
        resp = self._get()
        # Per-occurrence row wins so the per-event cancel stays available.
        self.assertEqual(resp.context['registration_source'], 'event')
        self.assertTrue(resp.context['is_effectively_registered'])

    def test_neither_resolves_source_none(self):
        resp = self._get()
        self.assertEqual(resp.context['registration_source'], 'none')
        self.assertFalse(resp.context['is_effectively_registered'])

    def test_anonymous_does_not_resolve_series_flag(self):
        SeriesRegistration.objects.create(series=self.series, user=self.user)
        resp = self.client.get(self.event.get_absolute_url())
        self.assertFalse(resp.context['is_series_registered'])
        self.assertEqual(resp.context['registration_source'], 'none')

    def test_standalone_event_never_series_registered(self):
        standalone = _make_occurrence(series=None, slug='standalone')
        # A series flag on an unrelated series must not bleed in.
        SeriesRegistration.objects.create(series=self.series, user=self.user)
        self.client.login(email='main@test.com', password='pass')
        resp = self.client.get(standalone.get_absolute_url())
        self.assertFalse(resp.context['is_series_registered'])
        self.assertEqual(resp.context['registration_source'], 'none')


@tag('core')
class EventDetailRegistrationCardTemplateTest(TierSetupMixin, TestCase):
    """The card renders the right variant for each source."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.user = User.objects.create_user(
            email='main@test.com', password='pass', email_verified=True,
        )
        cls.user.tier = cls.main_tier
        cls.user.save()
        cls.series = _make_series()
        cls.event = _make_occurrence(cls.series, slug='covered')

    def setUp(self):
        self.client = Client()
        self.client.login(email='main@test.com', password='pass')

    def test_series_only_shows_series_heading_and_manage_link(self):
        SeriesRegistration.objects.create(series=self.series, user=self.user)
        resp = self.client.get(self.event.get_absolute_url())
        self.assertContains(resp, "You're registered for this series")
        self.assertContains(resp, 'data-testid="event-manage-series-registration-link"')
        self.assertContains(resp, self.series.get_absolute_url())
        # The series variant keeps the calendar control.
        self.assertContains(resp, 'data-testid="event-add-to-calendar"')

    def test_series_only_hides_per_occurrence_cancel_button(self):
        SeriesRegistration.objects.create(series=self.series, user=self.user)
        resp = self.client.get(self.event.get_absolute_url())
        self.assertNotContains(resp, 'data-event-unregister-button')

    def test_series_only_does_not_show_register_button(self):
        SeriesRegistration.objects.create(series=self.series, user=self.user)
        resp = self.client.get(self.event.get_absolute_url())
        self.assertNotContains(resp, 'data-event-register-button')

    def test_per_occurrence_shows_default_heading_and_cancel(self):
        EventRegistration.objects.create(event=self.event, user=self.user)
        resp = self.client.get(self.event.get_absolute_url())
        self.assertContains(resp, "You're registered!")
        self.assertContains(resp, 'data-event-unregister-button')
        self.assertNotContains(resp, 'data-testid="event-manage-series-registration-link"')

    def test_both_present_shows_cancel_not_series_link(self):
        SeriesRegistration.objects.create(series=self.series, user=self.user)
        EventRegistration.objects.create(event=self.event, user=self.user)
        resp = self.client.get(self.event.get_absolute_url())
        self.assertContains(resp, 'data-event-unregister-button')
        self.assertNotContains(resp, 'data-testid="event-manage-series-registration-link"')

    def test_unregistered_shows_register_button(self):
        resp = self.client.get(self.event.get_absolute_url())
        self.assertContains(resp, 'data-event-register-button')
        self.assertNotContains(resp, 'data-testid="event-registered-confirmation"')


@tag('core')
class EventDetailSeriesJoinAndFeedbackTest(TierSetupMixin, TestCase):
    """The combined flag drives the join link and feedback surfaces."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.user = User.objects.create_user(
            email='main@test.com', password='pass', email_verified=True,
        )
        cls.user.tier = cls.main_tier
        cls.user.save()
        cls.series = _make_series()

    def setUp(self):
        self.client = Client()
        self.client.login(email='main@test.com', password='pass')

    def test_series_registrant_gets_join_link_in_window(self):
        start = timezone.now() + timedelta(minutes=2)
        event = Event.objects.create(
            title='Joinable', slug='joinable',
            start_datetime=start, end_datetime=start + timedelta(hours=1),
            status='upcoming', required_level=LEVEL_OPEN,
            event_series=self.series, series_position=1,
            zoom_join_url='https://zoom.us/j/123',
        )
        SeriesRegistration.objects.create(series=self.series, user=self.user)
        resp = self.client.get(event.get_absolute_url())
        self.assertTrue(resp.context['show_zoom_link'])
        self.assertContains(resp, 'data-testid="event-join-now"')

    def test_series_registrant_can_submit_feedback_on_past_event(self):
        start = timezone.now() - timedelta(days=2)
        event = Event.objects.create(
            title='Past', slug='past-occ',
            start_datetime=start, end_datetime=start + timedelta(hours=1),
            status='completed', required_level=LEVEL_OPEN,
            event_series=self.series, series_position=1,
        )
        SeriesRegistration.objects.create(series=self.series, user=self.user)
        resp = self.client.get(event.get_absolute_url())
        self.assertTrue(resp.context['can_submit_feedback'])

        # And the submit endpoint accepts the POST (no per-occurrence row).
        resp = self.client.post(
            f'/events/{event.pk}/{event.slug}/feedback',
            {'rating': '5', 'comment': 'Great series'},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(
            event.feedback.filter(user=self.user, rating=5).exists()
        )


@tag('core')
class EventDetailSeriesAccessLossTest(TierSetupMixin, TestCase):
    """A downgraded series registrant sees the upgrade CTA, not registered."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.user = User.objects.create_user(
            email='main@test.com', password='pass', email_verified=True,
        )
        # Free-tier user can NOT access a main-only occurrence.
        cls.user.tier = cls.free_tier
        cls.user.save()
        cls.series = _make_series(slug='gated-series', name='Gated Series')
        cls.event = _make_occurrence(
            cls.series, slug='gated-occ', required_level=LEVEL_MAIN,
        )

    def setUp(self):
        self.client = Client()
        self.client.login(email='main@test.com', password='pass')

    def test_no_access_takes_precedence_over_series_block(self):
        SeriesRegistration.objects.create(series=self.series, user=self.user)
        resp = self.client.get(self.event.get_absolute_url())
        self.assertFalse(resp.context['has_access'])
        # Upgrade CTA, not the registered confirmation block.
        self.assertNotContains(resp, 'data-testid="event-registered-confirmation"')
        self.assertContains(resp, 'View Pricing')
