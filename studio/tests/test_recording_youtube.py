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

from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, Client

from content.models import Recording

User = get_user_model()


class RecordingPublishYouTubeSuccessTest(TestCase):
    """Test successful YouTube upload enqueue via the studio endpoint."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='testpass')
        self.recording = Recording.objects.create(
            title='S3 Recording',
            slug='s3-recording',
            date=date.today(),
            s3_url='https://bucket.s3.eu-central-1.amazonaws.com/recordings/2026/s3-recording.mp4',
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
        self.recording = Recording.objects.create(
            title='No S3',
            slug='no-s3',
            date=date.today(),
            s3_url='',
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
        self.recording = Recording.objects.create(
            title='Has YT',
            slug='has-yt',
            date=date.today(),
            s3_url='https://bucket.s3.eu-central-1.amazonaws.com/recordings/2026/has-yt.mp4',
            youtube_url='https://www.youtube.com/watch?v=existing123',
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
        self.recording = Recording.objects.create(
            title='Method Test',
            slug='method-test-yt',
            date=date.today(),
            s3_url='https://bucket.s3.eu-central-1.amazonaws.com/recordings/2026/method-test.mp4',
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
        self.recording = Recording.objects.create(
            title='Access Test',
            slug='access-test-yt',
            date=date.today(),
            s3_url='https://bucket.s3.eu-central-1.amazonaws.com/recordings/2026/access-test.mp4',
        )

    def test_non_staff_user_returns_403(self):
        """A non-staff authenticated user gets 403 via staff_required decorator."""
        regular_user = User.objects.create_user(
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


class RecordingYouTubeTemplateTest(TestCase):
    """Test that the recording form template correctly shows YouTube upload section."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='testpass')

    def test_create_form_does_not_show_youtube_section(self):
        """The create form (new recording) should NOT have the YouTube section."""
        response = self.client.get('/studio/recordings/new')
        content = response.content.decode()
        self.assertNotIn('id="youtube-upload-section"', content)
        self.assertNotIn('Publish to YouTube', content)

    def test_edit_recording_with_s3_url_shows_publish_button(self):
        """Edit form for recording with s3_url but no youtube_url shows Publish button."""
        recording = Recording.objects.create(
            title='Ready for YT',
            slug='ready-for-yt',
            date=date.today(),
            s3_url='https://bucket.s3.eu-central-1.amazonaws.com/recordings/2026/ready.mp4',
        )
        response = self.client.get(f'/studio/recordings/{recording.pk}/edit')
        content = response.content.decode()
        self.assertIn('id="youtube-upload-section"', content)
        self.assertIn('Publish to YouTube', content)
        self.assertIn('id="publish-youtube-btn"', content)

    def test_edit_recording_with_youtube_url_shows_url(self):
        """Edit form for recording with youtube_url shows the URL, not the button."""
        recording = Recording.objects.create(
            title='Has YouTube',
            slug='has-youtube',
            date=date.today(),
            youtube_url='https://www.youtube.com/watch?v=abc123',
        )
        response = self.client.get(f'/studio/recordings/{recording.pk}/edit')
        content = response.content.decode()
        self.assertIn('id="youtube-upload-section"', content)
        self.assertIn('https://www.youtube.com/watch?v=abc123', content)
        self.assertNotIn('id="publish-youtube-btn"', content)

    def test_edit_recording_without_s3_or_youtube_shows_message(self):
        """Edit form for recording without s3_url or youtube_url shows info message."""
        recording = Recording.objects.create(
            title='No URLs',
            slug='no-urls',
            date=date.today(),
        )
        response = self.client.get(f'/studio/recordings/{recording.pk}/edit')
        content = response.content.decode()
        self.assertIn('id="youtube-upload-section"', content)
        self.assertIn('Upload the recording to S3 first', content)
        self.assertNotIn('id="publish-youtube-btn"', content)

    def test_template_has_status_span(self):
        """The template includes a status span for upload status messages."""
        recording = Recording.objects.create(
            title='Status Span',
            slug='status-span-yt',
            date=date.today(),
            s3_url='https://bucket.s3.eu-central-1.amazonaws.com/recordings/2026/test.mp4',
        )
        response = self.client.get(f'/studio/recordings/{recording.pk}/edit')
        content = response.content.decode()
        self.assertIn('id="youtube-status"', content)

    def test_template_js_disables_button_during_request(self):
        """The JS code disables the button and changes text to Uploading... during request."""
        recording = Recording.objects.create(
            title='Disable Test',
            slug='disable-test-yt',
            date=date.today(),
            s3_url='https://bucket.s3.eu-central-1.amazonaws.com/recordings/2026/test.mp4',
        )
        response = self.client.get(f'/studio/recordings/{recording.pk}/edit')
        content = response.content.decode()
        self.assertIn('btn.disabled = true', content)
        self.assertIn("btn.textContent = 'Uploading...'", content)

    def test_template_js_sends_csrf_token(self):
        """The JS code includes the CSRF token in the POST request."""
        recording = Recording.objects.create(
            title='CSRF Test',
            slug='csrf-test-yt',
            date=date.today(),
            s3_url='https://bucket.s3.eu-central-1.amazonaws.com/recordings/2026/test.mp4',
        )
        response = self.client.get(f'/studio/recordings/{recording.pk}/edit')
        content = response.content.decode()
        self.assertIn('X-CSRFToken', content)
        self.assertIn('csrfmiddlewaretoken', content)

    def test_template_js_handles_network_error(self):
        """The JS code has a .catch handler for network errors."""
        recording = Recording.objects.create(
            title='Network Test',
            slug='network-test-yt',
            date=date.today(),
            s3_url='https://bucket.s3.eu-central-1.amazonaws.com/recordings/2026/test.mp4',
        )
        response = self.client.get(f'/studio/recordings/{recording.pk}/edit')
        content = response.content.decode()
        self.assertIn('.catch(', content)
        self.assertIn('Network error', content)

    def test_template_js_shows_success_message(self):
        """The JS code shows a queued message on success."""
        recording = Recording.objects.create(
            title='Success Test',
            slug='success-test-yt',
            date=date.today(),
            s3_url='https://bucket.s3.eu-central-1.amazonaws.com/recordings/2026/test.mp4',
        )
        response = self.client.get(f'/studio/recordings/{recording.pk}/edit')
        content = response.content.decode()
        self.assertIn('text-green-500', content)

    def test_template_js_shows_error_message(self):
        """The JS code shows an error message with red text on failure."""
        recording = Recording.objects.create(
            title='Error Test',
            slug='error-test-yt',
            date=date.today(),
            s3_url='https://bucket.s3.eu-central-1.amazonaws.com/recordings/2026/test.mp4',
        )
        response = self.client.get(f'/studio/recordings/{recording.pk}/edit')
        content = response.content.decode()
        self.assertIn('text-red-500', content)

    def test_template_shows_s3_url_value(self):
        """The template shows the S3 URL value for reference."""
        recording = Recording.objects.create(
            title='S3 URL Show',
            slug='s3-url-show',
            date=date.today(),
            s3_url='https://bucket.s3.eu-central-1.amazonaws.com/recordings/2026/s3-show.mp4',
        )
        response = self.client.get(f'/studio/recordings/{recording.pk}/edit')
        content = response.content.decode()
        self.assertIn(
            'https://bucket.s3.eu-central-1.amazonaws.com/recordings/2026/s3-show.mp4',
            content,
        )
