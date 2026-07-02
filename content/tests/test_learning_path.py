"""Tests for learning path articles and content-owned include expansion."""

import os
import tempfile
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
            content_markdown='## Stages\n\nRendered content.',
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

    def test_uses_shared_blog_detail_template(self):
        response = self.client.get('/blog/ai-engineer-learning-path')
        self.assertTemplateUsed(response, 'content/blog_detail.html')
        self.assertTemplateNotUsed(response, 'content/learning_path_detail.html')

    def test_shows_title_description_and_content(self):
        response = self.client.get('/blog/ai-engineer-learning-path')
        self.assertContains(response, 'AI Engineer Learning Path')
        self.assertContains(response, 'A visual learning path for AI engineers.')
        self.assertContains(response, 'Some learning path content here.')

    def test_context_has_learning_stages_for_jsonld(self):
        response = self.client.get('/blog/ai-engineer-learning-path')
        self.assertEqual(len(response.context['learning_stages']), 1)

    def test_has_learning_path_structured_data(self):
        response = self.client.get('/blog/ai-engineer-learning-path')
        self.assertContains(response, 'application/ld+json')
        self.assertContains(response, '"@type": "Course"')
        self.assertContains(response, '"name": "AI Engineer Learning Path"')


class LearningPathRedirectTest(TestCase):
    """Test that the legacy URL redirects to the article URL."""

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
    """Test article sync include expansion for learning path content."""

    def setUp(self):
        self.source = ContentSource.objects.create(
            repo_name='AI-Shipping-Labs/content',
        )

    def _write(self, root, rel_path, content):
        path = os.path.join(root, rel_path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)

    def _article_frontmatter(
        self,
        *,
        content_id='00000000-0000-4000-8000-000000000001',
        slug='ai-engineer-learning-path',
        title='AI Engineer Learning Path',
    ):
        return (
            '---\n'
            f'title: "{title}"\n'
            f'slug: "{slug}"\n'
            'description: "A visual learning path."\n'
            'date: "2026-01-15"\n'
            f'content_id: "{content_id}"\n'
            'page_type: learning_path\n'
            'data:\n'
            '  learning_stages:\n'
            '    - stage: "1"\n'
            '      title: "Foundations"\n'
            '      items:\n'
            '        - "Python fluency"\n'
            '  skill_categories:\n'
            '    - label: "GenAI"\n'
            '      description: "Core skills"\n'
            '      skills:\n'
            '        - name: "RAG"\n'
            '          pct: 35.9\n'
            '          priority: essential\n'
            '  tool_categories:\n'
            '    - label: "LLM Providers"\n'
            '      tools:\n'
            '        - name: "OpenAI API"\n'
            '          pct: 8.7\n'
            '  responsibilities:\n'
            '    core:\n'
            '      - title: "Build AI Systems"\n'
            '        description: "Ship useful systems."\n'
            '  portfolio_projects:\n'
            '    - number: "01"\n'
            '      title: "Production RAG System"\n'
            '      difficulty: Foundational\n'
            '      description: "Build a real RAG app."\n'
            '---\n\n'
        )

    def test_sync_expands_content_owned_shared_widgets(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write(
                tmp,
                'widgets/learning_stages.html',
                (
                    '<section id="stages">'
                    '{% for stage in data.learning_stages %}'
                    '<h3>{{ stage.title }}</h3>'
                    '{% for item in stage.items %}<p>{{ item }}</p>{% endfor %}'
                    '{% endfor %}'
                    '</section>'
                ),
            )
            self._write(
                tmp,
                'blog/ai-engineer-learning-path/index.md',
                self._article_frontmatter()
                + '## Learning Stages\n\n'
                + '<!-- include:widgets/learning_stages.html -->\n',
            )

            sync_log = sync_content_source(self.source, repo_dir=tmp)

        self.assertEqual(sync_log.status, 'success')
        self.assertEqual(sync_log.errors, [])
        article = Article.objects.get(slug='ai-engineer-learning-path')
        self.assertEqual(article.page_type, 'learning_path')
        self.assertIn('learning_stages', article.data_json)
        self.assertIn('Foundations', article.content_html)
        self.assertIn('Python fluency', article.content_html)
        self.assertNotIn('<!-- include:', article.content_html)
        self.assertNotIn('<!-- widget:', article.content_html)

    def test_article_include_receives_full_frontmatter_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            templates = {
                'widgets/skill_chart.html': (
                    '{% for category in data.skill_categories %}'
                    '<p>{{ category.label }} {{ category.skills.0.name }} '
                    '{{ category.skills.0.pct }}%</p>{% endfor %}'
                ),
                'widgets/tool_chart.html': (
                    '{% for category in data.tool_categories %}'
                    '<p>{{ category.label }} {{ category.tools.0.name }}</p>'
                    '{% endfor %}'
                ),
                'widgets/responsibilities.html': (
                    '{% for item in data.responsibilities.core %}'
                    '<p>{{ item.title }} {{ item.description }}</p>{% endfor %}'
                ),
                'widgets/project_grid.html': (
                    '{% for project in data.portfolio_projects %}'
                    '<p>{{ project.number }} {{ project.title }}</p>{% endfor %}'
                ),
            }
            for rel_path, body in templates.items():
                self._write(tmp, rel_path, body)
            self._write(
                tmp,
                'blog/ai-engineer-learning-path/index.md',
                self._article_frontmatter()
                + '<!-- include:widgets/skill_chart.html -->\n'
                + '<!-- include:widgets/tool_chart.html -->\n'
                + '<!-- include:widgets/responsibilities.html -->\n'
                + '<!-- include:widgets/project_grid.html -->\n',
            )

            sync_log = sync_content_source(self.source, repo_dir=tmp)

        self.assertEqual(sync_log.errors, [])
        article = Article.objects.get(slug='ai-engineer-learning-path')
        self.assertIn('GenAI RAG 35.9%', article.content_html)
        self.assertIn('LLM Providers OpenAI API', article.content_html)
        self.assertIn('Build AI Systems Ship useful systems.', article.content_html)
        self.assertIn('01 Production RAG System', article.content_html)
        self.assertNotIn('items', article.content_html)

    def test_source_relative_article_include_still_works_outside_widget_namespace(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write(tmp, 'partials/callout.html', '<p>Wrong root callout</p>')
            self._write(
                tmp,
                'blog/example/partials/callout.html',
                '<aside>Article-local callout</aside>',
            )
            self._write(
                tmp,
                'blog/example/index.md',
                (
                    '---\n'
                    'title: "Example"\n'
                    'slug: "example"\n'
                    'date: "2026-02-01"\n'
                    'content_id: "11111111-1111-4111-8111-111111111111"\n'
                    '---\n\n'
                    '<!-- include:partials/callout.html -->\n'
                ),
            )

            sync_log = sync_content_source(self.source, repo_dir=tmp)

        self.assertEqual(sync_log.errors, [])
        article = Article.objects.get(slug='example')
        self.assertIn('Article-local callout', article.content_html)
        self.assertNotIn('Wrong root callout', article.content_html)

    def test_shared_widget_namespace_does_not_fallback_to_article_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write(
                tmp,
                'blog/example/widgets/learning_stages.html',
                '<p>Should not render</p>',
            )
            self._write(
                tmp,
                'blog/example/index.md',
                (
                    '---\n'
                    'title: "Example"\n'
                    'slug: "example"\n'
                    'date: "2026-02-01"\n'
                    'content_id: "22222222-2222-4222-8222-222222222222"\n'
                    '---\n\n'
                    '<!-- include:widgets/learning_stages.html -->\n'
                ),
            )

            sync_log = sync_content_source(self.source, repo_dir=tmp)

        self.assertEqual(sync_log.status, 'partial')
        self.assertIn('widgets/learning_stages.html', str(sync_log.errors))
        self.assertFalse(Article.objects.filter(slug='example').exists())

    def test_bad_article_include_fails_visibly(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write(
                tmp,
                'blog/missing/index.md',
                (
                    '---\n'
                    'title: "Missing Include"\n'
                    'slug: "missing-include"\n'
                    'date: "2026-02-01"\n'
                    'content_id: "33333333-3333-4333-8333-333333333333"\n'
                    '---\n\n'
                    '<!-- include:partials/missing.html -->\n'
                ),
            )
            self._write(
                tmp,
                'blog/traversal/index.md',
                (
                    '---\n'
                    'title: "Traversal Include"\n'
                    'slug: "traversal-include"\n'
                    'date: "2026-02-02"\n'
                    'content_id: "44444444-4444-4444-8444-444444444444"\n'
                    '---\n\n'
                    '<!-- include:../secret.html -->\n'
                ),
            )

            sync_log = sync_content_source(self.source, repo_dir=tmp)

        self.assertEqual(sync_log.status, 'partial')
        error_text = str(sync_log.errors)
        self.assertIn('Include file not found: partials/missing.html', error_text)
        self.assertIn('Include path escapes content repo: ../secret.html', error_text)
        self.assertFalse(Article.objects.filter(slug='missing-include').exists())
        self.assertFalse(Article.objects.filter(slug='traversal-include').exists())

    def test_sync_regular_article_unaffected(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write(
                tmp,
                'blog/regular.md',
                (
                    '---\n'
                    'title: "Regular Post"\n'
                    'slug: "regular-post"\n'
                    'date: "2026-02-01"\n'
                    'content_id: "55555555-5555-4555-8555-555555555555"\n'
                    '---\n\n'
                    '# Hello\n\n'
                    'Normal content.\n'
                ),
            )

            sync_log = sync_content_source(self.source, repo_dir=tmp)

        self.assertEqual(sync_log.status, 'success')
        article = Article.objects.get(slug='regular-post')
        self.assertEqual(article.page_type, 'blog')
        self.assertEqual(article.data_json, {})
        self.assertIn('Normal content', article.content_html)
