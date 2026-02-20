"""Tests for studio download management views."""

from django.contrib.auth import get_user_model
from django.test import TestCase, Client

from content.models import Download

User = get_user_model()


class StudioDownloadListTest(TestCase):
    """Test download list view."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='testpass')

    def test_list_returns_200(self):
        response = self.client.get('/studio/downloads/')
        self.assertEqual(response.status_code, 200)

    def test_list_uses_correct_template(self):
        response = self.client.get('/studio/downloads/')
        self.assertTemplateUsed(response, 'studio/downloads/list.html')

    def test_list_shows_downloads(self):
        Download.objects.create(
            title='Test Download', slug='test-dl',
            file_url='https://example.com/file.pdf',
        )
        response = self.client.get('/studio/downloads/')
        self.assertContains(response, 'Test Download')

    def test_list_search(self):
        Download.objects.create(
            title='Python Guide', slug='python',
            file_url='https://example.com/python.pdf',
        )
        Download.objects.create(
            title='Java Guide', slug='java',
            file_url='https://example.com/java.pdf',
        )
        response = self.client.get('/studio/downloads/?q=Python')
        self.assertContains(response, 'Python Guide')
        self.assertNotContains(response, 'Java Guide')


class StudioDownloadCreateTest(TestCase):
    """Test download creation."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='testpass')

    def test_create_form_returns_200(self):
        response = self.client.get('/studio/downloads/new')
        self.assertEqual(response.status_code, 200)

    def test_create_download_post(self):
        response = self.client.post('/studio/downloads/new', {
            'title': 'New Download',
            'slug': 'new-dl',
            'description': 'A test download',
            'file_url': 'https://example.com/file.pdf',
            'file_type': 'pdf',
            'file_size_bytes': '1024',
            'published': 'on',
            'required_level': '0',
        })
        self.assertEqual(response.status_code, 302)
        dl = Download.objects.get(slug='new-dl')
        self.assertEqual(dl.title, 'New Download')
        self.assertEqual(dl.file_type, 'pdf')
        self.assertEqual(dl.file_size_bytes, 1024)
        self.assertTrue(dl.published)

    def test_create_download_auto_slug(self):
        self.client.post('/studio/downloads/new', {
            'title': 'Auto Slug Download',
            'file_url': 'https://example.com/file.pdf',
            'file_type': 'pdf',
            'required_level': '0',
        })
        self.assertTrue(Download.objects.filter(slug='auto-slug-download').exists())


class StudioDownloadEditTest(TestCase):
    """Test download editing."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='testpass')
        self.download = Download.objects.create(
            title='Edit DL', slug='edit-dl',
            file_url='https://example.com/file.pdf',
        )

    def test_edit_form_returns_200(self):
        response = self.client.get(f'/studio/downloads/{self.download.pk}/edit')
        self.assertEqual(response.status_code, 200)

    def test_edit_download_post(self):
        self.client.post(f'/studio/downloads/{self.download.pk}/edit', {
            'title': 'Updated DL',
            'slug': 'edit-dl',
            'file_url': 'https://example.com/updated.pdf',
            'file_type': 'zip',
            'file_size_bytes': '2048',
            'published': 'on',
            'required_level': '10',
        })
        self.download.refresh_from_db()
        self.assertEqual(self.download.title, 'Updated DL')
        self.assertEqual(self.download.file_type, 'zip')
        self.assertEqual(self.download.file_size_bytes, 2048)
        self.assertEqual(self.download.required_level, 10)

    def test_edit_nonexistent_returns_404(self):
        response = self.client.get('/studio/downloads/99999/edit')
        self.assertEqual(response.status_code, 404)
