from django.test import SimpleTestCase, TestCase

from website import settings as website_settings


class SecuritySettingsConfigTest(SimpleTestCase):
    def test_production_enables_secure_cookies_and_short_hsts(self):
        values = website_settings._security_setting_values(debug=False)

        self.assertTrue(values["SESSION_COOKIE_SECURE"])
        self.assertTrue(values["CSRF_COOKIE_SECURE"])
        self.assertEqual(values["SECURE_HSTS_SECONDS"], 3600)

    def test_development_keeps_plain_http_cookies_working(self):
        values = website_settings._security_setting_values(debug=True)

        self.assertFalse(values["SESSION_COOKIE_SECURE"])
        self.assertFalse(values["CSRF_COOKIE_SECURE"])
        self.assertEqual(values["SECURE_HSTS_SECONDS"], 0)

    def test_redirect_and_aggressive_hsts_options_stay_disabled(self):
        self.assertFalse(getattr(website_settings, "SECURE_SSL_REDIRECT", False))
        self.assertFalse(
            getattr(website_settings, "SECURE_HSTS_INCLUDE_SUBDOMAINS", False)
        )
        self.assertFalse(getattr(website_settings, "SECURE_HSTS_PRELOAD", False))


class ReferrerPolicyHeaderTest(TestCase):
    def test_pages_send_origin_to_cross_origin_embeds(self):
        # YouTube rejects embeds without a Referer (Error 153); Django's
        # default `same-origin` would strip it for every cross-origin iframe.
        response = self.client.get("/")

        self.assertEqual(
            response.headers["Referrer-Policy"],
            "strict-origin-when-cross-origin",
        )
