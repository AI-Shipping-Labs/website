"""Tests for the Studio event Regenerate Banner endpoint + edit panel (#895).

Mirrors ``test_regenerate_banner_views.py`` for the new ``event`` content
type: access gating, GET -> 405, staff POST enqueues exactly one task,
and the event edit page renders the placeholder / image / disabled-button
states of the shared banner-generator section.
"""

import datetime as dt
import os
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase

from events.models import Event
from integrations.config import clear_config_cache
from integrations.models import IntegrationSetting

User = get_user_model()

ENQUEUE_PATCH = 'studio.views.banner_regenerate.enqueue_force'
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
    for key, value in (
        ('BANNER_GENERATOR_FUNCTION_URL', 'https://lambda.example.com/'),
        ('BANNER_GENERATOR_AUTH_TOKEN', 'token-abc'),
        ('AWS_S3_CONTENT_BUCKET', 'content-bucket'),
        ('CONTENT_CDN_BASE', 'https://cdn.example.com'),
    ):
        IntegrationSetting.objects.update_or_create(
            key=key,
            defaults={
                'value': value if enabled else '',
                'is_secret': False,
                'group': 'banner_generator',
                'description': '',
            },
        )
    if not enabled:
        IntegrationSetting.objects.filter(
            key__startswith='BANNER_GENERATOR_',
        ).delete()
    clear_config_cache()


def _make_event(**overrides):
    defaults = {
        'slug': 'studio-event',
        'title': 'Studio Event',
        'description': 'A live session.',
        'start_datetime': dt.datetime(2026, 5, 28, 16, 0, tzinfo=dt.timezone.utc),
        'timezone': 'Europe/Berlin',
        'origin': 'studio',
        'status': 'upcoming',
    }
    defaults.update(overrides)
    return Event.objects.create(**defaults)


class EventRegenerateBannerAccessTest(
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
        self.event = _make_event()
        _set_banner_generator(enabled=True)

    @property
    def url(self):
        return f'/studio/events/{self.event.pk}/regenerate-banner'

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

    @patch(ENQUEUE_PATCH)
    def test_staff_post_force_enqueues_event_and_redirects(self, mock_enqueue):
        mock_enqueue.return_value = 'task-id-evt'
        self.client.login(email='staff@test.com', password='testpass')
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, f'/studio/events/{self.event.pk}/edit')
        mock_enqueue.assert_called_once_with('event', self.event.pk)


class EventRegenerateNoOpWhenDisabledTest(
    _BannerGeneratorCacheCleanupMixin, TestCase,
):

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='testpass')
        _set_banner_generator(enabled=False)

    @patch(DISPATCH_ASYNC_PATCH)
    def test_staff_post_does_not_enqueue_when_disabled(self, mock_async):
        event = _make_event()
        response = self.client.post(
            f'/studio/events/{event.pk}/regenerate-banner',
        )
        self.assertEqual(response.status_code, 302)
        mock_async.assert_not_called()


ENQUEUE_IF_MISSING_PATCH = 'studio.views.events.enqueue_if_missing'


class EventCreateEditEnqueueHookTest(
    _BannerGeneratorCacheCleanupMixin, TestCase,
):
    """Issue #895: Studio create/edit enqueue an auto-banner render.

    The hook is fire-and-forget: ``enqueue_if_missing('event', pk)`` is
    called after every ``event.save()``; its own short-circuits (cover,
    title-hash, disabled) decide whether a task is actually queued. These
    tests assert the call wiring, mocking the dispatcher so they run
    independently of banner-generator config.
    """

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='testpass')

    @patch(ENQUEUE_IF_MISSING_PATCH)
    def test_create_calls_enqueue_with_new_event_pk(self, mock_enqueue):
        response = self.client.post('/studio/events/new', {
            'title': 'Shipping Agents in Production',
            'event_date': '28/05/2026',
            'event_time': '18:00',
        })
        self.assertEqual(response.status_code, 302)
        event = Event.objects.get(title='Shipping Agents in Production')
        mock_enqueue.assert_called_once_with('event', event.pk)

    @patch(ENQUEUE_IF_MISSING_PATCH)
    def test_edit_calls_enqueue_after_save(self, mock_enqueue):
        event = _make_event(title='Original Title')
        mock_enqueue.reset_mock()
        response = self.client.post(
            f'/studio/events/{event.pk}/edit',
            {
                'title': 'Renamed Title',
                'event_date': '28/05/2026',
                'event_time': '18:00',
                'duration_hours': '1',
                'timezone': 'Europe/Berlin',
                'platform': 'zoom',
                'status': 'upcoming',
                'required_level': '0',
            },
        )
        self.assertEqual(response.status_code, 302)
        mock_enqueue.assert_called_once_with('event', event.pk)

    def test_title_change_drifts_title_hash_so_render_requeues(self):
        """Acceptance: renaming drifts the title hash (enqueue would fire)."""
        from integrations.services.banner_generator.dispatch import title_hash

        event = _make_event(title='Original Title')
        Event.objects.filter(pk=event.pk).update(
            auto_banner_url='https://cdn.example.com/banners/event/x.jpg',
            auto_banner_title_hash=title_hash('Original Title'),
        )
        self.client.post(
            f'/studio/events/{event.pk}/edit',
            {
                'title': 'Renamed Title',
                'event_date': '28/05/2026',
                'event_time': '18:00',
                'duration_hours': '1',
                'timezone': 'Europe/Berlin',
                'platform': 'zoom',
                'status': 'upcoming',
                'required_level': '0',
            },
        )
        event.refresh_from_db()
        # The stored hash no longer matches the new title — the
        # already-rendered short-circuit will not fire on the next sync.
        self.assertNotEqual(
            event.auto_banner_title_hash, title_hash(event.title),
        )


class EventEditBannerSectionTest(_BannerGeneratorCacheCleanupMixin, TestCase):

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='testpass')

    def test_placeholder_shown_when_no_banner(self):
        _set_banner_generator(enabled=True)
        event = _make_event()
        response = self.client.get(f'/studio/events/{event.pk}/edit')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="banner-generator-section"')
        self.assertContains(
            response, 'data-testid="banner-generator-placeholder"',
        )
        self.assertContains(response, 'No banner generated yet')

    def test_image_shown_when_banner_url_set(self):
        _set_banner_generator(enabled=True)
        event = _make_event()
        Event.objects.filter(pk=event.pk).update(
            auto_banner_url='https://cdn.example.com/banners/event/x.jpg',
        )
        response = self.client.get(f'/studio/events/{event.pk}/edit')
        self.assertContains(response, 'data-testid="banner-generator-image"')
        self.assertContains(
            response, 'src="https://cdn.example.com/banners/event/x.jpg"',
        )

    def test_regenerate_button_disabled_when_function_url_unset(self):
        _set_banner_generator(enabled=False)
        event = _make_event()
        response = self.client.get(f'/studio/events/{event.pk}/edit')
        self.assertContains(
            response,
            'data-testid="banner-generator-regenerate-button-disabled"',
        )
        self.assertContains(response, '/studio/settings/#content_tools')

    def test_regenerate_button_enabled_when_configured(self):
        _set_banner_generator(enabled=True)
        event = _make_event()
        response = self.client.get(f'/studio/events/{event.pk}/edit')
        self.assertContains(
            response, 'data-testid="banner-generator-regenerate-button"',
        )
        self.assertNotContains(
            response,
            'data-testid="banner-generator-regenerate-button-disabled"',
        )

    def test_section_absent_on_create_form(self):
        """The create flow has no event yet, so no banner section renders."""
        _set_banner_generator(enabled=True)
        response = self.client.get('/studio/events/new')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(
            response, 'data-testid="banner-generator-section"',
        )
