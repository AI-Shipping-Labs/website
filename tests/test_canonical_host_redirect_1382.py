"""Canonical production host redirect regression tests (#1382)."""

from django.test import Client, TestCase, override_settings

PRODUCTION_SETTINGS = override_settings(
    SITE_BASE_URL='https://aishippinglabs.com',
    ALLOWED_HOSTS=[
        'aishippinglabs.com',
        'www.aishippinglabs.com',
    ],
)


@PRODUCTION_SETTINGS
class CanonicalHostRedirectTest(TestCase):
    def setUp(self):
        self.client = Client(raise_request_exception=False)

    def assertPermanentRedirect(self, response, location):
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response['Location'], location)
        self.assertEqual(response.content, b'')

    def test_apex_http_redirects_to_clean_https_apex(self):
        response = self.client.get(
            '/',
            HTTP_HOST='aishippinglabs.com',
            HTTP_X_FORWARDED_PROTO='http',
        )

        self.assertPermanentRedirect(response, 'https://aishippinglabs.com/')

    def test_www_http_and_https_redirect_to_clean_https_apex(self):
        cases = (
            ('http', 'www.aishippinglabs.com:80'),
            ('https', 'www.aishippinglabs.com:443'),
        )
        for forwarded_proto, host in cases:
            with self.subTest(forwarded_proto=forwarded_proto):
                response = self.client.get(
                    '/',
                    HTTP_HOST=host,
                    HTTP_X_FORWARDED_PROTO=forwarded_proto,
                )

                self.assertPermanentRedirect(
                    response,
                    'https://aishippinglabs.com/',
                )

    def test_path_and_query_are_preserved_without_default_port_or_empty_query(self):
        response = self.client.get(
            '/blog?utm_source=test&campaign=one%20two',
            HTTP_HOST='www.aishippinglabs.com',
            HTTP_X_FORWARDED_PROTO='https',
        )

        self.assertPermanentRedirect(
            response,
            'https://aishippinglabs.com/blog'
            '?utm_source=test&campaign=one%20two',
        )
        self.assertNotIn(':443', response['Location'])

        root_response = self.client.get(
            '/',
            HTTP_HOST='www.aishippinglabs.com',
            HTTP_X_FORWARDED_PROTO='https',
        )
        self.assertFalse(root_response['Location'].endswith('?'))

    def test_host_redirect_runs_before_trailing_slash_redirect(self):
        response = self.client.get(
            '/blog/?utm_source=test',
            HTTP_HOST='www.aishippinglabs.com',
            HTTP_X_FORWARDED_PROTO='https',
        )

        self.assertPermanentRedirect(
            response,
            'https://aishippinglabs.com/blog/?utm_source=test',
        )

    def test_canonical_https_reaches_application_without_host_redirect(self):
        response = self.client.get(
            '/robots.txt',
            HTTP_HOST='aishippinglabs.com',
            HTTP_X_FORWARDED_PROTO='https',
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotIn('Location', response)

    def test_unknown_host_is_rejected_instead_of_redirected(self):
        response = self.client.get(
            '/',
            HTTP_HOST='untrusted.example.com',
            HTTP_X_FORWARDED_PROTO='http',
        )

        self.assertEqual(response.status_code, 400)
        self.assertNotIn('Location', response)

    @override_settings(VERSION='test-version-1382')
    def test_ping_bypasses_host_validation_and_canonical_redirect(self):
        for host in ('10.0.1.189:8000', 'www.aishippinglabs.com'):
            with self.subTest(host=host):
                response = self.client.get(
                    '/ping',
                    HTTP_HOST=host,
                    HTTP_X_FORWARDED_PROTO='http',
                )

                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.content, b'test-version-1382')
                self.assertNotIn('Location', response)


@override_settings(
    SITE_BASE_URL='https://dev.aishippinglabs.com',
    ALLOWED_HOSTS=['dev.aishippinglabs.com'],
)
class DevelopmentHostBehaviorTest(TestCase):
    def test_dev_http_host_is_not_changed_by_production_canonicalizer(self):
        response = self.client.get(
            '/robots.txt',
            HTTP_HOST='dev.aishippinglabs.com',
            HTTP_X_FORWARDED_PROTO='http',
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotIn('Location', response)
        self.assertEqual(
            response['X-Robots-Tag'],
            'noindex, nofollow, noarchive',
        )


@override_settings(
    SITE_BASE_URL='https://canonical.example.test/',
    ALLOWED_HOSTS=['canonical.example.test', 'www.canonical.example.test'],
)
class ConfiguredCanonicalOriginTest(TestCase):
    def test_redirect_origin_is_derived_from_process_site_configuration(self):
        response = self.client.get(
            '/blog?source=config',
            HTTP_HOST='www.canonical.example.test',
            HTTP_X_FORWARDED_PROTO='https',
        )

        self.assertEqual(response.status_code, 301)
        self.assertEqual(
            response['Location'],
            'https://canonical.example.test/blog?source=config',
        )
