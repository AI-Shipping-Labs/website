"""Tests for Studio "Publish to YouTube" button - issue #111.

Covers:
- Successful YouTube publish enqueue
- Recording without S3 URL returns 400
- Recording already has YouTube URL returns 400
- Non-existent recording returns 404
- GET request returns 405 Method Not Allowed
- Non-staff user gets 403
- Anonymous user redirected to login
- Template: YouTube section visible on edit form
- Template: "Publish to YouTube" button shown when s3_url set and no youtube_url
- Template: YouTube URL displayed when youtube_url set
- Template: No S3 message shown when neither URL set
- Template: Button not on create form
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.utils import timezone

from events.models import Event

User = get_user_model()


class RecordingPublishYouTubeSuccessTest(TestCase):
    """Test successful YouTube upload enqueue via the studio endpoint."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='testpass')
        self.recording = Event.objects.create(
            title='S3 Recording',
            slug='s3-recording',
            start_datetime=timezone.now(), status='completed',
            recording_s3_url='https://bucket.s3.eu-central-1.amazonaws.com/recordings/2026/s3-recording.mp4',
        )

    @patch('studio.views.recordings.async_task')
    def test_publish_youtube_enqueues_task(self, mock_async_task):
        """POST to publish-youtube enqueues background task and returns 200."""
        mock_async_task.return_value = 'task-id-yt-001'
        response = self.client.post(
            f'/studio/recordings/{self.recording.pk}/publish-youtube',
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['status'], 'queued')
        self.assertEqual(data['task_id'], 'task-id-yt-001')

    @patch('studio.views.recordings.async_task')
    def test_publish_youtube_calls_correct_task(self, mock_async_task):
        """async_task is called with the correct function path and recording ID."""
        mock_async_task.return_value = 'task-id-yt-002'
        self.client.post(
            f'/studio/recordings/{self.recording.pk}/publish-youtube',
        )
        mock_async_task.assert_called_once_with(
            'jobs.tasks.youtube_upload.upload_recording_to_youtube',
            self.recording.id,
            max_retries=3,
        )

    @patch('studio.views.recordings.async_task')
    def test_publish_youtube_enqueue_error_returns_500(self, mock_async_task):
        """When async_task raises an exception, the endpoint returns 500."""
        mock_async_task.side_effect = Exception('Queue connection failed')
        response = self.client.post(
            f'/studio/recordings/{self.recording.pk}/publish-youtube',
        )
        self.assertEqual(response.status_code, 500)
        data = response.json()
        self.assertIn('error', data)
        self.assertIn('Queue connection failed', data['error'])


class RecordingPublishYouTubeNoS3Test(TestCase):
    """Test that publishing to YouTube without S3 URL returns 400."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='testpass')
        self.recording = Event.objects.create(
            title='No S3',
            slug='no-s3',
            start_datetime=timezone.now(), status='completed',
            recording_s3_url='',
        )

    def test_returns_400(self):
        response = self.client.post(
            f'/studio/recordings/{self.recording.pk}/publish-youtube',
        )
        self.assertEqual(response.status_code, 400)

    def test_error_message(self):
        response = self.client.post(
            f'/studio/recordings/{self.recording.pk}/publish-youtube',
        )
        data = response.json()
        self.assertIn('error', data)
        self.assertIn('S3', data['error'])


class RecordingPublishYouTubeAlreadyHasURLTest(TestCase):
    """Test that publishing to YouTube when URL already set returns 400."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='testpass')
        self.recording = Event.objects.create(
            title='Has YT',
            slug='has-yt',
            start_datetime=timezone.now(), status='completed',
            recording_s3_url='https://bucket.s3.eu-central-1.amazonaws.com/recordings/2026/has-yt.mp4',
            recording_url='https://www.youtube.com/watch?v=existing123',
        )

    def test_returns_400(self):
        response = self.client.post(
            f'/studio/recordings/{self.recording.pk}/publish-youtube',
        )
        self.assertEqual(response.status_code, 400)

    def test_error_message(self):
        response = self.client.post(
            f'/studio/recordings/{self.recording.pk}/publish-youtube',
        )
        data = response.json()
        self.assertIn('error', data)
        self.assertIn('already has', data['error'].lower())


class RecordingPublishYouTube404Test(TestCase):
    """Test that publishing to YouTube for a non-existent recording returns 404."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='testpass')

    def test_nonexistent_recording_returns_404(self):
        response = self.client.post('/studio/recordings/99999/publish-youtube')
        self.assertEqual(response.status_code, 404)


class RecordingPublishYouTube405Test(TestCase):
    """Test that GET requests to the publish-youtube endpoint return 405."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='testpass')
        self.recording = Event.objects.create(
            title='Method Test',
            slug='method-test-yt',
            start_datetime=timezone.now(), status='completed',
            recording_s3_url='https://bucket.s3.eu-central-1.amazonaws.com/recordings/2026/method-test.mp4',
        )

    def test_get_returns_405(self):
        response = self.client.get(
            f'/studio/recordings/{self.recording.pk}/publish-youtube',
        )
        self.assertEqual(response.status_code, 405)


class RecordingPublishYouTubeAccessControlTest(TestCase):
    """Test access control for the publish-youtube endpoint."""

    def setUp(self):
        self.client = Client()
        self.recording = Event.objects.create(
            title='Access Test',
            slug='access-test-yt',
            start_datetime=timezone.now(), status='completed',
            recording_s3_url='https://bucket.s3.eu-central-1.amazonaws.com/recordings/2026/access-test.mp4',
        )

    def test_non_staff_user_returns_403(self):
        """A non-staff authenticated user gets 403 via staff_required decorator."""
        User.objects.create_user(
            email='regular@test.com', password='testpass', is_staff=False,
        )
        self.client.login(email='regular@test.com', password='testpass')
        response = self.client.post(
            f'/studio/recordings/{self.recording.pk}/publish-youtube',
        )
        self.assertEqual(response.status_code, 403)

    def test_anonymous_user_redirected_to_login(self):
        """An anonymous user is redirected to the login page."""
        response = self.client.post(
            f'/studio/recordings/{self.recording.pk}/publish-youtube',
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)


# `RecordingYouTubeTemplateTest` removed under `_docs/testing-guidelines.md`
# Rule 4 (template string-matching: `id="youtube-upload-section"`,
# `id="publish-youtube-btn"`, `id="youtube-status"` etc).
# Endpoint behavior (POST enqueues task, error returns 400/500, auth gates)
# is covered by the other classes above. The "create URL is removed" check
# (`/studio/recordings/new` returns 404) is covered indirectly by URL routing
# tests in studio access tests.
