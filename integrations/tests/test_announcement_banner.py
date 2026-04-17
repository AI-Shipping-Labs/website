"""Tests for the AnnouncementBanner model and its in-process cache helpers."""

from django.test import RequestFactory, TestCase

from integrations.middleware import (
    clear_announcement_banner_cache,
    get_announcement_banner,
)
from integrations.models import AnnouncementBanner
from website.context_processors import announcement_banner_context


class AnnouncementBannerModelTest(TestCase):
    """Cover the AnnouncementBanner model fields and the singleton helper."""

    def test_get_singleton_creates_first_row(self):
        AnnouncementBanner.objects.all().delete()
        banner = AnnouncementBanner.get_singleton()
        self.assertEqual(banner.pk, 1)
        self.assertFalse(banner.is_enabled)
        self.assertTrue(banner.is_dismissible)
        self.assertEqual(banner.version, 1)
        self.assertEqual(banner.link_label, 'Read more')

    def test_get_singleton_returns_existing(self):
        first = AnnouncementBanner.get_singleton()
        first.message = 'Hello world'
        first.save()
        second = AnnouncementBanner.get_singleton()
        self.assertEqual(second.pk, first.pk)
        self.assertEqual(second.message, 'Hello world')

    def test_str_representation(self):
        banner = AnnouncementBanner.get_singleton()
        banner.is_enabled = True
        banner.message = 'Spring cohort starts soon'
        banner.save()
        self.assertIn('on', str(banner))
        self.assertIn('Spring cohort', str(banner))


class AnnouncementBannerCacheTest(TestCase):
    """The module-level cache must invalidate after explicit clear calls."""

    def setUp(self):
        clear_announcement_banner_cache()

    def tearDown(self):
        clear_announcement_banner_cache()

    def test_cache_returns_none_when_no_row(self):
        AnnouncementBanner.objects.all().delete()
        clear_announcement_banner_cache()
        self.assertIsNone(get_announcement_banner())

    def test_cache_returns_singleton(self):
        banner = AnnouncementBanner.get_singleton()
        banner.message = 'Cached message'
        banner.is_enabled = True
        banner.save()
        clear_announcement_banner_cache()
        cached = get_announcement_banner()
        self.assertIsNotNone(cached)
        self.assertEqual(cached.message, 'Cached message')

    def test_cache_invalidation_picks_up_changes(self):
        banner = AnnouncementBanner.get_singleton()
        banner.message = 'Original'
        banner.save()
        clear_announcement_banner_cache()

        # Prime the cache with the original message.
        first = get_announcement_banner()
        self.assertEqual(first.message, 'Original')

        # Mutate the row in the DB without going through the cache helper.
        AnnouncementBanner.objects.filter(pk=banner.pk).update(message='Updated')

        # Without invalidation, the cache still returns the stale value.
        self.assertEqual(get_announcement_banner().message, 'Original')

        # Once cleared, the next call refetches and sees the new message.
        clear_announcement_banner_cache()
        self.assertEqual(get_announcement_banner().message, 'Updated')


class AnnouncementBannerContextProcessorTest(TestCase):
    """The context processor returns the banner only on public URLs."""

    def setUp(self):
        self.factory = RequestFactory()
        clear_announcement_banner_cache()

    def tearDown(self):
        clear_announcement_banner_cache()

    def _enable_banner(self, message='Public banner'):
        banner = AnnouncementBanner.get_singleton()
        banner.message = message
        banner.is_enabled = True
        banner.save()
        clear_announcement_banner_cache()
        return banner

    def test_returns_banner_on_public_path(self):
        self._enable_banner()
        ctx = announcement_banner_context(self.factory.get('/'))
        self.assertIsNotNone(ctx['announcement_banner'])
        self.assertEqual(ctx['announcement_banner'].message, 'Public banner')

    def test_returns_none_when_disabled(self):
        banner = AnnouncementBanner.get_singleton()
        banner.message = 'Hidden'
        banner.is_enabled = False
        banner.save()
        clear_announcement_banner_cache()
        ctx = announcement_banner_context(self.factory.get('/'))
        self.assertIsNone(ctx['announcement_banner'])

    def test_returns_none_when_no_row(self):
        AnnouncementBanner.objects.all().delete()
        clear_announcement_banner_cache()
        ctx = announcement_banner_context(self.factory.get('/'))
        self.assertIsNone(ctx['announcement_banner'])

    def test_returns_none_under_studio(self):
        self._enable_banner()
        ctx = announcement_banner_context(self.factory.get('/studio/'))
        self.assertIsNone(ctx['announcement_banner'])

    def test_returns_none_under_admin(self):
        self._enable_banner()
        ctx = announcement_banner_context(self.factory.get('/admin/'))
        self.assertIsNone(ctx['announcement_banner'])

    def test_returns_banner_on_nested_public_path(self):
        self._enable_banner()
        ctx = announcement_banner_context(self.factory.get('/blog/some-article'))
        self.assertIsNotNone(ctx['announcement_banner'])
