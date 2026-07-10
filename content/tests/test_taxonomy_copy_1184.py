"""Taxonomy contract coverage for issue #1184."""

from datetime import date, timedelta
from pathlib import Path

from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from content.access import LEVEL_MAIN, LEVEL_OPEN
from content.models import CuratedLink, SiteConfig, Workshop
from events.models import Event
from tests.fixtures import TierSetupMixin


def _past_event(slug, *, title=None, **overrides):
    start = timezone.now() - timedelta(days=7)
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


class TaxonomyDocs1184Test(SimpleTestCase):
    def test_product_doc_contains_public_taxonomy_contract(self):
        product = Path('_docs/product.md').read_text(encoding='utf-8')

        self.assertIn('## Product Taxonomy Contract', product)
        for term in [
            'Community',
            'Events',
            'Workshops',
            'Recordings',
            'Resources',
            'Activities',
        ]:
            self.assertIn(f'| {term} |', product)
        for route in [
            '/events',
            '/events/calendar',
            '/events?filter=past',
            '/workshops',
            '/resources',
            '/activities#access-by-tier',
            '/sprints',
        ]:
            self.assertIn(route, product)

    def test_evergreen_specs_do_not_describe_recordings_as_active_route(self):
        resources = Path('specs/06-content-resources.md').read_text(
            encoding='utf-8',
        )
        events = Path('specs/07-events.md').read_text(encoding='utf-8')
        readme = Path('specs/README.md').read_text(encoding='utf-8')

        self.assertIn(
            'does not use an active standalone `Recording` content table',
            resources,
        )
        self.assertNotIn('### `/recordings`', resources)
        self.assertNotIn('GET /api/recordings', resources)
        self.assertNotIn('recording_id: FK -> Recording', events)
        self.assertNotIn('Create a Recording record', events)
        self.assertNotIn('/recordings/{recording_slug}', events)
        self.assertIn('legacy past-event-recording discovery', readme)


class PublicTaxonomyCopy1184Test(TierSetupMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        SiteConfig.objects.create(
            key='tiers',
            data=[
                {
                    'name': 'Basic',
                    'stripe_key': 'basic',
                    'activities': [
                        {
                            'icon': 'book-open',
                            'title': 'Self-serve learning',
                            'description': 'Curated content and guided practice.',
                            'features': [],
                        },
                    ],
                },
                {
                    'name': 'Main',
                    'stripe_key': 'main',
                    'activities': [
                        {
                            'icon': 'users',
                            'title': 'Community accountability',
                            'description': 'Sprints, events, and Slack participation.',
                            'features': [],
                        },
                    ],
                },
                {
                    'name': 'Premium',
                    'stripe_key': 'premium',
                    'activities': [
                        {
                            'icon': 'star',
                            'title': 'Career feedback',
                            'description': 'Feedback and premium learning paths.',
                            'features': [],
                        },
                    ],
                },
            ],
        )
        cls.upcoming = Event.objects.create(
            title='Live Taxonomy Session',
            slug='live-taxonomy-session',
            start_datetime=timezone.now() + timedelta(days=7),
            end_datetime=timezone.now() + timedelta(days=7, hours=1),
            status='upcoming',
            published=True,
        )
        cls.standalone_recording = _past_event(
            'standalone-taxonomy-recording',
            title='Standalone Taxonomy Recording',
            recording_url='https://video.example.test/standalone',
            required_level=LEVEL_OPEN,
            tags=['taxonomy'],
        )
        cls.workshop_event = _past_event(
            'linked-taxonomy-workshop-event',
            title='Linked Taxonomy Workshop',
            kind='workshop',
            recording_url='https://video.example.test/workshop',
            tags=['workshop'],
        )
        cls.workshop = Workshop.objects.create(
            slug='linked-taxonomy-workshop',
            title='Linked Taxonomy Workshop',
            date=date(2026, 7, 1),
            status='published',
            description='Hands-on workshop artifact.',
            landing_required_level=LEVEL_OPEN,
            pages_required_level=LEVEL_OPEN,
            recording_required_level=LEVEL_MAIN,
            event=cls.workshop_event,
            core_tools=['Python'],
            tags=['workshop'],
        )
        cls.curated_link = CuratedLink.objects.create(
            item_id='taxonomy-curated-link',
            title='Taxonomy Link',
            description='A focused external reference.',
            url='https://example.com/taxonomy',
            category='articles',
            published=True,
        )

    def test_existing_public_routes_resolve_for_anonymous_visitors(self):
        for path in [
            '/events',
            '/events?filter=past',
            '/events/calendar',
            '/workshops',
            '/resources',
            '/activities',
            '/sprints',
            '/pricing',
        ]:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)

    def test_events_and_calendar_copy_describe_live_sessions(self):
        events_response = self.client.get('/events')
        calendar_response = self.client.get('/events/calendar')

        self.assertContains(events_response, 'Live community events')
        self.assertContains(events_response, 'scheduled live sessions')
        self.assertContains(events_response, 'Past event recordings')
        self.assertContains(
            events_response,
            'Scheduled live community sessions, registration, calendar view',
        )
        self.assertNotContains(events_response, 'Community Events &amp; Workshops')
        self.assertNotContains(events_response, 'Join live workshops')

        self.assertContains(calendar_response, 'Live community events calendar')
        self.assertContains(calendar_response, 'plan registration and attendance')
        self.assertContains(
            calendar_response,
            'Monthly calendar view of scheduled AI Shipping Labs live community events',
        )

    def test_past_recordings_filter_preserves_standalone_and_workshop_links(self):
        response = self.client.get('/events?filter=past')

        self.assertContains(response, 'Past event recordings')
        self.assertContains(response, 'Recordings from past events')
        self.assertContains(response, self.standalone_recording.title)
        self.assertContains(response, self.standalone_recording.get_absolute_url())
        self.assertContains(response, self.workshop.title)
        self.assertContains(response, self.workshop.get_absolute_url())
        self.assertContains(response, f'{self.workshop.get_absolute_url()}/video')
        self.assertContains(response, 'Main or above')

    def test_workshops_resources_and_activities_copy_match_taxonomy(self):
        workshops_response = self.client.get('/workshops')
        resources_response = self.client.get('/resources')
        activities_response = self.client.get('/activities')

        self.assertContains(workshops_response, 'Hands-on AI workshops')
        self.assertContains(
            workshops_response,
            'durable hands-on learning artifacts',
        )
        self.assertContains(workshops_response, 'writeup, recording, materials')

        self.assertContains(resources_response, 'Curated links for AI builders')
        self.assertContains(
            resources_response,
            'Curated links to workshops, courses, articles, tools, and references',
        )
        self.assertNotContains(resources_response, 'Workshops, Courses &amp; More')

        self.assertContains(activities_response, 'Membership benefits by tier')
        self.assertContains(
            activities_response,
            'Activities are membership benefits and participation modes',
        )
        for href in ['/pricing', '/sprints', '/events', '/workshops']:
            self.assertContains(activities_response, f'href="{href}"')
