"""Tests for the Studio sprint destructive controls (issue #949).

Covers sprint cancel (soft), sprint delete (hard, guarded to empty
sprints), and sprint unenroll (hard-delete of a single membership row),
plus the shared staff-gating and POST-only contract.
"""

from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from events.models import EventSeries
from plans.models import (
    Plan,
    PlanRequest,
    Sprint,
    SprintAccountabilityPartner,
    SprintEnrollment,
    SprintFeedbackRequest,
    SprintFeedbackSummary,
)
from questionnaires.models import Questionnaire
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


class SprintCompleteTest(TierSetupMixin, TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            email='staff@example.com', password='pw', is_staff=True,
        )
        self.member = User.objects.create_user(
            email='member@example.com', password='pw',
        )
        self.partner = User.objects.create_user(
            email='partner@example.com', password='pw',
        )
        self.sprint = _make_sprint(slug='complete', status='active')

    def test_complete_sets_status_and_preserves_related_work(self):
        series = EventSeries.objects.create(
            name='Sprint calls',
            slug='sprint-calls',
            start_time='18:00',
        )
        self.sprint.event_series = series
        self.sprint.save(update_fields=['event_series'])
        enrollment = SprintEnrollment.objects.create(
            sprint=self.sprint, user=self.member, enrolled_by=self.staff,
        )
        Plan.objects.create(sprint=self.sprint, member=self.member)
        request = PlanRequest.objects.create(
            sprint=self.sprint, member=self.member,
        )
        questionnaire = Questionnaire.objects.create(
            title='Sprint Feedback', purpose='feedback',
        )
        feedback_request = SprintFeedbackRequest.objects.create(
            sprint=self.sprint,
            questionnaire=questionnaire,
            created_by=self.staff,
        )
        summary = SprintFeedbackSummary.objects.create(
            feedback_request=feedback_request,
            result_json={
                'themes': [],
                'what_went_well': [],
                'what_to_improve': [],
                'recommendations': [],
                'next_sprint_signal': '',
                'response_count': 0,
            },
            response_count=0,
            model_name='test-model',
            generated_by=self.staff,
            generated_at=timezone.now(),
        )
        SprintEnrollment.objects.create(
            sprint=self.sprint, user=self.partner, enrolled_by=self.staff,
        )
        assignment = SprintAccountabilityPartner.objects.create(
            sprint=self.sprint,
            member=self.member,
            partner=self.partner,
            assigned_by=self.staff,
        )
        self.client.login(email='staff@example.com', password='pw')

        response = self.client.post(f'/studio/sprints/{self.sprint.pk}/complete')

        self.assertRedirects(response, f'/studio/sprints/{self.sprint.pk}/')
        self.sprint.refresh_from_db()
        self.assertEqual(self.sprint.status, 'completed')
        self.assertEqual(self.sprint.event_series_id, series.pk)
        self.assertTrue(SprintEnrollment.objects.filter(pk=enrollment.pk).exists())
        self.assertEqual(self.sprint.enrollments.count(), 2)
        self.assertEqual(self.sprint.plans.count(), 1)
        self.assertTrue(PlanRequest.objects.filter(pk=request.pk).exists())
        self.assertTrue(
            SprintFeedbackRequest.objects.filter(pk=feedback_request.pk).exists(),
        )
        self.assertTrue(SprintFeedbackSummary.objects.filter(pk=summary.pk).exists())
        self.assertTrue(
            SprintAccountabilityPartner.objects.filter(pk=assignment.pk).exists(),
        )

    def test_complete_is_idempotent_for_already_completed_sprint(self):
        self.sprint.status = 'completed'
        self.sprint.save(update_fields=['status'])
        self.client.login(email='staff@example.com', password='pw')

        response = self.client.post(f'/studio/sprints/{self.sprint.pk}/complete')

        self.assertRedirects(response, f'/studio/sprints/{self.sprint.pk}/')
        self.sprint.refresh_from_db()
        self.assertEqual(self.sprint.status, 'completed')

    def test_complete_refuses_cancelled_sprint_and_ui_hides_action(self):
        self.sprint.status = 'cancelled'
        self.sprint.save(update_fields=['status'])
        self.client.login(email='staff@example.com', password='pw')

        response = self.client.get(f'/studio/sprints/{self.sprint.pk}/')

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'data-testid="sprint-complete-form"')

        response = self.client.post(f'/studio/sprints/{self.sprint.pk}/complete')

        self.assertRedirects(response, f'/studio/sprints/{self.sprint.pk}/')
        self.sprint.refresh_from_db()
        self.assertEqual(self.sprint.status, 'cancelled')

    def test_complete_get_returns_405(self):
        self.client.login(email='staff@example.com', password='pw')
        response = self.client.get(f'/studio/sprints/{self.sprint.pk}/complete')
        self.assertEqual(response.status_code, 405)
        self.sprint.refresh_from_db()
        self.assertEqual(self.sprint.status, 'active')

    def test_complete_anonymous_redirects_to_login(self):
        response = self.client.post(f'/studio/sprints/{self.sprint.pk}/complete')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response['Location'])
        self.sprint.refresh_from_db()
        self.assertEqual(self.sprint.status, 'active')

    def test_complete_non_staff_forbidden(self):
        self.client.login(email='member@example.com', password='pw')
        response = self.client.post(f'/studio/sprints/{self.sprint.pk}/complete')
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


class SprintDetailMembersTest(TierSetupMixin, TestCase):
    """The sprint detail page merges enrollments and plans into one table."""

    def setUp(self):
        self.staff = User.objects.create_user(
            email='staff@example.com', password='pw', is_staff=True,
        )
        self.enrolled_with_plan = User.objects.create_user(
            email='with-plan@example.com', password='pw',
            first_name='With', last_name='Plan',
        )
        self.enrolled_without_plan = User.objects.create_user(
            email='no-plan@example.com', password='pw',
        )
        self.plan_only = User.objects.create_user(
            email='plan-only@example.com', password='pw',
        )
        self.sprint = _make_sprint(slug='roster')

    def test_detail_merges_enrollments_and_plans(self):
        enrollment = SprintEnrollment.objects.create(
            sprint=self.sprint,
            user=self.enrolled_with_plan,
            enrolled_by=self.staff,
        )
        SprintEnrollment.objects.create(
            sprint=self.sprint,
            user=self.enrolled_without_plan,
        )
        plan = Plan.objects.create(
            sprint=self.sprint,
            member=self.enrolled_with_plan,
            visibility='cohort',
            shared_at=timezone.now(),
        )
        plan_only = Plan.objects.create(
            sprint=self.sprint,
            member=self.plan_only,
            visibility='private',
        )
        SprintEnrollment.objects.filter(
            sprint=self.sprint,
            user=self.plan_only,
        ).delete()

        self.client.login(email='staff@example.com', password='pw')
        response = self.client.get(f'/studio/sprints/{self.sprint.pk}/')

        self.assertEqual(response.status_code, 200)
        rows = response.context['sprint_member_rows']
        self.assertEqual([row['member'].email for row in rows], [
            'with-plan@example.com',
            'no-plan@example.com',
            'plan-only@example.com',
        ])
        self.assertEqual(response.context['enrollment_count'], 2)
        self.assertEqual(response.context['plan_count'], 2)
        self.assertEqual(rows[0]['enrollment'], enrollment)
        self.assertEqual(rows[0]['plan'], plan)
        self.assertIsNotNone(rows[1]['enrollment'])
        self.assertIsNone(rows[1]['plan'])
        self.assertIsNone(rows[2]['enrollment'])
        self.assertEqual(rows[2]['plan'], plan_only)

        self.assertContains(response, 'Sprint members')
        self.assertContains(response, '2 enrolled')
        self.assertContains(response, '2 plans')
        self.assertNotContains(response, 'Plans in this sprint')
        self.assertNotContains(response, 'Enrolled members')
        self.assertContains(response, 'with-plan@example.com')
        self.assertContains(response, 'With Plan')
        self.assertContains(response, 'Enrolled')
        self.assertContains(response, 'by staff@example.com')
        self.assertContains(response, 'View plan')
        self.assertContains(response, 'Edit plan')
        self.assertContains(response, 'Cohort (visible to other members of the same sprint)')
        self.assertContains(response, 'Shared')
        self.assertContains(response, 'no-plan@example.com')
        self.assertContains(response, 'No plan yet')
        self.assertContains(
            response,
            f'/studio/plans/new?user={self.enrolled_without_plan.pk}&amp;sprint={self.sprint.pk}',
        )
        self.assertContains(response, 'plan-only@example.com')
        self.assertContains(response, 'Not enrolled')
        self.assertContains(response, 'Private (only the member and staff)')

        body = response.content.decode()
        plan_only_row_start = body.index('data-user-email="plan-only@example.com"')
        plan_only_row_end = body.index('</tr>', plan_only_row_start)
        plan_only_row = body[plan_only_row_start:plan_only_row_end]
        self.assertNotIn('sprint-unenroll-form', plan_only_row)
        self.assertNotIn('Plan.status', body)
        self.assertNotIn('get_status_display', body)

    def test_detail_lists_enrolled_member_and_unenroll_control(self):
        SprintEnrollment.objects.create(
            sprint=self.sprint,
            user=self.enrolled_without_plan,
        )

        self.client.login(email='staff@example.com', password='pw')
        response = self.client.get(f'/studio/sprints/{self.sprint.pk}/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'sprint-members-section')
        self.assertContains(response, 'no-plan@example.com')
        self.assertContains(
            response,
            f'/studio/sprints/{self.sprint.pk}/enrollments/',
        )
        self.assertContains(response, 'sprint-unenroll-form')

    def test_cancelled_sprint_shows_cancelled_status(self):
        self.sprint.status = 'cancelled'
        self.sprint.save(update_fields=['status'])
        self.client.login(email='staff@example.com', password='pw')
        response = self.client.get(f'/studio/sprints/{self.sprint.pk}/')
        self.assertContains(response, 'sprint-admin-status-badge')
        self.assertContains(response, 'Cancelled')

    def test_member_rows_order_enrolled_then_plan_only_by_created_at(self):
        enrolled_later = SprintEnrollment.objects.create(
            sprint=self.sprint,
            user=self.enrolled_without_plan,
        )
        enrolled_earlier = SprintEnrollment.objects.create(
            sprint=self.sprint,
            user=self.enrolled_with_plan,
        )
        plan_only_older_user = User.objects.create_user(
            email='plan-only-older@example.com', password='pw',
        )
        plan_only_newer_user = User.objects.create_user(
            email='plan-only-newer@example.com', password='pw',
        )
        older_plan = Plan.objects.create(
            sprint=self.sprint,
            member=plan_only_older_user,
        )
        newer_plan = Plan.objects.create(
            sprint=self.sprint,
            member=plan_only_newer_user,
        )
        now = timezone.now()
        SprintEnrollment.objects.filter(pk=enrolled_earlier.pk).update(
            enrolled_at=now - timedelta(days=2),
        )
        SprintEnrollment.objects.filter(pk=enrolled_later.pk).update(
            enrolled_at=now - timedelta(days=1),
        )
        Plan.objects.filter(pk=older_plan.pk).update(
            created_at=now - timedelta(days=4),
        )
        Plan.objects.filter(pk=newer_plan.pk).update(
            created_at=now - timedelta(days=3),
        )
        SprintEnrollment.objects.filter(
            sprint=self.sprint,
            user__in=[plan_only_older_user, plan_only_newer_user],
        ).delete()

        self.client.login(email='staff@example.com', password='pw')
        response = self.client.get(f'/studio/sprints/{self.sprint.pk}/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [row['member'].email for row in response.context['sprint_member_rows']],
            [
                'with-plan@example.com',
                'no-plan@example.com',
                'plan-only-older@example.com',
                'plan-only-newer@example.com',
            ],
        )

    def test_empty_detail_renders_one_unified_empty_state(self):
        self.client.login(email='staff@example.com', password='pw')

        response = self.client.get(f'/studio/sprints/{self.sprint.pk}/')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="sprint-members-empty"')
        self.assertContains(response, 'No members or plans in this sprint yet.')
        self.assertNotContains(response, 'No enrolled members in this sprint yet.')
