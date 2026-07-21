"""Regression coverage for catalog tag presentation and semantics (#1228)."""

from __future__ import annotations

import datetime
from html.parser import HTMLParser

from django.test import TestCase, tag

from content.models import Article, CuratedLink, Download, Tutorial

CLICKABLE_CHIP_CLASSES = (
    'inline-flex items-center gap-1 rounded-full bg-secondary px-2.5 py-0.5 '
    'text-xs font-medium text-muted-foreground transition-colors '
    'hover:bg-secondary/80 focus-visible:outline-none focus-visible:ring-2 '
    'focus-visible:ring-accent focus-visible:ring-offset-2 '
    'focus-visible:ring-offset-background'
)
STATIC_CHIP_CLASSES = (
    'inline-flex items-center gap-1 rounded-full bg-secondary px-2.5 py-0.5 '
    'text-xs font-medium text-muted-foreground'
)
ARROW_CLASSES = (
    'hidden sm:block h-5 w-5 flex-shrink-0 text-muted-foreground '
    'transition-transform group-hover:translate-x-1 group-hover:text-accent'
)


class _ElementParser(HTMLParser):
    """Collect rendered elements, their text, and anchor ancestry."""

    def __init__(self):
        super().__init__()
        self.elements = []
        self.stack = []
        self.nested_anchor_hrefs = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        anchor_ancestors = [
            item for item in self.stack if item['tag'] == 'a'
        ]
        if tag == 'a' and anchor_ancestors:
            self.nested_anchor_hrefs.append(attributes.get('href', ''))
        element = {
            'tag': tag,
            'attrs': attributes,
            'text': '',
            'anchor_ancestors': [
                item['attrs'].get('href', '') for item in anchor_ancestors
            ],
        }
        self.elements.append(element)
        if tag not in {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'source', 'track', 'wbr'}:
            self.stack.append(element)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index]['tag'] == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        for element in self.stack:
            element['text'] += data


def _parse(response):
    parser = _ElementParser()
    parser.feed(response.content.decode())
    return parser


def _elements_with_text(parser, text):
    return [
        element for element in parser.elements
        if element['text'].strip() == text
    ]


class CatalogTagPresentationTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.article = Article.objects.create(
            title='Catalog Article 1228',
            slug='catalog-article-1228',
            description='Article description for catalog hierarchy.',
            content_markdown='Article body.',
            author='Catalog Author',
            tags=['agents-1228', 'python-1228', 'evaluation-1228', 'production-1228'],
            published=True,
            date=datetime.date(2026, 7, 13),
        )
        cls.download = Download.objects.create(
            title='Catalog Download 1228',
            slug='catalog-download-1228',
            description='Download description.',
            file_url='https://example.com/catalog-download.pdf',
            file_type='pdf',
            tags=['python-1228', 'agents-1228', 'shipping-1228', 'extra-1228'],
            required_level=10,
            published=True,
        )
        cls.tutorial = Tutorial.objects.create(
            title='Catalog Tutorial 1228',
            slug='catalog-tutorial-1228',
            description='Tutorial description for catalog hierarchy.',
            content_markdown='Tutorial body.',
            date=datetime.date(2026, 7, 12),
            tags=['tutorial-static-1228'],
            published=True,
        )
        cls.accessible_link = CuratedLink.objects.create(
            item_id='accessible-catalog-link-1228',
            title='Accessible Catalog Link 1228',
            description='Accessible resource.',
            url='https://example.com/accessible-catalog-link-1228',
            category='workshops',
            tags=['accessible-static-1228', 'agents-1228', 'python-1228', 'extra-1228'],
            required_level=0,
            published=True,
        )
        cls.gated_link = CuratedLink.objects.create(
            item_id='gated-catalog-link-1228',
            title='Gated Catalog Link 1228',
            description='Gated resource.',
            url='https://example.com/secret-catalog-link-1228',
            category='courses',
            tags=['gated-static-1228', 'agents-1228', 'python-1228', 'extra-1228', 'fifth-1228'],
            required_level=20,
            published=True,
        )

    @tag('visual_regression')
    def test_clickable_chips_use_exact_classes_and_native_destinations(self):
        cases = (
            ('/blog', 'agents-1228', '/blog?tag=agents-1228'),
            ('/downloads', 'python-1228', '/downloads?tag=python-1228'),
            ('/tags/agents-1228', 'python-1228', '/tags/python-1228'),
        )
        for path, text, href in cases:
            with self.subTest(path=path):
                parser = _parse(self.client.get(path))
                chip = next(
                    element for element in _elements_with_text(parser, text)
                    if element['tag'] == 'a'
                    and element['attrs'].get('href') == href
                    and (
                        path != '/downloads'
                        or element['attrs'].get('class') == CLICKABLE_CHIP_CLASSES
                    )
                )
                self.assertEqual(chip['attrs'].get('class'), CLICKABLE_CHIP_CLASSES)
                self.assertNotIn('min-h-[44px]', chip['attrs'].get('class', ''))

    def test_blog_and_download_tag_links_preserve_additive_filters(self):
        for path, text, expected_href in (
            ('/blog?tag=python-1228', 'agents-1228', '/blog?tag=python-1228&tag=agents-1228'),
            ('/downloads?tag=agents-1228', 'python-1228', '/downloads?tag=agents-1228&tag=python-1228'),
        ):
            with self.subTest(path=path):
                parser = _parse(self.client.get(path))
                chip = next(
                    element for element in _elements_with_text(parser, text)
                    if element['tag'] == 'a'
                    and (
                        not path.startswith('/downloads')
                        or element['attrs'].get('class') == CLICKABLE_CHIP_CLASSES
                    )
                )
                self.assertEqual(chip['attrs'].get('href'), expected_href)

    @tag('visual_regression')
    def test_tutorial_keeps_static_tag_semantics(self):
        parser = _parse(self.client.get('/tutorials'))
        matches = _elements_with_text(parser, 'tutorial-static-1228')
        static_chip = next(item for item in matches if item['tag'] == 'span')
        self.assertEqual(
            static_chip['attrs'].get('class'), STATIC_CHIP_CLASSES,
        )
        self.assertFalse(any(item['tag'] == 'a' for item in matches))

    @tag('visual_regression')
    def test_resource_chips_are_clickable_on_both_card_states(self):
        """Curated-link card chips link to the tag-filtered view for both
        accessible and gated cards, and never nest inside the card anchor.
        Chips were inert spans until the /resources tag-filter defect fix."""
        resources = _parse(self.client.get('/resources'))
        for text in ('accessible-static-1228', 'gated-static-1228'):
            with self.subTest(text=text):
                chip = next(
                    item for item in _elements_with_text(resources, text)
                    if item['tag'] == 'a'
                    and item['attrs'].get('class') == CLICKABLE_CHIP_CLASSES
                )
                self.assertEqual(
                    chip['attrs'].get('href'), f'/resources?tag={text}',
                )
                self.assertEqual(chip['anchor_ancestors'], [])
        self.assertEqual(resources.nested_anchor_hrefs, [])

        gated_card = next(
            element for element in resources.elements
            if element['attrs'].get('aria-label')
            == 'Show access options for Gated Catalog Link 1228'
        )
        self.assertEqual(gated_card['tag'], 'div')
        self.assertEqual(gated_card['attrs'].get('role'), 'button')

    @tag('visual_regression')
    def test_overflow_chips_are_static_accessible_and_keep_three_tag_cap(self):
        cases = (
            ('/blog', '1 more article tags', '+1', 'production-1228'),
            ('/downloads', '1 more download tags', '+1', 'extra-1228'),
            ('/resources', '1 more resource tags', '+1', 'extra-1228'),
            ('/resources', '2 more resource tags', '+2', 'extra-1228'),
        )
        for path, label, text, hidden_tag in cases:
            with self.subTest(path=path, label=label):
                response = self.client.get(path)
                parser = _parse(response)
                overflow = next(
                    element for element in parser.elements
                    if element['attrs'].get('aria-label') == label
                )
                self.assertEqual(overflow['tag'], 'span')
                self.assertEqual(overflow['attrs'].get('class'), STATIC_CHIP_CLASSES)
                self.assertEqual(overflow['text'].strip(), text)
                # Downloads and resources expose every available topic in
                # the separate 44px filter row, so the hidden fourth *card*
                # tag can still legitimately appear elsewhere on the page.
                if path not in ('/downloads', '/resources'):
                    self.assertNotIn(
                        f'>{hidden_tag}<',
                        response.content.decode(),
                    )

    def test_tag_detail_has_distinct_card_and_related_tag_anchors(self):
        parser = _parse(self.client.get('/tags/agents-1228'))
        self.assertEqual(parser.nested_anchor_hrefs, [])
        article_link = next(
            element for element in parser.elements
            if element['tag'] == 'a'
            and element['attrs'].get('href') == '/blog/catalog-article-1228'
        )
        related_link = next(
            element for element in parser.elements
            if element['tag'] == 'a'
            and element['attrs'].get('href') == '/tags/python-1228'
        )
        self.assertEqual(article_link['anchor_ancestors'], [])
        self.assertEqual(related_link['anchor_ancestors'], [])

    @tag('visual_regression')
    def test_mobile_arrows_use_exact_hidden_non_shrinking_treatment(self):
        for path in ('/tags/agents-1228', '/tutorials'):
            with self.subTest(path=path):
                parser = _parse(self.client.get(path))
                arrows = [
                    element for element in parser.elements
                    if element['attrs'].get('data-lucide') == 'arrow-right'
                    and element['attrs'].get('class') == ARROW_CLASSES
                ]
                self.assertGreaterEqual(len(arrows), 1)

    @tag('visual_regression')
    def test_result_typography_and_tag_eyebrow_match_catalog_contract(self):
        cases = (
            ('/blog', 'Catalog Article 1228', 'Article description for catalog hierarchy.'),
            ('/tutorials', 'Catalog Tutorial 1228', 'Tutorial description for catalog hierarchy.'),
            ('/tags/agents-1228', 'Catalog Article 1228', 'Article description for catalog hierarchy.'),
        )
        for path, title, description in cases:
            with self.subTest(path=path):
                parser = _parse(self.client.get(path))
                heading = next(
                    item for item in _elements_with_text(parser, title)
                    if item['tag'] == 'h2'
                )
                self.assertIn('text-lg font-semibold text-foreground', heading['attrs']['class'])
                body = next(
                    item for item in _elements_with_text(parser, description)
                    if item['tag'] == 'p'
                )
                self.assertIn('text-sm sm:text-base', body['attrs']['class'])

        tag_parser = _parse(self.client.get('/tags/agents-1228'))
        eyebrow = next(
            item for item in _elements_with_text(tag_parser, 'Tag')
            if item['tag'] == 'p'
        )
        self.assertEqual(
            eyebrow['attrs'].get('class'),
            'text-sm font-medium uppercase tracking-widest text-accent',
        )
        heading = next(
            item for item in _elements_with_text(tag_parser, 'agents-1228')
            if item['tag'] == 'h1'
        )
        self.assertIn('mt-4', heading['attrs'].get('class', '').split())
        self.assertFalse(
            any(
                item['attrs'].get('data-lucide') == 'tag'
                for item in tag_parser.elements
            )
        )
