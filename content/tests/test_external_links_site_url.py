"""External-link classification must use the resolved ``SITE_BASE_URL``
so a Studio override changes which links open in a new tab (issue #435).
"""

from django.test import TestCase, override_settings

from content.models.article import render_markdown as render_article_md
from integrations.config import clear_config_cache
from integrations.models import IntegrationSetting


@override_settings(SITE_BASE_URL='https://env.example.com')
class ExternalLinkSiteUrlOverrideTest(TestCase):
    """``_site_hosts`` reads the override; same-host links stay
    untreated, foreign hosts gain ``target="_blank"``."""

    def setUp(self):
        clear_config_cache()

    def tearDown(self):
        clear_config_cache()

    def test_override_host_treated_as_internal(self):
        # With the override pointing at override.example.com, links to
        # that host must NOT gain target=_blank — they're our own.
        IntegrationSetting.objects.create(
            key='SITE_BASE_URL',
            value='https://override.example.com',
            group='site',
        )
        clear_config_cache()
        md = (
            '[ours](https://override.example.com/blog) '
            '[theirs](https://other.example.com/x)'
        )
        html = render_article_md(md)
        # The override host link must be untouched.
        self.assertIn('href="https://override.example.com/blog"', html)
        self.assertNotIn(
            '<a href="https://override.example.com/blog" target="_blank"',
            html,
        )
        # The foreign host link must gain target=_blank + noopener.
        self.assertIn(
            'href="https://other.example.com/x"', html,
        )
        self.assertIn('target="_blank"', html)
        self.assertIn('noopener', html)

    def test_env_host_no_longer_internal_when_override_differs(self):
        # When the override is set, the env host is NOT internal — a
        # link to env.example.com gains target=_blank.
        IntegrationSetting.objects.create(
            key='SITE_BASE_URL',
            value='https://override.example.com',
            group='site',
        )
        clear_config_cache()
        html = render_article_md('[old](https://env.example.com/blog)')
        self.assertIn('href="https://env.example.com/blog"', html)
        self.assertIn('target="_blank"', html)
        self.assertIn('noopener', html)

    def test_env_host_internal_without_override(self):
        # Regression guard: with no DB row, env value drives the
        # internal-host set. Link to env host stays untouched.
        html = render_article_md('[ours](https://env.example.com/blog)')
        self.assertIn('href="https://env.example.com/blog"', html)
        self.assertNotIn('target="_blank"', html)
