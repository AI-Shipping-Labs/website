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

from pathlib import Path

from django.conf import settings as django_settings
from django.contrib.auth import get_user_model
from django.core.cache import caches
from django.test import Client, RequestFactory, TestCase, override_settings

from integrations import config as config_module
from integrations.config import clear_config_cache, get_config
from integrations.models import IntegrationSetting
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
        SITE_BASE_URL='https://aishippinglabs.com',
        ALLOWED_HOSTS=['testserver'],
    )
    def test_banner_defaults_to_compact_collapsed_state(self):
        response = self.client.get('/studio/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="env-mismatch-banner"')
        self.assertContains(response, 'data-testid="env-mismatch-toggle"')
        self.assertContains(response, 'aria-controls="env-mismatch-details"')
        self.assertContains(response, 'aria-expanded="false"')
        self.assertContains(response, 'id="env-mismatch-details"')
        self.assertContains(response, 'class="hidden mt-2')

    @override_settings(
        SITE_BASE_URL='https://aishippinglabs.com',
        ALLOWED_HOSTS=['testserver'],
    )
    def test_banner_details_keep_generated_link_risk_copy(self):
        response = self.client.get('/studio/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            'Generated links (unsubscribe, calendar invites, password resets, '
            'share URLs, webhook configs) will point to',
        )
        self.assertContains(response, 'Fix <code')
        self.assertContains(response, 'SITE_BASE_URL')

    @override_settings(
        SITE_BASE_URL='https://aishippinglabs.com',
        ALLOWED_HOSTS=['testserver'],
    )
    def test_banner_is_not_fully_dismissible(self):
        response = self.client.get('/studio/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="env-mismatch-banner"')
        self.assertContains(response, 'data-testid="env-mismatch-configured"')
        self.assertContains(response, 'data-testid="env-mismatch-request"')
        self.assertNotContains(response, 'data-testid="env-mismatch-dismiss"')
        self.assertNotContains(response, 'Dismiss')

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


class ProxySSLHeaderTest(TestCase):
    """Behind AWS ALB, TLS terminates at the load balancer and the
    container sees the request as HTTP. ``SECURE_PROXY_SSL_HEADER`` must
    be honored in production so ``request.scheme`` reports the original
    scheme (``https``) and the Studio host-mismatch banner does not
    false-positive (issue #350).

    Tests cover both directions:

    - With ``SECURE_PROXY_SSL_HEADER`` configured (prod-like), the
      forwarded header is trusted: ``request.scheme`` becomes
      ``'https'`` and the banner does not fire when configured host
      matches the request host.
    - The banner still fires when host genuinely differs (regression
      guard — the fix must not silence real mismatches).
    - With ``SECURE_PROXY_SSL_HEADER`` unset (local dev), the header is
      ignored — anyone could otherwise spoof ``https`` from the LAN.
    """

    def setUp(self):
        self.factory = RequestFactory()

    @override_settings(
        DEBUG=False,
        SECURE_PROXY_SSL_HEADER=('HTTP_X_FORWARDED_PROTO', 'https'),
        ALLOWED_HOSTS=['dev.aishippinglabs.com'],
    )
    def test_forwarded_proto_makes_request_secure(self):
        request = self.factory.get(
            '/studio/',
            HTTP_HOST='dev.aishippinglabs.com',
            HTTP_X_FORWARDED_PROTO='https',
        )
        self.assertEqual(request.scheme, 'https')
        self.assertTrue(request.is_secure())

    @override_settings(
        DEBUG=False,
        SECURE_PROXY_SSL_HEADER=('HTTP_X_FORWARDED_PROTO', 'https'),
        SITE_BASE_URL='https://dev.aishippinglabs.com',
        ALLOWED_HOSTS=['dev.aishippinglabs.com'],
    )
    def test_no_banner_when_behind_alb_with_matching_host(self):
        # Reproduces the dev.aishippinglabs.com bug: ALB terminates TLS,
        # forwards X-Forwarded-Proto: https, request host matches
        # SITE_BASE_URL host. Banner must not fire.
        request = self.factory.get(
            '/studio/',
            HTTP_HOST='dev.aishippinglabs.com',
            HTTP_X_FORWARDED_PROTO='https',
        )
        self.assertIsNone(_build_env_mismatch_payload(request))

    @override_settings(
        DEBUG=False,
        SECURE_PROXY_SSL_HEADER=('HTTP_X_FORWARDED_PROTO', 'https'),
        SITE_BASE_URL='https://prod.aishippinglabs.com',
        ALLOWED_HOSTS=['dev.aishippinglabs.com'],
    )
    def test_banner_still_fires_when_host_genuinely_differs(self):
        # Regression guard: the fix must not silence real mismatches.
        # Configured host is prod, request lands on dev — that's a stale
        # config and the banner should still warn the operator.
        request = self.factory.get(
            '/studio/',
            HTTP_HOST='dev.aishippinglabs.com',
            HTTP_X_FORWARDED_PROTO='https',
        )
        payload = _build_env_mismatch_payload(request)
        self.assertIsNotNone(payload)
        self.assertEqual(
            payload['configured_host'], 'prod.aishippinglabs.com',
        )

    @override_settings(SECURE_PROXY_SSL_HEADER=None)
    def test_forwarded_proto_ignored_when_header_setting_unset(self):
        # Sanity check on Django's own behavior: when SECURE_PROXY_SSL_HEADER
        # is not configured, the forwarded header is ignored. This isn't the
        # current production config (we always set the header now), but it
        # locks the underlying invariant we depend on.
        request = self.factory.get(
            '/studio/',
            HTTP_HOST='localhost:8000',
            HTTP_X_FORWARDED_PROTO='https',
        )
        self.assertEqual(request.scheme, 'http')
        self.assertFalse(request.is_secure())

    def test_settings_module_sets_secure_proxy_header_unconditionally(self):
        # Lock the unconditional `SECURE_PROXY_SSL_HEADER` line in
        # website/settings.py. The previous `if not DEBUG:` gate broke
        # dev (where DEBUG=True is set on the ECS task), causing the
        # host-mismatch banner to false-fire. A grep-style assertion
        # guards against the gate being re-added.
        settings_path = (
            Path(django_settings.BASE_DIR) / 'website' / 'settings.py'
        )
        source = settings_path.read_text()
        self.assertIn(
            "SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')",
            source,
        )
        self.assertNotIn(
            "if not DEBUG:\n"
            "    SECURE_PROXY_SSL_HEADER",
            source,
        )


class EnvMismatchAliasTest(TestCase):
    """Aliases from ``SITE_BASE_URL_ALIASES`` suppress the banner without
    changing the canonical URL used elsewhere (issue #369).

    The aliases setting lives in the DB (``IntegrationSetting`` row) and
    is parsed via ``re.split(r'[\\s,]+', value)`` so operators can use
    commas, whitespace, or newlines to separate entries.
    """

    def setUp(self):
        self.factory = RequestFactory()
        clear_config_cache()

    def tearDown(self):
        clear_config_cache()

    def _request(self, host, scheme='http'):
        return self.factory.get(
            '/studio/',
            HTTP_HOST=host,
            **{'wsgi.url_scheme': scheme},
        )

    @override_settings(
        SITE_BASE_URL='https://aishippinglabs.com',
        ALLOWED_HOSTS=[
            'aishippinglabs.com', 'prod.aishippinglabs.com',
            'www.aishippinglabs.com', 'dev.aishippinglabs.com',
            'localhost',
        ],
    )
    def test_alias_match_suppresses_banner(self):
        IntegrationSetting.objects.create(
            key='SITE_BASE_URL_ALIASES',
            value='https://prod.aishippinglabs.com',
            group='site',
        )
        clear_config_cache()
        request = self._request('prod.aishippinglabs.com', scheme='https')
        self.assertIsNone(_build_env_mismatch_payload(request))

    @override_settings(
        SITE_BASE_URL='https://aishippinglabs.com',
        ALLOWED_HOSTS=[
            'aishippinglabs.com', 'prod.aishippinglabs.com',
            'www.aishippinglabs.com', 'dev.aishippinglabs.com',
            'localhost',
        ],
    )
    def test_canonical_match_no_banner_with_aliases_set(self):
        # Canonical match still wins even when aliases are configured.
        IntegrationSetting.objects.create(
            key='SITE_BASE_URL_ALIASES',
            value='https://prod.aishippinglabs.com',
            group='site',
        )
        clear_config_cache()
        request = self._request('aishippinglabs.com', scheme='https')
        self.assertIsNone(_build_env_mismatch_payload(request))

    @override_settings(
        SITE_BASE_URL='https://aishippinglabs.com',
        ALLOWED_HOSTS=[
            'aishippinglabs.com', 'prod.aishippinglabs.com',
            'www.aishippinglabs.com', 'dev.aishippinglabs.com',
            'localhost',
        ],
    )
    def test_multiple_aliases_parsed_from_comma_separated(self):
        IntegrationSetting.objects.create(
            key='SITE_BASE_URL_ALIASES',
            value='https://prod.aishippinglabs.com, https://www.aishippinglabs.com',
            group='site',
        )
        clear_config_cache()
        prod_request = self._request('prod.aishippinglabs.com', scheme='https')
        www_request = self._request('www.aishippinglabs.com', scheme='https')
        self.assertIsNone(_build_env_mismatch_payload(prod_request))
        self.assertIsNone(_build_env_mismatch_payload(www_request))

    @override_settings(
        SITE_BASE_URL='https://aishippinglabs.com',
        ALLOWED_HOSTS=[
            'aishippinglabs.com', 'prod.aishippinglabs.com',
            'www.aishippinglabs.com', 'dev.aishippinglabs.com',
            'localhost',
        ],
    )
    def test_multiple_aliases_parsed_from_newlines(self):
        IntegrationSetting.objects.create(
            key='SITE_BASE_URL_ALIASES',
            value=(
                'https://prod.aishippinglabs.com\n'
                'https://www.aishippinglabs.com'
            ),
            group='site',
        )
        clear_config_cache()
        prod_request = self._request('prod.aishippinglabs.com', scheme='https')
        www_request = self._request('www.aishippinglabs.com', scheme='https')
        self.assertIsNone(_build_env_mismatch_payload(prod_request))
        self.assertIsNone(_build_env_mismatch_payload(www_request))

    @override_settings(
        SITE_BASE_URL='https://aishippinglabs.com',
        ALLOWED_HOSTS=[
            'aishippinglabs.com', 'prod.aishippinglabs.com',
            'www.aishippinglabs.com', 'dev.aishippinglabs.com',
            'localhost',
        ],
    )
    def test_real_mismatch_still_warns_with_aliases_set(self):
        # Aliases list prod and www only — a request to `dev` must still
        # fire the banner.
        IntegrationSetting.objects.create(
            key='SITE_BASE_URL_ALIASES',
            value='https://prod.aishippinglabs.com https://www.aishippinglabs.com',
            group='site',
        )
        clear_config_cache()
        request = self._request('dev.aishippinglabs.com', scheme='https')
        payload = _build_env_mismatch_payload(request)
        self.assertIsNotNone(payload)
        self.assertEqual(payload['configured_host'], 'aishippinglabs.com')
        self.assertEqual(
            payload['request_url'], 'https://dev.aishippinglabs.com',
        )

    @override_settings(
        SITE_BASE_URL='https://aishippinglabs.com',
        ALLOWED_HOSTS=[
            'aishippinglabs.com', 'prod.aishippinglabs.com',
            'www.aishippinglabs.com', 'dev.aishippinglabs.com',
            'localhost',
        ],
    )
    def test_malformed_alias_skipped_silently(self):
        # A garbage alias entry must not crash; the canonical comparison
        # still works alongside it.
        IntegrationSetting.objects.create(
            key='SITE_BASE_URL_ALIASES',
            value='not a url, https://prod.aishippinglabs.com',
            group='site',
        )
        clear_config_cache()
        # Garbage alias doesn't crash and doesn't suppress a real mismatch.
        bad_request = self._request('dev.aishippinglabs.com', scheme='https')
        self.assertIsNotNone(_build_env_mismatch_payload(bad_request))
        # The well-formed alias still suppresses its match.
        prod_request = self._request('prod.aishippinglabs.com', scheme='https')
        self.assertIsNone(_build_env_mismatch_payload(prod_request))

    @override_settings(
        SITE_BASE_URL='https://aishippinglabs.com',
        ALLOWED_HOSTS=['localhost', 'aishippinglabs.com'],
    )
    def test_empty_aliases_preserves_existing_behavior(self):
        # No IntegrationSetting row, no env override — banner fires
        # exactly as it did before this issue. Mirrors the canonical
        # mismatch case in BuildEnvMismatchPayloadTest.
        request = self._request('localhost:8000', scheme='http')
        payload = _build_env_mismatch_payload(request)
        self.assertIsNotNone(payload)
        self.assertEqual(payload['configured_host'], 'aishippinglabs.com')
        self.assertEqual(payload['request_url'], 'http://localhost:8000')


class EnvMismatchOverrideTest(TestCase):
    """A Studio DB override of ``SITE_BASE_URL`` must drive the banner
    comparison instead of ``settings.SITE_BASE_URL`` (issue #435).

    Mirrors :class:`EnvMismatchAliasTest`'s setup style: a fresh
    ``RequestFactory`` per test, ``clear_config_cache()`` in
    ``setUp``/``tearDown`` so DB rows are visible but don't leak.
    """

    def setUp(self):
        self.factory = RequestFactory()
        clear_config_cache()

    def tearDown(self):
        clear_config_cache()

    def _request(self, host, scheme='http'):
        return self.factory.get(
            '/studio/',
            HTTP_HOST=host,
            **{'wsgi.url_scheme': scheme},
        )

    @override_settings(
        SITE_BASE_URL='https://aishippinglabs.com',
        ALLOWED_HOSTS=[
            'aishippinglabs.com', 'prod.aishippinglabs.com',
            'localhost',
        ],
    )
    def test_db_override_clears_banner_when_matches_request_host(self):
        # The original bug: env says aishippinglabs.com, override is
        # prod.aishippinglabs.com, request comes in on prod — banner
        # MUST be suppressed because the override is what we want.
        IntegrationSetting.objects.create(
            key='SITE_BASE_URL',
            value='https://prod.aishippinglabs.com',
            group='site',
        )
        clear_config_cache()
        request = self._request('prod.aishippinglabs.com', scheme='https')
        self.assertIsNone(_build_env_mismatch_payload(request))

    @override_settings(
        SITE_BASE_URL='https://aishippinglabs.com',
        ALLOWED_HOSTS=[
            'aishippinglabs.com', 'prod.aishippinglabs.com',
            'localhost',
        ],
    )
    def test_db_override_replaces_env_value_in_banner_payload(self):
        # When the override does not match the request host, the
        # payload must show the OVERRIDE value (not the env value)
        # under "Configured" — this is the operator-visible symptom.
        IntegrationSetting.objects.create(
            key='SITE_BASE_URL',
            value='https://prod.aishippinglabs.com',
            group='site',
        )
        clear_config_cache()
        request = self._request('localhost:8000', scheme='http')
        payload = _build_env_mismatch_payload(request)
        self.assertIsNotNone(payload)
        self.assertEqual(
            payload['configured_base_url'],
            'https://prod.aishippinglabs.com',
        )
        self.assertEqual(
            payload['configured_host'], 'prod.aishippinglabs.com',
        )
        # Negative assertion: the env value must not appear.
        self.assertNotEqual(
            payload['configured_base_url'], 'https://aishippinglabs.com',
        )

    @override_settings(
        SITE_BASE_URL='https://aishippinglabs.com',
        ALLOWED_HOSTS=['aishippinglabs.com', 'localhost'],
    )
    def test_no_override_preserves_env_value_in_banner_payload(self):
        # No DB row => the banner reads the env value, exactly as it
        # did before this issue. Regression guard.
        self.assertFalse(
            IntegrationSetting.objects.filter(key='SITE_BASE_URL').exists()
        )
        request = self._request('localhost:8000', scheme='http')
        payload = _build_env_mismatch_payload(request)
        self.assertIsNotNone(payload)
        self.assertEqual(
            payload['configured_base_url'], 'https://aishippinglabs.com',
        )

    @override_settings(
        SITE_BASE_URL='https://aishippinglabs.com',
        ALLOWED_HOSTS=[
            'aishippinglabs.com', 'prod.aishippinglabs.com',
            'localhost',
        ],
    )
    def test_clearing_override_via_save_revives_env_value(self):
        # Mirrors the Studio empty-string flow at
        # studio/views/settings.py: saving an empty value deletes the
        # row, which must restore env-only behaviour.
        IntegrationSetting.objects.create(
            key='SITE_BASE_URL',
            value='https://prod.aishippinglabs.com',
            group='site',
        )
        clear_config_cache()
        # Sanity: with override, prod request is suppressed.
        prod_request = self._request('prod.aishippinglabs.com', scheme='https')
        self.assertIsNone(_build_env_mismatch_payload(prod_request))

        # Now delete the override the same way settings_save_group does.
        IntegrationSetting.objects.filter(key='SITE_BASE_URL').delete()
        clear_config_cache()

        # With no override, the prod request now triggers the banner
        # (env value is aishippinglabs.com which doesn't match prod).
        payload = _build_env_mismatch_payload(prod_request)
        self.assertIsNotNone(payload)
        self.assertEqual(
            payload['configured_base_url'], 'https://aishippinglabs.com',
        )


class EnvMismatchCrossProcessTest(TestCase):
    """Reproduce the operator-reported scenario from issue #462.

    Env var is ``https://prod.aishippinglabs.com``, the DB row is
    ``https://aishippinglabs.com``, the request comes in on
    ``aishippinglabs.com``. Before the cross-process stamp fix, a
    gunicorn worker that had populated its in-process ``_cache`` BEFORE
    the Studio save would never re-read the DB and would keep using the
    env value, falsely firing the banner. The fix publishes a stamp on
    every ``clear_config_cache()`` so the next ``get_config()`` notices
    and repopulates.
    """

    def setUp(self):
        self.factory = RequestFactory()
        clear_config_cache()
        caches['django_q'].delete('integration_settings_stamp')
        config_module._cache = {}
        config_module._cache_populated = False
        config_module._cache_stamp = None

    def tearDown(self):
        clear_config_cache()
        caches['django_q'].delete('integration_settings_stamp')
        config_module._cache = {}
        config_module._cache_populated = False
        config_module._cache_stamp = None

    def _request(self, host, scheme='https'):
        return self.factory.get(
            '/studio/',
            HTTP_HOST=host,
            **{'wsgi.url_scheme': scheme},
        )

    @override_settings(
        SITE_BASE_URL='https://prod.aishippinglabs.com',
        ALLOWED_HOSTS=[
            'aishippinglabs.com', 'prod.aishippinglabs.com',
        ],
    )
    def test_stale_worker_picks_up_db_override_after_save(self):
        # Worker A boots and populates its cache with no DB row in place
        # (so SITE_BASE_URL falls back to the env value via settings).
        first_read = get_config('SITE_BASE_URL', '')
        # With no DB row, get_config returns settings.SITE_BASE_URL (the
        # env-time snapshot). The cache itself is populated but empty.
        self.assertTrue(config_module._cache_populated)
        self.assertEqual(first_read, 'https://prod.aishippinglabs.com')
        stamp_seen_by_worker_a = config_module._cache_stamp

        # Worker B (simulated) handles the Studio save: it writes the DB
        # row and calls clear_config_cache(). That publishes a fresh
        # stamp into caches['django_q'].
        IntegrationSetting.objects.create(
            key='SITE_BASE_URL',
            value='https://aishippinglabs.com',
            group='site',
        )
        clear_config_cache()

        # Restore Worker A's perspective: cache_populated=True with the
        # OLD stamp. It receives the next request on aishippinglabs.com.
        config_module._cache = {}
        config_module._cache_populated = True
        config_module._cache_stamp = stamp_seen_by_worker_a

        request = self._request('aishippinglabs.com', scheme='https')
        # Banner must NOT fire. Before the fix, Worker A never refreshed
        # its cache and kept comparing against settings.SITE_BASE_URL
        # (= prod.aishippinglabs.com), which produced a false-positive
        # banner.
        self.assertIsNone(_build_env_mismatch_payload(request))

    @override_settings(
        SITE_BASE_URL='https://prod.aishippinglabs.com',
        ALLOWED_HOSTS=[
            'aishippinglabs.com', 'prod.aishippinglabs.com',
        ],
    )
    def test_alias_set_via_db_suppresses_banner_after_cross_process_save(self):
        # Worker A populates with no DB rows.
        from integrations.config import get_config  # noqa: PLC0415
        get_config('SITE_BASE_URL', '')
        stamp_seen_by_worker_a = config_module._cache_stamp

        # Worker B saves both SITE_BASE_URL and SITE_BASE_URL_ALIASES.
        IntegrationSetting.objects.create(
            key='SITE_BASE_URL',
            value='https://aishippinglabs.com',
            group='site',
        )
        IntegrationSetting.objects.create(
            key='SITE_BASE_URL_ALIASES',
            value='https://aishippinglabs.com',
            group='site',
        )
        clear_config_cache()

        # Worker A sees the old stamp.
        config_module._cache = {}
        config_module._cache_populated = True
        config_module._cache_stamp = stamp_seen_by_worker_a

        request = self._request('aishippinglabs.com', scheme='https')
        # Both the canonical and alias paths must agree to suppress the
        # banner once the stamp is invalidated.
        self.assertIsNone(_build_env_mismatch_payload(request))
