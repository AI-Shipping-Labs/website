"""TopicPage support in the ``seo_tags`` template tags (#1803).

Unit-level contract: description chain (summary, then body-derived, then
the title fallback), the JSON-LD builder, and the og/twitter tags for
topic guide pages. The response-level behavior is covered by
``topics.tests.test_views``.
"""

import json
import re

from django.template import Context, Template
from django.test import TestCase

from content.templatetags.seo_tags import (
    CONTENT_TYPE_LABELS,
    build_seo_description,
)
from topics.models import TopicPage


def _page(**overrides):
    fields = {
        'slug': 'rag',
        'title': 'RAG',
        'summary': 'Short summary.',
        'body': 'Body prose.',
    }
    fields.update(overrides)
    return TopicPage(**fields)


def _render(page):
    template = Template(
        '{% load seo_tags %}{% structured_data page %}{% og_tags page %}'
    )
    return template.render(Context({'page': page}))


def _jsonld_objects(content):
    blocks = re.findall(
        r'<script type="application/ld\+json">\n(.*?)\n</script>',
        content, re.S,
    )
    return [json.loads(block) for block in blocks]


def _meta(content, attr, key):
    match = re.search(rf'<meta {attr}="{key}" content="([^"]*)">', content)
    return match.group(1) if match else None


class TopicPageDescriptionChainTest(TestCase):
    def test_summary_is_the_description_source(self):
        self.assertEqual(
            build_seo_description(_page()),
            'Short summary.',
        )

    def test_body_fallback_without_summary(self):
        page = _page(summary='')
        self.assertEqual(build_seo_description(page), 'Body prose.')

    def test_body_fallback_strips_leading_title_h1(self):
        page = _page(summary='', body='# RAG\n\nBody prose.')
        self.assertEqual(build_seo_description(page), 'Body prose.')

    def test_title_fallback_when_summary_and_body_empty(self):
        page = _page(summary='', body='')
        self.assertEqual(
            build_seo_description(page),
            'Explore RAG, an AI Shipping Labs member topic guide.',
        )

    def test_content_type_label_registered(self):
        self.assertEqual(
            CONTENT_TYPE_LABELS['topicpage'],
            'member topic guide',
        )


class TopicPageStructuredDataTest(TestCase):
    def _article(self, page):
        articles = [
            obj for obj in _jsonld_objects(_render(page))
            if obj.get('@type') == 'Article'
        ]
        self.assertEqual(len(articles), 1)
        return articles[0]

    def test_jsonld_carries_title_description_and_canonical_url(self):
        article = self._article(_page())
        self.assertEqual(article['headline'], 'RAG')
        self.assertEqual(article['description'], 'Short summary.')
        self.assertEqual(
            article['url'],
            'https://aishippinglabs.com/topics/rag/',
        )
        self.assertEqual(
            article['mainEntityOfPage']['@id'],
            'https://aishippinglabs.com/topics/rag/',
        )
        self.assertEqual(
            article['publisher']['name'],
            'AI Shipping Labs',
        )

    def test_hub_page_jsonld_uses_the_hub_url(self):
        hub = _page(slug='index', title='Topics')
        article = self._article(hub)
        self.assertEqual(article['url'], 'https://aishippinglabs.com/topics/')


class TopicPageOgTagsTest(TestCase):
    def test_og_tags_use_the_content_page_contract(self):
        content = _render(_page())
        self.assertEqual(_meta(content, 'property', 'og:type'), 'article')
        self.assertEqual(
            _meta(content, 'property', 'og:url'),
            'https://aishippinglabs.com/topics/rag/',
        )
        self.assertEqual(
            _meta(content, 'property', 'og:title'),
            'RAG | AI Shipping Labs',
        )
        self.assertEqual(
            _meta(content, 'property', 'og:description'),
            'Short summary.',
        )
