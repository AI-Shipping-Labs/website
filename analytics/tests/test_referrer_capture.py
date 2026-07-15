"""End-to-end tests for organic-referrer capture (#772).

Exercises the middleware + signal together via the real test Client. Each
scenario maps to one BDD-style block in the issue spec.
"""

import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase, override_settings

from analytics.middleware import (
    FIRST_TOUCH_REFERRER_COOKIE,
    SESSION_LAST_TOUCH_REFERRER,
)
from analytics.models import UserAttribution

User = get_user_model()


# Browser-like UA so the bot regex doesn't drop our test traffic.
BROWSER_UA = 'Mozilla/5.0 (test browser)'

# Force inline execution of background queue tasks so assertions run
# immediately after the request returns.
SYNC_Q_CLUSTER = {'sync': True, 'orm': 'default', 'name': 'test', 'workers': 1}


def make_browser_client():
    client = Client(HTTP_USER_AGENT=BROWSER_UA)
    client.cookies['aslab_analytics_consent'] = 'granted'
    return client


def signup_email_password(client, email='new@test.com', password='pw1234ABcd'):
    return client.post(
        '/api/register',
        data=json.dumps({'email': email, 'password': password}),
        content_type='application/json',
    )


# ---------------------------------------------------------------------------
# Scenario 1: LinkedIn visitor signs up after browsing — first-touch sticks
# ---------------------------------------------------------------------------

@override_settings(IP_HASH_SALT='', Q_CLUSTER=SYNC_Q_CLUSTER)
@patch('email_app.services.email_service.EmailService._send_ses', return_value='ses-id')
class LinkedInFirstTouchStickyTest(TestCase):

    def setUp(self):
        cache.clear()

    def test_first_linkedin_visit_writes_ft_ref_cookie(self, _ses):
        c = make_browser_client()
        resp = c.get('/blog', HTTP_REFERER='https://www.linkedin.com/feed/abc')
        cookie = resp.cookies.get(FIRST_TOUCH_REFERRER_COOKIE)
        self.assertIsNotNone(cookie)
        payload = json.loads(cookie.value)
        self.assertEqual(payload['host'], 'www.linkedin.com')
        self.assertEqual(payload['source'], 'linkedin')
        self.assertIn('ts', payload)

    def test_first_linkedin_visit_seeds_session_last_touch(self, _ses):
        c = make_browser_client()
        c.get('/blog', HTTP_REFERER='https://www.linkedin.com/feed/abc')
        last = c.session.get(SESSION_LAST_TOUCH_REFERRER)
        self.assertIsNotNone(last)
        self.assertEqual(last['source'], 'linkedin')

    def test_second_linkedin_visit_does_not_overwrite_ft_ref_cookie(self, _ses):
        c = make_browser_client()
        c.get('/blog', HTTP_REFERER='https://www.linkedin.com/feed/abc')
        original = c.cookies[FIRST_TOUCH_REFERRER_COOKIE].value
        r2 = c.get('/pricing', HTTP_REFERER='https://www.linkedin.com/in/foo')
        # Second response did not re-set the cookie — sticky first-touch.
        self.assertNotIn(FIRST_TOUCH_REFERRER_COOKIE, r2.cookies)
        # Cookie value still equals the original.
        self.assertEqual(c.cookies[FIRST_TOUCH_REFERRER_COOKIE].value, original)

    def test_signup_copies_linkedin_to_user_attribution(self, _ses):
        c = make_browser_client()
        c.get('/blog', HTTP_REFERER='https://www.linkedin.com/feed/abc')
        c.get('/pricing', HTTP_REFERER='https://www.linkedin.com/in/foo')
        resp = signup_email_password(c, email='linkedin@test.com')
        self.assertEqual(resp.status_code, 201)
        attr = UserAttribution.objects.get(user__email='linkedin@test.com')
        self.assertEqual(attr.first_touch_referrer_host, 'www.linkedin.com')
        self.assertEqual(attr.first_touch_referrer_source, 'linkedin')
        self.assertEqual(attr.last_touch_referrer_source, 'linkedin')


# ---------------------------------------------------------------------------
# Scenario 2: Direct landing — no referrer header at all
# ---------------------------------------------------------------------------

@override_settings(IP_HASH_SALT='', Q_CLUSTER=SYNC_Q_CLUSTER)
@patch('email_app.services.email_service.EmailService._send_ses', return_value='ses-id')
class DirectLandingTest(TestCase):

    def setUp(self):
        cache.clear()

    def test_direct_landing_does_not_write_ft_ref_cookie(self, _ses):
        c = make_browser_client()
        resp = c.get('/')
        self.assertNotIn(FIRST_TOUCH_REFERRER_COOKIE, resp.cookies)

    def test_direct_signup_has_empty_host_and_direct_source(self, _ses):
        c = make_browser_client()
        c.get('/')
        resp = signup_email_password(c, email='direct@test.com')
        self.assertEqual(resp.status_code, 201)
        attr = UserAttribution.objects.get(user__email='direct@test.com')
        self.assertEqual(attr.first_touch_referrer_host, '')
        self.assertEqual(attr.first_touch_referrer_source, 'direct')
        self.assertEqual(attr.last_touch_referrer_host, '')
        self.assertEqual(attr.last_touch_referrer_source, 'direct')


# ---------------------------------------------------------------------------
# Scenario 3: Same-origin navigation does not pollute first-touch
# ---------------------------------------------------------------------------

@override_settings(IP_HASH_SALT='', Q_CLUSTER=SYNC_Q_CLUSTER)
@patch('email_app.services.email_service.EmailService._send_ses', return_value='ses-id')
class SameOriginSkipTest(TestCase):

    def setUp(self):
        cache.clear()

    def test_same_origin_referrer_does_not_overwrite_first_touch(self, _ses):
        c = make_browser_client()
        # Land from Google.
        c.get('/blog', HTTP_REFERER='https://www.google.com/search?q=ai+ship',
              HTTP_HOST='testserver')
        # Then click an internal link — referrer is our own host.
        r2 = c.get('/pricing', HTTP_REFERER='http://testserver/blog',
                   HTTP_HOST='testserver')
        # Cookie was not re-set on response 2.
        self.assertNotIn(FIRST_TOUCH_REFERRER_COOKIE, r2.cookies)
        # Session last-touch was NOT overwritten with the internal nav.
        last = c.session.get(SESSION_LAST_TOUCH_REFERRER)
        self.assertEqual(last['source'], 'google')

    def test_signup_after_same_origin_clicks_preserves_google_first_touch(
        self, _ses
    ):
        c = make_browser_client()
        c.get('/blog', HTTP_REFERER='https://www.google.com/search?q=ai+ship',
              HTTP_HOST='testserver')
        c.get('/pricing', HTTP_REFERER='http://testserver/blog',
              HTTP_HOST='testserver')
        resp = signup_email_password(c, email='sameorigin@test.com')
        self.assertEqual(resp.status_code, 201)
        attr = UserAttribution.objects.get(user__email='sameorigin@test.com')
        self.assertEqual(attr.first_touch_referrer_source, 'google')
        self.assertEqual(attr.last_touch_referrer_source, 'google')

    def test_same_origin_only_then_signup_has_direct_source(self, _ses):
        """User who only had same-origin referrers gets direct attribution."""
        c = make_browser_client()
        c.get('/blog', HTTP_REFERER='http://testserver/', HTTP_HOST='testserver')
        c.get('/pricing', HTTP_REFERER='http://testserver/blog',
              HTTP_HOST='testserver')
        resp = signup_email_password(c, email='internalonly@test.com')
        self.assertEqual(resp.status_code, 201)
        attr = UserAttribution.objects.get(user__email='internalonly@test.com')
        self.assertEqual(attr.first_touch_referrer_host, '')
        self.assertEqual(attr.first_touch_referrer_source, 'direct')


# ---------------------------------------------------------------------------
# Scenario 4: User lands from YouTube, then ChatGPT — last-touch reflects ChatGPT
# ---------------------------------------------------------------------------

@override_settings(IP_HASH_SALT='', Q_CLUSTER=SYNC_Q_CLUSTER)
@patch('email_app.services.email_service.EmailService._send_ses', return_value='ses-id')
class LastTouchOverwriteTest(TestCase):

    def setUp(self):
        cache.clear()

    def test_youtube_then_chatgpt_signup_shows_youtube_first_chatgpt_last(
        self, _ses
    ):
        c = make_browser_client()
        c.get('/blog', HTTP_REFERER='https://www.youtube.com/watch?v=foo')
        c.get('/tutorials', HTTP_REFERER='https://chat.openai.com/c/abc')
        # first-touch cookie still YouTube.
        payload = json.loads(c.cookies[FIRST_TOUCH_REFERRER_COOKIE].value)
        self.assertEqual(payload['source'], 'youtube')
        # session last-touch now ChatGPT.
        self.assertEqual(
            c.session[SESSION_LAST_TOUCH_REFERRER]['source'], 'chatgpt',
        )
        # Signup snapshots both correctly.
        signup_email_password(c, email='multi@test.com')
        attr = UserAttribution.objects.get(user__email='multi@test.com')
        self.assertEqual(attr.first_touch_referrer_source, 'youtube')
        self.assertEqual(attr.last_touch_referrer_source, 'chatgpt')


# ---------------------------------------------------------------------------
# Scenario 5: Bot user-agent leaves no referrer trail
# ---------------------------------------------------------------------------

@override_settings(IP_HASH_SALT='', Q_CLUSTER=SYNC_Q_CLUSTER)
class BotReferrerSkipTest(TestCase):

    def setUp(self):
        cache.clear()

    def test_googlebot_with_linkedin_referrer_writes_no_cookie(self):
        c = Client(HTTP_USER_AGENT='Googlebot/2.1 (+http://www.google.com/bot.html)')
        c.cookies['aslab_analytics_consent'] = 'granted'
        resp = c.get('/blog', HTTP_REFERER='https://www.linkedin.com/feed/abc')
        self.assertNotIn(FIRST_TOUCH_REFERRER_COOKIE, resp.cookies)
        self.assertIsNone(c.session.get(SESSION_LAST_TOUCH_REFERRER))


# ---------------------------------------------------------------------------
# Scenario 6: Referrer from login/register page is ignored
# ---------------------------------------------------------------------------

@override_settings(IP_HASH_SALT='', Q_CLUSTER=SYNC_Q_CLUSTER)
@patch('email_app.services.email_service.EmailService._send_ses', return_value='ses-id')
class LoginPageReferrerSkipTest(TestCase):

    def setUp(self):
        cache.clear()

    def test_register_page_referrer_does_not_overwrite_first_touch(self, _ses):
        c = make_browser_client()
        # 1. Land from LinkedIn on /blog.
        c.get('/blog', HTTP_REFERER='https://www.linkedin.com/feed/abc',
              HTTP_HOST='testserver')
        # 2. Visit /accounts/register with a same-origin referrer from login.
        r2 = c.get('/accounts/register',
                   HTTP_REFERER='http://testserver/accounts/login',
                   HTTP_HOST='testserver')
        # No new cookie written.
        self.assertNotIn(FIRST_TOUCH_REFERRER_COOKIE, r2.cookies)
        # Session last-touch still LinkedIn (not clobbered).
        last = c.session.get(SESSION_LAST_TOUCH_REFERRER)
        self.assertEqual(last['source'], 'linkedin')
        # 3. Signup — first-touch reflects LinkedIn.
        signup_email_password(c, email='login-filter@test.com')
        attr = UserAttribution.objects.get(user__email='login-filter@test.com')
        self.assertEqual(attr.first_touch_referrer_source, 'linkedin')


# ---------------------------------------------------------------------------
# Scenario 7: Direct signup snapshots cleanly — separate from signal robustness
# ---------------------------------------------------------------------------

@override_settings(IP_HASH_SALT='', Q_CLUSTER=SYNC_Q_CLUSTER)
@patch('email_app.services.email_service.EmailService._send_ses', return_value='ses-id')
class MalformedReferrerCookieTest(TestCase):

    def setUp(self):
        cache.clear()

    def test_malformed_ft_ref_cookie_does_not_crash_signup(self, _ses):
        c = make_browser_client()
        c.cookies[FIRST_TOUCH_REFERRER_COOKIE] = 'not-json{{'
        resp = signup_email_password(c, email='broken-ref@test.com')
        self.assertEqual(resp.status_code, 201)
        attr = UserAttribution.objects.get(user__email='broken-ref@test.com')
        # Malformed cookie treated as missing → direct.
        self.assertEqual(attr.first_touch_referrer_host, '')
        self.assertEqual(attr.first_touch_referrer_source, 'direct')
