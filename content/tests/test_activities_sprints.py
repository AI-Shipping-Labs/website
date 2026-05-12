import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from content.models import CuratedLink
from payments.models import Tier
from plans.models import Plan, Sprint, SprintEnrollment

User = get_user_model()


class ActivitiesSprintHubTest(TestCase):
    def test_global_nav_keeps_expected_order(self):
        response = self.client.get('/activities')
        content = response.content.decode()
        header = content[:content.index('</header>')]
        # Slice the desktop primary nav to assert top-level ordering
        # without confusing nested dropdown links with top-level ones.
        primary = content[
            content.index('data-testid="desktop-primary-nav"'):
            content.index('<div class="hidden md:flex md:items-center md:gap-4">')
        ]

        about_trigger = primary.index('id="about-dropdown-btn"')
        membership_index = primary.index('data-testid="nav-membership"')
        community_index = primary.index('id="community-dropdown-btn"')
        sprints_index = primary.index('data-testid="nav-sprints"')
        events_index = primary.index('data-testid="nav-events"')
        resources_index = primary.index('id="resources-dropdown-btn"')

        self.assertLess(about_trigger, membership_index)
        self.assertLess(membership_index, community_index)
        self.assertLess(community_index, sprints_index)
        self.assertLess(sprints_index, events_index)
        self.assertLess(events_index, resources_index)

        self.assertIn('href="/about"', header)
        self.assertIn('href="/pricing"', header)
        self.assertIn('href="/courses"', header)
        self.assertIn('href="/sprints"', header)
        self.assertIn('href="/resources"', header)
        self.assertIn('href="/faq"', header)
        self.assertIn('>Membership</a>', primary)
        self.assertNotIn('href="/activities"', header)
        self.assertNotIn('>Activities</a>', header)

    def test_active_sprint_details_render_for_anonymous_users(self):
        sprint = Sprint.objects.create(
            name='May Shipping Sprint',
            slug='may-shipping-sprint',
            start_date=datetime.date(2026, 5, 15),
            duration_weeks=4,
            status='active',
            min_tier_level=20,
        )

        response = self.client.get('/activities')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="activities-sprints-section"')
        self.assertContains(response, 'id="community-sprints"')
        self.assertContains(response, 'Active community sprints')
        self.assertContains(response, 'time-bound cohorts for shipping projects')
        self.assertContains(response, sprint.name)
        self.assertContains(response, 'May 15, 2026')
        self.assertContains(response, '4 weeks')
        self.assertContains(response, 'Active')
        self.assertContains(response, 'Membership: Main')
        self.assertContains(response, 'Joining requires Main membership')
        self.assertContains(response, 'Log in to join')
        self.assertContains(response, 'data-testid="activities-sprints-intro-row"')
        self.assertContains(response, 'data-testid="activities-sprints-card-row"')
        self.assertContains(response, 'data-testid="activities-sprint-facts"')
        self.assertContains(
            response,
            f'{reverse("account_login")}?next=/sprints/{sprint.slug}',
        )

    def test_sprint_section_uses_stacked_detail_layout(self):
        Sprint.objects.create(
            name='May Shipping Sprint',
            slug='may-shipping-sprint',
            start_date=datetime.date(2026, 5, 15),
            duration_weeks=4,
            status='active',
            min_tier_level=20,
        )

        response = self.client.get('/activities')
        content = response.content.decode()

        intro_index = content.index('data-testid="activities-sprints-intro-row"')
        card_row_index = content.index('data-testid="activities-sprints-card-row"')
        card_index = content.index('data-testid="activities-sprint-card"')
        facts_index = content.index('data-testid="activities-sprint-facts"')
        guidance_index = content.index('data-testid="activities-sprint-guidance"')
        cta_index = content.index('data-testid="activities-sprint-cta"')

        self.assertLess(intro_index, card_row_index)
        self.assertLess(card_row_index, card_index)
        self.assertLess(card_index, facts_index)
        self.assertLess(facts_index, guidance_index)
        self.assertLess(guidance_index, cta_index)
        self.assertNotIn(
            'lg:grid-cols-[minmax(0,0.78fr)_minmax(420px,1fr)]',
            content,
        )
        facts_markup = content[facts_index:guidance_index]
        self.assertNotIn('sm:grid-cols-2', facts_markup)
        self.assertNotIn('sm:flex-row sm:items-start sm:justify-between', content)

    def test_sprints_render_before_secondary_nav_and_tier_activity_content(self):
        Sprint.objects.create(
            name='May Shipping Sprint',
            slug='may-shipping-sprint',
            start_date=datetime.date(2026, 5, 15),
            duration_weeks=4,
            status='active',
            min_tier_level=20,
        )

        response = self.client.get('/activities')
        content = response.content.decode()

        sprint_section_index = content.index(
            'data-testid="activities-sprints-section"'
        )
        sprint_card_index = content.index('data-testid="activities-sprint-card"')
        secondary_nav_index = content.index(
            'data-testid="activities-secondary-nav"'
        )
        access_by_tier_index = content.index('Access by Tier')
        quick_comparison_index = content.index('Quick comparison')

        self.assertLess(sprint_section_index, sprint_card_index)
        self.assertLess(sprint_card_index, secondary_nav_index)
        self.assertLess(secondary_nav_index, access_by_tier_index)
        self.assertLess(access_by_tier_index, quick_comparison_index)

    def test_draft_sprint_is_hidden_from_anonymous_and_member(self):
        Sprint.objects.create(
            name='Draft Sprint',
            slug='draft-sprint',
            start_date=datetime.date(2026, 6, 1),
            status='draft',
        )
        member = User.objects.create_user(email='member@example.com', password='pw')

        anonymous_response = self.client.get('/activities')
        self.assertNotContains(anonymous_response, 'Draft Sprint')

        self.client.force_login(member)
        member_response = self.client.get('/activities')
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
        response = self.client.get('/activities')

        self.assertContains(response, 'Draft Sprint')
        self.assertContains(response, 'Draft')

    def test_completed_sprints_are_not_rendered(self):
        Sprint.objects.create(
            name='Completed Sprint',
            slug='completed-sprint',
            start_date=datetime.date(2026, 4, 1),
            status='completed',
        )

        response = self.client.get('/activities')

        self.assertNotContains(response, 'Completed Sprint')

    def test_empty_state_renders_when_no_active_sprints_exist(self):
        response = self.client.get('/activities')

        self.assertContains(response, 'data-testid="activities-sprints-empty"')
        self.assertContains(response, 'Next sprint coming soon')
        self.assertContains(response, 'href="/events"')
        self.assertContains(response, 'href="/workshops"')
        self.assertNotContains(response, 'data-testid="activities-sprint-card"')

    def test_member_cta_points_to_pricing_when_under_required_tier(self):
        Sprint.objects.create(
            name='Premium Sprint',
            slug='premium-sprint',
            start_date=datetime.date(2026, 6, 1),
            status='active',
            min_tier_level=30,
        )
        member = User.objects.create_user(email='free@example.com', password='pw')
        member.tier = Tier.objects.get(slug='free')
        member.save(update_fields=['tier'])

        self.client.force_login(member)
        response = self.client.get('/activities')

        self.assertContains(response, 'Upgrade to Premium')
        self.assertContains(response, f'href="{reverse("pricing")}"')

    def test_enrolled_member_cta_points_to_existing_plan(self):
        sprint = Sprint.objects.create(
            name='Main Sprint',
            slug='main-sprint',
            start_date=datetime.date(2026, 6, 1),
            status='active',
            min_tier_level=20,
        )
        member = User.objects.create_user(email='main@example.com', password='pw')
        member.tier = Tier.objects.get(slug='main')
        member.save(update_fields=['tier'])
        SprintEnrollment.objects.create(sprint=sprint, user=member)
        plan = Plan.objects.create(member=member, sprint=sprint, visibility='cohort')

        self.client.force_login(member)
        response = self.client.get('/activities')

        self.assertContains(response, 'Open my plan')
        self.assertContains(
            response,
            reverse(
                'my_plan_detail',
                kwargs={'sprint_slug': sprint.slug, 'plan_id': plan.pk},
            ),
        )


class ResourcesSprintIsolationTest(TestCase):
    def test_resources_remains_curated_links_without_sprint_cards(self):
        CuratedLink.objects.create(
            item_id='tool-1',
            title='Useful Tool',
            description='A durable reference link',
            url='https://example.com/tool',
            category='tools',
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
        self.assertNotContains(response, 'data-testid="activities-sprint-card"')
