"""Tests for the Studio sprint destructive controls (issue #949).

Covers sprint cancel (soft), sprint delete (hard, guarded to empty
sprints), and sprint unenroll (hard-delete of a single membership row),
plus the shared staff-gating and POST-only contract.
"""

from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase

from plans.models import Plan, Sprint, SprintEnrollment
from tests.fixtures import TierSetupMixin

User = get_user_model()


def _make_sprint(slug='s', status='active'):
    return Sprint.objects.create(
        name=f'Sprint {slug}',
        slug=slug,
        start_date=date(2026, 5, 1),
        duration_weeks=6,
        status=status,
    )


class SprintCancelTest(TierSetupMixin, TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            email='staff@example.com', password='pw', is_staff=True,
        )
        self.member = User.objects.create_user(
            email='member@example.com', password='pw',
        )
        self.sprint = _make_sprint(slug='cancel')

    def test_cancel_sets_status_and_preserves_work(self):
        SprintEnrollment.objects.create(sprint=self.sprint, user=self.member)
        plan = Plan.objects.create(sprint=self.sprint, member=self.member)
        self.client.login(email='staff@example.com', password='pw')

        response = self.client.post(f'/studio/sprints/{self.sprint.pk}/cancel')

        self.assertEqual(response.status_code, 302)
        self.sprint.refresh_from_db()
        self.assertEqual(self.sprint.status, 'cancelled')
        # Enrollments and plans survive the soft cancel.
        self.assertEqual(self.sprint.enrollments.count(), 1)
        self.assertTrue(Plan.objects.filter(pk=plan.pk).exists())

    def test_cancel_is_idempotent(self):
        self.sprint.status = 'cancelled'
        self.sprint.save(update_fields=['status'])
        self.client.login(email='staff@example.com', password='pw')

        response = self.client.post(f'/studio/sprints/{self.sprint.pk}/cancel')

        self.assertEqual(response.status_code, 302)
        self.sprint.refresh_from_db()
        self.assertEqual(self.sprint.status, 'cancelled')

    def test_cancel_get_returns_405(self):
        self.client.login(email='staff@example.com', password='pw')
        response = self.client.get(f'/studio/sprints/{self.sprint.pk}/cancel')
        self.assertEqual(response.status_code, 405)

    def test_cancel_anonymous_redirects_to_login(self):
        response = self.client.post(f'/studio/sprints/{self.sprint.pk}/cancel')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response['Location'])
        self.sprint.refresh_from_db()
        self.assertEqual(self.sprint.status, 'active')

    def test_cancel_non_staff_forbidden(self):
        self.client.login(email='member@example.com', password='pw')
        response = self.client.post(f'/studio/sprints/{self.sprint.pk}/cancel')
        self.assertEqual(response.status_code, 403)
        self.sprint.refresh_from_db()
        self.assertEqual(self.sprint.status, 'active')


class SprintDeleteTest(TierSetupMixin, TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            email='staff@example.com', password='pw', is_staff=True,
        )
        self.member = User.objects.create_user(
            email='member@example.com', password='pw',
        )

    def test_delete_empty_sprint(self):
        sprint = _make_sprint(slug='empty', status='draft')
        self.client.login(email='staff@example.com', password='pw')

        response = self.client.post(f'/studio/sprints/{sprint.pk}/delete')

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], '/studio/sprints/')
        self.assertFalse(Sprint.objects.filter(pk=sprint.pk).exists())

    def test_delete_refuses_sprint_with_enrollment(self):
        sprint = _make_sprint(slug='enrolled')
        SprintEnrollment.objects.create(sprint=sprint, user=self.member)
        self.client.login(email='staff@example.com', password='pw')

        response = self.client.post(f'/studio/sprints/{sprint.pk}/delete')

        self.assertEqual(response.status_code, 302)
        # Sprint survives.
        self.assertTrue(Sprint.objects.filter(pk=sprint.pk).exists())

    def test_delete_refuses_sprint_with_plan(self):
        sprint = _make_sprint(slug='withplan')
        Plan.objects.create(sprint=sprint, member=self.member)
        self.client.login(email='staff@example.com', password='pw')

        response = self.client.post(f'/studio/sprints/{sprint.pk}/delete')

        self.assertEqual(response.status_code, 302)
        self.assertTrue(Sprint.objects.filter(pk=sprint.pk).exists())

    def test_delete_get_returns_405(self):
        sprint = _make_sprint(slug='get405', status='draft')
        self.client.login(email='staff@example.com', password='pw')
        response = self.client.get(f'/studio/sprints/{sprint.pk}/delete')
        self.assertEqual(response.status_code, 405)
        self.assertTrue(Sprint.objects.filter(pk=sprint.pk).exists())

    def test_delete_non_staff_forbidden(self):
        sprint = _make_sprint(slug='acl', status='draft')
        self.client.login(email='member@example.com', password='pw')
        response = self.client.post(f'/studio/sprints/{sprint.pk}/delete')
        self.assertEqual(response.status_code, 403)
        self.assertTrue(Sprint.objects.filter(pk=sprint.pk).exists())


class SprintUnenrollTest(TierSetupMixin, TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            email='staff@example.com', password='pw', is_staff=True,
        )
        self.member = User.objects.create_user(
            email='member@example.com', password='pw',
        )
        self.sprint = _make_sprint(slug='unenroll')
        self.enrollment = SprintEnrollment.objects.create(
            sprint=self.sprint, user=self.member,
        )

    def test_unenroll_deletes_only_membership_keeps_plan(self):
        plan = Plan.objects.create(sprint=self.sprint, member=self.member)
        self.client.login(email='staff@example.com', password='pw')

        response = self.client.post(
            f'/studio/sprints/{self.sprint.pk}/enrollments/'
            f'{self.enrollment.pk}/unenroll',
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            SprintEnrollment.objects.filter(pk=self.enrollment.pk).exists(),
        )
        # Plan and member account survive.
        self.assertTrue(Plan.objects.filter(pk=plan.pk).exists())
        self.assertTrue(User.objects.filter(pk=self.member.pk).exists())

    def test_unenroll_cross_sprint_id_404(self):
        other_sprint = _make_sprint(slug='other')
        self.client.login(email='staff@example.com', password='pw')

        response = self.client.post(
            f'/studio/sprints/{other_sprint.pk}/enrollments/'
            f'{self.enrollment.pk}/unenroll',
        )

        self.assertEqual(response.status_code, 404)
        # The mismatched enrollment is untouched.
        self.assertTrue(
            SprintEnrollment.objects.filter(pk=self.enrollment.pk).exists(),
        )

    def test_unenroll_get_returns_405(self):
        self.client.login(email='staff@example.com', password='pw')
        response = self.client.get(
            f'/studio/sprints/{self.sprint.pk}/enrollments/'
            f'{self.enrollment.pk}/unenroll',
        )
        self.assertEqual(response.status_code, 405)
        self.assertTrue(
            SprintEnrollment.objects.filter(pk=self.enrollment.pk).exists(),
        )

    def test_unenroll_anonymous_redirects_to_login(self):
        response = self.client.post(
            f'/studio/sprints/{self.sprint.pk}/enrollments/'
            f'{self.enrollment.pk}/unenroll',
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response['Location'])
        self.assertTrue(
            SprintEnrollment.objects.filter(pk=self.enrollment.pk).exists(),
        )

    def test_unenroll_non_staff_forbidden(self):
        self.client.login(email='member@example.com', password='pw')
        response = self.client.post(
            f'/studio/sprints/{self.sprint.pk}/enrollments/'
            f'{self.enrollment.pk}/unenroll',
        )
        self.assertEqual(response.status_code, 403)
        self.assertTrue(
            SprintEnrollment.objects.filter(pk=self.enrollment.pk).exists(),
        )


class SprintDetailEnrolledMembersTest(TierSetupMixin, TestCase):
    """The sprint detail page lists enrolled members with an Unenroll form."""

    def setUp(self):
        self.staff = User.objects.create_user(
            email='staff@example.com', password='pw', is_staff=True,
        )
        self.member = User.objects.create_user(
            email='member@example.com', password='pw',
        )
        self.sprint = _make_sprint(slug='roster')
        SprintEnrollment.objects.create(sprint=self.sprint, user=self.member)

    def test_detail_lists_enrolled_member_and_unenroll_control(self):
        self.client.login(email='staff@example.com', password='pw')
        response = self.client.get(f'/studio/sprints/{self.sprint.pk}/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'sprint-enrolled-members-section')
        self.assertContains(response, 'member@example.com')
        self.assertContains(
            response,
            f'/studio/sprints/{self.sprint.pk}/enrollments/',
        )

    def test_cancelled_sprint_shows_cancelled_status(self):
        self.sprint.status = 'cancelled'
        self.sprint.save(update_fields=['status'])
        self.client.login(email='staff@example.com', password='pw')
        response = self.client.get(f'/studio/sprints/{self.sprint.pk}/')
        self.assertContains(response, 'sprint-status-badge')
        self.assertContains(response, 'Cancelled')
