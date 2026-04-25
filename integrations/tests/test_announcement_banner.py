"""Tests for the AnnouncementBanner model and its cross-process cache helpers."""

from django.core.cache import caches
from django.db import connection
from django.test import RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext

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


class AnnouncementBannerCrossProcessCacheTest(TestCase):
    """The cache must be cross-process: an invalidation by one caller is
    observed by another caller that has already primed its own read.

    This is what the module-level dict implementation got wrong: each
    process had its own dict and ``clear_announcement_banner_cache()`` only
    affected the caller's dict. With the cross-process Django cache, an
    invalidation from any process is visible to every other process.
    """

    def setUp(self):
        clear_announcement_banner_cache()

    def tearDown(self):
        clear_announcement_banner_cache()

    def test_invalidation_is_visible_to_another_caller(self):
        # Caller A primes the cache with the original message.
        banner = AnnouncementBanner.get_singleton()
        banner.message = 'Original'
        banner.is_enabled = True
        banner.save()
        clear_announcement_banner_cache()
        first = get_announcement_banner()
        self.assertEqual(first.message, 'Original')

        # The DB row changes (by a different process / writer).
        AnnouncementBanner.objects.filter(pk=banner.pk).update(message='Updated')

        # Caller B (the writer) invalidates the shared cache.
        clear_announcement_banner_cache()

        # Caller A — without restarting, without touching its own dict —
        # must now see the updated value, because the cache lives in a
        # cross-process backend rather than in module memory.
        second = get_announcement_banner()
        self.assertEqual(second.message, 'Updated')

    def test_uses_django_cache_not_module_dict(self):
        # Smoke check: the named django_q cache contains the banner key
        # after a read. This guards against accidental regression to the
        # in-process dict.
        AnnouncementBanner.get_singleton()
        get_announcement_banner()
        self.assertIsNotNone(caches['django_q'].get('announcement_banner:v1'))

    def test_missing_row_is_cached_via_sentinel(self):
        # When no row exists, the second call must not hit the DB.
        AnnouncementBanner.objects.all().delete()
        clear_announcement_banner_cache()

        with CaptureQueriesContext(connection) as ctx:
            self.assertIsNone(get_announcement_banner())
            queries_after_first = len(ctx.captured_queries)
            self.assertIsNone(get_announcement_banner())
            queries_after_second = len(ctx.captured_queries)

        # The second call must add zero AnnouncementBanner queries.
        self.assertEqual(queries_after_first, queries_after_second)


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
