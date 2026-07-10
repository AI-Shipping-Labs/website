"""Focused tests for the public /community launch landing page."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import resolve
from django.utils import timezone

from community.views import community_landing
from events.models import Event
from tests.fixtures import TierSetupMixin

User = get_user_model()


def create_launch_event(**overrides):
    now = timezone.now()
    defaults = {
        'title': 'AI Shipping Labs Community Launch',
        'slug': 'community-launch',
        'description': 'Original event detail description.',
        'start_datetime': now - timedelta(days=3),
        'end_datetime': now - timedelta(days=3) + timedelta(hours=1),
        'status': 'completed',
        'published': True,
        'recording_url': 'https://www.youtube.com/watch?v=launch',
        'recap_html': (
            '<section id="launch-story">'
            '<h2>What happened at the AI Shipping Labs Community Launch</h2>'
            '<p>Builders saw how the community helps them ship real AI projects.</p>'
            '<a href="/pricing">Start building with the community</a>'
            '</section>'
        ),
    }
    defaults.update(overrides)
    return Event.objects.create(**defaults)


class CommunityLandingRoutingTest(TestCase):
    def test_community_route_resolves_to_landing_view(self):
        match = resolve('/community')
        self.assertEqual(match.func, community_landing)


class CommunityLandingViewTest(TierSetupMixin, TestCase):
    def test_anonymous_visitor_gets_synced_launch_recap(self):
        create_launch_event()

        response = self.client.get('/community')

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'community/community_landing.html')
        self.assertContains(response, 'AI Shipping Labs Community Launch')
        self.assertContains(
            response,
            'What happened at the AI Shipping Labs Community Launch',
        )
        self.assertContains(
            response,
            'Builders saw how the community helps them ship real AI projects.',
        )

    def test_landing_page_keeps_global_chrome_but_omits_event_detail_chrome(self):
        create_launch_event()

        response = self.client.get('/community')

        self.assertContains(response, 'id="site-header"')
        self.assertContains(response, '<footer', html=False)
        self.assertContains(response, 'data-testid="community-landing-page"')
        for forbidden in [
            'Back to Events',
            'data-testid="event-registration-card"',
            'data-testid="event-feedback-section"',
            'data-testid="event-attendee-count"',
            'data-testid="event-add-to-calendar"',
            'data-testid="event-anonymous-email-form"',
            'data-testid="event-post-resources"',
        ]:
            self.assertNotContains(response, forbidden)

    def test_anonymous_visitor_sees_existing_conversion_paths(self):
        create_launch_event()

        response = self.client.get('/community')

        self.assertContains(response, 'href="/subscribe"')
        self.assertContains(response, 'href="/register"')
        self.assertContains(response, 'href="/pricing"')
        self.assertContains(response, 'data-testid="community-landing-subscribe-cta"')
        self.assertContains(response, 'data-testid="community-landing-register-cta"')
        self.assertContains(response, 'data-testid="community-landing-pricing-cta"')

    def test_all_member_tiers_can_open_landing_page(self):
        create_launch_event()
        cases = [
            ('free-member@example.com', self.free_tier),
            ('basic-member@example.com', self.basic_tier),
            ('main-member@example.com', self.main_tier),
            ('premium-member@example.com', self.premium_tier),
        ]

        for email, tier in cases:
            with self.subTest(tier=tier.slug):
                user = User.objects.create_user(email=email, password='pw')
                user.tier = tier
                user.save(update_fields=['tier'])
                self.client.force_login(user)

                response = self.client.get('/community')

                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'AI Shipping Labs Community Launch')
                self.client.logout()

    def test_missing_launch_content_returns_normal_404(self):
        response = self.client.get('/community')
        self.assertEqual(response.status_code, 404)

    def test_unrendered_unpublished_or_upcoming_launch_content_returns_404(self):
        create_launch_event(slug='no-recap-launch', recap_html='')
        create_launch_event(slug='draft-launch', published=False)
        create_launch_event(
            slug='upcoming-launch',
            status='upcoming',
            start_datetime=timezone.now() + timedelta(days=7),
            end_datetime=timezone.now() + timedelta(days=7, hours=1),
        )

        response = self.client.get('/community')

        self.assertEqual(response.status_code, 404)

    def test_event_detail_and_past_recordings_listing_still_work(self):
        event = create_launch_event()

        detail_response = self.client.get(event.get_absolute_url())
        self.assertEqual(detail_response.status_code, 200)
        self.assertTemplateUsed(detail_response, 'events/event_detail.html')
        self.assertContains(detail_response, 'Back to Events')
        self.assertContains(detail_response, 'What happened at the AI Shipping Labs')

        listing_response = self.client.get('/events?filter=past')
        self.assertEqual(listing_response.status_code, 200)
        self.assertContains(listing_response, 'AI Shipping Labs Community Launch')
        self.assertContains(listing_response, event.get_absolute_url())

    def test_landing_page_has_specific_seo_and_canonical(self):
        create_launch_event()

        response = self.client.get('/community')

        self.assertContains(
            response,
            '<title>AI Shipping Labs Community Launch | AI Shipping Labs</title>',
            html=True,
        )
        self.assertContains(
            response,
            'Read the AI Shipping Labs Community Launch recap and see how builders use the community',
        )
        self.assertContains(
            response,
            '<link rel="canonical" href="https://aishippinglabs.com/community">',
            html=True,
        )

    def test_sitemap_includes_community_landing(self):
        create_launch_event()

        response = self.client.get('/sitemap.xml')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '/community</loc>')


class CommunityLandingLookupTest(TestCase):
    def test_exact_known_slug_is_preferred_over_title_match(self):
        create_launch_event(
            title='AI Shipping Labs Community Launch',
            slug='older-title-match',
            start_datetime=timezone.now() - timedelta(days=10),
            end_datetime=timezone.now() - timedelta(days=10) + timedelta(hours=1),
            recap_html='<h2>Older title match</h2>',
        )
        create_launch_event(
            title='Launch recap from synced content',
            slug='community-launch',
            recap_html='<h2>Preferred slug match</h2>',
        )

        response = self.client.get('/community')

        self.assertContains(response, 'Preferred slug match')
        self.assertNotContains(response, 'Older title match')
