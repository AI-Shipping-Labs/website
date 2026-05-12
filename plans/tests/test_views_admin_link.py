"""Tests for the staff-only "Open in Django admin" links (issue #585).

The link MUST render only when ``request.user.is_staff`` is True. It
appears in three places:
- ``my_plan_detail.html`` header nav (own plan view, owner is staff).
- ``member_plan_detail.html`` header nav (teammate plan view).
- ``cohort_board.html`` per-row, next to each cohort member's name.
"""

import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from plans.models import Plan, Sprint, SprintEnrollment

User = get_user_model()


class StaffAdminLinkOnPlanViewsTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.sprint = Sprint.objects.create(
            name='May 2026', slug='may-2026',
            start_date=datetime.date(2026, 5, 1),
        )
        cls.alex = User.objects.create_user(
            email='alex@test.com', password='pw',
            first_name='Alex', last_name='Member',
        )
        cls.alex_plan = Plan.objects.create(
            member=cls.alex, sprint=cls.sprint, visibility='cohort',
        )
        cls.bob = User.objects.create_user(
            email='bob@test.com', password='pw',
            first_name='Bob', last_name='Buddy',
        )
        cls.bob_plan = Plan.objects.create(
            member=cls.bob, sprint=cls.sprint, visibility='cohort',
        )
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        # Staff also needs an enrollment for the cohort board test.
        SprintEnrollment.objects.get_or_create(
            sprint=cls.sprint, user=cls.staff,
        )

    def test_my_plan_detail_renders_admin_link_for_staff_owner(self):
        # Make staff own a plan in this sprint.
        staff_plan = Plan.objects.create(
            member=self.staff, sprint=self.sprint, visibility='private',
        )
        self.client.force_login(self.staff)
        url = reverse(
            'my_plan_detail',
            kwargs={
                'sprint_slug': self.sprint.slug,
                'plan_id': staff_plan.pk,
            },
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="plan-admin-link"')
        self.assertContains(
            response,
            f'/admin/plans/plan/{staff_plan.pk}/change/',
        )

    def test_my_plan_detail_hides_admin_link_for_non_staff_owner(self):
        self.client.force_login(self.alex)
        url = reverse(
            'my_plan_detail',
            kwargs={
                'sprint_slug': self.sprint.slug,
                'plan_id': self.alex_plan.pk,
            },
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'data-testid="plan-admin-link"')
        self.assertNotContains(
            response,
            f'/admin/plans/plan/{self.alex_plan.pk}/change/',
        )

    def test_member_plan_detail_renders_admin_link_for_staff_viewer(self):
        self.client.force_login(self.staff)
        url = reverse(
            'member_plan_detail',
            kwargs={
                'sprint_slug': self.sprint.slug,
                'plan_id': self.bob_plan.pk,
            },
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="plan-admin-link"')
        self.assertContains(
            response,
            f'/admin/plans/plan/{self.bob_plan.pk}/change/',
        )

    def test_member_plan_detail_hides_admin_link_for_non_staff(self):
        self.client.force_login(self.alex)
        url = reverse(
            'member_plan_detail',
            kwargs={
                'sprint_slug': self.sprint.slug,
                'plan_id': self.bob_plan.pk,
            },
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'data-testid="plan-admin-link"')

    def test_cohort_board_per_row_admin_link_for_staff(self):
        self.client.force_login(self.staff)
        url = reverse(
            'cohort_board', kwargs={'sprint_slug': self.sprint.slug},
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        # One per-row admin link for each plan-owning member visible
        # to staff. Alex and Bob both have cohort plans.
        self.assertContains(
            response,
            f'data-testid="cohort-row-admin-link-{self.alex.pk}"',
        )
        self.assertContains(
            response,
            f'data-testid="cohort-row-admin-link-{self.bob.pk}"',
        )
        self.assertContains(
            response,
            f'/admin/plans/plan/{self.alex_plan.pk}/change/',
        )

    def test_cohort_board_no_admin_link_for_non_staff(self):
        self.client.force_login(self.alex)
        url = reverse(
            'cohort_board', kwargs={'sprint_slug': self.sprint.slug},
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(
            response,
            f'data-testid="cohort-row-admin-link-{self.bob.pk}"',
        )
        self.assertNotContains(
            response, '/admin/plans/plan/',
        )


class AdminLinkResolvesForStaffTest(TestCase):
    """Clicking the link from a staff user reaches the admin change page."""

    @classmethod
    def setUpTestData(cls):
        cls.sprint = Sprint.objects.create(
            name='May 2026', slug='may-2026',
            start_date=datetime.date(2026, 5, 1),
        )
        cls.member = User.objects.create_user(
            email='m@test.com', password='pw',
        )
        cls.plan = Plan.objects.create(
            member=cls.member, sprint=cls.sprint,
        )
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw',
            is_staff=True, is_superuser=True,
        )

    def test_admin_change_page_returns_200_for_staff(self):
        self.client.force_login(self.staff)
        url = f'/admin/plans/plan/{self.plan.pk}/change/'
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
