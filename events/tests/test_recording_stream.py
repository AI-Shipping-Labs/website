"""Access control + redirect behavior for the recording serving endpoint.

Issue #1134 (Phase A). ``event_recording_stream`` serves an event's
private-S3 recording through an access-controlled Django view that
302-redirects to a short-lived presigned S3 ``GetObject`` URL. A bug here
leaks paid content, so the deny paths (anonymous, under-tier, missing
asset) are tested strongly: each asserts the presigned URL is NOT emitted
and that the presigned-URL machinery was never even reached.
"""

from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from analytics.models import UserActivity
from content.access import LEVEL_BASIC, LEVEL_OPEN
from events.models import Event
from integrations.config import clear_config_cache
from tests.fixtures import TierSetupMixin

User = get_user_model()

# A realistic presigned URL shape: what boto3 returns for a
# generate_presigned_url('get_object', ...) call. Both marker query params
# the acceptance criteria require are present.
FAKE_PRESIGNED_URL = (
    'https://recordings-bucket.s3.eu-central-1.amazonaws.com/'
    'recordings/2026/basic-rec-event.mp4'
    '?X-Amz-Algorithm=AWS4-HMAC-SHA256'
    '&X-Amz-Expires=900'
    '&X-Amz-Signature=deadbeefcafef00d'
)

RECORDINGS_S3_CLIENT_PATH = 'jobs.tasks.recordings_s3.get_recordings_s3_client'


@override_settings(
    AWS_S3_RECORDINGS_BUCKET='recordings-bucket',
    AWS_S3_RECORDINGS_REGION='eu-central-1',
    AWS_ACCESS_KEY_ID='key',
    AWS_SECRET_ACCESS_KEY='secret',
)
class EventRecordingStreamTest(TierSetupMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.basic_user = User.objects.create_user(
            email='basic-rec@test.com', password='pw', tier=cls.basic_tier,
            email_verified=True,
        )
        cls.free_user = User.objects.create_user(
            email='free-rec@test.com', password='pw', tier=cls.free_tier,
            email_verified=True,
        )
        cls.gated_event = Event.objects.create(
            title='Basic Rec Event',
            slug='basic-rec-event',
            start_datetime=timezone.now(),
            status='completed',
            kind='workshop',
            required_level=LEVEL_BASIC,
            recording_s3_url=(
                'https://recordings-bucket.s3.eu-central-1.amazonaws.com/'
                'recordings/2026/basic-rec-event.mp4'
            ),
            published=True,
        )
        cls.open_event = Event.objects.create(
            title='Open Rec Event',
            slug='open-rec-event',
            start_datetime=timezone.now(),
            status='completed',
            kind='workshop',
            required_level=LEVEL_OPEN,
            recording_s3_url=(
                'https://recordings-bucket.s3.eu-central-1.amazonaws.com/'
                'recordings/2026/open-rec-event.mp4'
            ),
            published=True,
        )
        cls.no_asset_event = Event.objects.create(
            title='No Asset Event',
            slug='no-asset-event',
            start_datetime=timezone.now(),
            status='completed',
            kind='workshop',
            required_level=LEVEL_BASIC,
            recording_s3_url='',
            published=True,
        )

    def setUp(self):
        clear_config_cache()
        UserActivity.objects.all().delete()

    def tearDown(self):
        clear_config_cache()

    def _url(self, event):
        return reverse(
            'event_recording_stream',
            kwargs={'event_id': event.pk, 'slug': event.slug},
        )

    def _mock_client(self):
        """Return a MagicMock S3 client whose presign returns a canned URL."""
        client = MagicMock()
        client.generate_presigned_url.return_value = FAKE_PRESIGNED_URL
        return client

    # --- Authorized path -------------------------------------------------

    def test_entitled_member_gets_302_to_presigned_amazonaws_url(self):
        client = self._mock_client()
        self.client.force_login(self.basic_user)
        with patch(RECORDINGS_S3_CLIENT_PATH, return_value=client):
            response = self.client.get(self._url(self.gated_event))

        self.assertEqual(response.status_code, 302)
        location = response['Location']
        self.assertIn('amazonaws.com', location)
        self.assertIn('X-Amz-Signature', location)
        self.assertIn('X-Amz-Expires', location)

    def test_presigned_generated_for_key_derived_from_recording_s3_url(self):
        client = self._mock_client()
        self.client.force_login(self.basic_user)
        with patch(RECORDINGS_S3_CLIENT_PATH, return_value=client):
            self.client.get(self._url(self.gated_event))

        client.generate_presigned_url.assert_called_once()
        args, kwargs = client.generate_presigned_url.call_args
        self.assertEqual(args[0], 'get_object')
        self.assertEqual(
            kwargs['Params']['Key'],
            'recordings/2026/basic-rec-event.mp4',
        )

    @override_settings(RECORDING_PRESIGNED_URL_TTL_SECONDS='1800')
    def test_presigned_ttl_respects_registered_setting(self):
        client = self._mock_client()
        self.client.force_login(self.basic_user)
        with patch(RECORDINGS_S3_CLIENT_PATH, return_value=client):
            self.client.get(self._url(self.gated_event))

        _, kwargs = client.generate_presigned_url.call_args
        self.assertEqual(kwargs['ExpiresIn'], 1800)

    def test_default_ttl_is_900_seconds(self):
        client = self._mock_client()
        self.client.force_login(self.basic_user)
        with patch(RECORDINGS_S3_CLIENT_PATH, return_value=client):
            self.client.get(self._url(self.gated_event))

        _, kwargs = client.generate_presigned_url.call_args
        self.assertEqual(kwargs['ExpiresIn'], 900)

    def test_open_recording_plays_for_anonymous(self):
        client = self._mock_client()
        with patch(RECORDINGS_S3_CLIENT_PATH, return_value=client):
            response = self.client.get(self._url(self.open_event))

        self.assertEqual(response.status_code, 302)
        self.assertIn('amazonaws.com', response['Location'])

    # --- Deny paths (must never emit a presigned URL) --------------------

    def test_under_tier_member_gets_403_and_no_presigned_url(self):
        client = self._mock_client()
        self.client.force_login(self.free_user)
        with patch(RECORDINGS_S3_CLIENT_PATH, return_value=client):
            response = self.client.get(self._url(self.gated_event))

        self.assertEqual(response.status_code, 403)
        body = response.content.decode()
        self.assertNotIn('amazonaws.com', body)
        self.assertNotIn('X-Amz-Signature', body)
        # The presigned machinery must not even be reached on a denial.
        client.generate_presigned_url.assert_not_called()

    def test_anonymous_denied_gated_recording_redirects_to_login(self):
        client = self._mock_client()
        with patch(RECORDINGS_S3_CLIENT_PATH, return_value=client):
            response = self.client.get(self._url(self.gated_event))

        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login', response['Location'])
        # Redirect target is login, not a presigned S3 URL.
        self.assertNotIn('amazonaws.com', response['Location'])
        client.generate_presigned_url.assert_not_called()

    def test_missing_recording_s3_url_returns_404(self):
        client = self._mock_client()
        self.client.force_login(self.basic_user)
        with patch(RECORDINGS_S3_CLIENT_PATH, return_value=client):
            response = self.client.get(self._url(self.no_asset_event))

        self.assertEqual(response.status_code, 404)
        client.generate_presigned_url.assert_not_called()

    def test_draft_event_404s_for_non_staff(self):
        draft = Event.objects.create(
            title='Draft Rec',
            slug='draft-rec-event',
            start_datetime=timezone.now(),
            status='draft',
            required_level=LEVEL_OPEN,
            recording_s3_url=(
                'https://recordings-bucket.s3.eu-central-1.amazonaws.com/'
                'recordings/2026/draft-rec-event.mp4'
            ),
        )
        client = self._mock_client()
        self.client.force_login(self.basic_user)
        with patch(RECORDINGS_S3_CLIENT_PATH, return_value=client):
            response = self.client.get(self._url(draft))

        self.assertEqual(response.status_code, 404)
        client.generate_presigned_url.assert_not_called()

    def test_slug_mismatch_redirects_to_canonical(self):
        client = self._mock_client()
        self.client.force_login(self.basic_user)
        wrong = reverse(
            'event_recording_stream',
            kwargs={'event_id': self.gated_event.pk, 'slug': 'wrong-slug'},
        )
        with patch(RECORDINGS_S3_CLIENT_PATH, return_value=client):
            response = self.client.get(wrong)

        self.assertEqual(response.status_code, 301)
        self.assertEqual(
            response['Location'],
            f'/events/{self.gated_event.pk}/basic-rec-event/recording.mp4',
        )

    # --- Activity recording ---------------------------------------------

    def test_authorized_viewer_records_resource_view(self):
        client = self._mock_client()
        self.client.force_login(self.basic_user)
        with patch(RECORDINGS_S3_CLIENT_PATH, return_value=client):
            self.client.get(self._url(self.gated_event))

        views = UserActivity.objects.filter(
            user=self.basic_user,
            event_type=UserActivity.EVENT_RESOURCE_VIEW,
            object_type='recording',
            object_id=f'event:{self.gated_event.pk}',
        )
        self.assertEqual(views.count(), 1)

    def test_denied_viewer_records_no_resource_view(self):
        client = self._mock_client()
        self.client.force_login(self.free_user)
        with patch(RECORDINGS_S3_CLIENT_PATH, return_value=client):
            self.client.get(self._url(self.gated_event))

        self.assertFalse(
            UserActivity.objects.filter(
                user=self.free_user,
                event_type=UserActivity.EVENT_RESOURCE_VIEW,
                object_type='recording',
            ).exists()
        )
