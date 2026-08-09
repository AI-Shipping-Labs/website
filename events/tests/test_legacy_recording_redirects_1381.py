"""Legacy event-recording compatibility redirects (issue #1381)."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from content.models import Workshop
from events.models import Event
from payments.models import Tier


def _event(slug, **overrides):
    start = timezone.now() - timedelta(days=7)
    defaults = {
        'title': slug.replace('-', ' ').title(),
        'slug': slug,
        'start_datetime': start,
        'end_datetime': start + timedelta(hours=1),
        'status': 'completed',
        'published': True,
        'recording_url': f'https://video.example.test/{slug}',
    }
    defaults.update(overrides)
    return Event.objects.create(**defaults)


def _workshop(event, *, slug='replacement-workshop', status='published', **overrides):
    defaults = {
        'slug': slug,
        'title': event.title,
        'date': event.start_datetime.date(),
        'status': status,
        'landing_required_level': 0,
        'pages_required_level': 0,
        'recording_required_level': 0,
        'event': event,
    }
    defaults.update(overrides)
    return Workshop.objects.create(**defaults)


class LegacyRecordingListRedirect1381Test(TestCase):
    def test_redirects_permanently_directly_to_past_events(self):
        response = self.client.get('/event-recordings')

        self.assertEqual(response.status_code, 301)
        self.assertEqual(response['Location'], '/events?filter=past')

    def test_forces_one_filter_and_preserves_repeated_and_blank_parameters(self):
        response = self.client.get(
            '/event-recordings?filter=upcoming&tag=agents&tag=python'
            '&page=2&page=3&utm_source=bookmark&blank=',
        )

        self.assertEqual(response.status_code, 301)
        self.assertEqual(
            response['Location'],
            '/events?filter=past&tag=agents&tag=python&page=2&page=3'
            '&utm_source=bookmark&blank=',
        )
        self.assertEqual(response['Location'].count('filter='), 1)


class LegacyRecordingDetailRedirect1381Test(TestCase):
    def test_all_recording_sources_redirect_standalone_events_directly(self):
        recording_fields = {
            'recording_url': 'https://video.example.test/watch',
            'recording_s3_url': 'https://private.example.test/recording.mp4',
            'recording_embed_url': 'https://embed.example.test/player',
        }
        for index, (field, value) in enumerate(recording_fields.items()):
            with self.subTest(field=field):
                recording_overrides = {
                    'recording_url': '',
                    'recording_s3_url': '',
                    'recording_embed_url': '',
                }
                recording_overrides[field] = value
                event = _event(
                    f'{field.replace("_", "-")}-{index}',
                    **recording_overrides,
                )

                response = self.client.get(f'/event-recordings/{event.slug}')

                self.assertEqual(response.status_code, 301)
                self.assertEqual(response['Location'], event.get_absolute_url())
                self.assertNotIn('/events/', value)

    def test_published_linked_workshop_redirects_directly_to_video(self):
        event = _event('historic-event-slug')
        workshop = _workshop(event, slug='current-workshop-slug')

        response = self.client.get(f'/event-recordings/{event.slug}')

        self.assertEqual(response.status_code, 301)
        self.assertEqual(
            response['Location'],
            f'{workshop.get_absolute_url()}/video',
        )

    def test_detail_redirect_preserves_query_string(self):
        event = _event('bookmarked-recording')

        response = self.client.get(
            f'/event-recordings/{event.slug}?utm_source=search&tag=a&tag=b&t=90',
        )

        self.assertEqual(response.status_code, 301)
        self.assertEqual(
            response['Location'],
            f'{event.get_absolute_url()}?utm_source=search&tag=a&tag=b&t=90',
        )

    def test_unknown_malformed_and_nested_paths_are_genuine_404s(self):
        _event('known-recording')

        for path in (
            '/event-recordings/unknown-recording',
            '/event-recordings/not.a.slug',
            '/event-recordings/known-recording/extra',
        ):
            with self.subTest(path=path):
                response = self.client.get(path)

                self.assertEqual(response.status_code, 404)
                self.assertNotIn('Location', response)

    def test_ineligible_event_boundaries_return_404(self):
        now = timezone.now()
        ineligible_events = [
            _event('no-recording', recording_url=''),
            _event('draft-recording', status='draft'),
            _event('cancelled-recording', status='cancelled'),
            _event('unpublished-recording', published=False),
            _event(
                'future-recording',
                start_datetime=now + timedelta(days=1),
                end_datetime=now + timedelta(days=1, hours=1),
                status='upcoming',
            ),
            _event(
                'not-yet-ended-recording',
                start_datetime=now - timedelta(minutes=15),
                end_datetime=now + timedelta(minutes=45),
            ),
        ]

        for event in ineligible_events:
            with self.subTest(slug=event.slug):
                response = self.client.get(f'/event-recordings/{event.slug}')

                self.assertEqual(response.status_code, 404)
                self.assertNotIn('Location', response)

    def test_linked_draft_workshop_returns_404_without_event_fallback(self):
        event = _event('draft-workshop-recording')
        _workshop(event, status='draft')

        response = self.client.get(f'/event-recordings/{event.slug}')

        self.assertEqual(response.status_code, 404)
        self.assertNotIn('Location', response)


class LegacyRecordingRedirectAccessSafety1381Test(TestCase):
    @classmethod
    def setUpTestData(cls):
        basic = Tier.objects.get(level=10)
        cls.user = get_user_model().objects.create_user(
            email='basic-legacy-1381@example.com',
            password='test-password',
            tier=basic,
            email_verified=True,
        )

    def setUp(self):
        self.client.force_login(self.user)

    def test_workshop_destination_keeps_paywall_and_raw_urls_private(self):
        recording_sources = {
            'recording_url': 'https://video.example.test/private-watch',
            'recording_s3_url': (
                'https://private.example.test/workshop-secret.mp4'
                '?X-Amz-Signature=secret'
            ),
            'recording_embed_url': 'https://embed.example.test/private-player',
        }
        for index, (field, raw_url) in enumerate(recording_sources.items()):
            with self.subTest(field=field):
                event_fields = {
                    'recording_url': '',
                    'recording_s3_url': '',
                    'recording_embed_url': '',
                }
                event_fields[field] = raw_url
                event = _event(
                    f'gated-workshop-recording-{index}',
                    **event_fields,
                )
                workshop = _workshop(
                    event,
                    slug=f'gated-workshop-destination-{index}',
                    recording_required_level=20,
                )

                redirect_response = self.client.get(
                    f'/event-recordings/{event.slug}',
                )
                destination_response = self.client.get(
                    redirect_response['Location'],
                )

                self.assertEqual(redirect_response.status_code, 301)
                self.assertEqual(
                    redirect_response['Location'],
                    f'{workshop.get_absolute_url()}/video',
                )
                self.assertEqual(destination_response.status_code, 403)
                self.assertContains(
                    destination_response,
                    'data-testid="video-paywall"',
                    status_code=403,
                )
                self.assertNotContains(
                    destination_response,
                    raw_url,
                    status_code=403,
                )

    def test_standalone_destination_keeps_protected_resources_private(self):
        raw_url = 'https://private.example.test/standalone-secret.mp4'
        event = _event(
            'gated-standalone-recording',
            required_level=20,
            recording_url='',
            recording_s3_url=raw_url,
        )

        redirect_response = self.client.get(
            f'/event-recordings/{event.slug}',
        )
        destination_response = self.client.get(redirect_response['Location'])

        self.assertEqual(redirect_response.status_code, 301)
        self.assertEqual(redirect_response['Location'], event.get_absolute_url())
        self.assertEqual(destination_response.status_code, 200)
        self.assertNotContains(destination_response, raw_url)
        self.assertFalse(
            destination_response.context['post_event_resources']['has_resources'],
        )
