"""Tests for project detail mobile responsive fixes - issue #178.

Covers:
- Project detail header has break-words for overflow protection
- Project title has overflow-wrap: break-word
"""

from datetime import date

from django.test import TestCase

from content.models import Project


class ProjectDetailOverflowProtectionTest(TestCase):
    """Project detail header should protect against overflow on narrow viewports."""

    @classmethod
    def setUpTestData(cls):
        cls.project = Project.objects.create(
            title='A' * 100,  # Very long title
            slug='long-title-project',
            description='Test description',
            date=date(2025, 6, 15),
            author='Test Author',
            difficulty='intermediate',
            content_html='<p>Test content</p>',
            published=True,
        )

    def test_header_has_break_words(self):
        response = self.client.get('/projects/long-title-project')
        content = response.content.decode()
        # The project detail header (inside <article>) should have break-words class
        # Find the article-level header, not the site navigation header
        article_pos = content.index('<article')
        article_section = content[article_pos:]
        header_pos = article_section.index('<header')
        header_tag = article_section[header_pos:header_pos + 200]
        self.assertIn('break-words', header_tag)

    def test_title_has_overflow_wrap(self):
        response = self.client.get('/projects/long-title-project')
        content = response.content.decode()
        # The h1 should have overflow-wrap: break-word
        self.assertIn('overflow-wrap: break-word', content)

    def test_page_renders_200(self):
        response = self.client.get('/projects/long-title-project')
        self.assertEqual(response.status_code, 200)
