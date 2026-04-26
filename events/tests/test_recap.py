"""Tests for event recap landing pages — issue #191.

Covers:
- Event.recap JSONField and has_recap / get_recap_url helpers
- /events/<slug>/recap view: 200 vs 404 cases
- Section gating (each of the 8 sections renders only when present)
- Tier -> Stripe payment-link resolution via STRIPE_PAYMENT_LINKS
- Existing event detail page surfaces the "View event recap" link
- _sync_events reads the recap key from frontmatter and validates it
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from events.models import Event
from tests.fixtures import TierSetupMixin

User = get_user_model()


STRIPE_LINKS_FOR_TESTS = {
    'basic': {
        'monthly': 'https://buy.stripe.com/basic-monthly',
        'annual': 'https://buy.stripe.com/basic-annual',
    },
    'main': {
        'monthly': 'https://buy.stripe.com/main-monthly',
        'annual': 'https://buy.stripe.com/main-annual',
    },
    'premium': {
        'monthly': 'https://buy.stripe.com/premium-monthly',
        'annual': 'https://buy.stripe.com/premium-annual',
    },
}


def _full_recap():
    """Return a full recap dict matching the schema (all 8 sections)."""
    return {
        'hero': {
            'eyebrow': 'Event Recap',
            'title': 'AI Shipping Labs Launch Stream Recap',
            'subtitle': 'If you missed the launch stream, this page gives you the key ideas.',
            'duration': '90-minute live session',
            'format': 'Community-focused format',
            'primary_cta': {'label': 'Join AI Shipping Labs', 'href': '#plans'},
            'secondary_cta': {
                'label': 'Read the full summary',
                'href': 'https://docs.google.com/document/d/123',
                'external': True,
            },
            'jump_to': [
                {'label': 'Recording', 'href': '#watch-stream'},
                {'label': 'Plans', 'href': '#plans'},
            ],
        },
        'watch_stream': {
            'embed_url': 'https://www.youtube.com/embed/WQAs1LNxdvM',
            'title': 'Watch the launch stream',
        },
        'key_topics': {
            'section_eyebrow': 'Launch stream summary',
            'section_title': 'What You Need to Know',
            'items': [
                {'title': 'The core problem', 'summary': 'Builders need execution.'},
                {'title': 'The learning model', 'summary': 'Learn by building.'},
                {'title': 'What members do', 'summary': 'Weekly rhythm of activities.'},
            ],
        },
        'activities': {
            'section_title': 'Main Community Activities',
            'section_intro': 'This is the working format inside the community.',
            'items': [
                {
                    'title': '1. Accountability circles',
                    'hook': 'Build in sprints with shared momentum.',
                    'details': ['Pick a project.', 'Join check-ins.'],
                },
                {
                    'title': '2. Group learning',
                    'hook': 'Turn solo research into shared leverage.',
                    'details': ['Research a tool.', 'Share findings.'],
                },
                {
                    'title': '3. Building sessions',
                    'hook': 'Live working sessions, not webinars.',
                    'details': ['90 to 120 minute sessions.'],
                },
                {
                    'title': '4. Trend breakdowns',
                    'hook': 'Understand trends without chasing hype.',
                    'details': ['Engineering lens on a trend.'],
                },
                {
                    'title': '5. Career support',
                    'hook': 'Practical support for real career moves.',
                    'details': ['Discuss interviews and offers.'],
                },
            ],
        },
        'early_member': {
            'section_eyebrow': 'Early member value',
            'section_title': 'Why Joining Early Matters',
            'intro': 'While the community is still small.',
            'plan_title': 'Personalized plan + sprint execution',
            'plan_description': 'You get a tailored plan.',
            'plan_steps': [
                'Answer a short set of questions.',
                'Optional live chat.',
                'Alexey reviews and prepares plan.',
                'Apply it in sprints.',
            ],
            'focus_areas_title': 'This plan can focus on:',
            'focus_areas': [
                'Build a clearer learning path.',
                'Start or improve a real project.',
                'Prepare for a new role.',
                'Grow in current role.',
                'Get unstuck.',
            ],
            'closing': 'This level of attention is available because the community is small.',
            'primary_cta': {'label': 'Join Main Tier', 'tier': 'main'},
            'secondary_cta': {
                'label': 'Ask about your plan',
                'href': 'mailto:team@aishippinglabs.com',
            },
        },
        'upcoming_events': {
            'section_eyebrow': 'Upcoming events',
            'section_title': 'Next Live Sessions',
            'items': [
                {
                    'title': 'Deploy Your AI Agent Project',
                    'date': 'Apr 21, Tuesday',
                    'description': 'Hands-on session.',
                    'href': 'https://luma.com/j1zzd47e',
                },
                {
                    'title': 'Build Your LinkedIn',
                    'date': 'Apr 28, Tuesday',
                    'description': '30-Day Posting Challenge.',
                    'href': 'https://luma.com/3jd8wugp',
                },
            ],
        },
        'plans': {
            'section_eyebrow': 'Membership',
            'section_title': 'Pick the Right Level of Support',
            'section_intro': 'Start with the level that matches.',
            'items': [
                {
                    'tier': 'basic',
                    'label': 'Content only',
                    'description': 'Written summaries.',
                    'best_for': 'Best if you want material.',
                    'highlight': False,
                },
                {
                    'tier': 'main',
                    'label': 'Most popular',
                    'description': 'Full community access.',
                    'best_for': 'Best if you want structure.',
                    'highlight': True,
                },
                {
                    'tier': 'premium',
                    'label': 'Deepest support',
                    'description': 'Community plus structured courses.',
                    'best_for': 'Best for deeper learning.',
                    'highlight': False,
                    'extras': [
                        'Potential course directions include Python for AI Engineering.',
                    ],
                },
            ],
        },
        'final_cta': {
            'title': 'Ready to Join?',
            'description': 'Choose a plan and start building.',
            'buttons': [
                {'label': 'View plans', 'href': '#plans'},
                {'label': 'Contact team', 'href': 'mailto:team@aishippinglabs.com'},
            ],
        },
    }


# -------------------- Model tests --------------------


class EventRecapModelTest(TestCase):
    """Test the recap field, has_recap property, and get_recap_url helper."""

    def test_recap_default_is_empty_dict(self):
        event = Event.objects.create(
            title='Default Recap', slug='default-recap',
            start_datetime=timezone.now(),
        )
        self.assertEqual(event.recap, {})
        self.assertFalse(event.has_recap)

    def test_has_recap_true_when_recap_populated(self):
        event = Event.objects.create(
            title='With Recap', slug='with-recap',
            start_datetime=timezone.now(),
            recap={'hero': {'title': 'Hello'}},
        )
        self.assertTrue(event.has_recap)

    def test_get_recap_url(self):
        event = Event(slug='my-event')
        self.assertEqual(event.get_recap_url(), '/events/my-event/recap')


# -------------------- View tests --------------------


@override_settings(STRIPE_PAYMENT_LINKS=STRIPE_LINKS_FOR_TESTS)
class EventRecapViewTest(TierSetupMixin, TestCase):
    """Test /events/<slug>/recap view behavior and rendering."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.event_with_recap = Event.objects.create(
            title='Launch',
            slug='launch',
            start_datetime=timezone.now() - timedelta(days=2),
            status='completed',
            recap=_full_recap(),
        )
        cls.event_no_recap = Event.objects.create(
            title='No Recap',
            slug='no-recap',
            start_datetime=timezone.now(),
            status='upcoming',
        )
        cls.draft_event = Event.objects.create(
            title='Draft Recap',
            slug='draft-recap',
            start_datetime=timezone.now(),
            status='draft',
            recap=_full_recap(),
        )

    def setUp(self):
        self.client = Client()

    def test_recap_page_returns_200_when_recap_exists(self):
        response = self.client.get('/events/launch/recap')
        self.assertEqual(response.status_code, 200)

    def test_recap_page_returns_404_when_event_missing(self):
        response = self.client.get('/events/does-not-exist/recap')
        self.assertEqual(response.status_code, 404)

    def test_recap_page_returns_404_when_no_recap_data(self):
        response = self.client.get('/events/no-recap/recap')
        self.assertEqual(response.status_code, 404)

    def test_recap_page_returns_404_for_draft_to_anonymous(self):
        response = self.client.get('/events/draft-recap/recap')
        self.assertEqual(response.status_code, 404)

    def test_recap_page_renders_for_staff_when_draft(self):
        staff = User.objects.create_user(
            email='staff@x.com', password='pw', is_staff=True,
        )
        self.client.force_login(staff)
        response = self.client.get('/events/draft-recap/recap')
        self.assertEqual(response.status_code, 200)

    def test_recap_page_uses_correct_template(self):
        response = self.client.get('/events/launch/recap')
        self.assertTemplateUsed(response, 'events/event_recap.html')

    def test_recap_page_renders_hero_title_and_subtitle(self):
        response = self.client.get('/events/launch/recap')
        content = response.content.decode()
        self.assertIn('AI Shipping Labs Launch Stream Recap', content)
        self.assertIn('If you missed the launch stream', content)

    def test_recap_page_renders_watch_stream_iframe(self):
        response = self.client.get('/events/launch/recap')
        content = response.content.decode()
        self.assertIn('id="watch-stream"', content)
        self.assertIn('youtube.com/embed/WQAs1LNxdvM', content)

    def test_recap_page_renders_all_three_key_topics(self):
        response = self.client.get('/events/launch/recap')
        content = response.content.decode()
        self.assertIn('The core problem', content)
        self.assertIn('The learning model', content)
        self.assertIn('What members do', content)

    def test_recap_page_renders_all_five_activities(self):
        response = self.client.get('/events/launch/recap')
        content = response.content.decode()
        self.assertIn('1. Accountability circles', content)
        self.assertIn('2. Group learning', content)
        self.assertIn('3. Building sessions', content)
        self.assertIn('4. Trend breakdowns', content)
        self.assertIn('5. Career support', content)

    def test_recap_page_renders_early_member_section(self):
        response = self.client.get('/events/launch/recap')
        content = response.content.decode()
        self.assertIn('Why Joining Early Matters', content)
        self.assertIn('id="early-member-plan"', content)
        # All five focus areas
        self.assertIn('Build a clearer learning path.', content)
        self.assertIn('Start or improve a real project.', content)
        self.assertIn('Prepare for a new role.', content)
        self.assertIn('Grow in current role.', content)
        self.assertIn('Get unstuck.', content)
        # Mailto secondary CTA
        self.assertIn('mailto:team@aishippinglabs.com', content)

    def test_recap_page_renders_upcoming_events(self):
        response = self.client.get('/events/launch/recap')
        content = response.content.decode()
        self.assertIn('id="upcoming-events"', content)
        self.assertIn('Deploy Your AI Agent Project', content)
        self.assertIn('https://luma.com/j1zzd47e', content)

    def test_recap_page_renders_plans_with_stripe_links(self):
        response = self.client.get('/events/launch/recap')
        content = response.content.decode()
        self.assertIn('id="plans"', content)
        # Each tier resolves to its annual Stripe link
        self.assertIn('https://buy.stripe.com/basic-annual', content)
        self.assertIn('https://buy.stripe.com/main-annual', content)
        self.assertIn('https://buy.stripe.com/premium-annual', content)
        # Premium extras render
        self.assertIn('Python for AI Engineering', content)
        # data-tier attributes for each plan card
        self.assertIn('data-tier="basic"', content)
        self.assertIn('data-tier="main"', content)
        self.assertIn('data-tier="premium"', content)

    def test_recap_page_renders_final_cta(self):
        response = self.client.get('/events/launch/recap')
        content = response.content.decode()
        self.assertIn('Ready to Join?', content)
        self.assertIn('View plans', content)

    def test_recap_page_seo_title_includes_event_title(self):
        response = self.client.get('/events/launch/recap')
        content = response.content.decode()
        self.assertIn('<title>Launch Recap | AI Shipping Labs</title>', content)

    def test_recap_page_meta_description_uses_subtitle(self):
        response = self.client.get('/events/launch/recap')
        content = response.content.decode()
        self.assertIn(
            'name="description" content="If you missed the launch stream',
            content,
        )

    def test_recap_page_canonical_url(self):
        response = self.client.get('/events/launch/recap')
        content = response.content.decode()
        self.assertIn('rel="canonical"', content)
        self.assertIn('/events/launch/recap', content)

    def test_in_page_anchor_ids_present(self):
        """The hero jump-to nav references anchors that must exist on the page."""
        response = self.client.get('/events/launch/recap')
        content = response.content.decode()
        for anchor_id in ('watch-stream', 'plans', 'upcoming-events',
                          'activities', 'early-member-plan'):
            self.assertIn(f'id="{anchor_id}"', content,
                          f'missing anchor id={anchor_id!r}')


# -------------------- Section-gating tests --------------------


@override_settings(STRIPE_PAYMENT_LINKS=STRIPE_LINKS_FOR_TESTS)
class EventRecapSectionGatingTest(TierSetupMixin, TestCase):
    """Sections are hidden when their data is absent."""

    def test_only_hero_and_plans_omits_other_sections(self):
        recap = {
            'hero': {'title': 'Just hero', 'subtitle': 'Sub'},
            'plans': {
                'items': [
                    {'tier': 'main', 'label': 'Main', 'description': 'desc',
                     'best_for': 'all', 'highlight': True},
                ],
            },
        }
        Event.objects.create(
            title='Partial', slug='partial',
            start_datetime=timezone.now(), status='completed', recap=recap,
        )
        response = self.client.get('/events/partial/recap')
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        # Hero and plans render
        self.assertIn('Just hero', content)
        self.assertIn('id="plans"', content)
        # Omitted sections do not render any heading
        self.assertNotIn('id="watch-stream"', content)
        self.assertNotIn('What You Need to Know', content)
        self.assertNotIn('Main Community Activities', content)
        self.assertNotIn('Why Joining Early Matters', content)
        self.assertNotIn('id="upcoming-events"', content)
        self.assertNotIn('Ready to Join?', content)

    def test_empty_recap_dict_returns_404(self):
        Event.objects.create(
            title='Empty', slug='empty-recap',
            start_datetime=timezone.now(), status='completed', recap={},
        )
        response = self.client.get('/events/empty-recap/recap')
        self.assertEqual(response.status_code, 404)

    def test_watch_stream_without_embed_url_is_skipped(self):
        recap = {
            'hero': {'title': 'X', 'subtitle': 'Y'},
            'watch_stream': {'title': 'Watch'},  # no embed_url
        }
        Event.objects.create(
            title='No URL', slug='no-embed-url',
            start_datetime=timezone.now(), status='completed', recap=recap,
        )
        response = self.client.get('/events/no-embed-url/recap')
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        # Watch-stream section not rendered without embed_url
        self.assertNotIn('id="watch-stream"', content)


# -------------------- Event detail page integration --------------------


class EventDetailRecapLinkTest(TestCase):
    """Existing event detail page should surface a link to the recap."""

    def test_event_detail_shows_recap_link_when_recap_present(self):
        Event.objects.create(
            title='Has Recap', slug='has-recap',
            start_datetime=timezone.now(),
            status='upcoming',
            recap={'hero': {'title': 'Hi'}},
        )
        response = self.client.get('/events/has-recap')
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn('View event recap', content)
        self.assertIn('/events/has-recap/recap', content)

    def test_event_detail_hides_recap_link_when_no_recap(self):
        Event.objects.create(
            title='No Recap', slug='no-recap-event',
            start_datetime=timezone.now(),
            status='upcoming',
        )
        response = self.client.get('/events/no-recap-event')
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertNotIn('View event recap', content)
        self.assertNotIn('/events/no-recap-event/recap', content)


# -------------------- Tier link helper --------------------


@override_settings(STRIPE_PAYMENT_LINKS=STRIPE_LINKS_FOR_TESTS)
class TierPaymentLinkResolutionTest(TierSetupMixin, TestCase):
    """The view's tier-link helper must resolve via STRIPE_PAYMENT_LINKS."""

    def test_resolve_known_tier_returns_annual_link(self):
        from events.views.pages import _resolve_tier_payment_link
        self.assertEqual(
            _resolve_tier_payment_link('main'),
            'https://buy.stripe.com/main-annual',
        )

    def test_resolve_unknown_tier_returns_placeholder(self):
        from events.views.pages import _resolve_tier_payment_link
        self.assertEqual(_resolve_tier_payment_link('unknown'), '#')

    def test_resolve_empty_tier_returns_placeholder(self):
        from events.views.pages import _resolve_tier_payment_link
        self.assertEqual(_resolve_tier_payment_link(''), '#')


# -------------------- Sync integration --------------------


class SyncEventsRecapTest(TestCase):
    """_sync_events should read the recap key from frontmatter."""

    def _write_event_yaml(self, tmp_path, recap_block=None, slug='launch-test'):
        import os
        events_dir = os.path.join(tmp_path, 'events')
        os.makedirs(events_dir, exist_ok=True)
        recap_yaml = ''
        if recap_block is not None:
            import yaml
            recap_yaml = yaml.safe_dump({'recap': recap_block}, sort_keys=False)
        contents = (
            'content_id: aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee\n'
            f'title: "Test Event"\n'
            f'slug: {slug}\n'
            'event_type: live\n'
            'status: upcoming\n'
            'start_datetime: "2026-04-13T16:30:00Z"\n'
            f'{recap_yaml}'
        )
        path = os.path.join(events_dir, f'{slug}.yaml')
        with open(path, 'w') as f:
            f.write(contents)
        return events_dir

    def _make_source(self):
        from integrations.models import ContentSource
        return ContentSource.objects.create(
            repo_name='test-content',
        )

    def test_sync_persists_recap_dict(self):
        import tempfile

        from integrations.services.github import sync_content_source
        recap = {'hero': {'title': 'My Event'}}
        with tempfile.TemporaryDirectory() as tmp:
            self._write_event_yaml(tmp, recap_block=recap)
            source = self._make_source()
            sync_log = sync_content_source(source, repo_dir=tmp)
        self.assertEqual(sync_log.errors, [])
        event = Event.objects.get(slug='launch-test')
        self.assertEqual(event.recap, recap)
        self.assertTrue(event.has_recap)

    def test_sync_with_no_recap_leaves_field_empty(self):
        import tempfile

        from integrations.services.github import sync_content_source
        with tempfile.TemporaryDirectory() as tmp:
            self._write_event_yaml(tmp, recap_block=None)
            source = self._make_source()
            sync_content_source(source, repo_dir=tmp)
        event = Event.objects.get(slug='launch-test')
        self.assertEqual(event.recap, {})
        self.assertFalse(event.has_recap)

    def test_sync_with_invalid_recap_logs_error_and_skips(self):
        """If recap is not a dict, the sync logs an error but the event still syncs."""
        import os
        import tempfile

        from integrations.services.github import sync_content_source

        with tempfile.TemporaryDirectory() as tmp:
            events_dir = os.path.join(tmp, 'events')
            os.makedirs(events_dir)
            # recap as a list, not a dict
            with open(os.path.join(events_dir, 'bad-recap.yaml'), 'w') as f:
                f.write(
                    'content_id: aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee\n'
                    'title: "Bad"\n'
                    'slug: bad-recap\n'
                    'start_datetime: "2026-04-13T16:30:00Z"\n'
                    'recap:\n'
                    '  - item1\n'
                    '  - item2\n'
                )
            source = self._make_source()
            sync_log = sync_content_source(source, repo_dir=tmp)
        self.assertEqual(len(sync_log.errors), 1)
        self.assertIn('Invalid recap', sync_log.errors[0]['error'])
        # Event still synced, recap empty
        event = Event.objects.get(slug='bad-recap')
        self.assertEqual(event.recap, {})
