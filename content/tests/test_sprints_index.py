import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from payments.models import Tier
from plans.models import Plan, Sprint, SprintEnrollment

User = get_user_model()


class SprintsIndexTest(TestCase):
    def test_route_returns_200_for_anonymous_users(self):
        response = self.client.get('/sprints')

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'content/sprints_index.html')
        self.assertContains(response, 'Community Sprints')
        self.assertContains(response, 'data-testid="sprints-index-page"')

    def test_active_sprints_render_for_anonymous_users(self):
        sprint = Sprint.objects.create(
            name='May Shipping Sprint',
            slug='may-shipping-sprint',
            start_date=datetime.date(2026, 5, 15),
            duration_weeks=4,
            status='active',
            min_tier_level=20,
        )

        response = self.client.get('/sprints')

        self.assertContains(response, 'data-testid="sprints-sprint-card"')
        self.assertContains(response, sprint.name)
        self.assertContains(response, 'Active')
        self.assertContains(response, 'May 15, 2026')
        self.assertContains(response, '4 weeks')
        self.assertContains(response, 'Membership: Main')
        self.assertContains(response, 'Log in to join')
        self.assertContains(
            response,
            f'href="{reverse("account_login")}?next=/sprints/{sprint.slug}"',
        )
        self.assertContains(
            response,
            f'href="{reverse("sprint_detail", kwargs={"sprint_slug": sprint.slug})}"',
        )

    def test_draft_sprint_visibility_matches_activities_rules(self):
        Sprint.objects.create(
            name='Draft Sprint',
            slug='draft-sprint',
            start_date=datetime.date(2026, 6, 1),
            status='draft',
        )
        member = User.objects.create_user(email='member545@example.com', password='pw')

        anonymous_response = self.client.get('/sprints')
        self.assertNotContains(anonymous_response, 'Draft Sprint')

        self.client.force_login(member)
        member_response = self.client.get('/sprints')
        self.assertNotContains(member_response, 'Draft Sprint')

        self.client.logout()
        staff = User.objects.create_user(
            email='staff545@example.com',
            password='pw',
            is_staff=True,
        )
        self.client.force_login(staff)
        staff_response = self.client.get('/sprints')
        self.assertContains(staff_response, 'Draft Sprint')
        self.assertContains(staff_response, 'Draft')

    def test_completed_sprints_are_excluded(self):
        Sprint.objects.create(
            name='Completed Sprint',
            slug='completed-sprint',
            start_date=datetime.date(2026, 4, 1),
            status='completed',
        )

        response = self.client.get('/sprints')

        self.assertNotContains(response, 'Completed Sprint')
        self.assertContains(response, 'data-testid="sprints-empty"')

    def test_empty_state_renders_when_no_visible_active_sprints_exist(self):
        response = self.client.get('/sprints')

        self.assertContains(response, 'data-testid="sprints-empty"')
        self.assertContains(response, 'Next sprint coming soon')
        self.assertContains(response, 'href="/events"')
        self.assertContains(response, 'href="/workshops"')
        self.assertNotContains(response, 'data-testid="sprints-sprint-card"')

    def test_member_cta_points_to_pricing_when_under_required_tier(self):
        Sprint.objects.create(
            name='Premium Sprint',
            slug='premium-sprint',
            start_date=datetime.date(2026, 6, 1),
            status='active',
            min_tier_level=30,
        )
        member = User.objects.create_user(email='free545@example.com', password='pw')
        member.tier = Tier.objects.get(slug='free')
        member.save(update_fields=['tier'])

        self.client.force_login(member)
        response = self.client.get('/sprints')

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
        member = User.objects.create_user(email='main545@example.com', password='pw')
        member.tier = Tier.objects.get(slug='main')
        member.save(update_fields=['tier'])
        SprintEnrollment.objects.create(sprint=sprint, user=member)
        plan = Plan.objects.create(member=member, sprint=sprint, visibility='cohort')

        self.client.force_login(member)
        response = self.client.get('/sprints')

        self.assertContains(response, 'Open my plan')
        self.assertContains(
            response,
            reverse(
                'my_plan_detail',
                kwargs={'sprint_slug': sprint.slug, 'plan_id': plan.pk},
            ),
        )

    def test_enrolled_member_without_plan_cta_points_to_board(self):
        sprint = Sprint.objects.create(
            name='Board Sprint',
            slug='board-sprint',
            start_date=datetime.date(2026, 6, 1),
            status='active',
            min_tier_level=20,
        )
        member = User.objects.create_user(email='board545@example.com', password='pw')
        member.tier = Tier.objects.get(slug='main')
        member.save(update_fields=['tier'])
        SprintEnrollment.objects.create(sprint=sprint, user=member)

        self.client.force_login(member)
        response = self.client.get('/sprints')

        self.assertContains(response, 'Open cohort board')
        self.assertContains(
            response,
            reverse('cohort_board', kwargs={'sprint_slug': sprint.slug}),
        )

    def test_sprint_detail_route_still_resolves_after_index_route(self):
        sprint = Sprint.objects.create(
            name='Detail Sprint',
            slug='detail-sprint',
            start_date=datetime.date(2026, 6, 1),
            status='active',
        )

        response = self.client.get(
            reverse('sprint_detail', kwargs={'sprint_slug': sprint.slug}),
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'plans/sprint_detail.html')
        self.assertContains(response, 'data-testid="sprint-detail-name"')
