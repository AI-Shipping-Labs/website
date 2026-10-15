"""Analytics cookie deletions must not fire on cookieless requests.

Withdrawal deletions are meaningful only for visitors who actually
carry the analytics cookies. Emitting deletion Set-Cookie headers on
every cookieless response (the crawler case) would make every public
page Set-Cookie-bearing, which PublicCachePolicyMiddleware and the
CloudFront cache policy treat as uncacheable.
"""

from django.test import TestCase

from analytics.middleware import ANALYTICS_COOKIES


class CookielessRequestHasNoSetCookieTest(TestCase):
    def test_cookieless_response_sets_no_analytics_cookies(self):
        # The csrf cookie may legitimately appear (form pages); what the
        # CDN contract needs is that the analytics layer adds nothing.
        response = self.client.get('/', HTTP_USER_AGENT='Mozilla/5.0')
        for name in ANALYTICS_COOKIES:
            self.assertNotIn(name, response.cookies)


class WithdrawalStillDeletesLegacyCookiesTest(TestCase):
    def test_withdrawal_with_legacy_cookies_still_deletes(self):
        response = self.client.get(
            '/',
            HTTP_USER_AGENT='Mozilla/5.0',
            HTTP_COOKIE='aslab_aid=11111111-1111-4111-8111-111111111111',
        )
        set_cookies = {m.key: m for m in response.cookies.values()}
        for name in ANALYTICS_COOKIES:
            self.assertIn(name, set_cookies)
            self.assertEqual(set_cookies[name]['max-age'], 0)
