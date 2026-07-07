"""Workshop video page wires the S3 serving endpoint into the player.

Issue #1134 (Phase A). When the linked event has a ``recording_s3_url``
and the viewer can access the recording, the workshop video page renders
the self-hosted ``<video>`` player fed by the access-controlled serving
endpoint (a stable ``.mp4`` URL), and NEVER emits the raw S3 URL (nor any
presigned ``amazonaws.com`` URL) into the page HTML. Legacy YouTube events
(no ``recording_s3_url``) keep rendering the existing YouTube embed.
"""

from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from content.access import LEVEL_BASIC, LEVEL_OPEN
from content.models import Workshop
from events.models import Event
from tests.fixtures import TierSetupMixin

User = get_user_model()

RAW_S3_URL = (
    'https://recordings-bucket.s3.eu-central-1.amazonaws.com/'
    'recordings/2026/s3-workshop-event.mp4'
)


class WorkshopRecordingS3PlayerTest(TierSetupMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.basic_user = User.objects.create_user(
            email='ws-basic@test.com', password='pw', tier=cls.basic_tier,
            email_verified=True,
        )
        cls.free_user = User.objects.create_user(
            email='ws-free@test.com', password='pw', tier=cls.free_tier,
            email_verified=True,
        )

        # S3-backed workshop: event has recording_s3_url, recording gated
        # to Basic.
        cls.s3_event = Event.objects.create(
            title='S3 Workshop Event',
            slug='s3-workshop-event',
            start_datetime=timezone.now(),
            status='completed',
            kind='workshop',
            required_level=LEVEL_BASIC,
            recording_s3_url=RAW_S3_URL,
            published=True,
        )
        cls.s3_workshop = Workshop.objects.create(
            slug='s3-workshop',
            title='S3 Workshop',
            status='published',
            date=date(2026, 4, 21),
            landing_required_level=LEVEL_OPEN,
            pages_required_level=LEVEL_OPEN,
            recording_required_level=LEVEL_BASIC,
            event=cls.s3_event,
        )

        # Legacy YouTube workshop: no recording_s3_url, open recording.
        cls.yt_event = Event.objects.create(
            title='YT Workshop Event',
            slug='yt-workshop-event',
            start_datetime=timezone.now(),
            status='completed',
            kind='workshop',
            required_level=LEVEL_OPEN,
            recording_url='https://www.youtube.com/watch?v=dQw4w9WgXcQ',
            published=True,
        )
        cls.yt_workshop = Workshop.objects.create(
            slug='yt-workshop',
            title='YT Workshop',
            status='published',
            date=date(2026, 4, 21),
            landing_required_level=LEVEL_OPEN,
            pages_required_level=LEVEL_OPEN,
            recording_required_level=LEVEL_OPEN,
            event=cls.yt_event,
        )

    def _video_url(self, workshop):
        return reverse('workshop_video', kwargs={'slug': workshop.slug})

    def test_entitled_member_sees_serving_endpoint_video_source(self):
        self.client.force_login(self.basic_user)
        response = self.client.get(self._video_url(self.s3_workshop))

        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        expected_path = (
            f'/events/{self.s3_event.pk}/s3-workshop-event/recording.mp4'
        )
        # Self-hosted <video> with a <source> pointing at OUR serving
        # endpoint (an absolute URL on our own host, ending in .mp4).
        self.assertIn('<video', html)
        self.assertIn('<source src="', html)
        self.assertIn(f'{expected_path}"', html)
        self.assertIn('type="video/mp4"', html)

    def test_raw_s3_url_never_appears_in_page_html(self):
        self.client.force_login(self.basic_user)
        response = self.client.get(self._video_url(self.s3_workshop))

        html = response.content.decode()
        # Neither the raw S3 object URL nor any presigned S3 URL may leak.
        self.assertNotIn(RAW_S3_URL, html)
        self.assertNotIn('amazonaws.com', html)
        self.assertNotIn('X-Amz-Signature', html)

    def test_under_tier_member_sees_paywall_not_player(self):
        self.client.force_login(self.free_user)
        response = self.client.get(self._video_url(self.s3_workshop))

        # The workshop video page returns 403 for an under-tier viewer
        # (the recording gate trips). The player source and any S3 URL
        # must never appear in that gated response.
        self.assertEqual(response.status_code, 403)
        html = response.content.decode()
        serving_src = (
            f'/events/{self.s3_event.pk}/s3-workshop-event/recording.mp4'
        )
        self.assertNotIn(serving_src, html)
        self.assertNotIn('amazonaws.com', html)

    def test_legacy_youtube_event_still_renders_youtube_embed(self):
        response = self.client.get(self._video_url(self.yt_workshop))

        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        # YouTube fallback branch is preserved when recording_s3_url is
        # empty. The player uses the YouTube IFrame API, so the video id is
        # carried on the container's data attributes rather than an embed
        # URL in the HTML. The S3 serving endpoint is not wired in.
        self.assertIn('data-source="youtube"', html)
        self.assertIn('data-video-id="dQw4w9WgXcQ"', html)
        self.assertNotIn('/recording.mp4', html)
