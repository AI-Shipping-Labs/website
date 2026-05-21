"""Tests for the Google Analytics loader wiring (issue #771).

The GA4 measurement ID is exposed via
``website.context_processors.site_context['google_analytics_id']`` and
gates the ``<script>`` block in ``templates/base.html``. When the
setting is blank (the default for fresh installs, CI, and local dev),
the page must contain no ``gtag`` / ``googletagmanager`` markup. When
the setting is populated, the page must inline the configured ID in
both the loader URL and the ``gtag('config', ...)`` call.
"""

from django.test import RequestFactory, TestCase, override_settings

from integrations.config import clear_config_cache
from integrations.models import IntegrationSetting
from integrations.settings_registry import get_group_by_name
from website.context_processors import site_context


class GoogleAnalyticsRegistryTest(TestCase):
    """The `analytics` group is registered with the expected key."""

    def test_analytics_group_exposes_google_analytics_id(self):
        group = get_group_by_name('analytics')
        self.assertIsNotNone(group)
        keys = [k['key'] for k in group['keys']]
        self.assertEqual(keys, ['GOOGLE_ANALYTICS_ID'])

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
        context = site_context(request)
        self.assertEqual(context['google_analytics_id'], 'G-DBVALUE')

    @override_settings(GOOGLE_ANALYTICS_ID='G-FROMSETTINGS')
    def test_django_setting_is_used_when_db_empty(self):
        request = RequestFactory().get('/')
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
        # No GA script tags at all.
        self.assertNotContains(response, 'gtag')
        self.assertNotContains(response, 'googletagmanager')

    def test_home_page_renders_loader_when_set(self):
        IntegrationSetting.objects.create(
            key='GOOGLE_ANALYTICS_ID',
            value='G-TEST123456',
            group='analytics',
        )
        clear_config_cache()
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
        # AC: no GA on any other public page either when unset.
        response = self.client.get('/pricing')
        # Pricing may redirect or 200 depending on routing; either way
        # the rendered HTML body must not contain GA markup.
        if response.status_code in (301, 302):
            response = self.client.get(response.url)
        self.assertNotContains(response, 'gtag')
        self.assertNotContains(response, 'googletagmanager')

    def test_pricing_page_renders_loader_when_set(self):
        IntegrationSetting.objects.create(
            key='GOOGLE_ANALYTICS_ID',
            value='G-TEST123456',
            group='analytics',
        )
        clear_config_cache()
        response = self.client.get('/pricing')
        if response.status_code in (301, 302):
            response = self.client.get(response.url)
        self.assertContains(response, 'G-TEST123456')


class GoogleAnalyticsHardcodedReferenceTest(TestCase):
    """Regression: the literal production measurement ID must not appear
    in templates anymore. The setting is now the single source of truth
    and must be configured via Studio.
    """

    def test_base_template_has_no_hardcoded_id(self):
        from pathlib import Path

        base_html = Path(
            __file__
        ).resolve().parent.parent.parent / 'templates' / 'base.html'
        content = base_html.read_text()
        self.assertNotIn('G-HXSHF376NY', content)
        self.assertNotIn(
            'googletagmanager.com/gtag/js?id=G-',
            content,
        )
