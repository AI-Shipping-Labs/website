"""Tests for Articles (Blog) - issue #72.

Covers:
- Article model fields (cover_image_url, status, published_at, etc.)
- Markdown rendering with syntax highlighting
- Auto-generated fields on save (content_html, reading_time, description)
- Tag filtering on /blog via ?tag=X
- Related articles on detail page
- Admin CRUD (publish/unpublish actions)
- Title tag format on detail page
- Publish/unpublish model methods
"""

from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase, Client
from django.utils import timezone

from content.access import LEVEL_OPEN, LEVEL_BASIC
from content.models import Article
from content.models.article import render_markdown

User = get_user_model()


# --- Model field tests ---


class ArticleFieldsTest(TestCase):
    """Test that Article has all required fields from issue #72."""

    def test_cover_image_url_field_exists(self):
        article = Article.objects.create(
            title='Test', slug='test-fields', date=date(2025, 1, 1),
            cover_image_url='https://example.com/image.png',
        )
        self.assertEqual(article.cover_image_url, 'https://example.com/image.png')

    def test_cover_image_url_default_empty(self):
        article = Article.objects.create(
            title='Test', slug='test-cover-default', date=date(2025, 1, 1),
        )
        self.assertEqual(article.cover_image_url, '')

    def test_status_field_exists(self):
        article = Article.objects.create(
            title='Test', slug='test-status', date=date(2025, 1, 1),
            published=True,
        )
        self.assertEqual(article.status, 'published')

    def test_published_at_field_exists(self):
        article = Article.objects.create(
            title='Test', slug='test-pub-at', date=date(2025, 1, 1),
            published=True,
        )
        self.assertIsNotNone(article.published_at)

    def test_published_at_null_for_draft(self):
        article = Article.objects.create(
            title='Test', slug='test-draft-pub-at', date=date(2025, 1, 1),
            published=False,
        )
        self.assertIsNone(article.published_at)

    def test_tags_field_is_list(self):
        article = Article.objects.create(
            title='Test', slug='test-tags', date=date(2025, 1, 1),
            tags=['python', 'ai', 'mcp'],
            published=True,
        )
        self.assertEqual(article.tags, ['python', 'ai', 'mcp'])


# --- Status/Published sync tests ---


class ArticleStatusSyncTest(TestCase):
    """Test that status and published stay in sync."""

    def test_published_true_sets_status_published(self):
        article = Article.objects.create(
            title='Published', slug='pub-sync', date=date(2025, 1, 1),
            published=True,
        )
        self.assertEqual(article.status, 'published')

    def test_published_false_sets_status_draft(self):
        article = Article.objects.create(
            title='Draft', slug='draft-sync', date=date(2025, 1, 1),
            published=False,
        )
        self.assertEqual(article.status, 'draft')

    def test_published_true_sets_published_at(self):
        article = Article.objects.create(
            title='Test', slug='pub-at-sync', date=date(2025, 1, 1),
            published=True,
        )
        self.assertIsNotNone(article.published_at)

    def test_published_false_no_published_at(self):
        article = Article.objects.create(
            title='Test', slug='no-pub-at', date=date(2025, 1, 1),
            published=False,
        )
        self.assertIsNone(article.published_at)

    def test_publish_method(self):
        article = Article.objects.create(
            title='Test', slug='publish-method', date=date(2025, 1, 1),
            published=False,
        )
        self.assertFalse(article.published)
        article.publish()
        article.refresh_from_db()
        self.assertTrue(article.published)
        self.assertEqual(article.status, 'published')
        self.assertIsNotNone(article.published_at)

    def test_unpublish_method(self):
        article = Article.objects.create(
            title='Test', slug='unpublish-method', date=date(2025, 1, 1),
            published=True,
        )
        self.assertTrue(article.published)
        article.unpublish()
        article.refresh_from_db()
        self.assertFalse(article.published)
        self.assertEqual(article.status, 'draft')


# --- Markdown rendering tests ---


class MarkdownRenderingTest(TestCase):
    """Test markdown rendering with syntax highlighting."""

    def test_headings_rendered(self):
        html = render_markdown('# Heading 1\n## Heading 2\n### Heading 3')
        self.assertIn('<h1>Heading 1</h1>', html)
        self.assertIn('<h2>Heading 2</h2>', html)
        self.assertIn('<h3>Heading 3</h3>', html)

    def test_bold_italic_rendered(self):
        html = render_markdown('**bold** and *italic*')
        self.assertIn('<strong>bold</strong>', html)
        self.assertIn('<em>italic</em>', html)

    def test_links_rendered(self):
        html = render_markdown('[Click here](https://example.com)')
        self.assertIn('<a href="https://example.com">Click here</a>', html)

    def test_images_rendered(self):
        html = render_markdown('![Alt text](https://example.com/image.png)')
        self.assertIn('<img', html)
        self.assertIn('src="https://example.com/image.png"', html)

    def test_code_blocks_with_language(self):
        md = '```python\nprint("hello")\n```'
        html = render_markdown(md)
        # codehilite wraps code blocks in a div with class "codehilite"
        self.assertIn('codehilite', html)

    def test_code_blocks_have_syntax_classes(self):
        md = '```python\ndef hello():\n    return "world"\n```'
        html = render_markdown(md)
        # Pygments should add span elements with syntax classes
        self.assertIn('codehilite', html)
        # Should have actual syntax highlighting spans
        self.assertIn('<span', html)

    def test_fenced_code_without_language(self):
        md = '```\nsome code\n```'
        html = render_markdown(md)
        self.assertIn('code', html)

    def test_tables_rendered(self):
        md = '| A | B |\n|---|---|\n| 1 | 2 |'
        html = render_markdown(md)
        self.assertIn('<table>', html)
        self.assertIn('<td>1</td>', html)


class ArticleSaveRendersMarkdownTest(TestCase):
    """Test that saving an article auto-renders markdown to HTML."""

    def test_content_html_generated_on_save(self):
        article = Article.objects.create(
            title='Test', slug='md-render', date=date(2025, 1, 1),
            content_markdown='# Hello\nThis is **bold** content.',
            published=True,
        )
        self.assertIn('<h1>Hello</h1>', article.content_html)
        self.assertIn('<strong>bold</strong>', article.content_html)

    def test_reading_time_auto_calculated(self):
        # 200 words = 1 min read
        words = ' '.join(['word'] * 400)
        article = Article.objects.create(
            title='Test', slug='reading-time', date=date(2025, 1, 1),
            content_markdown=words,
            published=True,
        )
        self.assertEqual(article.reading_time, '2 min read')

    def test_description_auto_generated_from_markdown(self):
        article = Article.objects.create(
            title='Test', slug='auto-desc', date=date(2025, 1, 1),
            content_markdown='This is a long markdown body that will be used to auto-generate the description field.',
            published=True,
        )
        self.assertTrue(article.description.startswith('This is a long'))

    def test_explicit_description_not_overridden(self):
        article = Article.objects.create(
            title='Test', slug='explicit-desc', date=date(2025, 1, 1),
            description='My custom description',
            content_markdown='Some content here',
            published=True,
        )
        self.assertEqual(article.description, 'My custom description')

    def test_syntax_highlighting_in_saved_html(self):
        md = '```python\ndef hello():\n    return "world"\n```'
        article = Article.objects.create(
            title='Code', slug='code-highlight', date=date(2025, 1, 1),
            content_markdown=md,
            published=True,
        )
        self.assertIn('codehilite', article.content_html)


# --- Tag filtering tests ---


class BlogListTagFilteringTest(TestCase):
    """Test tag filtering on /blog via ?tag=X query param."""

    def setUp(self):
        self.client = Client()
        self.python_article = Article.objects.create(
            title='Python Tutorial',
            slug='python-tutorial',
            description='Learn Python',
            date=date(2025, 6, 15),
            tags=['python', 'tutorial'],
            published=True,
        )
        self.ai_article = Article.objects.create(
            title='AI Engineering',
            slug='ai-engineering',
            description='AI stuff',
            date=date(2025, 6, 14),
            tags=['ai', 'engineering'],
            published=True,
        )
        self.both_article = Article.objects.create(
            title='Python AI',
            slug='python-ai',
            description='Python for AI',
            date=date(2025, 6, 13),
            tags=['python', 'ai'],
            published=True,
        )

    def test_no_filter_shows_all_articles(self):
        response = self.client.get('/blog')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Python Tutorial')
        self.assertContains(response, 'AI Engineering')
        self.assertContains(response, 'Python AI')

    def test_filter_by_python_tag(self):
        response = self.client.get('/blog?tag=python')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Python Tutorial')
        self.assertContains(response, 'Python AI')
        self.assertNotContains(response, 'AI Engineering')

    def test_filter_by_ai_tag(self):
        response = self.client.get('/blog?tag=ai')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'AI Engineering')
        self.assertContains(response, 'Python AI')
        self.assertNotContains(response, 'Python Tutorial')

    def test_filter_by_nonexistent_tag(self):
        response = self.client.get('/blog?tag=nonexistent')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Python Tutorial')
        self.assertNotContains(response, 'AI Engineering')

    def test_tag_links_in_listing(self):
        response = self.client.get('/blog')
        content = response.content.decode()
        self.assertIn('?tag=python', content)
        self.assertIn('?tag=ai', content)

    def test_all_tags_displayed(self):
        response = self.client.get('/blog')
        content = response.content.decode()
        # The tag filter bar should show all unique tags
        self.assertIn('Filter by tag', content)

    def test_current_tag_in_context(self):
        response = self.client.get('/blog?tag=python')
        self.assertEqual(response.context['current_tag'], 'python')
        self.assertEqual(response.context['selected_tags'], ['python'])

    def test_clear_filter_link(self):
        response = self.client.get('/blog?tag=python')
        content = response.content.decode()
        self.assertIn('Clear all', content)


# --- Related articles tests ---


class RelatedArticlesTest(TestCase):
    """Test related articles on blog detail page."""

    def setUp(self):
        self.client = Client()
        self.main_article = Article.objects.create(
            title='Main Article',
            slug='main-article',
            description='Main description',
            content_markdown='# Main\nContent here.',
            date=date(2025, 6, 15),
            tags=['python', 'ai'],
            published=True,
        )
        self.related1 = Article.objects.create(
            title='Related Python',
            slug='related-python',
            description='Related Python article',
            date=date(2025, 6, 14),
            tags=['python'],
            published=True,
        )
        self.related2 = Article.objects.create(
            title='Related AI',
            slug='related-ai',
            description='Related AI article',
            date=date(2025, 6, 13),
            tags=['ai', 'engineering'],
            published=True,
        )
        self.unrelated = Article.objects.create(
            title='Unrelated',
            slug='unrelated',
            description='Unrelated article',
            date=date(2025, 6, 12),
            tags=['golang'],
            published=True,
        )

    def test_related_articles_shown_on_detail(self):
        response = self.client.get('/blog/main-article')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Related Articles')
        self.assertContains(response, 'Related Python')
        self.assertContains(response, 'Related AI')

    def test_unrelated_article_not_shown(self):
        response = self.client.get('/blog/main-article')
        self.assertNotContains(response, 'Unrelated')

    def test_article_not_related_to_itself(self):
        related = self.main_article.get_related_articles()
        self.assertNotIn(self.main_article, related)

    def test_no_related_articles_section_when_none(self):
        response = self.client.get('/blog/unrelated')
        self.assertNotContains(response, 'Related Articles')

    def test_related_articles_limited_to_3(self):
        # Create 5 related articles
        for i in range(5):
            Article.objects.create(
                title=f'Extra Related {i}',
                slug=f'extra-related-{i}',
                date=date(2025, 6, 1),
                tags=['python'],
                published=True,
            )
        related = self.main_article.get_related_articles(limit=3)
        self.assertEqual(related.count(), 3)

    def test_unpublished_articles_not_in_related(self):
        Article.objects.create(
            title='Unpublished Related',
            slug='unpublished-related',
            date=date(2025, 6, 10),
            tags=['python', 'ai'],
            published=False,
        )
        related = self.main_article.get_related_articles()
        slugs = [a.slug for a in related]
        self.assertNotIn('unpublished-related', slugs)

    def test_no_tags_returns_empty(self):
        article = Article.objects.create(
            title='No Tags',
            slug='no-tags',
            date=date(2025, 6, 1),
            tags=[],
            published=True,
        )
        related = article.get_related_articles()
        self.assertEqual(related.count(), 0)


# --- Blog listing display tests ---


class BlogListDisplayTest(TestCase):
    """Test that blog listing shows required fields."""

    def setUp(self):
        self.client = Client()
        self.article = Article.objects.create(
            title='Full Article',
            slug='full-article',
            description='Full description',
            cover_image_url='https://example.com/cover.jpg',
            date=date(2025, 6, 15),
            author='Test Author',
            tags=['python', 'ai'],
            published=True,
        )

    def test_shows_title(self):
        response = self.client.get('/blog')
        self.assertContains(response, 'Full Article')

    def test_shows_description(self):
        response = self.client.get('/blog')
        self.assertContains(response, 'Full description')

    def test_shows_author(self):
        response = self.client.get('/blog')
        self.assertContains(response, 'Test Author')

    def test_shows_date(self):
        response = self.client.get('/blog')
        self.assertContains(response, 'June 15, 2025')

    def test_shows_tags(self):
        response = self.client.get('/blog')
        self.assertContains(response, 'python')
        self.assertContains(response, 'ai')

    def test_shows_cover_image(self):
        response = self.client.get('/blog')
        self.assertContains(response, 'https://example.com/cover.jpg')

    def test_gated_article_shows_lock_icon(self):
        Article.objects.create(
            title='Gated', slug='gated-article',
            description='Gated desc', date=date(2025, 6, 14),
            required_level=LEVEL_BASIC, published=True,
        )
        response = self.client.get('/blog')
        self.assertContains(response, 'data-lucide="lock"')

    def test_gated_article_shows_tier_name(self):
        Article.objects.create(
            title='Gated', slug='gated-tier-name',
            description='Gated desc', date=date(2025, 6, 14),
            required_level=LEVEL_BASIC, published=True,
        )
        response = self.client.get('/blog')
        self.assertContains(response, 'Basic+')


# --- Title tag tests ---


class BlogDetailTitleTagTest(TestCase):
    """Test that detail page title follows '{Article Title} | AI Shipping Labs' format."""

    def setUp(self):
        self.client = Client()
        self.article = Article.objects.create(
            title='My Great Article',
            slug='my-great-article',
            description='Description',
            content_markdown='Content here',
            date=date(2025, 6, 15),
            published=True,
        )

    def test_title_tag_format(self):
        response = self.client.get('/blog/my-great-article')
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn('<title>My Great Article | AI Shipping Labs</title>', content)


# --- Blog detail display tests ---


class BlogDetailDisplayTest(TestCase):
    """Test that blog detail page shows all required elements."""

    def setUp(self):
        self.client = Client()
        self.article = Article.objects.create(
            title='Detail Article',
            slug='detail-article',
            description='Detail description',
            content_markdown='# Hello\n\nThis is the **full** content.\n\n```python\nprint("hello")\n```',
            cover_image_url='https://example.com/detail-cover.jpg',
            date=date(2025, 6, 15),
            author='Detail Author',
            tags=['python', 'tutorial'],
            published=True,
        )

    def test_shows_cover_image(self):
        response = self.client.get('/blog/detail-article')
        self.assertContains(response, 'https://example.com/detail-cover.jpg')

    def test_shows_author(self):
        response = self.client.get('/blog/detail-article')
        self.assertContains(response, 'Detail Author')

    def test_shows_rendered_markdown(self):
        response = self.client.get('/blog/detail-article')
        content = response.content.decode()
        self.assertIn('<h1>Hello</h1>', content)
        self.assertIn('<strong>full</strong>', content)

    def test_shows_syntax_highlighted_code(self):
        response = self.client.get('/blog/detail-article')
        content = response.content.decode()
        self.assertIn('codehilite', content)

    def test_tag_links_point_to_filter(self):
        response = self.client.get('/blog/detail-article')
        content = response.content.decode()
        self.assertIn('?tag=python', content)
        self.assertIn('?tag=tutorial', content)

    def test_gated_article_shows_cta(self):
        gated = Article.objects.create(
            title='Gated Detail',
            slug='gated-detail',
            description='Gated description',
            content_html='<p>Secret content</p>',
            date=date(2025, 6, 14),
            required_level=LEVEL_BASIC,
            published=True,
        )
        response = self.client.get('/blog/gated-detail')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Secret content')
        self.assertContains(response, 'Upgrade to Basic to read this article')
        self.assertContains(response, '/pricing')


# --- Admin tests ---


class ArticleAdminTest(TestCase):
    """Test admin CRUD for articles."""

    def setUp(self):
        self.client = Client()
        self.admin_user = User.objects.create_superuser(
            email='admin@test.com', password='testpass',
        )
        self.client.login(email='admin@test.com', password='testpass')

    def test_admin_article_list(self):
        Article.objects.create(
            title='Admin Test', slug='admin-test', date=date(2025, 6, 15),
            published=True,
        )
        response = self.client.get('/admin/content/article/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Admin Test')

    def test_admin_article_add_page(self):
        response = self.client.get('/admin/content/article/add/')
        self.assertEqual(response.status_code, 200)

    def test_admin_create_article(self):
        response = self.client.post('/admin/content/article/add/', {
            'title': 'New Article',
            'slug': 'new-article',
            'description': 'A new article',
            'content_markdown': '# New\nContent here.',
            'cover_image_url': '',
            'author': 'Admin',
            'tags': '[]',
            'required_level': 0,
            'published': True,
            'date': '2025-06-15',
        })
        self.assertEqual(Article.objects.filter(slug='new-article').count(), 1)
        article = Article.objects.get(slug='new-article')
        self.assertEqual(article.title, 'New Article')

    def test_admin_edit_article(self):
        article = Article.objects.create(
            title='Edit Me', slug='edit-me', date=date(2025, 6, 15),
            published=True,
        )
        response = self.client.get(f'/admin/content/article/{article.pk}/change/')
        self.assertEqual(response.status_code, 200)

    def test_admin_delete_article(self):
        article = Article.objects.create(
            title='Delete Me', slug='delete-me', date=date(2025, 6, 15),
            published=True,
        )
        response = self.client.post(
            f'/admin/content/article/{article.pk}/delete/',
            {'post': 'yes'},
        )
        self.assertEqual(Article.objects.filter(slug='delete-me').count(), 0)

    def test_admin_publish_action(self):
        article = Article.objects.create(
            title='Draft', slug='draft-action', date=date(2025, 6, 15),
            published=False,
        )
        response = self.client.post('/admin/content/article/', {
            'action': 'publish_articles',
            '_selected_action': [article.pk],
        })
        article.refresh_from_db()
        self.assertTrue(article.published)
        self.assertEqual(article.status, 'published')
        self.assertIsNotNone(article.published_at)

    def test_admin_unpublish_action(self):
        article = Article.objects.create(
            title='Published', slug='published-action', date=date(2025, 6, 15),
            published=True,
        )
        response = self.client.post('/admin/content/article/', {
            'action': 'unpublish_articles',
            '_selected_action': [article.pk],
        })
        article.refresh_from_db()
        self.assertFalse(article.published)
        self.assertEqual(article.status, 'draft')

    def test_admin_slug_auto_generated(self):
        """Verify prepopulated_fields config for slug from title."""
        from content.admin.article import ArticleAdmin
        self.assertEqual(ArticleAdmin.prepopulated_fields, {'slug': ('title',)})

    def test_admin_list_filter_by_status(self):
        Article.objects.create(
            title='Draft Article', slug='filter-draft',
            date=date(2025, 6, 15), published=False,
        )
        Article.objects.create(
            title='Published Article', slug='filter-pub',
            date=date(2025, 6, 15), published=True,
        )
        response = self.client.get('/admin/content/article/?status__exact=published')
        self.assertEqual(response.status_code, 200)

    def test_admin_search(self):
        Article.objects.create(
            title='Searchable Article', slug='searchable',
            description='find me', date=date(2025, 6, 15),
            published=True,
        )
        response = self.client.get('/admin/content/article/?q=Searchable')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Searchable Article')


# --- Sorting tests ---


class BlogListSortingTest(TestCase):
    """Test that articles are sorted by date descending."""

    def setUp(self):
        self.client = Client()
        self.old_article = Article.objects.create(
            title='Old Article',
            slug='old-article',
            date=date(2025, 1, 1),
            published=True,
        )
        self.new_article = Article.objects.create(
            title='New Article',
            slug='new-article',
            date=date(2025, 6, 15),
            published=True,
        )

    def test_newer_article_appears_first(self):
        response = self.client.get('/blog')
        content = response.content.decode()
        new_pos = content.index('New Article')
        old_pos = content.index('Old Article')
        self.assertLess(new_pos, old_pos)


# --- Draft article visibility tests ---


class DraftArticleVisibilityTest(TestCase):
    """Test that draft articles are not visible on public pages."""

    def setUp(self):
        self.client = Client()
        self.draft = Article.objects.create(
            title='Draft Article',
            slug='draft-visibility',
            date=date(2025, 6, 15),
            published=False,
        )

    def test_draft_not_in_listing(self):
        response = self.client.get('/blog')
        self.assertNotContains(response, 'Draft Article')

    def test_draft_returns_404_on_detail(self):
        response = self.client.get('/blog/draft-visibility')
        self.assertEqual(response.status_code, 404)
