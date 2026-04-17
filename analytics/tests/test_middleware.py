"""Tests for CampaignTrackingMiddleware."""

import hashlib
import json
import uuid

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase, override_settings

from analytics.middleware import (
    ANON_ID_COOKIE,
    COOKIE_MAX_AGE,
    FIRST_TOUCH_COOKIE,
    SESSION_LAST_TOUCH,
)
from analytics.models import CampaignVisit
from integrations.models import UtmCampaign


User = get_user_model()


# Browser-like UA so the bot regex doesn't drop our test traffic.
BROWSER_UA = 'Mozilla/5.0 (test browser)'

# Force inline execution of the record_visit task so the visit row exists
# before the test assertions run. The middleware reads `Q_CLUSTER['sync']`
# directly from settings, so this override is enough — no need to set the
# `Q_SYNC` env var when running the analytics test suite.
SYNC_Q_CLUSTER = {'sync': True, 'orm': 'default', 'name': 'test', 'workers': 1}


def make_browser_client():
    """Django test Client with a non-bot User-Agent header."""
    return Client(HTTP_USER_AGENT=BROWSER_UA)


@override_settings(IP_HASH_SALT='', Q_CLUSTER=SYNC_Q_CLUSTER)
class FirstTouchCaptureTest(TestCase):
    """Scenario: First-time visitor from a tagged newsletter link captured."""

    def setUp(self):
        cache.clear()
        UtmCampaign.objects.create(
            name='Launch April 2026',
            slug='launch_april2026',
            default_utm_source='newsletter',
            default_utm_medium='email',
        )

    def test_visit_creates_one_visit_row_with_all_utms(self):
        client = make_browser_client()
        response = client.get(
            '/blog?utm_source=newsletter&utm_medium=email'
            '&utm_campaign=launch_april2026&utm_content=hero'
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(CampaignVisit.objects.count(), 1)
        v = CampaignVisit.objects.get()
        self.assertEqual(v.utm_source, 'newsletter')
        self.assertEqual(v.utm_medium, 'email')
        self.assertEqual(v.utm_campaign, 'launch_april2026')
        self.assertEqual(v.utm_content, 'hero')
        self.assertEqual(v.path, '/blog')
        self.assertIsNotNone(v.campaign_id)
        self.assertEqual(v.campaign.slug, 'launch_april2026')

    def test_response_sets_anon_id_cookie_as_uuid(self):
        client = make_browser_client()
        response = client.get(
            '/blog?utm_source=newsletter&utm_campaign=launch_april2026'
        )
        cookie = response.cookies.get(ANON_ID_COOKIE)
        self.assertIsNotNone(cookie)
        # Must parse as UUID4
        parsed = uuid.UUID(cookie.value)
        self.assertEqual(parsed.version, 4)
        # 90-day max-age
        self.assertEqual(cookie['max-age'], COOKIE_MAX_AGE)
        self.assertEqual(cookie['samesite'], 'Lax')
        self.assertTrue(cookie['httponly'])

    def test_response_sets_first_touch_cookie_with_utm_json(self):
        client = make_browser_client()
        response = client.get(
            '/blog?utm_source=newsletter&utm_medium=email'
            '&utm_campaign=launch_april2026&utm_content=hero'
        )
        cookie = response.cookies.get(FIRST_TOUCH_COOKIE)
        self.assertIsNotNone(cookie)
        payload = json.loads(cookie.value)
        self.assertEqual(payload['source'], 'newsletter')
        self.assertEqual(payload['medium'], 'email')
        self.assertEqual(payload['campaign'], 'launch_april2026')
        self.assertEqual(payload['content'], 'hero')
        self.assertIn('ts', payload)
        self.assertEqual(cookie['max-age'], COOKIE_MAX_AGE)
        self.assertEqual(cookie['samesite'], 'Lax')
        self.assertTrue(cookie['httponly'])


@override_settings(IP_HASH_SALT='', Q_CLUSTER=SYNC_Q_CLUSTER)
class FirstTouchStickyTest(TestCase):
    """Scenario: Returning visitor keeps first-touch but updates last-touch."""

    def setUp(self):
        cache.clear()
        UtmCampaign.objects.create(
            name='Launch', slug='launch_april2026',
            default_utm_source='newsletter', default_utm_medium='email',
        )
        UtmCampaign.objects.create(
            name='Summer', slug='summer_drop',
            default_utm_source='twitter', default_utm_medium='social',
        )

    def test_two_visits_create_two_rows_but_first_touch_set_only_once(self):
        client = make_browser_client()
        r1 = client.get(
            '/blog?utm_source=newsletter&utm_medium=email'
            '&utm_campaign=launch_april2026&utm_content=hero'
        )
        # First response sets first-touch cookie.
        self.assertIn(FIRST_TOUCH_COOKIE, r1.cookies)
        r2 = client.get(
            '/courses?utm_source=twitter&utm_medium=social'
            '&utm_campaign=summer_drop&utm_content=cta_a'
        )
        # Second response does NOT set first-touch (sticky from first request).
        self.assertNotIn(FIRST_TOUCH_COOKIE, r2.cookies)
        # And two visit rows now exist.
        self.assertEqual(CampaignVisit.objects.count(), 2)

    def test_session_last_touch_overwritten_by_second_request(self):
        client = make_browser_client()
        client.get(
            '/blog?utm_source=newsletter&utm_medium=email'
            '&utm_campaign=launch_april2026&utm_content=hero'
        )
        client.get(
            '/courses?utm_source=twitter&utm_medium=social'
            '&utm_campaign=summer_drop&utm_content=cta_a'
        )
        last_touch = client.session.get(SESSION_LAST_TOUCH)
        self.assertIsNotNone(last_touch)
        self.assertEqual(last_touch['source'], 'twitter')
        self.assertEqual(last_touch['campaign'], 'summer_drop')


@override_settings(IP_HASH_SALT='', Q_CLUSTER=SYNC_Q_CLUSTER)
class NoUtmTrackingTest(TestCase):
    """Scenario: visitor browsing without UTMs gets identity but no visit row."""

    def setUp(self):
        cache.clear()

    def test_no_visit_row_when_no_utm(self):
        client = make_browser_client()
        response = client.get('/blog')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(CampaignVisit.objects.count(), 0)

    def test_anon_id_cookie_set_even_without_utm(self):
        client = make_browser_client()
        response = client.get('/blog')
        self.assertIn(ANON_ID_COOKIE, response.cookies)

    def test_first_touch_cookie_NOT_set_without_utm(self):
        client = make_browser_client()
        response = client.get('/blog')
        self.assertNotIn(FIRST_TOUCH_COOKIE, response.cookies)


@override_settings(IP_HASH_SALT='', Q_CLUSTER=SYNC_Q_CLUSTER)
class AuthenticatedUserTrackingTest(TestCase):
    """Scenario: logged-in user clicking a campaign link is attributed."""

    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(email='main@test.com', password='pw1234ABcd')
        UtmCampaign.objects.create(
            name='Reactivation', slug='reactivation',
            default_utm_source='email', default_utm_medium='email',
        )

    def test_logged_in_user_gets_visit_row_with_user_id(self):
        client = make_browser_client()
        client.force_login(self.user)
        client.get(
            '/pricing?utm_source=email&utm_medium=email'
            '&utm_campaign=reactivation&utm_content=upgrade_cta'
        )
        v = CampaignVisit.objects.get()
        self.assertEqual(v.user_id, self.user.pk)
        self.assertNotEqual(v.anonymous_id, '')
        self.assertIsNotNone(v.campaign_id)
        self.assertEqual(v.campaign.slug, 'reactivation')


@override_settings(IP_HASH_SALT='', Q_CLUSTER=SYNC_Q_CLUSTER)
class BotFilterTest(TestCase):
    """Scenario: bot crawls do not pollute analytics."""

    def setUp(self):
        cache.clear()
        UtmCampaign.objects.create(
            name='Launch', slug='launch_april2026',
            default_utm_source='newsletter', default_utm_medium='email',
        )

    def test_googlebot_produces_no_visit(self):
        c = Client(HTTP_USER_AGENT='Googlebot/2.1 (+http://www.google.com/bot.html)')
        r = c.get('/blog?utm_source=newsletter&utm_campaign=launch_april2026')
        self.assertEqual(CampaignVisit.objects.count(), 0)
        self.assertNotIn(ANON_ID_COOKIE, r.cookies)
        self.assertNotIn(FIRST_TOUCH_COOKIE, r.cookies)

    def test_python_requests_produces_no_visit(self):
        c = Client(HTTP_USER_AGENT='python-requests/2.31.0')
        r = c.get('/blog?utm_source=newsletter&utm_campaign=launch_april2026')
        self.assertEqual(CampaignVisit.objects.count(), 0)
        self.assertNotIn(ANON_ID_COOKIE, r.cookies)
        self.assertNotIn(FIRST_TOUCH_COOKIE, r.cookies)

    def test_curl_produces_no_visit(self):
        c = Client(HTTP_USER_AGENT='curl/8.0.1')
        r = c.get('/blog?utm_source=newsletter&utm_campaign=launch_april2026')
        self.assertEqual(CampaignVisit.objects.count(), 0)
        self.assertNotIn(ANON_ID_COOKIE, r.cookies)
        self.assertNotIn(FIRST_TOUCH_COOKIE, r.cookies)


@override_settings(IP_HASH_SALT='', Q_CLUSTER=SYNC_Q_CLUSTER)
class SkipPathsTest(TestCase):
    """Scenario: system endpoints are excluded from tracking."""

    def setUp(self):
        cache.clear()

    def test_admin_path_skipped(self):
        c = make_browser_client()
        c.get('/admin/login/?utm_source=spam&utm_campaign=test')
        self.assertEqual(CampaignVisit.objects.count(), 0)

    def test_static_path_skipped(self):
        c = make_browser_client()
        # URL may 404 — middleware should still skip before any DB write.
        c.get('/static/css/main.css?utm_source=spam&utm_campaign=test')
        self.assertEqual(CampaignVisit.objects.count(), 0)

    def test_webhook_post_skipped(self):
        c = make_browser_client()
        # POST to webhook is doubly skipped (non-GET + skip path).
        c.post('/api/webhooks/payments?utm_source=spam&utm_campaign=test', data={})
        self.assertEqual(CampaignVisit.objects.count(), 0)

    def test_sitemap_skipped(self):
        c = make_browser_client()
        c.get('/sitemap.xml?utm_source=spam&utm_campaign=test')
        self.assertEqual(CampaignVisit.objects.count(), 0)


@override_settings(IP_HASH_SALT='', Q_CLUSTER=SYNC_Q_CLUSTER)
class NonGetSkippedTest(TestCase):
    """Acceptance: POST requests produce ZERO visit rows."""

    def setUp(self):
        cache.clear()

    def test_post_with_utms_does_not_create_visit(self):
        c = make_browser_client()
        # Hit a path that accepts POST. Use the email subscribe endpoint or
        # any URL — even if it 404s, middleware drops it before DB write.
        c.post('/blog?utm_source=newsletter&utm_campaign=test', data={})
        self.assertEqual(CampaignVisit.objects.count(), 0)


@override_settings(IP_HASH_SALT='', Q_CLUSTER=SYNC_Q_CLUSTER)
class UnknownCampaignSlugTest(TestCase):
    """Scenario: unknown campaign slug still logs visit; no retroactive backfill."""

    def setUp(self):
        cache.clear()

    def test_unknown_slug_logs_visit_with_null_campaign(self):
        c = make_browser_client()
        c.get('/blog?utm_source=somewhere&utm_medium=link'
              '&utm_campaign=mystery_link&utm_content=v1')
        v = CampaignVisit.objects.get()
        self.assertEqual(v.utm_campaign, 'mystery_link')
        self.assertIsNone(v.campaign_id)

    def test_creating_campaign_after_visit_does_not_backfill(self):
        c = make_browser_client()
        c.get('/blog?utm_campaign=mystery_link')
        v = CampaignVisit.objects.get()
        self.assertIsNone(v.campaign_id)
        # Now create the campaign — old visit should still have NULL campaign.
        UtmCampaign.objects.create(
            name='Mystery', slug='mystery_link',
            default_utm_source='x', default_utm_medium='y',
        )
        v.refresh_from_db()
        self.assertIsNone(v.campaign_id)


@override_settings(IP_HASH_SALT='', Q_CLUSTER=SYNC_Q_CLUSTER)
class NormalizationTest(TestCase):
    """Scenario: UTM values are normalized so analytics group correctly."""

    def setUp(self):
        cache.clear()
        UtmCampaign.objects.create(
            name='Launch', slug='launch',
            default_utm_source='newsletter', default_utm_medium='email',
        )

    def test_uppercase_and_whitespace_normalized(self):
        c = make_browser_client()
        # Note: %20 -> spaces; query string has trailing spaces around 'launch'
        c.get('/blog?utm_source=Newsletter&utm_medium=EMAIL'
              '&utm_campaign=Launch%20%20&utm_content=Hero%20Image')
        v = CampaignVisit.objects.get()
        self.assertEqual(v.utm_source, 'newsletter')
        self.assertEqual(v.utm_medium, 'email')
        self.assertEqual(v.utm_campaign, 'launch')
        self.assertEqual(v.utm_content, 'hero image')
        # FK resolves despite original casing in URL.
        self.assertIsNotNone(v.campaign_id)

    def test_long_utm_value_truncated_not_rejected(self):
        c = make_browser_client()
        long_source = 'a' * 250
        c.get(f'/blog?utm_source={long_source}&utm_campaign=launch')
        v = CampaignVisit.objects.get()
        # utm_source max_length is 100
        self.assertEqual(len(v.utm_source), 100)


class IpHashPrivacyTest(TestCase):
    """Scenario: privacy — raw IP is never stored."""

    def setUp(self):
        cache.clear()

    @override_settings(IP_HASH_SALT='test_salt', Q_CLUSTER=SYNC_Q_CLUSTER)
    def test_ip_is_hashed_never_stored_raw(self):
        c = make_browser_client()
        c.get(
            '/blog?utm_source=newsletter&utm_campaign=launch',
            REMOTE_ADDR='203.0.113.42',
        )
        v = CampaignVisit.objects.get()
        expected = hashlib.sha256(b'203.0.113.42test_salt').hexdigest()
        self.assertEqual(v.ip_hash, expected)
        self.assertEqual(len(v.ip_hash), 64)
        # No column contains the literal IP.
        for field_name in [f.name for f in v._meta.get_fields() if hasattr(v, f.name)]:
            value = getattr(v, field_name, None)
            if isinstance(value, str):
                self.assertNotIn('203.0.113.42', value,
                                 f'Raw IP found in field {field_name}')

    @override_settings(IP_HASH_SALT='', Q_CLUSTER=SYNC_Q_CLUSTER)
    def test_ip_hash_blank_when_salt_unset(self):
        c = make_browser_client()
        c.get(
            '/blog?utm_source=newsletter&utm_campaign=launch',
            REMOTE_ADDR='203.0.113.42',
        )
        v = CampaignVisit.objects.get()
        self.assertEqual(v.ip_hash, '')


@override_settings(IP_HASH_SALT='', Q_CLUSTER=SYNC_Q_CLUSTER)
class CampaignSlugCacheTest(TestCase):
    """Scenario: repeated UTM hits do not re-query Campaign table."""

    def setUp(self):
        cache.clear()
        UtmCampaign.objects.create(
            name='Launch', slug='launch',
            default_utm_source='newsletter', default_utm_medium='email',
        )

    def test_three_hits_only_one_campaign_lookup(self):
        c = make_browser_client()
        # First hit primes the cache. After that, subsequent hits with the
        # same utm_campaign must not re-query the integrations_utmcampaign
        # table — we capture the SQL on the next two requests and assert.
        c.get('/blog?utm_campaign=launch')

        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        with self.settings(DEBUG=True):
            with CaptureQueriesContext(connection) as ctx:
                c.get('/blog?utm_campaign=launch')
                c.get('/blog?utm_campaign=launch')
            select_sql = ' '.join(
                q['sql'].lower() for q in ctx.captured_queries
                if q['sql'].lower().lstrip().startswith('select')
            )
        # The cache short-circuits the lookup — no SELECT against the
        # campaign table should appear in the second/third requests.
        self.assertNotIn('integrations_utmcampaign', select_sql,
                         f'Campaign re-queried after cache primed. SQL: {select_sql}')
        # Sanity: three visit rows exist (one per request).
        self.assertEqual(CampaignVisit.objects.count(), 3)


@override_settings(IP_HASH_SALT='', Q_CLUSTER=SYNC_Q_CLUSTER)
class FirstTouchSurvivesSessionTest(TestCase):
    """Scenario: first-touch survives across sessions; last-touch is per-session."""

    def setUp(self):
        cache.clear()

    def test_first_touch_cookie_persists_after_session_clear(self):
        c = make_browser_client()
        c.get('/blog?utm_campaign=newsletter')
        # First-touch cookie was set
        self.assertIn(FIRST_TOUCH_COOKIE, c.cookies)
        first_touch_value = c.cookies[FIRST_TOUCH_COOKIE].value
        # Simulate "session expiry": flush server-side session but keep cookies.
        c.session.flush()
        c.get('/blog?utm_campaign=blog_link')
        # First-touch cookie value should not have changed.
        # When the second response does NOT set the cookie, the existing
        # cookie value remains in c.cookies.
        self.assertEqual(c.cookies[FIRST_TOUCH_COOKIE].value, first_touch_value)
        # Verify the JSON content is still 'newsletter', not 'blog_link'.
        payload = json.loads(c.cookies[FIRST_TOUCH_COOKIE].value)
        self.assertEqual(payload['campaign'], 'newsletter')
        # Last-touch in the new session is now 'blog_link'.
        self.assertEqual(c.session[SESSION_LAST_TOUCH]['campaign'], 'blog_link')


@override_settings(IP_HASH_SALT='', Q_CLUSTER=SYNC_Q_CLUSTER)
class StableAnonymousIdTest(TestCase):
    """Scenario: anon visitor across multiple visits has a stable anonymous_id."""

    def setUp(self):
        cache.clear()

    def test_three_visits_share_same_anonymous_id(self):
        c = make_browser_client()
        c.get('/blog?utm_campaign=launch')
        c.get('/courses?utm_campaign=summer_drop')
        c.get('/events?utm_campaign=q3_event')
        anon_ids = list(CampaignVisit.objects.values_list('anonymous_id', flat=True))
        self.assertEqual(len(anon_ids), 3)
        self.assertEqual(len(set(anon_ids)), 1, f'anon ids differ: {anon_ids}')
        # And it equals the cookie value set on the first response.
        cookie_value = c.cookies[ANON_ID_COOKIE].value
        self.assertEqual(anon_ids[0], cookie_value)
