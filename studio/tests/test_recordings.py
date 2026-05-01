"""Tests for studio recording CRUD views (now using Event model)."""

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.utils import timezone

from events.models import Event

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
        Event.objects.create(
            title='Test Recording', slug='test-rec',
            start_datetime=timezone.now(), status='completed',
            recording_url='https://youtube.com/watch?v=test',
        )
        response = self.client.get('/studio/recordings/')
        self.assertContains(response, 'Test Recording')

    def test_list_search(self):
        Event.objects.create(
            title='Python Workshop', slug='python',
            start_datetime=timezone.now(), status='completed',
            recording_url='https://youtube.com/watch?v=python',
        )
        Event.objects.create(
            title='Java Workshop', slug='java',
            start_datetime=timezone.now(), status='completed',
            recording_url='https://youtube.com/watch?v=java',
        )
        response = self.client.get('/studio/recordings/?q=Python')
        self.assertContains(response, 'Python Workshop')
        self.assertNotContains(response, 'Java Workshop')


class StudioRecordingCreateRemovedTest(TestCase):
    """Test that recording create URL has been removed."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='testpass')

    def test_create_url_returns_404(self):
        response = self.client.get('/studio/recordings/new')
        self.assertEqual(response.status_code, 404)


class StudioRecordingEditTest(TestCase):
    """Test recording editing."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='testpass')
        self.recording = Event.objects.create(
            title='Edit Rec', slug='edit-rec',
            start_datetime=timezone.now(), status='completed',
            recording_url='https://youtube.com/watch?v=edit',
        )

    def test_edit_form_returns_200(self):
        response = self.client.get(f'/studio/recordings/{self.recording.pk}/edit')
        self.assertEqual(response.status_code, 200)

    def test_edit_recording_post(self):
        self.client.post(f'/studio/recordings/{self.recording.pk}/edit', {
            'title': 'Updated Rec',
            'slug': 'edit-rec',
            'recording_url': 'https://youtube.com/updated',
            'published': 'on',
            'required_level': '10',
        })
        self.recording.refresh_from_db()
        self.assertEqual(self.recording.title, 'Updated Rec')
        self.assertEqual(self.recording.recording_url, 'https://youtube.com/updated')
        self.assertTrue(self.recording.published)

    def test_edit_nonexistent_recording_returns_404(self):
        response = self.client.get('/studio/recordings/99999/edit')
        self.assertEqual(response.status_code, 404)

    def test_synced_recording_shows_origin_panel(self):
        recording = Event.objects.create(
            title='Synced Rec',
            slug='synced-rec',
            start_datetime=timezone.now(),
            status='completed',
            recording_url='https://youtube.com/watch?v=synced',
            source_repo='AI-Shipping-Labs/content',
            source_path='events/synced-rec.md',
            source_commit='abc123def4567890',
        )

        response = self.client.get(f'/studio/recordings/{recording.pk}/edit')

        self.assertContains(response, 'data-testid="origin-panel"')
        self.assertContains(response, 'Synced from GitHub')
        self.assertContains(response, 'events/synced-rec.md')
        self.assertContains(response, 'Edit on GitHub')
        self.assertContains(response, 'Re-sync source')
        self.assertNotContains(response, 'data-testid="synced-banner"')

    def test_manual_recording_has_no_origin_panel(self):
        response = self.client.get(f'/studio/recordings/{self.recording.pk}/edit')

        self.assertNotContains(response, 'data-testid="origin-panel"')
        self.assertNotContains(response, 'data-testid="synced-banner"')
