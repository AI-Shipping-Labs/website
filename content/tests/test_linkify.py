"""Tests for bare URL auto-linking in markdown content -- issue #159.

Covers:
- linkify_urls utility: bare URLs get wrapped in <a> tags
- URLs inside <a>, <code>, <pre> are NOT linked
- URLs with query params and fragments
- Multiple URLs in one paragraph
- Integration with render_markdown for articles and course units
"""

from datetime import date

from django.test import TestCase

from content.utils.linkify import linkify_urls
from content.models.article import render_markdown as article_render_markdown
from content.models.course import render_markdown as course_render_markdown


class LinkifyUrlsTest(TestCase):
    """Tests for the linkify_urls() utility function."""

    def test_bare_url_gets_linked(self):
        html = '<p>Visit https://example.com for more info</p>'
        result = linkify_urls(html)
        self.assertIn(
            '<a href="https://example.com" target="_blank" rel="noopener noreferrer">'
            'https://example.com</a>',
            result,
        )

    def test_http_url_gets_linked(self):
        html = '<p>Visit http://example.com for more</p>'
        result = linkify_urls(html)
        self.assertIn(
            '<a href="http://example.com" target="_blank" rel="noopener noreferrer">'
            'http://example.com</a>',
            result,
        )

    def test_url_already_in_anchor_not_double_linked(self):
        html = '<p><a href="https://example.com">https://example.com</a></p>'
        result = linkify_urls(html)
        # Should remain unchanged -- no nested <a> tags
        self.assertEqual(result.count('<a '), 1)
        self.assertIn('href="https://example.com"', result)

    def test_url_inside_code_not_linked(self):
        html = '<p>Run <code>https://example.com/api</code> to test</p>'
        result = linkify_urls(html)
        self.assertNotIn('target="_blank"', result)
        self.assertIn('<code>https://example.com/api</code>', result)

    def test_url_inside_pre_not_linked(self):
        html = '<pre>https://example.com/raw</pre>'
        result = linkify_urls(html)
        self.assertNotIn('target="_blank"', result)
        self.assertIn('<pre>https://example.com/raw</pre>', result)

    def test_url_inside_pre_code_not_linked(self):
        html = '<pre><code>https://example.com/block</code></pre>'
        result = linkify_urls(html)
        self.assertNotIn('target="_blank"', result)

    def test_multiple_urls_in_paragraph(self):
        html = '<p>See https://one.com and https://two.com for details</p>'
        result = linkify_urls(html)
        self.assertIn('href="https://one.com"', result)
        self.assertIn('href="https://two.com"', result)
        self.assertEqual(result.count('target="_blank"'), 2)

    def test_url_at_start_of_text(self):
        html = '<p>https://start.com is the site</p>'
        result = linkify_urls(html)
        self.assertIn('href="https://start.com"', result)

    def test_url_at_end_of_text(self):
        html = '<p>The site is https://end.com</p>'
        result = linkify_urls(html)
        self.assertIn('href="https://end.com"', result)

    def test_url_with_path(self):
        html = '<p>See https://github.com/DataTalksClub/faq for details</p>'
        result = linkify_urls(html)
        self.assertIn('href="https://github.com/DataTalksClub/faq"', result)

    def test_url_with_query_params(self):
        html = '<p>Link: https://example.com/search?q=test&amp;page=2</p>'
        result = linkify_urls(html)
        self.assertIn(
            'href="https://example.com/search?q=test&amp;page=2"', result,
        )

    def test_url_with_fragment(self):
        html = '<p>See https://example.com/page#section for more</p>'
        result = linkify_urls(html)
        self.assertIn('href="https://example.com/page#section"', result)

    def test_url_with_query_and_fragment(self):
        html = '<p>Link: https://example.com/p?a=1#top</p>'
        result = linkify_urls(html)
        self.assertIn('href="https://example.com/p?a=1#top"', result)

    def test_links_have_noopener_noreferrer(self):
        html = '<p>https://example.com</p>'
        result = linkify_urls(html)
        self.assertIn('rel="noopener noreferrer"', result)

    def test_links_open_in_new_tab(self):
        html = '<p>https://example.com</p>'
        result = linkify_urls(html)
        self.assertIn('target="_blank"', result)

    def test_no_urls_unchanged(self):
        html = '<p>Just plain text here</p>'
        result = linkify_urls(html)
        self.assertEqual(result, html)

    def test_mixed_linked_and_code_urls(self):
        """URL in text gets linked, URL in code does not."""
        html = (
            '<p>Visit https://linked.com and run '
            '<code>https://not-linked.com</code></p>'
        )
        result = linkify_urls(html)
        self.assertIn('href="https://linked.com"', result)
        self.assertNotIn('href="https://not-linked.com"', result)


class RenderMarkdownLinkifyTest(TestCase):
    """Tests that render_markdown + linkify_urls pipeline auto-links bare URLs."""

    def _render_and_linkify(self, md):
        """Simulate the full content pipeline: markdown -> linkify."""
        return linkify_urls(article_render_markdown(md))

    def test_bare_url_in_markdown_gets_linked(self):
        html = self._render_and_linkify(
            'Check out https://github.com/DataTalksClub/faq for more info.'
        )
        self.assertIn('href="https://github.com/DataTalksClub/faq"', html)
        self.assertIn('target="_blank"', html)

    def test_markdown_link_syntax_not_double_linked(self):
        html = self._render_and_linkify('[Click here](https://example.com)')
        # Should have exactly one <a> tag
        self.assertEqual(html.count('<a '), 1)
        self.assertIn('href="https://example.com"', html)

    def test_url_in_inline_code_not_linked(self):
        html = self._render_and_linkify(
            'Run `https://example.com/api` to test'
        )
        self.assertNotIn('target="_blank"', html)

    def test_url_in_fenced_code_block_not_linked(self):
        html = self._render_and_linkify(
            '```\nhttps://example.com/code\n```'
        )
        self.assertNotIn('target="_blank"', html)

    def test_course_render_markdown_also_linkifies(self):
        html = linkify_urls(
            course_render_markdown('See https://example.com for details.')
        )
        self.assertIn('href="https://example.com"', html)
        self.assertIn('target="_blank"', html)

    def test_multiple_bare_urls_in_paragraph(self):
        html = self._render_and_linkify(
            'https://github.com/DataTalksClub/faq '
            '(source for https://datatalks.club/faq/) - FAQ'
        )
        self.assertIn('href="https://github.com/DataTalksClub/faq"', html)
        self.assertIn('href="https://datatalks.club/faq/"', html)


class ArticleModelLinkifyTest(TestCase):
    """Tests that Article.save() produces linked URLs in content_html."""

    @classmethod
    def setUpTestData(cls):
        from content.models import Article
        cls.article = Article.objects.create(
            title='Test Linkify',
            slug='test-linkify',
            content_markdown=(
                'Visit https://github.com/DataTalksClub/faq for the FAQ.\n\n'
                'Code example: `https://example.com/api`'
            ),
            date=date(2026, 1, 1),
            published=True,
        )

    def test_bare_url_linked_in_content_html(self):
        self.assertIn(
            'href="https://github.com/DataTalksClub/faq"',
            self.article.content_html,
        )
        self.assertIn('target="_blank"', self.article.content_html)

    def test_code_url_not_linked_in_content_html(self):
        self.assertNotIn(
            'href="https://example.com/api"',
            self.article.content_html,
        )


class CourseUnitLinkifyTest(TestCase):
    """Tests that Unit.save() produces linked URLs in body_html."""

    @classmethod
    def setUpTestData(cls):
        from content.models.course import Course, Module, Unit
        cls.course = Course.objects.create(
            title='Test Course',
            slug='test-course-linkify',
            status='published',
        )
        cls.module = Module.objects.create(
            course=cls.course,
            title='Module 1',
            slug='module-1',
            sort_order=1,
        )
        cls.unit = Unit.objects.create(
            module=cls.module,
            title='Unit 1',
            slug='unit-1',
            sort_order=1,
            body='See https://example.com/lesson for details.',
        )

    def test_bare_url_linked_in_unit_body_html(self):
        self.assertIn(
            'href="https://example.com/lesson"',
            self.unit.body_html,
        )
        self.assertIn('target="_blank"', self.unit.body_html)
