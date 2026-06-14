"""Studio tests for the occurrence/series access-level guardrail (#958).

Studio is the human-controlled override surface: the add-occurrence level
pre-fills the series level, and a differing level saves once confirmed (the
client-side prompt is the override). Server-side there is no reject — a
confirmed mismatch is honoured. Series create stamps the chosen level onto
both the series and every generated occurrence. Changing the series level
never rewrites existing occurrences.
"""

from datetime import date, time, timedelta

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.utils import timezone

from events.models import Event, EventSeries

User = get_user_model()


class StaffMixin:
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.staff = User.objects.create_user(
            email='staff-level@test.com', password='pass', is_staff=True,
        )

    def setUp(self):
        self.client = Client()
        self.client.login(email='staff-level@test.com', password='pass')


class StudioSeriesCreateStampsSeriesLevelTest(StaffMixin, TestCase):
    def _post(self, **overrides):
        start = date.today() + timedelta(days=14)
        payload = {
            'name': 'Gated Series',
            'slug': '',
            'description': '',
            'start_date': start.strftime('%d/%m/%Y'),
            'start_time': '18:00',
            'duration_hours': '1',
            'occurrences': '3',
            'timezone': 'Europe/Berlin',
            'required_level': '20',
            'kind': 'standard',
            'platform': 'zoom',
        }
        payload.update(overrides)
        return self.client.post('/studio/event-series/new', payload)

    def test_create_writes_level_on_series_and_each_occurrence(self):
        response = self._post(required_level='20')
        self.assertEqual(response.status_code, 302)
        series = EventSeries.objects.get(slug='gated-series')
        self.assertEqual(series.required_level, 20)
        self.assertEqual(series.events.count(), 3)
        for event in series.events.all():
            self.assertEqual(event.required_level, 20)


class StudioAddOccurrenceInheritsLevelTest(StaffMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.series = EventSeries.objects.create(
            name='Main Series',
            slug='main-series-studio',
            start_time=time(18, 0),
            required_level=20,
        )

    def test_pre_filled_level_inherits_series_level(self):
        start = (date.today() + timedelta(days=30)).strftime('%d/%m/%Y')
        # Omitting required_level mirrors leaving the pre-filled default.
        response = self.client.post(
            f'/studio/event-series/{self.series.pk}/add-occurrence',
            {'start_date': start, 'duration_hours': '1', 'timezone': 'UTC'},
        )
        self.assertEqual(response.status_code, 302)
        new_event = self.series.events.order_by('-series_position').first()
        self.assertEqual(new_event.required_level, 20)

    def test_submitting_matching_level_keeps_series_level(self):
        start = (date.today() + timedelta(days=31)).strftime('%d/%m/%Y')
        response = self.client.post(
            f'/studio/event-series/{self.series.pk}/add-occurrence',
            {
                'start_date': start,
                'duration_hours': '1',
                'timezone': 'UTC',
                'required_level': '20',
            },
        )
        self.assertEqual(response.status_code, 302)
        new_event = self.series.events.order_by('-series_position').first()
        self.assertEqual(new_event.required_level, 20)


class StudioAddOccurrenceOverrideTest(StaffMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.series = EventSeries.objects.create(
            name='Override Series',
            slug='override-series-studio',
            start_time=time(18, 0),
            required_level=20,
        )

    def test_confirmed_mismatch_is_saved_no_server_reject(self):
        start = (date.today() + timedelta(days=30)).strftime('%d/%m/%Y')
        response = self.client.post(
            f'/studio/event-series/{self.series.pk}/add-occurrence',
            {
                'start_date': start,
                'duration_hours': '1',
                'timezone': 'UTC',
                'required_level': '0',
            },
        )
        self.assertEqual(response.status_code, 302)
        new_event = self.series.events.order_by('-series_position').first()
        self.assertEqual(new_event.required_level, 0)


class StudioAddOccurrenceFormLevelSelectorTest(StaffMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.series = EventSeries.objects.create(
            name='Form Series',
            slug='form-series-studio',
            start_time=time(18, 0),
            required_level=20,
        )

    def test_detail_page_exposes_series_level_for_confirmation(self):
        response = self.client.get(
            f'/studio/event-series/{self.series.pk}/',
        )
        self.assertContains(response, 'data-series-required-level="20"')
        self.assertContains(response, 'add-occurrence-level')


class StudioSeriesMetadataEditLevelTest(StaffMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.series = EventSeries.objects.create(
            name='Edit Level Series',
            slug='edit-level-series',
            start_time=time(18, 0),
            required_level=0,
        )
        base = timezone.now().replace(microsecond=0) + timedelta(days=30)
        cls.occ_free = Event.objects.create(
            title='Edit Level — 1',
            slug='edit-level-1',
            start_datetime=base,
            end_datetime=base + timedelta(hours=1),
            status='upcoming',
            origin='studio',
            event_series=cls.series,
            series_position=1,
            required_level=0,
        )
        cls.occ_main = Event.objects.create(
            title='Edit Level — 2',
            slug='edit-level-2',
            start_datetime=base + timedelta(days=7),
            end_datetime=base + timedelta(days=7, hours=1),
            status='upcoming',
            origin='studio',
            event_series=cls.series,
            series_position=2,
            required_level=20,
        )

    def test_editing_series_level_does_not_rewrite_occurrences(self):
        response = self.client.post(
            f'/studio/event-series/{self.series.pk}/',
            {
                'name': self.series.name,
                'slug': self.series.slug,
                'description': '',
                'required_level': '20',
                'is_active': 'on',
            },
        )
        self.assertEqual(response.status_code, 302)
        self.series.refresh_from_db()
        self.assertEqual(self.series.required_level, 20)
        self.occ_free.refresh_from_db()
        self.occ_main.refresh_from_db()
        self.assertEqual(self.occ_free.required_level, 0)
        self.assertEqual(self.occ_main.required_level, 20)
