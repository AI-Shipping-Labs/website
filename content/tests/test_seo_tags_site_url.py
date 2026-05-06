"""SEO template tags must resolve ``SITE_BASE_URL`` through the
DB-aware helper (issue #435)."""

import json
from datetime import date

from django.template import Context, Template
from django.test import RequestFactory, TestCase, override_settings

from content.models import Article
from integrations.config import clear_config_cache
from integrations.models import IntegrationSetting


def _set_override(value):
    IntegrationSetting.objects.create(
        key='SITE_BASE_URL', value=value, group='site',
    )
    clear_config_cache()


def _extract_jsonld(html):
    start = html.index('<script type="application/ld+json">') + len(
        '<script type="application/ld+json">',
    )
    end = html.index('</script>', start)
    return json.loads(html[start:end])


@override_settings(SITE_BASE_URL='https://env.example.com')
class SeoSiteUrlOverrideTest(TestCase):
    """``structured_data`` and ``og_tags`` honor the override."""

    @classmethod
    def setUpTestData(cls):
        cls.article = Article.objects.create(
            title='SEO Article',
            slug='seo-article',
            description='SEO description.',
            content_markdown='# Body',
            date=date(2026, 1, 1),
            author='Author',
            published=True,
            required_level=0,
        )

    def setUp(self):
        clear_config_cache()

    def tearDown(self):
        clear_config_cache()

    def test_jsonld_site_url_uses_db_override(self):
        _set_override('https://override.example.com')
        result = Template(
            '{% load seo_tags %}{% structured_data article %}',
        ).render(Context({'article': self.article}))
        data = _extract_jsonld(result)
        # mainEntityOfPage.@id is built from the resolved site URL +
        # the article's absolute URL.
        self.assertEqual(
            data['mainEntityOfPage']['@id'],
            f'https://override.example.com{self.article.get_absolute_url()}',
        )
        self.assertEqual(
            data['publisher']['url'], 'https://override.example.com',
        )

    def test_jsonld_site_url_falls_back_to_settings(self):
        result = Template(
            '{% load seo_tags %}{% structured_data article %}',
        ).render(Context({'article': self.article}))
        data = _extract_jsonld(result)
        self.assertEqual(
            data['mainEntityOfPage']['@id'],
            f'https://env.example.com{self.article.get_absolute_url()}',
        )

    def test_og_tags_canonical_uses_db_override(self):
        _set_override('https://override.example.com')
        request = RequestFactory().get('/')
        result = Template(
            '{% load seo_tags %}{% og_tags article %}',
        ).render(Context({'article': self.article, 'request': request}))
        expected = (
            f'<meta property="og:url" '
            f'content="https://override.example.com'
            f'{self.article.get_absolute_url()}">'
        )
        self.assertIn(expected, result)
        self.assertNotIn(
            'content="https://env.example.com', result,
        )

    def test_og_tags_canonical_falls_back_to_settings(self):
        request = RequestFactory().get('/')
        result = Template(
            '{% load seo_tags %}{% og_tags article %}',
        ).render(Context({'article': self.article, 'request': request}))
        expected = (
            f'<meta property="og:url" '
            f'content="https://env.example.com'
            f'{self.article.get_absolute_url()}">'
        )
        self.assertIn(expected, result)
