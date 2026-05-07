import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from content.models import CuratedLink
from payments.models import Tier
from plans.models import Plan, Sprint, SprintEnrollment

User = get_user_model()


class ActivitiesSprintHubTest(TestCase):
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
        self.assertContains(response, sprint.name)
        self.assertContains(response, 'May 15, 2026')
        self.assertContains(response, '4 weeks')
        self.assertContains(response, 'Active')
        self.assertContains(response, 'Main tier required')
        self.assertContains(response, 'Log in to join')
        self.assertContains(
            response,
            f'{reverse("account_login")}?next=/sprints/{sprint.slug}',
        )

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
        self.assertContains(response, 'No active community sprints right now')
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
            reverse('my_plan_detail', kwargs={'plan_id': plan.pk}),
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
