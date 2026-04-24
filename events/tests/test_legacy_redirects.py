"""Tests for legacy recording URL redirects (issue #294).

The old /event-recordings list and /event-recordings/<slug> detail URLs
must now 301-redirect to the unified /events surface so bookmarks and
inbound links keep working.
"""

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from events.models import Event


class LegacyRecordingListRedirectTest(TestCase):
    """GET /event-recordings -> 301 -> /events?filter=past."""

    def test_list_redirect_is_permanent(self):
        response = self.client.get('/event-recordings')
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response['Location'], '/events?filter=past')

    def test_list_redirect_follows_to_past_events(self):
        Event.objects.create(
            title='Recorded Workshop',
            slug='recorded-workshop',
            start_datetime=timezone.now() - timedelta(days=7),
            status='completed',
            recording_url='https://youtube.com/watch?v=test',
            published=True,
        )
        response = self.client.get('/event-recordings', follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'events/events_list.html')
        self.assertContains(response, 'Recorded Workshop')


class LegacyRecordingDetailRedirectTest(TestCase):
    """GET /event-recordings/<slug> -> 301 -> /events/<slug>."""

    def test_detail_redirect_is_permanent(self):
        response = self.client.get('/event-recordings/some-slug')
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response['Location'], '/events/some-slug')

    def test_detail_redirect_preserves_slug(self):
        response = self.client.get(
            '/event-recordings/my-very-specific-slug-123',
        )
        self.assertEqual(response.status_code, 301)
        self.assertEqual(
            response['Location'],
            '/events/my-very-specific-slug-123',
        )

    def test_detail_redirect_follows_to_event_detail(self):
        Event.objects.create(
            title='Building AI Agents',
            slug='building-ai-agents',
            description='Workshop on agents.',
            start_datetime=timezone.now() - timedelta(days=7),
            status='completed',
            recording_url='https://youtube.com/watch?v=abc',
            published=True,
        )
        response = self.client.get(
            '/event-recordings/building-ai-agents',
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'events/event_detail.html')
        self.assertContains(response, 'Building AI Agents')
