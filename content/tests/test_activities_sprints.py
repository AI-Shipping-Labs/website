import datetime
from pathlib import Path

import yaml
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from content.models import CuratedLink, SiteConfig
from payments.models import Tier
from plans.models import Plan, Sprint, SprintEnrollment

User = get_user_model()


def _active_sprint_start():
    return datetime.date.today() - datetime.timedelta(days=14)


def _expected_sprint_range(start_date, duration_weeks):
    end_date = start_date + datetime.timedelta(weeks=duration_weeks)
    if start_date.year == end_date.year:
        return (
            f'{start_date:%B} {start_date.day} – '
            f'{end_date:%B} {end_date.day}, {end_date.year} '
            f'({duration_weeks} weeks)'
        )
    return (
        f'{start_date:%B} {start_date.day}, {start_date.year} – '
        f'{end_date:%B} {end_date.day}, {end_date.year} '
        f'({duration_weeks} weeks)'
    )


def _seed_full_tier_config():
    fixture_path = Path(__file__).parent / 'fixtures' / 'tiers.yaml'
    with open(fixture_path) as f:
        tiers_data = yaml.safe_load(f)
    SiteConfig.objects.update_or_create(
        key='tiers',
        defaults={'data': tiers_data},
    )


class ActivitiesSprintHubTest(TestCase):
    def test_global_nav_keeps_expected_order(self):
        response = self.client.get('/membership')
        content = response.content.decode()
        header = content[:content.index('</header>')]
        # Slice the desktop primary nav to assert top-level ordering
        # without confusing nested dropdown links with top-level ones.
        primary = content[
            content.index('data-testid="desktop-primary-nav"'):
            content.index('<div class="hidden md:flex md:items-center md:gap-4">')
        ]

        community_index = primary.index('id="community-dropdown-btn"')
        learning_index = primary.index('id="learning-dropdown-btn"')
        about_trigger = primary.index('id="about-dropdown-btn"')

        self.assertLess(community_index, learning_index)
        self.assertLess(learning_index, about_trigger)
        self.assertNotIn('id="resources-dropdown-btn"', primary)
        self.assertNotIn('data-testid="nav-sprints"', primary)
        self.assertNotIn('data-testid="nav-events"', primary)

        self.assertIn('href="/about"', header)
        self.assertIn('href="/membership"', header)
        self.assertIn('href="/courses"', header)
        self.assertIn('href="/sprints"', header)
        self.assertIn('href="/faq"', header)
        self.assertNotIn('nav-community-link-activities', header)
        top_level_ids = [
            'data-testid="nav-sprints"',
            'data-testid="nav-events"',
        ]
        for test_id in top_level_ids:
            self.assertNotIn(test_id, primary)

    def test_active_sprint_details_render_for_anonymous_users(self):
        start_date = _active_sprint_start()
        sprint = Sprint.objects.create(
            name='May Shipping Sprint',
            slug='may-shipping-sprint',
            start_date=start_date,
            duration_weeks=4,
            status='active',
            min_tier_level=20,
        )

        response = self.client.get('/membership')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="membership-sprints-section"')
        self.assertContains(response, 'id="community-sprints"')
        self.assertContains(response, 'Active community sprints')
        self.assertContains(response, 'time-bound cohorts for shipping projects')
        self.assertNotContains(
            response,
            'Anonymous visitors can browse active sprint windows',
        )
        self.assertContains(response, sprint.name)
        self.assertContains(response, _expected_sprint_range(start_date, 4))
        self.assertContains(response, 'Active')
        self.assertContains(response, 'Main or above')
        self.assertContains(response, 'data-testid="sprints-sprint-tier"')
        self.assertContains(response, 'data-component="member-badge"')
        self.assertNotContains(response, 'Joining requires Main membership')
        self.assertContains(
            response,
            'A sprint is a time-bound shipping cohort with project structure',
        )
        self.assertNotContains(response, 'data-testid="membership-sprint-cta"')
        self.assertContains(response, 'data-testid="membership-sprint-detail-link"')
        self.assertContains(response, 'data-testid="membership-sprints-intro"')
        self.assertContains(response, 'data-testid="membership-sprints-card-row"')
        self.assertContains(response, 'data-testid="sprints-sprint-dates"')
        self.assertContains(
            response,
            f'href="{reverse("sprint_detail", kwargs={"sprint_slug": sprint.slug})}"',
        )
        self.assertNotContains(response, '/studio/')
        self.assertNotContains(response, '/plans/')

        detail_response = self.client.get(
            reverse('sprint_detail', kwargs={'sprint_slug': sprint.slug})
        )
        self.assertEqual(detail_response.status_code, 200)
        self.assertContains(detail_response, sprint.name)
        self.assertContains(detail_response, 'Log in to join')

    def test_sprint_section_uses_the_canonical_editorial_card_layout(self):
        Sprint.objects.create(
            name='May Shipping Sprint',
            slug='may-shipping-sprint',
            start_date=_active_sprint_start(),
            duration_weeks=4,
            status='active',
            min_tier_level=20,
        )

        response = self.client.get('/membership')
        content = response.content.decode()

        intro_index = content.index('data-testid="membership-sprints-intro"')
        card_row_index = content.index('data-testid="membership-sprints-card-row"')
        card_index = content.index('data-testid="membership-sprint-card"')
        context_index = content.index('data-testid="sprints-sprint-context"')
        dates_index = content.index('data-testid="sprints-sprint-dates"')
        detail_link_index = content.index(
            'data-testid="membership-sprint-detail-link"'
        )

        self.assertLess(intro_index, card_row_index)
        self.assertLess(card_row_index, card_index)
        self.assertLess(card_index, detail_link_index)
        self.assertLess(detail_link_index, context_index)
        self.assertLess(context_index, dates_index)
        sprint_card = content[card_index:content.index('</article>', card_index)]
        self.assertIn('data-testid="sprints-sprint-context"', sprint_card)
        self.assertIn('data-testid="sprints-sprint-dates"', sprint_card)
        self.assertNotIn('data-testid="membership-sprint-cta"', content)

    def test_membership_previews_only_the_first_current_sprint(self):
        first = Sprint.objects.create(
            name='First Current Sprint',
            slug='first-current-sprint',
            start_date=_active_sprint_start(),
            duration_weeks=4,
            status='active',
            min_tier_level=20,
        )
        Sprint.objects.create(
            name='Second Current Sprint',
            slug='second-current-sprint',
            start_date=_active_sprint_start() + datetime.timedelta(days=1),
            duration_weeks=4,
            status='active',
            min_tier_level=20,
        )

        response = self.client.get('/membership')

        self.assertEqual(len(response.context['activity_sprints']), 1)
        self.assertEqual(response.context['activity_sprints'][0]['sprint'], first)
        self.assertContains(response, first.name)
        self.assertNotContains(response, 'Second Current Sprint')

    def test_plans_and_benefits_render_before_sprints(self):
        Sprint.objects.create(
            name='May Shipping Sprint',
            slug='may-shipping-sprint',
            start_date=_active_sprint_start(),
            duration_weeks=4,
            status='active',
            min_tier_level=20,
        )

        response = self.client.get('/membership')
        content = response.content.decode()

        plans_index = content.index('id="pricing-section"')
        benefits_index = content.index('data-testid="membership-benefits-section"')
        sprint_section_index = content.index(
            'data-testid="membership-sprints-section"'
        )
        sprint_card_index = content.index('data-testid="membership-sprint-card"')
        live_events_index = content.index(
            'data-testid="membership-live-events-section"'
        )

        self.assertLess(plans_index, benefits_index)
        self.assertLess(benefits_index, sprint_section_index)
        self.assertLess(sprint_section_index, sprint_card_index)
        self.assertLess(sprint_card_index, live_events_index)
        self.assertNotIn('data-testid="activities-secondary-nav"', content)
        self.assertNotIn('data-testid="activities-tier-empty"', content)

    def test_draft_sprint_is_hidden_from_anonymous_and_member(self):
        Sprint.objects.create(
            name='Draft Sprint',
            slug='draft-sprint',
            start_date=datetime.date(2026, 6, 1),
            status='draft',
        )
        member = User.objects.create_user(email='member@example.com', password='pw')

        anonymous_response = self.client.get('/membership')
        self.assertNotContains(anonymous_response, 'Draft Sprint')

        self.client.force_login(member)
        member_response = self.client.get('/membership')
        self.assertNotContains(member_response, 'Draft Sprint')

    def test_staff_can_preview_draft_sprint_on_activities(self):
        Sprint.objects.create(
            name='Draft Sprint',
            slug='draft-sprint',
            start_date=datetime.date(2026, 6, 1),
            status='draft',
        )
        staff = User.objects.create_user(
            email='staff@example.com',
            password='pw',
            is_staff=True,
        )

        self.client.force_login(staff)
        response = self.client.get('/membership')

        self.assertContains(response, 'Draft Sprint')
        self.assertContains(response, 'Draft')

    def test_completed_sprints_are_not_rendered(self):
        Sprint.objects.create(
            name='Completed Sprint',
            slug='completed-sprint',
            start_date=datetime.date(2026, 4, 1),
            status='completed',
        )

        response = self.client.get('/membership')

        self.assertNotContains(response, 'Completed Sprint')

    def test_stale_active_sprint_past_its_window_is_hidden(self):
        Sprint.objects.create(
            name='Old Active Sprint',
            slug='old-active-sprint',
            start_date=datetime.date.today() - datetime.timedelta(days=70),
            duration_weeks=4,
            status='active',
        )
        Sprint.objects.create(
            name='Current Active Sprint',
            slug='current-active-sprint',
            start_date=_active_sprint_start(),
            duration_weeks=4,
            status='active',
        )

        response = self.client.get('/membership')

        self.assertContains(response, 'Current Active Sprint')
        self.assertNotContains(response, 'Old Active Sprint')

    def test_empty_state_renders_when_no_active_sprints_exist(self):
        response = self.client.get('/membership')

        self.assertContains(response, 'data-testid="membership-sprints-empty"')
        self.assertContains(response, 'Next sprint coming soon')
        self.assertContains(response, 'href="/events"')
        self.assertContains(response, 'href="/workshops"')
        self.assertNotContains(response, 'data-testid="membership-sprint-card"')

    def test_under_tier_member_sees_canonical_access_badge_and_detail_link(self):
        Sprint.objects.create(
            name='Premium Sprint',
            slug='premium-sprint',
            start_date=_active_sprint_start(),
            status='active',
            min_tier_level=30,
        )
        member = User.objects.create_user(email='free@example.com', password='pw')
        member.tier = Tier.objects.get(slug='free')
        member.save(update_fields=['tier'])

        self.client.force_login(member)
        response = self.client.get('/membership')

        self.assertContains(response, 'Premium')
        self.assertContains(response, 'data-required-level="30"')
        self.assertContains(
            response,
            f'href="{reverse("sprint_detail", kwargs={"sprint_slug": "premium-sprint"})}"',
        )
        self.assertNotContains(response, 'Upgrade to Premium')

    def test_eligible_member_keeps_the_canonical_detail_link(self):
        sprint = Sprint.objects.create(
            name='Main Sprint',
            slug='main-sprint',
            start_date=_active_sprint_start(),
            status='active',
            min_tier_level=20,
        )
        member = User.objects.create_user(email='eligible@example.com', password='pw')
        member.tier = Tier.objects.get(slug='main')
        member.save(update_fields=['tier'])

        self.client.force_login(member)
        response = self.client.get('/membership')

        self.assertContains(
            response,
            f'href="{reverse("sprint_detail", kwargs={"sprint_slug": sprint.slug})}"',
        )
        self.assertContains(response, 'data-testid="membership-sprint-detail-link"')
        self.assertNotContains(response, 'data-testid="membership-sprint-cta"')

    def test_enrolled_member_keeps_enrolled_badge_on_canonical_card(self):
        sprint = Sprint.objects.create(
            name='Main Sprint',
            slug='main-sprint',
            start_date=_active_sprint_start(),
            status='active',
            min_tier_level=20,
        )
        member = User.objects.create_user(email='main@example.com', password='pw')
        member.tier = Tier.objects.get(slug='main')
        member.save(update_fields=['tier'])
        SprintEnrollment.objects.create(sprint=sprint, user=member)
        Plan.objects.create(member=member, sprint=sprint, visibility='cohort')

        self.client.force_login(member)
        response = self.client.get('/membership')

        self.assertContains(response, "You're enrolled")
        self.assertContains(response, 'data-testid="sprints-sprint-enrolled"')
        self.assertContains(response, f'href="{sprint.get_absolute_url()}"')
        self.assertNotContains(response, 'data-testid="membership-sprint-cta"')


class ActivitiesCardActionTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        _seed_full_tier_config()

    def _card_markup(self, response, title):
        content = response.content.decode()
        title_index = content.index(title, content.index('id="activities"'))
        return content[
            content.rfind('<article', 0, title_index):
            content.find('</article>', title_index)
        ]

    def test_explained_benefits_use_shared_editorial_rows(self):
        response = self.client.get('/membership#activities')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="membership-benefit-row"', count=8)
        self.assertContains(response, 'data-testid="membership-benefit-link"', count=6)
        self.assertContains(response, 'data-testid="membership-benefit-title"', count=8)
        self.assertContains(response, 'data-testid="membership-benefit-description"', count=8)
        self.assertContains(response, 'data-testid="membership-benefit-tier-badge"', count=8)

        expected = {
            'Community sprints': (
                '/sprints',
                'Join time-boxed cohorts with check-ins, deadlines, and accountability',
                20,
            ),
            'Live events': (
                '/events',
                'Take part in live building sessions, office hours, mock interviews',
                20,
            ),
            'Workshop content': (
                '/workshops',
                'Access recordings, step-by-step tutorials, and practical materials',
                10,
            ),
            'Exclusive written content': (
                '/blog',
                'Read exclusive articles, practical tutorials with code examples',
                10,
            ),
            'Courses': (
                '/courses',
                'Follow structured courses on specialized topics',
                30,
            ),
        }
        for title, (destination, description, required_level) in expected.items():
            card = self._card_markup(response, title)
            self.assertEqual(card.count('<a '), 1, title)
            self.assertEqual(card.count('</a>'), 1, title)
            self.assertIn(f'href="{destination}"', card)
            self.assertIn(description, card)
            self.assertIn(f'data-required-level="{required_level}"', card)
            self.assertEqual(card.count('data-testid="membership-benefit-tier-badge"'), 1)
            self.assertIn('focus-visible:ring-2', card)

        explained_titles = [
            item['title'] for item in response.context['membership_benefits']
        ]
        self.assertNotIn('Resume and LinkedIn teardown', explained_titles)
        self.assertNotIn('GitHub feedback', explained_titles)


class ResourcesSprintIsolationTest(TestCase):
    def test_resources_remains_curated_links_without_sprint_cards(self):
        CuratedLink.objects.create(
            item_id='tool-1',
            title='Useful Tool',
            description='A durable reference link',
            url='https://example.com/tool',
            category='workshops',
            published=True,
        )
        Sprint.objects.create(
            name='May Shipping Sprint',
            slug='may-shipping-sprint',
            start_date=datetime.date(2026, 5, 15),
            status='active',
        )

        response = self.client.get('/resources')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Useful Tool')
        self.assertContains(response, 'Curated Links')
        self.assertNotContains(response, 'May Shipping Sprint')
        self.assertNotContains(response, 'data-testid="membership-sprint-card"')
