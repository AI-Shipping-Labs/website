from datetime import timedelta
from pathlib import Path

import yaml
from allauth.socialaccount.models import SocialApp
from django.conf import settings
from django.contrib.sites.models import Site
from django.test import TestCase
from django.utils import timezone

from content.models import SiteConfig
from content.views.home import _get_homepage_public_upcoming_events
from events.models import Event
from plans.models import Sprint
from tests.fixtures import TierSetupMixin


def _seed_site_config_tiers():
    fixture_path = Path(__file__).parent / 'fixtures' / 'tiers.yaml'
    with fixture_path.open(encoding='utf-8') as handle:
        tiers_data = yaml.safe_load(handle)
    SiteConfig.objects.update_or_create(
        key='tiers',
        defaults={'data': tiers_data},
    )


def _make_event(slug, *, start_datetime, **overrides):
    defaults = {
        'title': slug.replace('-', ' ').title(),
        'slug': slug,
        'start_datetime': start_datetime,
        'status': 'upcoming',
        'published': True,
        'description': f'Description for {slug}.',
    }
    defaults.update(overrides)
    return Event.objects.create(**defaults)


class HomepageFunnelTest(TierSetupMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        _seed_site_config_tiers()

    def _body(self, response):
        return response.content.decode()

    def _card_html(self, response, slug):
        body = self._body(response)
        start = body.index(f'data-tier-card="{slug}"')
        next_card = body.find('data-tier-card="', start + 1)
        end = next_card if next_card != -1 else len(body)
        return body[start:end]

    def _section_html(self, response, testid):
        body = self._body(response)
        marker = f'data-testid="{testid}"'
        marker_start = body.index(marker)
        section_start = body.rfind('<section ', 0, marker_start)
        start = section_start if section_start != -1 else marker_start
        next_section = body.find('<section ', start + 1)
        end = next_section if next_section != -1 else len(body)
        return body[start:end]

    def test_anonymous_home_shows_free_inline_register_and_oauth_context(self):
        app = SocialApp.objects.create(
            provider='google',
            name='Google',
            client_id='google-cid',
            secret='google-secret',
        )
        app.sites.add(Site.objects.get_current())

        response = self.client.get('/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['next_url'], '/')
        self.assertTrue(response.context['oauth_google_enabled'])
        self.assertFalse(response.context['oauth_github_enabled'])
        self.assertFalse(response.context['oauth_slack_enabled'])
        self.assertEqual(
            [tier['stripe_key'] for tier in response.context['tiers']],
            ['basic', 'main', 'premium'],
        )
        free_card = self._card_html(response, 'free')
        self.assertIn('data-testid="inline-register-card"', free_card)
        self.assertIn('data-testid="inline-register-opt-in"', free_card)
        self.assertIn('/accounts/google/login/?next=/', free_card)
        self.assertNotIn('tier-cta-link', free_card)

        body = self._body(response)
        self.assertIn('/static/js/accounts/auth-helpers.js', body)
        self.assertIn('auth-next-url', body)
        self.assertIn('/static/js/accounts/inline-register.js', body)

    def test_home_paid_tiers_default_to_monthly_prices_and_links(self):
        response = self.client.get('/')

        self.assertContains(response, 'aria-pressed="false"')
        basic_card = self._card_html(response, 'basic')
        main_card = self._card_html(response, 'main')
        premium_card = self._card_html(response, 'premium')

        self.assertIn('&euro;20', basic_card)
        self.assertIn('/month', basic_card)
        self.assertIn(
            f'href="{settings.STRIPE_PAYMENT_LINKS["basic"]["monthly"]}"',
            basic_card,
        )
        self.assertIn('&euro;50', main_card)
        self.assertIn(
            f'href="{settings.STRIPE_PAYMENT_LINKS["main"]["monthly"]}"',
            main_card,
        )
        self.assertIn('&euro;100', premium_card)
        self.assertIn(
            f'href="{settings.STRIPE_PAYMENT_LINKS["premium"]["monthly"]}"',
            premium_card,
        )

    def test_home_sprint_story_uses_current_or_next_active_sprint(self):
        today = timezone.localdate()
        ended_active = Sprint.objects.create(
            name='Ended Active Sprint',
            slug='ended-active-sprint',
            start_date=today - timedelta(days=56),
            duration_weeks=4,
            status='active',
            min_tier_level=20,
        )
        current = Sprint.objects.create(
            name='Current Sprint',
            slug='current-sprint',
            start_date=today - timedelta(days=7),
            duration_weeks=4,
            status='active',
            min_tier_level=30,
        )
        Sprint.objects.create(
            name='Future Sprint',
            slug='future-sprint',
            start_date=today + timedelta(days=14),
            duration_weeks=4,
            status='active',
            min_tier_level=20,
        )

        response = self.client.get('/')

        self.assertEqual(response.context['featured_sprint'], current)
        self.assertContains(response, current.name)
        self.assertContains(response, 'data-testid="home-featured-sprint-tier"')
        self.assertContains(response, 'Premium')
        self.assertContains(response, 'data-component="member-badge"')
        self.assertContains(response, current.get_absolute_url())
        self.assertNotContains(response, ended_active.name)

    def test_home_sprint_story_falls_back_to_evergreen_copy_when_none_visible(self):
        today = timezone.localdate()
        Sprint.objects.create(
            name='Old Sprint',
            slug='old-sprint',
            start_date=today - timedelta(days=70),
            duration_weeks=4,
            status='active',
        )
        Sprint.objects.create(
            name='Completed Future Sprint',
            slug='completed-future-sprint',
            start_date=today + timedelta(days=14),
            duration_weeks=4,
            status='completed',
        )

        response = self.client.get('/')

        self.assertIsNone(response.context['featured_sprint'])
        section = self._section_html(response, 'home-sprint-story-section')
        self.assertIn('Next sprint coming soon', section)
        self.assertIn('href="/sprints"', section)
        self.assertNotIn('Old Sprint', section)
        self.assertNotIn('Completed Future Sprint', section)

    def test_home_upcoming_events_limit_to_published_public_future_rows(self):
        now = timezone.now().replace(microsecond=0)
        first = _make_event(
            'first-upcoming',
            start_datetime=now + timedelta(days=1),
        )
        second = _make_event(
            'second-upcoming',
            start_datetime=now + timedelta(days=2),
        )
        third = _make_event(
            'third-upcoming',
            start_datetime=now + timedelta(days=3),
        )
        _make_event(
            'fourth-upcoming',
            start_datetime=now + timedelta(days=4),
        )
        _make_event(
            'draft-upcoming',
            start_datetime=now + timedelta(days=5),
            status='draft',
        )
        _make_event(
            'cancelled-upcoming',
            start_datetime=now + timedelta(days=6),
            status='cancelled',
        )
        _make_event(
            'unpublished-upcoming',
            start_datetime=now + timedelta(days=7),
            published=False,
        )
        _make_event(
            'completed-future',
            start_datetime=now + timedelta(days=8),
            status='completed',
        )
        _make_event(
            'stale-ended-upcoming',
            start_datetime=now - timedelta(hours=2),
            end_datetime=now - timedelta(minutes=1),
            status='upcoming',
        )

        upcoming_events = _get_homepage_public_upcoming_events(now=now)

        self.assertEqual(
            [event.slug for event in upcoming_events],
            [first.slug, second.slug, third.slug],
        )

    def test_home_recordings_context_keeps_past_recordings_distinct_from_live_events(self):
        now = timezone.now().replace(microsecond=0)
        past_recording = _make_event(
            'past-recording',
            start_datetime=now - timedelta(days=3),
            end_datetime=now - timedelta(days=3) + timedelta(hours=1),
            status='completed',
            recording_url='https://video.test/past-recording',
        )
        _make_event(
            'future-recording-placeholder',
            start_datetime=now + timedelta(days=3),
            end_datetime=now + timedelta(days=3, hours=1),
            status='upcoming',
            recording_url='https://video.test/future-recording',
        )

        response = self.client.get('/')

        self.assertEqual(list(response.context['recordings']), [past_recording])
        section = self._section_html(response, 'home-past-recordings-section')
        self.assertIn('id="resources"', section)
        self.assertIn('Past event recordings', section)
        self.assertIn('Past Event Recordings', section)
        self.assertIn('View all past recordings', section)
        self.assertIn('href="/events?filter=past"', section)
        self.assertIn('data-testid="home-past-recordings-cta"', section)
        self.assertIn(f'href="{past_recording.get_absolute_url()}"', section)
        self.assertIn('View recording', section)
        self.assertNotIn('Workshops &amp; Learning Materials', section)
        self.assertContains(response, 'Upcoming live events')

    def test_authenticated_member_keeps_dashboard_route(self):
        from django.contrib.auth import get_user_model

        user = get_user_model().objects.create_user(
            email='member1162@example.com',
            password='pw',
        )
        user.tier = self.main_tier
        user.save(update_fields=['tier'])
        self.client.force_login(user)

        response = self.client.get('/')

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'content/dashboard.html')
        self.assertNotContains(response, 'data-testid="home-sprint-story-section"')
        self.assertNotContains(response, 'data-testid="home-upcoming-events-section"')
        self.assertNotContains(response, 'data-testid="home-free-tier-register"')
