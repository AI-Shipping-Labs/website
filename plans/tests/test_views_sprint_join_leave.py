"""Tests for sprint self-join and leave views (issue #443)."""

import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from payments.models import Tier
from plans.models import Plan, Sprint, SprintEnrollment

User = get_user_model()


def _premium_user(email):
    user = User.objects.create_user(email=email, password='pw')
    user.tier = Tier.objects.get(slug='premium')
    user.save(update_fields=['tier'])
    return user


def _free_user(email):
    user = User.objects.create_user(email=email, password='pw')
    user.tier = Tier.objects.get(slug='free')
    user.save(update_fields=['tier'])
    return user


class SprintJoinTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.sprint = Sprint.objects.create(
            name='May 2026', slug='may-2026',
            start_date=datetime.date(2026, 5, 1),
            status='active', min_tier_level=30,
        )

    def test_anonymous_join_redirects_to_login(self):
        url = reverse('sprint_join', kwargs={'sprint_slug': self.sprint.slug})
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response['Location'])
        # Side-effect-free: no enrollment created.
        self.assertEqual(SprintEnrollment.objects.count(), 0)

    def test_under_tier_join_redirects_to_pricing_no_enrollment(self):
        free = _free_user('free@test.com')
        self.client.force_login(free)
        url = reverse('sprint_join', kwargs={'sprint_slug': self.sprint.slug})
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], '/pricing')
        self.assertFalse(
            SprintEnrollment.objects.filter(
                sprint=self.sprint, user=free,
            ).exists()
        )

    def test_eligible_first_time_join_creates_enrollment(self):
        premium = _premium_user('p@test.com')
        self.client.force_login(premium)
        url = reverse('sprint_join', kwargs={'sprint_slug': self.sprint.slug})
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        # No plan -> redirect to cohort board.
        self.assertEqual(
            response['Location'],
            reverse('cohort_board', kwargs={'sprint_slug': self.sprint.slug}),
        )
        enrollment = SprintEnrollment.objects.get(
            sprint=self.sprint, user=premium,
        )
        # Self-join sets enrolled_by to NULL (audit signal).
        self.assertIsNone(enrollment.enrolled_by_id)

    def test_eligible_join_with_existing_plan_redirects_to_plan(self):
        premium = _premium_user('p@test.com')
        plan = Plan.objects.create(member=premium, sprint=self.sprint)
        # Plan creation already back-creates the enrollment via signal,
        # so we delete it to simulate "plan exists, user not yet enrolled"
        # (e.g. the operator pre-loaded a plan but the user hasn't joined).
        SprintEnrollment.objects.filter(
            sprint=self.sprint, user=premium,
        ).delete()
        self.client.force_login(premium)
        url = reverse('sprint_join', kwargs={'sprint_slug': self.sprint.slug})
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response['Location'],
            reverse(
                'my_plan_detail',
                kwargs={'sprint_slug': self.sprint.slug, 'plan_id': plan.pk},
            ),
        )
        # Enrollment was created on this join.
        self.assertTrue(
            SprintEnrollment.objects.filter(
                sprint=self.sprint, user=premium,
            ).exists()
        )

    def test_rejoin_is_idempotent(self):
        premium = _premium_user('p@test.com')
        SprintEnrollment.objects.create(sprint=self.sprint, user=premium)
        self.client.force_login(premium)
        url = reverse('sprint_join', kwargs={'sprint_slug': self.sprint.slug})
        before = SprintEnrollment.objects.count()
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(SprintEnrollment.objects.count(), before)


class SprintLeaveTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.sprint = Sprint.objects.create(
            name='May 2026', slug='may-2026',
            start_date=datetime.date(2026, 5, 1),
            status='active', min_tier_level=30,
        )

    def test_leave_deletes_enrollment_and_auto_privates_plan(self):
        premium = _premium_user('p@test.com')
        plan = Plan.objects.create(
            member=premium, sprint=self.sprint, visibility='cohort',
        )
        # Signal already created the enrollment; sanity check.
        self.assertTrue(
            SprintEnrollment.objects.filter(
                sprint=self.sprint, user=premium,
            ).exists()
        )
        self.client.force_login(premium)
        url = reverse('sprint_leave', kwargs={'sprint_slug': self.sprint.slug})
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response['Location'],
            reverse('sprint_detail', kwargs={'sprint_slug': self.sprint.slug}),
        )
        self.assertFalse(
            SprintEnrollment.objects.filter(
                sprint=self.sprint, user=premium,
            ).exists()
        )
        plan.refresh_from_db()
        self.assertEqual(plan.visibility, 'private')
        # Plan row itself still exists.
        self.assertTrue(Plan.objects.filter(pk=plan.pk).exists())

    def test_leave_without_plan_only_deletes_enrollment(self):
        premium = _premium_user('p@test.com')
        SprintEnrollment.objects.create(sprint=self.sprint, user=premium)
        self.client.force_login(premium)
        url = reverse('sprint_leave', kwargs={'sprint_slug': self.sprint.slug})
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            SprintEnrollment.objects.filter(
                sprint=self.sprint, user=premium,
            ).exists()
        )

    def test_re_leave_is_idempotent(self):
        premium = _premium_user('p@test.com')
        self.client.force_login(premium)
        url = reverse('sprint_leave', kwargs={'sprint_slug': self.sprint.slug})
        response = self.client.post(url)
        # 302 redirect, NOT 404. No exception.
        self.assertEqual(response.status_code, 302)


class SprintLeaveCohortBoardSideEffectTest(TestCase):
    """After leaving, the cohort board returns 404 to the leaver."""

    def test_left_user_loses_board_access(self):
        sprint = Sprint.objects.create(
            name='May 2026', slug='may-2026',
            start_date=datetime.date(2026, 5, 1),
            status='active',
        )
        member = _premium_user('p@test.com')
        Plan.objects.create(member=member, sprint=sprint, visibility='cohort')
        self.client.force_login(member)

        # Sanity: enrolled, board returns 200.
        board_url = reverse('cohort_board', kwargs={'sprint_slug': sprint.slug})
        self.assertEqual(self.client.get(board_url).status_code, 200)

        # Leave via the dedicated endpoint.
        leave_url = reverse('sprint_leave', kwargs={'sprint_slug': sprint.slug})
        self.client.post(leave_url)

        self.assertEqual(self.client.get(board_url).status_code, 404)
