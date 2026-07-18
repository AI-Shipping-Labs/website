"""Production API parity for idempotent series draft publication (#1285)."""

from datetime import time, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.urls import reverse
from django.utils import timezone

from accounts.models import Token
from events.models import Event, EventSeries

User = get_user_model()


@tag('core')
class EventSeriesPublishDraftsApiTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='api-publish-1285@test.com', password='pw', is_staff=True,
        )
        cls.token = Token.objects.create(user=cls.staff, name='publish-1285')
        cls.series = EventSeries.objects.create(
            name='API Publish', slug='api-publish-1285', cadence='weekly',
            day_of_week=1, start_time=time(17), timezone='UTC',
        )
        cls.other_series = EventSeries.objects.create(
            name='Other API Publish', slug='other-api-publish-1285',
            cadence='weekly', day_of_week=2, start_time=time(17), timezone='UTC',
        )
        start = timezone.now() + timedelta(days=7)
        cls.draft_ids = []
        for index in (1, 2):
            event = Event.objects.create(
                title=f'API Draft {index}', slug=f'api-draft-1285-{index}',
                start_datetime=start + timedelta(days=index),
                status='draft', origin='studio', event_series=cls.series,
                series_position=index,
            )
            cls.draft_ids.append(event.pk)
        cls.cancelled = Event.objects.create(
            title='API Cancelled', slug='api-cancelled-1285',
            start_datetime=start, status='cancelled', origin='studio',
            event_series=cls.series,
        )
        cls.other = Event.objects.create(
            title='Other Draft', slug='other-api-draft-1285',
            start_datetime=start, status='draft', origin='studio',
            event_series=cls.other_series,
        )

    def auth(self):
        return {'HTTP_AUTHORIZATION': f'Token {self.token.key}'}

    @patch('events.services.occurrence_publication.run_occurrence_publication_lifecycle')
    def test_publish_and_retry_return_canonical_summaries(self, lifecycle):
        url = reverse('api_event_series_publish_drafts', args=[self.series.pk])
        response = self.client.post(url, **self.auth())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            'series_id': self.series.pk,
            'published_count': 2,
            'occurrence_ids': self.draft_ids,
        })
        self.assertEqual(lifecycle.call_count, 2)
        self.assertEqual(
            Event.objects.filter(pk__in=self.draft_ids, status='upcoming').count(),
            2,
        )
        self.cancelled.refresh_from_db()
        self.other.refresh_from_db()
        self.assertEqual(self.cancelled.status, 'cancelled')
        self.assertEqual(self.other.status, 'draft')

        retry = self.client.post(url, **self.auth())
        self.assertEqual(retry.status_code, 200)
        self.assertEqual(retry.json(), {
            'series_id': self.series.pk,
            'published_count': 0,
            'occurrence_ids': [],
        })
        self.assertEqual(lifecycle.call_count, 2)

    def test_auth_method_unknown_series_and_session_boundaries(self):
        url = reverse('api_event_series_publish_drafts', args=[self.series.pk])
        self.assertEqual(self.client.post(url).status_code, 401)
        self.client.login(email=self.staff.email, password='pw')
        self.assertEqual(self.client.post(url).status_code, 401)
        self.assertEqual(self.client.get(url, **self.auth()).status_code, 405)
        missing = self.client.post(
            reverse('api_event_series_publish_drafts', args=[999999]),
            **self.auth(),
        )
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.json(), {
            'error': 'Event series not found',
            'code': 'unknown_series',
        })
