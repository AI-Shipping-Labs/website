"""Tests for the Studio event-series auto-banner wiring (issue #896).

Covers: create + inline-edit enqueue hooks, the staff-only "Regenerate
banner" endpoint (access gating, GET -> 405, force-enqueue + redirect,
no-op when disabled), and the shared banner-generator section rendering
on the series detail page. The dispatcher is mocked so these tests never
touch the Lambda or a real django-q worker.
"""

import datetime as dt
import os
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase

from events.models import EventSeries
from integrations.config import clear_config_cache
from integrations.models import IntegrationSetting

User = get_user_model()

FORCE_PATCH = 'studio.views.event_series.enqueue_force'
IF_MISSING_PATCH = 'studio.views.event_series.enqueue_if_missing'
DISPATCH_ASYNC_PATCH = (
    'integrations.services.banner_generator.dispatch.async_task'
)


class _BannerGeneratorCacheCleanupMixin:
    """Clear the in-process config cache before and after each test."""

    def setUp(self):
        super().setUp()
        env_patch = patch.dict(os.environ, {
            'BANNER_GENERATOR_FUNCTION_URL': '',
            'BANNER_GENERATOR_AUTH_TOKEN': '',
            'AWS_S3_CONTENT_BUCKET': '',
        })
        env_patch.start()
        self.addCleanup(env_patch.stop)
        clear_config_cache()
        self.addCleanup(clear_config_cache)


def _set_banner_generator(enabled=True):
    if not enabled:
        IntegrationSetting.objects.filter(
            key__startswith='BANNER_GENERATOR_',
        ).delete()
        clear_config_cache()
        return
    for key, value in (
        ('BANNER_GENERATOR_FUNCTION_URL', 'https://lambda.example.com/'),
        ('BANNER_GENERATOR_AUTH_TOKEN', 'token-abc'),
        ('AWS_S3_CONTENT_BUCKET', 'content-bucket'),
        ('CONTENT_CDN_BASE', 'https://cdn.example.com'),
    ):
        IntegrationSetting.objects.update_or_create(
            key=key,
            defaults={
                'value': value,
                'is_secret': False,
                'group': 'banner_generator',
                'description': '',
            },
        )
    clear_config_cache()


def _make_series(**overrides):
    defaults = {
        'name': 'AI Agents Office Hours',
        'slug': 'ai-agents-office-hours',
        'description': 'Weekly office hours.',
        'cadence': 'weekly',
        'cadence_weeks': 1,
        'day_of_week': 2,
        'start_time': dt.time(18, 0),
        'timezone': 'Europe/Berlin',
    }
    defaults.update(overrides)
    return EventSeries.objects.create(**defaults)


class SeriesRegenerateBannerAccessTest(
    _BannerGeneratorCacheCleanupMixin, TestCase,
):

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.normal = User.objects.create_user(
            email='user@test.com', password='testpass',
        )
        self.series = _make_series()
        _set_banner_generator(enabled=True)

    @property
    def url(self):
        return f'/studio/event-series/{self.series.pk}/regenerate-banner'

    def test_anonymous_post_redirects_to_login(self):
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)

    def test_non_staff_post_returns_403(self):
        self.client.login(email='user@test.com', password='testpass')
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 403)

    def test_get_returns_405(self):
        self.client.login(email='staff@test.com', password='testpass')
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 405)

    @patch(FORCE_PATCH)
    def test_staff_post_force_enqueues_and_redirects(self, mock_force):
        mock_force.return_value = 'task-id-series'
        self.client.login(email='staff@test.com', password='testpass')
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.url, f'/studio/event-series/{self.series.pk}/',
        )
        mock_force.assert_called_once_with('event_series', self.series.pk)


class SeriesRegenerateNoOpWhenDisabledTest(
    _BannerGeneratorCacheCleanupMixin, TestCase,
):

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='testpass')
        self.series = _make_series()
        _set_banner_generator(enabled=False)

    @patch(DISPATCH_ASYNC_PATCH)
    def test_disabled_post_does_not_enqueue(self, mock_async):
        response = self.client.post(
            f'/studio/event-series/{self.series.pk}/regenerate-banner',
        )
        self.assertEqual(response.status_code, 302)
        mock_async.assert_not_called()


class SeriesCreateEditEnqueueHookTest(
    _BannerGeneratorCacheCleanupMixin, TestCase,
):
    """Create + inline name/description edit enqueue an auto-banner render."""

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='testpass')

    @patch(IF_MISSING_PATCH)
    def test_create_calls_enqueue_with_new_series_pk(self, mock_enqueue):
        response = self.client.post('/studio/event-series/new', {
            'name': 'New Office Hours',
            'start_date': '15/07/2026',
            'start_time': '18:00',
            'duration_hours': '1',
            'occurrences': '4',
            'timezone': 'Europe/Berlin',
            'required_level': '0',
            'kind': 'standard',
            'platform': 'zoom',
        })
        self.assertEqual(response.status_code, 302)
        series = EventSeries.objects.get(name='New Office Hours')
        mock_enqueue.assert_called_once_with('event_series', series.pk)

    @patch(IF_MISSING_PATCH)
    def test_inline_edit_calls_enqueue_after_save(self, mock_enqueue):
        series = _make_series()
        mock_enqueue.reset_mock()
        response = self.client.post(
            f'/studio/event-series/{series.pk}/',
            {
                'name': 'Renamed Series',
                'slug': series.slug,
                'description': series.description,
            },
        )
        self.assertEqual(response.status_code, 302)
        series.refresh_from_db()
        self.assertEqual(series.name, 'Renamed Series')
        mock_enqueue.assert_called_once_with('event_series', series.pk)


class SeriesDetailBannerSectionRenderTest(
    _BannerGeneratorCacheCleanupMixin, TestCase,
):
    """The shared banner-generator section renders on the series detail."""

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='testpass')
        _set_banner_generator(enabled=True)

    def test_placeholder_when_no_banner(self):
        series = _make_series()
        response = self.client.get(f'/studio/event-series/{series.pk}/')
        self.assertContains(response, 'data-testid="banner-generator-section"')
        self.assertContains(
            response, 'data-testid="banner-generator-placeholder"',
        )

    def test_image_shown_when_banner_set(self):
        series = _make_series()
        EventSeries.objects.filter(pk=series.pk).update(
            auto_banner_url='https://cdn.example.com/banners/event_series/x.jpg',
        )
        response = self.client.get(f'/studio/event-series/{series.pk}/')
        self.assertContains(response, 'data-testid="banner-generator-image"')
        self.assertContains(
            response, 'https://cdn.example.com/banners/event_series/x.jpg',
        )
