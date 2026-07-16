"""Tests for the Google Analytics loader wiring (issue #771).

The GA4 measurement ID is exposed via
``website.context_processors.site_context['google_analytics_id']`` and
gates the ``<script>`` block in ``templates/base.html``. When the
setting is blank (the default for fresh installs, CI, and local dev),
the page must contain no ``gtag`` / ``googletagmanager`` markup. When
the setting is populated, the page must inline the configured ID in
both the loader URL and the ``gtag('config', ...)`` call.
"""

import subprocess
from pathlib import Path

from django.test import RequestFactory, TestCase, override_settings

from analytics.consent import (
    ANALYTICS_CONSENT_COOKIE,
    ANALYTICS_CONSENT_GRANTED,
)
from integrations.config import clear_config_cache
from integrations.models import IntegrationSetting
from integrations.settings_registry import get_group_by_name
from website.context_processors import site_context


def _grant_consent(target):
    target.COOKIES[ANALYTICS_CONSENT_COOKIE] = ANALYTICS_CONSENT_GRANTED


def _tracked_text_occurrences(repo_root, needle, paths=None):
    """Return tracked text files containing needle; binary files are ignored."""
    if paths is None:
        result = subprocess.run(
            ['git', '-C', str(repo_root), 'ls-files', '-z'],
            check=True,
            capture_output=True,
        )
        paths = [Path(item.decode()) for item in result.stdout.split(b'\0') if item]

    needle_bytes = needle.encode()
    matches = []
    for relative_path in paths:
        path = repo_root / relative_path
        if not path.is_file():
            continue
        data = path.read_bytes()
        if b'\0' in data:
            continue
        matches.extend([str(relative_path)] * data.count(needle_bytes))
    return matches


class GoogleAnalyticsRegistryTest(TestCase):
    """The `analytics` group is registered with the expected key."""

    def test_analytics_group_exposes_google_analytics_id(self):
        group = get_group_by_name('analytics')
        self.assertIsNotNone(group)
        keys = [k['key'] for k in group['keys']]
        self.assertIn('GOOGLE_ANALYTICS_ID', keys)

    def test_google_analytics_id_is_not_secret(self):
        group = get_group_by_name('analytics')
        key_def = next(
            k for k in group['keys'] if k['key'] == 'GOOGLE_ANALYTICS_ID'
        )
        self.assertFalse(key_def['is_secret'])
        self.assertTrue(key_def.get('optional'))
        self.assertIn(
            '_docs/integrations/analytics.md#google_analytics_id',
            key_def['docs_url'],
        )


class GoogleAnalyticsContextProcessorTest(TestCase):
    """site_context exposes the resolved measurement ID."""

    def setUp(self):
        clear_config_cache()

    def tearDown(self):
        clear_config_cache()

    def test_default_is_empty_string(self):
        request = RequestFactory().get('/')
        _grant_consent(request)
        context = site_context(request)
        self.assertEqual(context['google_analytics_id'], '')

    def test_db_value_wins(self):
        IntegrationSetting.objects.create(
            key='GOOGLE_ANALYTICS_ID',
            value='G-DBVALUE',
            group='analytics',
        )
        clear_config_cache()
        request = RequestFactory().get('/')
        _grant_consent(request)
        context = site_context(request)
        self.assertEqual(context['google_analytics_id'], 'G-DBVALUE')

    @override_settings(GOOGLE_ANALYTICS_ID='G-FROMSETTINGS')
    def test_django_setting_is_used_when_db_empty(self):
        request = RequestFactory().get('/')
        _grant_consent(request)
        context = site_context(request)
        self.assertEqual(context['google_analytics_id'], 'G-FROMSETTINGS')


class GoogleAnalyticsLoaderRenderingTest(TestCase):
    """The loader block in base.html is gated on `google_analytics_id`."""

    def setUp(self):
        clear_config_cache()

    def tearDown(self):
        clear_config_cache()

    def test_home_page_omits_loader_when_unset(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        # No GA loader markup. The form-handler ``gtag('event', ...)``
        # calls are still emitted (issue #774 — they guard with
        # ``typeof gtag === 'function'`` at runtime), so we narrow the
        # check to the loader-specific markup.
        self.assertNotContains(response, 'googletagmanager')
        self.assertNotContains(response, "gtag('js'")
        self.assertNotContains(response, "gtag('config'")

    def test_home_page_renders_loader_when_set(self):
        IntegrationSetting.objects.create(
            key='GOOGLE_ANALYTICS_ID',
            value='G-TEST123456',
            group='analytics',
        )
        clear_config_cache()
        self.client.cookies[ANALYTICS_CONSENT_COOKIE] = ANALYTICS_CONSENT_GRANTED
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        # The ID appears twice: once in the loader URL, once in
        # the gtag('config', ...) call.
        self.assertContains(response, 'G-TEST123456', count=2)
        self.assertContains(
            response,
            'https://www.googletagmanager.com/gtag/js?id=G-TEST123456',
        )

    def test_pricing_page_omits_loader_when_unset(self):
        # AC: no GA loader on any other public page either when unset.
        # The form-handler ``gtag('event', ...)`` calls remain (see
        # issue #774); narrow the check to loader-specific markup.
        response = self.client.get('/pricing')
        # Pricing may redirect or 200 depending on routing; either way
        # the rendered HTML body must not contain GA loader markup.
        if response.status_code in (301, 302):
            response = self.client.get(response.url)
        self.assertNotContains(response, 'googletagmanager')
        self.assertNotContains(response, "gtag('js'")
        self.assertNotContains(response, "gtag('config'")

    def test_pricing_page_renders_loader_when_set(self):
        IntegrationSetting.objects.create(
            key='GOOGLE_ANALYTICS_ID',
            value='G-TEST123456',
            group='analytics',
        )
        clear_config_cache()
        self.client.cookies[ANALYTICS_CONSENT_COOKIE] = ANALYTICS_CONSENT_GRANTED
        response = self.client.get('/pricing')
        if response.status_code in (301, 302):
            response = self.client.get(response.url)
        self.assertContains(response, 'G-TEST123456')


class GoogleAnalyticsHardcodedReferenceTest(TestCase):
    """The documented production ID may not leak into repository code."""

    def test_production_id_only_appears_in_operator_documentation(self):
        import re

        repo_root = Path(__file__).resolve().parent.parent.parent
        runbook = repo_root / '_docs' / 'integrations' / 'analytics.md'
        match = re.search(
            r'production GA4 property has measurement ID `(G-[A-Z0-9]+)`',
            runbook.read_text(),
        )
        self.assertIsNotNone(match, 'Analytics runbook must identify the live property.')
        production_id = match.group(1)

        matches = _tracked_text_occurrences(repo_root, production_id)
        self.assertEqual(
            matches,
            ['_docs/integrations/analytics.md'],
            'The live measurement ID must appear exactly once, in the operator runbook.',
        )

    def test_guard_scans_common_config_fixture_and_workflow_text(self):
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as directory:
            repo_root = Path(directory)
            paths = [
                Path('fixture.json'), Path('fixture.yaml'), Path('fixture.yml'),
                Path('pyproject.toml'), Path('.env.example'), Path('settings.conf'),
                Path('script.sh'), Path('.github/workflows/check.yml'),
                Path('Dockerfile'),
            ]
            for path in paths:
                (repo_root / path).parent.mkdir(parents=True, exist_ok=True)
                (repo_root / path).write_text('measurement=G-SYNTHETIC987\n')
            self.assertCountEqual(
                _tracked_text_occurrences(repo_root, 'G-SYNTHETIC987', paths),
                [str(path) for path in paths],
            )

    def test_base_template_uses_configured_loader_url(self):
        from pathlib import Path

        base_html = Path(
            __file__
        ).resolve().parent.parent.parent / 'templates' / 'base.html'
        content = base_html.read_text()
        self.assertNotIn('googletagmanager.com/gtag/js?id=G-', content)
        self.assertIn(
            'googletagmanager.com/gtag/js?id={{ google_analytics_id }}',
            content,
        )
