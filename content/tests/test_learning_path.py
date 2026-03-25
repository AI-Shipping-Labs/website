"""Tests for learning path rendered as Article with page_type='learning_path'."""

import os
import tempfile
import shutil
from datetime import date

from django.test import TestCase

from content.models import Article
from integrations.models import ContentSource
from integrations.services.github import sync_content_source


class LearningPathArticleModelTest(TestCase):
    """Test Article model with page_type and data_json fields."""

    @classmethod
    def setUpTestData(cls):
        cls.article = Article.objects.create(
            title='AI Engineer Learning Path',
            slug='ai-engineer-learning-path',
            description='A visual learning path.',
            content_markdown='## Stages\n\n<!-- widget:learning_stages data=learning_stages -->',
            page_type='learning_path',
            data_json={
                'learning_stages': [
                    {'stage': '1', 'title': 'Foundations', 'items': ['Python']},
                ],
            },
            date=date(2026, 1, 15),
            published=True,
        )

    def test_page_type_stored(self):
        self.assertEqual(self.article.page_type, 'learning_path')

    def test_data_json_stored(self):
        self.assertIn('learning_stages', self.article.data_json)
        self.assertEqual(len(self.article.data_json['learning_stages']), 1)

    def test_default_page_type_is_blog(self):
        blog = Article.objects.create(
            title='Regular Post',
            slug='regular-post',
            content_markdown='Hello',
            date=date(2026, 1, 1),
        )
        self.assertEqual(blog.page_type, 'blog')
        self.assertEqual(blog.data_json, {})


class LearningPathViewTest(TestCase):
    """Test view rendering for learning path articles."""

    @classmethod
    def setUpTestData(cls):
        cls.article = Article.objects.create(
            title='AI Engineer Learning Path',
            slug='ai-engineer-learning-path',
            description='A visual learning path for AI engineers.',
            content_markdown='## Stages\n\nSome learning path content here.',
            page_type='learning_path',
            data_json={
                'learning_stages': [
                    {'stage': '1', 'title': 'Foundations', 'items': ['Python']},
                ],
            },
            date=date(2026, 1, 15),
            published=True,
        )

    def test_learning_path_returns_200(self):
        response = self.client.get('/blog/ai-engineer-learning-path')
        self.assertEqual(response.status_code, 200)

    def test_uses_learning_path_template(self):
        response = self.client.get('/blog/ai-engineer-learning-path')
        self.assertTemplateUsed(response, 'content/learning_path_detail.html')

    def test_shows_title(self):
        response = self.client.get('/blog/ai-engineer-learning-path')
        self.assertContains(response, 'AI Engineer Learning Path')

    def test_shows_description(self):
        response = self.client.get('/blog/ai-engineer-learning-path')
        self.assertContains(response, 'A visual learning path for AI engineers.')

    def test_renders_content_html(self):
        response = self.client.get('/blog/ai-engineer-learning-path')
        self.assertContains(response, 'Some learning path content here.')

    def test_context_has_learning_stages(self):
        response = self.client.get('/blog/ai-engineer-learning-path')
        self.assertEqual(len(response.context['learning_stages']), 1)

    def test_has_structured_data(self):
        response = self.client.get('/blog/ai-engineer-learning-path')
        self.assertContains(response, 'application/ld+json')

    def test_shows_learning_path_label(self):
        response = self.client.get('/blog/ai-engineer-learning-path')
        self.assertContains(response, 'Learning Path')


class LearningPathRedirectTest(TestCase):
    """Test that the old URL redirects to the new article URL."""

    def test_old_url_returns_301(self):
        response = self.client.get('/learning-path/ai-engineer')
        self.assertEqual(response.status_code, 301)

    def test_old_url_redirects_to_article(self):
        response = self.client.get('/learning-path/ai-engineer')
        self.assertEqual(response['Location'], '/blog/ai-engineer-learning-path')


class BlogListExcludesLearningPathTest(TestCase):
    """Test that blog listing excludes learning_path articles."""

    @classmethod
    def setUpTestData(cls):
        cls.blog_article = Article.objects.create(
            title='Regular Blog Post',
            slug='regular-blog',
            content_markdown='Hello',
            page_type='blog',
            date=date(2026, 1, 1),
            published=True,
        )
        cls.lp_article = Article.objects.create(
            title='AI Engineer Learning Path',
            slug='ai-engineer-lp',
            content_markdown='LP content',
            page_type='learning_path',
            date=date(2026, 1, 15),
            published=True,
        )

    def test_blog_list_shows_blog_articles(self):
        response = self.client.get('/blog')
        self.assertContains(response, 'Regular Blog Post')

    def test_blog_list_excludes_learning_path(self):
        response = self.client.get('/blog')
        self.assertNotContains(response, 'AI Engineer Learning Path')


class RegularBlogUnaffectedTest(TestCase):
    """Test that regular blog articles still work normally."""

    @classmethod
    def setUpTestData(cls):
        cls.article = Article.objects.create(
            title='Normal Article',
            slug='normal-article',
            content_markdown='# Hello\n\nSome content.',
            author='Test Author',
            date=date(2026, 2, 1),
            published=True,
        )

    def test_blog_detail_returns_200(self):
        response = self.client.get('/blog/normal-article')
        self.assertEqual(response.status_code, 200)

    def test_uses_blog_detail_template(self):
        response = self.client.get('/blog/normal-article')
        self.assertTemplateUsed(response, 'content/blog_detail.html')

    def test_page_type_defaults_to_blog(self):
        self.assertEqual(self.article.page_type, 'blog')

    def test_data_json_defaults_to_empty(self):
        self.assertEqual(self.article.data_json, {})


class SyncLearningPathArticleTest(TestCase):
    """Test that the sync pipeline handles learning_path page_type articles."""

    def setUp(self):
        self.source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
            content_type='article',
            content_path='blog',
        )
        self.temp_dir = tempfile.mkdtemp()
        self.blog_dir = os.path.join(self.temp_dir, 'blog')
        os.makedirs(self.blog_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _write_md(self, subdir, filename, content):
        dirpath = os.path.join(self.blog_dir, subdir)
        os.makedirs(dirpath, exist_ok=True)
        with open(os.path.join(dirpath, filename), 'w') as f:
            f.write(content)

    def test_sync_creates_article_with_page_type(self):
        self._write_md('ai-engineer-learning-path', 'index.md', (
            '---\n'
            'title: "AI Engineer Learning Path"\n'
            'slug: "ai-engineer-learning-path"\n'
            'description: "A visual learning path."\n'
            'date: "2026-01-15"\n'
            'content_id: "a1b2c3d4-e5f6-7890-abcd-ef1234567890"\n'
            'page_type: learning_path\n'
            'data:\n'
            '  learning_stages:\n'
            '    - stage: "1"\n'
            '      title: "Foundations"\n'
            '      items:\n'
            '        - "Python fluency"\n'
            '---\n'
            '\n'
            '## Learning Stages\n'
            '\n'
            '<!-- widget:learning_stages data=learning_stages -->\n'
        ))

        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)
        self.assertEqual(sync_log.status, 'success')
        self.assertEqual(sync_log.items_created, 1)

        article = Article.objects.get(slug='ai-engineer-learning-path')
        self.assertEqual(article.page_type, 'learning_path')
        self.assertIn('learning_stages', article.data_json)
        # Widget should be expanded in content_html
        self.assertIn('Foundations', article.content_html)
        self.assertIn('Python fluency', article.content_html)
        # Widget marker should be replaced
        self.assertNotIn('<!-- widget:', article.content_html)

    def test_sync_regular_article_unaffected(self):
        self._write_md('', 'regular.md', (
            '---\n'
            'title: "Regular Post"\n'
            'slug: "regular-post"\n'
            'date: "2026-02-01"\n'
            'content_id: "b2c3d4e5-f6a7-8901-bcde-f12345678901"\n'
            '---\n'
            '\n'
            '# Hello\n'
            '\n'
            'Normal content.\n'
        ))

        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)
        self.assertEqual(sync_log.status, 'success')

        article = Article.objects.get(slug='regular-post')
        self.assertEqual(article.page_type, 'blog')
        self.assertEqual(article.data_json, {})
        self.assertIn('Normal content', article.content_html)

    def test_sync_stores_data_json(self):
        self._write_md('', 'lp.md', (
            '---\n'
            'title: "LP"\n'
            'slug: "lp-test"\n'
            'date: "2026-01-15"\n'
            'content_id: "c3d4e5f6-a7b8-9012-cdef-123456789012"\n'
            'page_type: learning_path\n'
            'data:\n'
            '  skill_categories:\n'
            '    - label: "GenAI"\n'
            '      description: "Core skills"\n'
            '      skills:\n'
            '        - name: "RAG"\n'
            '          pct: 35.9\n'
            '          priority: essential\n'
            '---\n'
            '\n'
            '## Skills\n'
            '\n'
            '<!-- widget:skill_chart data=skill_categories -->\n'
        ))

        sync_content_source(self.source, repo_dir=self.temp_dir)

        article = Article.objects.get(slug='lp-test')
        self.assertEqual(article.data_json['skill_categories'][0]['label'], 'GenAI')
        self.assertIn('RAG', article.content_html)
        self.assertIn('35.9%', article.content_html)

    def test_sync_logs_error_for_missing_widget_data_key(self):
        self._write_md('', 'bad-lp.md', (
            '---\n'
            'title: "Bad LP"\n'
            'slug: "bad-lp"\n'
            'date: "2026-01-15"\n'
            'content_id: "d4e5f6a7-b8c9-0123-defa-234567890123"\n'
            'page_type: learning_path\n'
            'data:\n'
            '  other_key: []\n'
            '---\n'
            '\n'
            '<!-- widget:skill_chart data=missing_key -->\n'
        ))

        sync_log = sync_content_source(self.source, repo_dir=self.temp_dir)
        # The error should be logged, not silently swallowed
        self.assertTrue(len(sync_log.errors) > 0)
        error_text = str(sync_log.errors)
        self.assertIn('missing_key', error_text)
