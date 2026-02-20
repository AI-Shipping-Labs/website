"""Tests for studio article CRUD views.

Verifies:
- Article list with search and status filter
- Article create form (GET and POST)
- Article edit form (GET and POST)
- Status management (publish/unpublish)
"""

from django.contrib.auth import get_user_model
from django.test import TestCase, Client
from django.utils import timezone

from content.models import Article

User = get_user_model()


class StudioArticleListTest(TestCase):
    """Test article list view."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='testpass')

    def test_list_returns_200(self):
        response = self.client.get('/studio/articles/')
        self.assertEqual(response.status_code, 200)

    def test_list_uses_correct_template(self):
        response = self.client.get('/studio/articles/')
        self.assertTemplateUsed(response, 'studio/articles/list.html')

    def test_list_shows_articles(self):
        Article.objects.create(
            title='Test Article', slug='test-article',
            date=timezone.now().date(),
        )
        response = self.client.get('/studio/articles/')
        self.assertContains(response, 'Test Article')

    def test_list_filter_published(self):
        Article.objects.create(
            title='PublishedArticleXYZ', slug='pub', date=timezone.now().date(), published=True,
        )
        Article.objects.create(
            title='DraftArticleXYZ', slug='draft', date=timezone.now().date(), published=False,
        )
        response = self.client.get('/studio/articles/?status=published')
        self.assertContains(response, 'PublishedArticleXYZ')
        self.assertNotContains(response, 'DraftArticleXYZ')

    def test_list_filter_draft(self):
        Article.objects.create(
            title='Pub', slug='pub', date=timezone.now().date(), published=True,
        )
        Article.objects.create(
            title='Draft', slug='draft', date=timezone.now().date(), published=False,
        )
        response = self.client.get('/studio/articles/?status=draft')
        self.assertContains(response, 'Draft')

    def test_list_search(self):
        Article.objects.create(
            title='Python Guide', slug='python', date=timezone.now().date(),
        )
        Article.objects.create(
            title='Java Guide', slug='java', date=timezone.now().date(),
        )
        response = self.client.get('/studio/articles/?q=Python')
        self.assertContains(response, 'Python Guide')
        self.assertNotContains(response, 'Java Guide')


class StudioArticleCreateTest(TestCase):
    """Test article creation."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='testpass')

    def test_create_form_returns_200(self):
        response = self.client.get('/studio/articles/new')
        self.assertEqual(response.status_code, 200)

    def test_create_article_post(self):
        response = self.client.post('/studio/articles/new', {
            'title': 'New Article',
            'slug': 'new-article',
            'description': 'A test article',
            'content_markdown': '# Hello World',
            'author': 'Test Author',
            'date': '2024-01-15',
            'status': 'draft',
            'required_level': '0',
        })
        self.assertEqual(response.status_code, 302)
        article = Article.objects.get(slug='new-article')
        self.assertEqual(article.title, 'New Article')
        self.assertFalse(article.published)

    def test_create_published_article(self):
        self.client.post('/studio/articles/new', {
            'title': 'Published Art',
            'slug': 'pub-art',
            'date': '2024-01-15',
            'status': 'published',
            'required_level': '0',
        })
        article = Article.objects.get(slug='pub-art')
        self.assertTrue(article.published)

    def test_create_article_with_tags(self):
        self.client.post('/studio/articles/new', {
            'title': 'Tagged Art',
            'slug': 'tagged-art',
            'date': '2024-01-15',
            'status': 'draft',
            'required_level': '0',
            'tags': 'python, ml',
        })
        article = Article.objects.get(slug='tagged-art')
        self.assertEqual(len(article.tags), 2)

    def test_create_article_auto_slug(self):
        self.client.post('/studio/articles/new', {
            'title': 'Auto Slug Test',
            'date': '2024-01-15',
            'status': 'draft',
            'required_level': '0',
        })
        self.assertTrue(Article.objects.filter(slug='auto-slug-test').exists())


class StudioArticleEditTest(TestCase):
    """Test article editing."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='testpass')
        self.article = Article.objects.create(
            title='Edit Me', slug='edit-me',
            date=timezone.now().date(), published=False,
        )

    def test_edit_form_returns_200(self):
        response = self.client.get(f'/studio/articles/{self.article.pk}/edit')
        self.assertEqual(response.status_code, 200)

    def test_edit_shows_article_data(self):
        response = self.client.get(f'/studio/articles/{self.article.pk}/edit')
        self.assertContains(response, 'Edit Me')

    def test_edit_article_post(self):
        self.client.post(f'/studio/articles/{self.article.pk}/edit', {
            'title': 'Updated Article',
            'slug': 'edit-me',
            'date': '2024-06-01',
            'author': 'New Author',
            'status': 'draft',
            'required_level': '0',
        })
        self.article.refresh_from_db()
        self.assertEqual(self.article.title, 'Updated Article')
        self.assertEqual(self.article.author, 'New Author')

    def test_edit_publish_article(self):
        self.assertFalse(self.article.published)
        self.client.post(f'/studio/articles/{self.article.pk}/edit', {
            'title': 'Edit Me',
            'slug': 'edit-me',
            'date': '2024-06-01',
            'status': 'published',
            'required_level': '0',
        })
        self.article.refresh_from_db()
        self.assertTrue(self.article.published)
        self.assertIsNotNone(self.article.published_at)

    def test_edit_unpublish_article(self):
        self.article.published = True
        self.article.save()
        self.client.post(f'/studio/articles/{self.article.pk}/edit', {
            'title': 'Edit Me',
            'slug': 'edit-me',
            'date': '2024-06-01',
            'status': 'draft',
            'required_level': '0',
        })
        self.article.refresh_from_db()
        self.assertFalse(self.article.published)

    def test_edit_nonexistent_article_returns_404(self):
        response = self.client.get('/studio/articles/99999/edit')
        self.assertEqual(response.status_code, 404)
