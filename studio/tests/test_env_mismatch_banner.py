"""Tests for the Studio host-mismatch banner (issue #321).

Two layers:

- Unit tests for ``_normalize_host_triple`` and
  ``_build_env_mismatch_payload`` in
  ``website.context_processors``. These cover the locked normalization
  rules (lowercase, default-port equality, scheme strictness, trailing
  slash, etc.) without spinning up a request.
- View tests that hit ``/studio/`` with matching and mismatched ``Host``
  headers and assert the banner's presence (or absence) in the rendered
  template. These exercise the context processor + partial together.

The banner is Studio-only by spec, so a non-Studio path with a deliberate
mismatch must NOT render the banner — that's its own test.
"""

from django.contrib.auth import get_user_model
from django.test import Client, RequestFactory, TestCase, override_settings

from website.context_processors import (
    _build_env_mismatch_payload,
    _normalize_host_triple,
    studio_env_mismatch_context,
)

User = get_user_model()


class NormalizeHostTripleTest(TestCase):
    """The locked normalization rules in _normalize_host_triple.

    Rules under test (issue #321 acceptance criteria):
      - scheme is lowercased; http != https.
      - host is lowercased; no www-stripping.
      - missing port resolves to the scheme default (80 / 443).
      - default-port equivalence: ":443" with https equals no port.
    """

    def test_lowercases_scheme(self):
        self.assertEqual(
            _normalize_host_triple('HTTPS', 'aishippinglabs.com'),
            ('https', 'aishippinglabs.com', 443),
        )

    def test_lowercases_host(self):
        self.assertEqual(
            _normalize_host_triple('http', 'Localhost:8000'),
            ('http', 'localhost', 8000),
        )

    def test_default_https_port_resolved(self):
        self.assertEqual(
            _normalize_host_triple('https', 'aishippinglabs.com'),
            ('https', 'aishippinglabs.com', 443),
        )

    def test_default_http_port_resolved(self):
        self.assertEqual(
            _normalize_host_triple('http', 'localhost'),
            ('http', 'localhost', 80),
        )

    def test_explicit_default_port_equal_to_omitted(self):
        with_port = _normalize_host_triple('https', 'aishippinglabs.com:443')
        without_port = _normalize_host_triple('https', 'aishippinglabs.com')
        self.assertEqual(with_port, without_port)

    def test_explicit_non_default_port_kept(self):
        self.assertEqual(
            _normalize_host_triple('http', 'localhost:8001'),
            ('http', 'localhost', 8001),
        )

    def test_scheme_mismatch_yields_different_triples(self):
        http_triple = _normalize_host_triple('http', 'aishippinglabs.com')
        https_triple = _normalize_host_triple('https', 'aishippinglabs.com')
        self.assertNotEqual(http_triple, https_triple)

    def test_apex_and_www_are_distinct(self):
        # No www-stripping by spec — www.aishippinglabs.com is its own
        # entry in ALLOWED_HOSTS so we treat it as a different host.
        apex = _normalize_host_triple('https', 'aishippinglabs.com')
        www = _normalize_host_triple('https', 'www.aishippinglabs.com')
        self.assertNotEqual(apex, www)


class BuildEnvMismatchPayloadTest(TestCase):
    """Helper that turns a request + SITE_BASE_URL into banner data."""

    def setUp(self):
        self.factory = RequestFactory()

    def _request(self, host, scheme='http'):
        # RequestFactory builds a request with HTTP_HOST = "testserver";
        # override both HTTP_HOST and the wsgi scheme directly.
        request = self.factory.get(
            '/studio/',
            HTTP_HOST=host,
            **{'wsgi.url_scheme': scheme},
        )
        return request

    @override_settings(SITE_BASE_URL='http://localhost:8000')
    def test_matching_host_returns_none(self):
        request = self._request('localhost:8000', scheme='http')
        self.assertIsNone(_build_env_mismatch_payload(request))

    @override_settings(SITE_BASE_URL='https://aishippinglabs.com')
    def test_host_mismatch_returns_payload(self):
        request = self._request('localhost:8000', scheme='http')
        payload = _build_env_mismatch_payload(request)
        self.assertIsNotNone(payload)
        self.assertEqual(
            payload['configured_base_url'], 'https://aishippinglabs.com',
        )
        self.assertEqual(payload['request_url'], 'http://localhost:8000')
        self.assertEqual(payload['configured_host'], 'aishippinglabs.com')

    @override_settings(
        SITE_BASE_URL='https://aishippinglabs.com/',
        ALLOWED_HOSTS=['aishippinglabs.com'],
    )
    def test_trailing_slash_does_not_trigger(self):
        request = self._request('aishippinglabs.com', scheme='https')
        self.assertIsNone(_build_env_mismatch_payload(request))

    @override_settings(
        SITE_BASE_URL='https://aishippinglabs.com',
        ALLOWED_HOSTS=['aishippinglabs.com'],
    )
    def test_default_https_port_does_not_trigger(self):
        request = self._request('aishippinglabs.com:443', scheme='https')
        self.assertIsNone(_build_env_mismatch_payload(request))

    @override_settings(SITE_BASE_URL='https://localhost:8000')
    def test_scheme_mismatch_triggers(self):
        # Scheme differences matter (running over http when config says
        # https is a stale-config smell).
        request = self._request('localhost:8000', scheme='http')
        payload = _build_env_mismatch_payload(request)
        self.assertIsNotNone(payload)

    @override_settings(SITE_BASE_URL='http://localhost:8000')
    def test_host_case_difference_does_not_trigger(self):
        request = self._request('LOCALHOST:8000', scheme='http')
        self.assertIsNone(_build_env_mismatch_payload(request))

    @override_settings(SITE_BASE_URL='')
    def test_empty_site_base_url_returns_none(self):
        request = self._request('localhost:8000', scheme='http')
        self.assertIsNone(_build_env_mismatch_payload(request))


class StudioEnvMismatchContextTest(TestCase):
    """The context processor entry point: scoping + payload key."""

    def setUp(self):
        self.factory = RequestFactory()

    @override_settings(SITE_BASE_URL='https://aishippinglabs.com')
    def test_non_studio_path_returns_none_payload(self):
        request = self.factory.get('/blog/', HTTP_HOST='localhost:8000')
        self.assertEqual(
            studio_env_mismatch_context(request),
            {'env_mismatch': None},
        )

    @override_settings(SITE_BASE_URL='https://aishippinglabs.com')
    def test_studio_path_with_mismatch_returns_payload(self):
        request = self.factory.get('/studio/', HTTP_HOST='localhost:8000')
        result = studio_env_mismatch_context(request)
        self.assertIsNotNone(result['env_mismatch'])
        self.assertEqual(
            result['env_mismatch']['configured_host'], 'aishippinglabs.com',
        )

    @override_settings(SITE_BASE_URL='http://localhost:8000')
    def test_studio_path_without_mismatch_returns_none_payload(self):
        request = self.factory.get('/studio/', HTTP_HOST='localhost:8000')
        self.assertEqual(
            studio_env_mismatch_context(request),
            {'env_mismatch': None},
        )


class StudioEnvMismatchBannerViewTest(TestCase):
    """End-to-end: the banner partial renders inside Studio's base.html
    when the configured host differs from the actual request host."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        self.client = Client()
        self.client.login(email='staff@test.com', password='testpass')

    @override_settings(
        SITE_BASE_URL='https://aishippinglabs.com',
        ALLOWED_HOSTS=['testserver', 'localhost'],
    )
    def test_banner_renders_on_studio_path_when_host_differs(self):
        response = self.client.get('/studio/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="env-mismatch-banner"')
        self.assertContains(response, 'Environment mismatch')
        self.assertContains(
            response,
            'data-testid="env-mismatch-configured"',
        )
        self.assertContains(
            response,
            'data-testid="env-mismatch-request"',
        )
        # The configured base URL is rendered into the banner.
        self.assertContains(response, 'https://aishippinglabs.com')
        # The actual request URL is also rendered (from
        # request.scheme + request.get_host()).
        self.assertContains(response, 'http://testserver')

    @override_settings(
        SITE_BASE_URL='http://testserver',
        ALLOWED_HOSTS=['testserver'],
    )
    def test_banner_hidden_on_studio_path_when_host_matches(self):
        response = self.client.get('/studio/')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'data-testid="env-mismatch-banner"')

    @override_settings(
        SITE_BASE_URL='https://aishippinglabs.com',
        ALLOWED_HOSTS=['testserver'],
    )
    def test_banner_hidden_on_non_studio_path_even_with_mismatch(self):
        # Banner is Studio-scoped: anonymous visitors / public pages
        # should never see infrastructure warnings.
        anon_client = Client()
        response = anon_client.get('/')
        self.assertNotContains(response, 'data-testid="env-mismatch-banner"')

    @override_settings(
        SITE_BASE_URL='https://aishippinglabs.com',
        ALLOWED_HOSTS=['testserver'],
    )
    def test_banner_renders_on_other_studio_pages(self):
        # The banner must appear on every Studio page, not just the
        # dashboard. Use the articles list as a representative sub-page.
        response = self.client.get('/studio/articles/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="env-mismatch-banner"')
