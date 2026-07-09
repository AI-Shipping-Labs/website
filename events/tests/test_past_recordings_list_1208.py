from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from content.access import LEVEL_MAIN, LEVEL_PREMIUM
from content.models import Workshop
from events.models import Event
from events.services.time_windows import past_recording_events_queryset
from events.views.pages import PUBLIC_EVENTS_PER_PAGE


def _past_event(slug, *, title=None, days_ago=7, **overrides):
    start = timezone.now() - timedelta(days=days_ago)
    defaults = {
        'title': title or slug.replace('-', ' ').title(),
        'slug': slug,
        'start_datetime': start,
        'end_datetime': start + timedelta(hours=1),
        'status': 'completed',
        'published': True,
    }
    defaults.update(overrides)
    return Event.objects.create(**defaults)


def _future_event(slug, *, title=None, **overrides):
    start = timezone.now() + timedelta(days=7)
    defaults = {
        'title': title or slug.replace('-', ' ').title(),
        'slug': slug,
        'start_datetime': start,
        'end_datetime': start + timedelta(hours=1),
        'status': 'upcoming',
        'published': True,
    }
    defaults.update(overrides)
    return Event.objects.create(**defaults)


def _linked_workshop(event, *, slug=None, recording_required_level=0):
    slug = slug or event.slug
    return Workshop.objects.create(
        slug=slug,
        title=event.title,
        date=event.start_datetime.date(),
        status='published',
        landing_required_level=0,
        pages_required_level=0,
        recording_required_level=recording_required_level,
        event=event,
    )


def _card_html(response, title):
    body = response.content.decode()
    start = body.index(title)
    article_start = body.rfind('<article', 0, start)
    article_end = body.index('</article>', start)
    return body[article_start:article_end]


class PastRecordingEventsQueryset1208Test(TestCase):
    def test_matches_event_recording_fields_and_public_past_rules(self):
        url_recording = _past_event(
            'url-recording',
            recording_url='https://video.example.test/url',
        )
        s3_recording = _past_event(
            's3-recording',
            recording_s3_url='https://storage.example.test/s3.mp4',
        )
        embed_recording = _past_event(
            'embed-recording',
            recording_embed_url='https://drive.example.test/embed',
        )
        _past_event('no-recording')
        _past_event(
            'unpublished-recording',
            published=False,
            recording_url='https://video.example.test/unpublished',
        )
        _past_event(
            'draft-recording',
            status='draft',
            recording_url='https://video.example.test/draft',
        )
        _past_event(
            'cancelled-recording',
            status='cancelled',
            recording_url='https://video.example.test/cancelled',
        )
        _future_event(
            'future-recording',
            recording_url='https://video.example.test/future',
        )

        self.assertCountEqual(
            past_recording_events_queryset(),
            [url_recording, s3_recording, embed_recording],
        )


class EventsPastRecordingsList1208Test(TestCase):
    def test_linked_workshop_s3_recording_uses_video_cta_and_safe_list_html(self):
        raw_s3_url = (
            'https://private-recordings.s3.amazonaws.com/events/secret.mp4'
            '?X-Amz-Signature=abc123'
        )
        event = _past_event(
            's3-linked-workshop',
            title='S3 Linked Workshop',
            kind='workshop',
            recording_s3_url=raw_s3_url,
            tags=['agents'],
        )
        workshop = _linked_workshop(event, slug='s3-linked-workshop-video')

        response = self.client.get('/events?filter=past')
        body = response.content.decode()
        card = _card_html(response, 'S3 Linked Workshop')

        self.assertContains(response, 'S3 Linked Workshop')
        self.assertIn('data-testid="past-card-workshop-badge"', card)
        self.assertIn(f'href="{workshop.get_absolute_url()}"', card)
        self.assertIn(
            f'href="{workshop.get_absolute_url()}/video"',
            card,
        )
        self.assertIn('Watch recording', card)
        self.assertNotIn(event.get_absolute_url(), card)
        self.assertNotIn('/event-recordings/', card)
        self.assertNotIn(raw_s3_url, body)
        self.assertNotIn('amazonaws.com', body)
        self.assertNotIn('X-Amz-Signature', body)

    def test_standalone_recording_cta_uses_event_detail_and_hides_raw_url(self):
        event = _past_event(
            'standalone-recording',
            title='Standalone Recording',
            recording_url='https://youtube.com/watch?v=standalone1208',
            tags=['python'],
        )

        response = self.client.get('/events?filter=past')
        body = response.content.decode()
        card = _card_html(response, 'Standalone Recording')

        self.assertContains(response, 'Standalone Recording')
        self.assertIn(f'href="{event.get_absolute_url()}"', card)
        self.assertIn('data-testid="past-card-recording-cta"', card)
        self.assertIn('Watch recording', card)
        self.assertNotIn('https://youtube.com/watch?v=standalone1208', body)
        self.assertNotIn('/event-recordings/', body)

    def test_linked_workshop_tier_cue_uses_workshop_recording_level(self):
        event = _past_event(
            'gated-linked-workshop',
            title='Gated Linked Workshop',
            kind='workshop',
            required_level=LEVEL_PREMIUM,
            recording_url='https://youtube.com/watch?v=gated1208',
        )
        _linked_workshop(
            event,
            recording_required_level=LEVEL_MAIN,
        )

        response = self.client.get('/events?filter=past')
        card = _card_html(response, 'Gated Linked Workshop')

        self.assertIn('data-testid="past-card-recording-tier"', card)
        self.assertIn('Main or above', card)
        self.assertNotIn('Premium', card)

    def test_past_events_without_recordings_are_excluded_without_fake_ctas(self):
        _past_event('no-recording-event', title='No Recording Event')
        _past_event(
            'recorded-event',
            title='Recorded Event',
            recording_embed_url='https://drive.example.test/embed',
        )

        response = self.client.get('/events?filter=past')

        self.assertContains(response, 'Recorded Event')
        self.assertNotContains(response, 'No Recording Event')
        self.assertContains(response, 'Watch recording', count=1)

    def test_tag_filtering_preserves_s3_only_linked_workshops(self):
        agents_event = _past_event(
            'agents-s3-workshop',
            title='Agents S3 Workshop',
            kind='workshop',
            recording_s3_url='https://storage.example.test/agents.mp4',
            tags=['agents'],
        )
        _linked_workshop(agents_event)
        python_event = _past_event(
            'python-recording',
            title='Python Recording',
            recording_url='https://video.example.test/python',
            tags=['python'],
        )

        response = self.client.get('/events?filter=past&tag=agents')
        past_ids = {event.id for event in response.context['past_events']}

        self.assertEqual(past_ids, {agents_event.id})
        self.assertContains(response, 'Agents S3 Workshop')
        self.assertNotContains(response, 'Python Recording')
        self.assertIn('agents', response.context['all_past_tags'])
        self.assertIn('python', response.context['all_past_tags'])
        self.assertNotIn(python_event.id, past_ids)

    def test_pagination_preserves_expanded_recording_availability(self):
        for index in range(PUBLIC_EVENTS_PER_PAGE + 1):
            field = {
                'recording_url': f'https://video.example.test/{index}',
            }
            if index == 0:
                field = {
                    'recording_s3_url': 'https://storage.example.test/first.mp4',
                }
            elif index == 1:
                field = {
                    'recording_embed_url': 'https://drive.example.test/embed',
                }
            _past_event(
                f'paginated-recording-{index}',
                title=f'Paginated Recording {index}',
                days_ago=index + 1,
                **field,
            )

        response = self.client.get('/events?filter=past')
        page_obj = response.context['page_obj']

        self.assertTrue(response.context['is_paginated'])
        self.assertEqual(page_obj.paginator.count, PUBLIC_EVENTS_PER_PAGE + 1)
        self.assertEqual(len(page_obj.object_list), PUBLIC_EVENTS_PER_PAGE)
        self.assertContains(response, 'Paginated Recording 0')
        self.assertContains(response, 'Paginated Recording 1')

        response = self.client.get('/events?filter=past&page=2')
        self.assertEqual(len(response.context['page_obj'].object_list), 1)
