"""Query-budget tests for request-path DatabaseCache memoization (issue #1504).

LocMemCache hides the SQL amplification. These tests force the production
``django_q`` backend (``DatabaseCache``) using the same override pattern as
``studio/tests/test_worker_health_cache.py``.
"""

from unittest.mock import patch

from django.core.cache import caches
from django.core.management import call_command
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext

from content.nav_availability import (
    get_marketing_pages_nav,
    has_published_downloads_for_nav,
    set_marketing_pages_nav,
    set_published_downloads_nav_available,
)
from integrations.config import (
    clear_config_cache,
    get_config,
    reset_local_config_cache,
)
from integrations.middleware import (
    clear_announcement_banner_cache,
    clear_redirect_cache,
    get_active_redirects,
    get_announcement_banner,
)
from integrations.models import AnnouncementBanner, IntegrationSetting, Redirect
from integrations.shared_cache import (
    LOCAL_TTL_SECONDS,
    SHARED_CACHE_ALIAS,
    get_shared_cache,
    reset_local_shared_cache_memo,
    restore_local_shared_cache_memo,
    snapshot_local_shared_cache_memo,
)

CACHE_TABLE = 'test_django_q_cache'

DATABASE_CACHE_OVERRIDE = {
    'CACHES': {
        'default': {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        },
        'django_q': {
            'BACKEND': 'django.core.cache.backends.db.DatabaseCache',
            'LOCATION': CACHE_TABLE,
        },
    },
}


class _StubMarketingPage:
    """Picklable stand-in so DatabaseCache can store marketing-nav entries."""

    def __init__(self, nav_text, url):
        self.nav_text = nav_text
        self._url = url

    def get_absolute_url(self):
        return self._url


def _queries_mentioning(ctx, fragment):
    needle = fragment.lower()
    return [
        query['sql']
        for query in ctx.captured_queries
        if needle in query['sql'].lower()
    ]


def _other_process(write):
    """Run ``write`` without updating this process's local TTL memo."""
    snapshot = snapshot_local_shared_cache_memo()
    write()
    restore_local_shared_cache_memo(snapshot)


@override_settings(**DATABASE_CACHE_OVERRIDE)
class SharedCacheQueryBudgetTest(TestCase):
    """Warm request-path helpers must not amplify DatabaseCache SQL."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        call_command('createcachetable', CACHE_TABLE, verbosity=0)

    def setUp(self):
        caches.close_all()
        reset_local_shared_cache_memo()
        reset_local_config_cache()
        clear_announcement_banner_cache()
        clear_redirect_cache()
        set_published_downloads_nav_available(False)
        set_marketing_pages_nav(
            {'about': [], 'community': [], 'resources': []},
        )
        self.addCleanup(reset_local_shared_cache_memo)
        self.addCleanup(reset_local_config_cache)
        self.addCleanup(clear_announcement_banner_cache)
        self.addCleanup(clear_redirect_cache)

    def _seed_public_header_state(self):
        IntegrationSetting.objects.update_or_create(
            key='STRIPE_CUSTOMER_PORTAL_URL',
            defaults={
                'value': 'https://billing.example.com/portal',
                'group': 'stripe',
            },
        )
        IntegrationSetting.objects.update_or_create(
            key='GOOGLE_ANALYTICS_ID',
            defaults={'value': 'G-TEST1504', 'group': 'analytics'},
        )
        for key, value in (
            ('SOCIAL_YOUTUBE_URL', 'https://youtube.com/@aisl'),
            ('SOCIAL_LINKEDIN_URL', 'https://linkedin.com/company/aisl'),
            ('SOCIAL_GITHUB_URL', 'https://github.com/aisl'),
            ('SOCIAL_X_URL', 'https://x.com/aisl'),
        ):
            IntegrationSetting.objects.update_or_create(
                key=key,
                defaults={'value': value, 'group': 'site'},
            )
        banner = AnnouncementBanner.get_singleton()
        banner.message = 'Query-budget banner'
        banner.is_enabled = True
        banner.link_url = '/blog'
        banner.save()
        Redirect.objects.create(
            source_path='/budget-old',
            target_path='/budget-new',
            redirect_type=301,
            is_active=True,
        )
        set_published_downloads_nav_available(True)
        set_marketing_pages_nav({
            'about': [_StubMarketingPage('Budget about', '/budget-about')],
            'community': [],
            'resources': [],
        })
        clear_config_cache()
        clear_announcement_banner_cache()
        clear_redirect_cache()

    def _warm_homepage(self):
        self.client.cookies['aslab_analytics_consent'] = 'granted'
        warm = self.client.get('/')
        self.assertContains(warm, 'data-testid="desktop-primary-nav"')
        return warm

    def test_warm_homepage_cache_sql_stays_within_budget(self):
        self._seed_public_header_state()
        self._warm_homepage()

        with CaptureQueriesContext(connection) as ctx:
            response = self.client.get('/')
            cache_sql = _queries_mentioning(ctx, CACHE_TABLE)
            get_config('STRIPE_CUSTOMER_PORTAL_URL', '')
            get_config('SOCIAL_YOUTUBE_URL', '')
            get_config('SOCIAL_LINKEDIN_URL', '')
            get_config('SOCIAL_GITHUB_URL', '')
            get_config('SOCIAL_X_URL', '')
            get_announcement_banner()
            get_active_redirects()
            has_published_downloads_for_nav()
            get_marketing_pages_nav()
            cache_sql_after_helpers = _queries_mentioning(ctx, CACHE_TABLE)
            setting_sql = _queries_mentioning(
                ctx, 'integrations_integrationsetting',
            )

        self.assertContains(response, 'data-testid="desktop-primary-nav"')
        self.assertLessEqual(len(cache_sql), 2, cache_sql)
        self.assertEqual(
            len(cache_sql_after_helpers),
            len(cache_sql),
            cache_sql_after_helpers,
        )
        self.assertEqual(setting_sql, [])

        self.assertEqual(
            response.context['stripe_customer_portal_url'],
            'https://billing.example.com/portal',
        )
        self.assertEqual(
            response.context['footer_social'],
            {
                'youtube': 'https://youtube.com/@aisl',
                'linkedin': 'https://linkedin.com/company/aisl',
                'github': 'https://github.com/aisl',
                'x': 'https://x.com/aisl',
            },
        )
        self.assertEqual(response.context['google_analytics_id'], 'G-TEST1504')
        self.assertTrue(response.context['has_published_downloads'])
        about = response.context['marketing_nav']['about']
        self.assertEqual(about[0].nav_text, 'Budget about')
        self.assertEqual(about[0].get_absolute_url(), '/budget-about')
        self.assertIsNotNone(response.context['announcement_banner'])
        self.assertEqual(
            response.context['announcement_banner'].message,
            'Query-budget banner',
        )

    def test_repeated_get_config_issues_at_most_one_stamp_read(self):
        IntegrationSetting.objects.update_or_create(
            key='STRIPE_CUSTOMER_PORTAL_URL',
            defaults={
                'value': 'https://billing.example.com/portal',
                'group': 'stripe',
            },
        )
        clear_config_cache()
        get_config('STRIPE_CUSTOMER_PORTAL_URL', '')

        backend = caches[SHARED_CACHE_ALIAS]
        original_get = backend.get
        stamp_reads = []

        def counting_get(key, default=None, version=None):
            if key == 'integration_settings_stamp':
                stamp_reads.append(key)
            return original_get(key, default, version=version)

        with patch.object(backend, 'get', side_effect=counting_get):
            with CaptureQueriesContext(connection) as ctx:
                for _ in range(10):
                    self.assertEqual(
                        get_config('STRIPE_CUSTOMER_PORTAL_URL', ''),
                        'https://billing.example.com/portal',
                    )
        self.assertLessEqual(len(stamp_reads), 1, stamp_reads)
        self.assertEqual(
            _queries_mentioning(ctx, 'integrations_integrationsetting'),
            [],
        )

    def test_banner_redirect_and_nav_propagate_after_local_ttl(self):
        from integrations import shared_cache as shared_cache_module

        banner = AnnouncementBanner.get_singleton()
        banner.message = 'Original banner'
        banner.is_enabled = True
        banner.save()
        clear_announcement_banner_cache()
        Redirect.objects.create(
            source_path='/ttl-old',
            target_path='/ttl-new',
            redirect_type=301,
            is_active=True,
        )
        clear_redirect_cache()
        set_published_downloads_nav_available(False)
        set_marketing_pages_nav(
            {'about': [], 'community': [], 'resources': []},
        )

        clock = {'now': 1_000.0}

        def fake_now():
            return clock['now']

        with patch.object(shared_cache_module, 'monotonic_time', fake_now):
            reset_local_shared_cache_memo()
            self.assertEqual(
                get_announcement_banner().message, 'Original banner',
            )
            self.assertIn('/ttl-old', get_active_redirects())
            self.assertFalse(has_published_downloads_for_nav())
            self.assertEqual(get_marketing_pages_nav()['about'], [])

            AnnouncementBanner.objects.filter(pk=banner.pk).update(
                message='Updated banner',
            )
            Redirect.objects.filter(source_path='/ttl-old').update(
                is_active=False,
            )

            def publish():
                clear_announcement_banner_cache()
                clear_redirect_cache()
                set_published_downloads_nav_available(True)
                set_marketing_pages_nav({
                    'about': [
                        _StubMarketingPage('TTL about', '/ttl-about'),
                    ],
                    'community': [],
                    'resources': [],
                })

            _other_process(publish)

            self.assertEqual(
                get_announcement_banner().message, 'Original banner',
            )
            self.assertIn('/ttl-old', get_active_redirects())
            self.assertFalse(has_published_downloads_for_nav())
            self.assertEqual(get_marketing_pages_nav()['about'], [])

            clock['now'] += LOCAL_TTL_SECONDS + 0.01
            self.assertEqual(
                get_announcement_banner().message, 'Updated banner',
            )
            self.assertNotIn('/ttl-old', get_active_redirects())
            self.assertTrue(has_published_downloads_for_nav())
            about = get_marketing_pages_nav()['about']
            self.assertEqual(about[0].nav_text, 'TTL about')

    def test_same_process_invalidation_is_immediate(self):
        banner = AnnouncementBanner.get_singleton()
        banner.message = 'Before save'
        banner.is_enabled = True
        banner.save()
        clear_announcement_banner_cache()
        self.assertEqual(get_announcement_banner().message, 'Before save')

        AnnouncementBanner.objects.filter(pk=banner.pk).update(
            message='After save',
        )
        clear_announcement_banner_cache()
        self.assertEqual(get_announcement_banner().message, 'After save')

        set_published_downloads_nav_available(False)
        self.assertFalse(has_published_downloads_for_nav())
        set_published_downloads_nav_available(True)
        self.assertTrue(has_published_downloads_for_nav())

    def test_memoized_miss_does_not_hit_backend_again(self):
        reset_local_shared_cache_memo()
        backend = caches[SHARED_CACHE_ALIAS]
        original_get = backend.get
        reads = []

        def counting_get(key, default=None, version=None):
            reads.append(key)
            return original_get(key, default, version=version)

        with patch.object(backend, 'get', side_effect=counting_get):
            self.assertIsNone(get_shared_cache('missing-budget-key'))
            self.assertIsNone(get_shared_cache('missing-budget-key'))
        self.assertEqual(reads, ['missing-budget-key'])
