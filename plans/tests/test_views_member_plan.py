"""Tests for the read-only individual plan view (issue #440).

The view is the cross-member read of one plan. The owner is redirected
to ``my_plan_detail`` so the visibility toggle is reachable; teammates
see the shareable body without any interview-note or toggle UI.
"""

import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from plans.models import InterviewNote, Plan, Sprint

User = get_user_model()


class MemberPlanDetailTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.sprint = Sprint.objects.create(
            name='May 2026', slug='may-2026',
            start_date=datetime.date(2026, 5, 1),
        )
        cls.other_sprint = Sprint.objects.create(
            name='June 2026', slug='june-2026',
            start_date=datetime.date(2026, 6, 1),
        )
        cls.alice = User.objects.create_user(
            email='alice@test.com', password='pw',
            first_name='Alice', last_name='Smith',
        )
        cls.alice_plan_cohort = Plan.objects.create(
            member=cls.alice, sprint=cls.sprint, visibility='cohort',
            focus_main='Ship the SME agent prototype',
        )
        cls.bob = User.objects.create_user(
            email='bob@test.com', password='pw',
            first_name='Bob', last_name='Jones',
        )
        cls.bob_plan_cohort = Plan.objects.create(
            member=cls.bob, sprint=cls.sprint, visibility='cohort',
        )
        cls.charlie = User.objects.create_user(
            email='charlie@test.com', password='pw',
        )
        cls.charlie_plan_private = Plan.objects.create(
            member=cls.charlie, sprint=cls.sprint, visibility='private',
        )
        cls.outsider = User.objects.create_user(
            email='outsider@test.com', password='pw',
        )
        Plan.objects.create(
            member=cls.outsider, sprint=cls.other_sprint,
            visibility='cohort',
        )

    def test_member_plan_detail_owner_redirects_to_my_plan(self):
        self.client.force_login(self.alice)
        url = reverse(
            'member_plan_detail',
            kwargs={
                'sprint_slug': self.sprint.slug,
                'plan_id': self.alice_plan_cohort.pk,
            },
        )
        my_plan_url = reverse(
            'my_plan_detail',
            kwargs={'plan_id': self.alice_plan_cohort.pk},
        )
        response = self.client.get(url)
        self.assertRedirects(response, my_plan_url, fetch_redirect_response=False)

    def test_member_plan_detail_teammate_can_view_cohort_plan(self):
        self.client.force_login(self.bob)
        url = reverse(
            'member_plan_detail',
            kwargs={
                'sprint_slug': self.sprint.slug,
                'plan_id': self.alice_plan_cohort.pk,
            },
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Ship the SME agent prototype')

    def test_member_plan_detail_teammate_blocked_from_private_plan(self):
        self.client.force_login(self.bob)
        url = reverse(
            'member_plan_detail',
            kwargs={
                'sprint_slug': self.sprint.slug,
                'plan_id': self.charlie_plan_private.pk,
            },
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    def test_member_plan_detail_other_sprint_blocked(self):
        """A user enrolled only in another sprint cannot view the plan.

        Even when the plan's visibility is ``cohort`` -- the cohort
        scope is per-sprint, not global.
        """
        self.client.force_login(self.outsider)
        url = reverse(
            'member_plan_detail',
            kwargs={
                'sprint_slug': self.sprint.slug,
                'plan_id': self.alice_plan_cohort.pk,
            },
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    def test_member_plan_detail_anonymous_redirects(self):
        url = reverse(
            'member_plan_detail',
            kwargs={
                'sprint_slug': self.sprint.slug,
                'plan_id': self.alice_plan_cohort.pk,
            },
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response['Location'])


class MemberPlanDetailNoteIsolationTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.sprint = Sprint.objects.create(
            name='May 2026', slug='may-2026',
            start_date=datetime.date(2026, 5, 1),
        )
        cls.alice = User.objects.create_user(
            email='alice@test.com', password='pw',
            first_name='Alice', last_name='Smith',
        )
        cls.alice_plan = Plan.objects.create(
            member=cls.alice, sprint=cls.sprint, visibility='cohort',
        )
        cls.bob = User.objects.create_user(
            email='bob@test.com', password='pw',
        )
        Plan.objects.create(
            member=cls.bob, sprint=cls.sprint, visibility='cohort',
        )

    def setUp(self):
        self.client.force_login(self.bob)

    def _url(self):
        return reverse(
            'member_plan_detail',
            kwargs={
                'sprint_slug': self.sprint.slug,
                'plan_id': self.alice_plan.pk,
            },
        )

    def test_member_plan_detail_does_not_render_internal_note(self):
        InterviewNote.objects.create(
            plan=self.alice_plan, member=self.alice,
            visibility='internal', body='SECRET_INTERNAL_DETAIL',
        )
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'SECRET_INTERNAL_DETAIL')

    def test_member_plan_detail_does_not_render_external_note(self):
        InterviewNote.objects.create(
            plan=self.alice_plan, member=self.alice,
            visibility='external', body='NOT_SURFACED_DETAIL',
        )
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'NOT_SURFACED_DETAIL')
