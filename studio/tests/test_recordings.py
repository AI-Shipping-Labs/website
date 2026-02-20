"""Tests for studio recording CRUD views."""

from django.contrib.auth import get_user_model
from django.test import TestCase, Client
from django.utils import timezone

from content.models import Recording

User = get_user_model()


class StudioRecordingListTest(TestCase):
    """Test recording list view."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='testpass')

    def test_list_returns_200(self):
        response = self.client.get('/studio/recordings/')
        self.assertEqual(response.status_code, 200)

    def test_list_uses_correct_template(self):
        response = self.client.get('/studio/recordings/')
        self.assertTemplateUsed(response, 'studio/recordings/list.html')

    def test_list_shows_recordings(self):
        Recording.objects.create(
            title='Test Recording', slug='test-rec',
            date=timezone.now().date(),
        )
        response = self.client.get('/studio/recordings/')
        self.assertContains(response, 'Test Recording')

    def test_list_search(self):
        Recording.objects.create(
            title='Python Workshop', slug='python',
            date=timezone.now().date(),
        )
        Recording.objects.create(
            title='Java Workshop', slug='java',
            date=timezone.now().date(),
        )
        response = self.client.get('/studio/recordings/?q=Python')
        self.assertContains(response, 'Python Workshop')
        self.assertNotContains(response, 'Java Workshop')


class StudioRecordingCreateTest(TestCase):
    """Test recording creation."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='testpass')

    def test_create_form_returns_200(self):
        response = self.client.get('/studio/recordings/new')
        self.assertEqual(response.status_code, 200)

    def test_create_recording_post(self):
        response = self.client.post('/studio/recordings/new', {
            'title': 'New Recording',
            'slug': 'new-rec',
            'description': 'Test recording',
            'date': '2024-01-15',
            'youtube_url': 'https://youtube.com/test',
            'published': 'on',
            'required_level': '0',
        })
        self.assertEqual(response.status_code, 302)
        rec = Recording.objects.get(slug='new-rec')
        self.assertEqual(rec.title, 'New Recording')
        self.assertTrue(rec.published)

    def test_create_recording_unpublished(self):
        self.client.post('/studio/recordings/new', {
            'title': 'Draft Rec',
            'slug': 'draft-rec',
            'date': '2024-01-15',
            'required_level': '0',
        })
        rec = Recording.objects.get(slug='draft-rec')
        self.assertFalse(rec.published)


class StudioRecordingEditTest(TestCase):
    """Test recording editing."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='testpass')
        self.recording = Recording.objects.create(
            title='Edit Rec', slug='edit-rec',
            date=timezone.now().date(),
        )

    def test_edit_form_returns_200(self):
        response = self.client.get(f'/studio/recordings/{self.recording.pk}/edit')
        self.assertEqual(response.status_code, 200)

    def test_edit_recording_post(self):
        self.client.post(f'/studio/recordings/{self.recording.pk}/edit', {
            'title': 'Updated Rec',
            'slug': 'edit-rec',
            'date': '2024-06-01',
            'youtube_url': 'https://youtube.com/updated',
            'published': 'on',
            'required_level': '10',
        })
        self.recording.refresh_from_db()
        self.assertEqual(self.recording.title, 'Updated Rec')
        self.assertEqual(self.recording.youtube_url, 'https://youtube.com/updated')
        self.assertTrue(self.recording.published)

    def test_edit_nonexistent_recording_returns_404(self):
        response = self.client.get('/studio/recordings/99999/edit')
        self.assertEqual(response.status_code, 404)
