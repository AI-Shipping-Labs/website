"""Studio first-sprint draft and plan polish tests (issue #1205)."""

import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase

from plans.models import FirstSprintPlanDraft, Plan, Sprint, Week
from plans.services.first_sprint_draft import DraftWeek, FirstSprintDraftResult
from questionnaires.models import Questionnaire, Response

User = get_user_model()


def _make_plan(member, *, slug='july-studio-first'):
    sprint = Sprint.objects.create(
        name='July',
        slug=slug,
        start_date=datetime.date(2026, 7, 1),
        duration_weeks=2,
    )
    plan = Plan.objects.create(member=member, sprint=sprint)
    for n in range(1, 3):
        Week.objects.create(plan=plan, week_number=n, position=n - 1)
    return plan


def _draft_result():
    return FirstSprintDraftResult(
        title='First sprint',
        goal='Ship first app',
        summary_goal='Build a small app.',
        weeks=[
            DraftWeek(week_number=1, theme='Scope', checkpoints=['Pick idea']),
            DraftWeek(week_number=2, theme='Ship', checkpoints=['Publish']),
        ],
        deliverables=['Demo'],
        next_steps=['Confirm project'],
        internal_notes='Staff-only scope check.',
        rationale='Onboarding.',
    )


class FirstSprintDraftStudioViewTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com',
            password='pw',
            is_staff=True,
        )
        cls.member = User.objects.create_user(
            email='member@test.com',
            password='pw',
            first_name='Member',
            last_name='One',
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='pw')
        self.plan = _make_plan(self.member)
        questionnaire = Questionnaire.objects.get(slug='onboarding-general')
        self.response = Response.objects.create(
            questionnaire=questionnaire,
            respondent=self.member,
            status='submitted',
        )

    def _create_draft(self):
        return FirstSprintPlanDraft.objects.create(
            plan=self.plan,
            source_response=self.response,
            result_json=_draft_result().model_dump(),
            generated_by=self.staff,
            generated_at=datetime.datetime(
                2026, 7, 1, 12, tzinfo=datetime.UTC,
            ),
        )

    def test_editor_renders_first_draft_panel_only_when_draft_exists(self):
        response = self.client.get(f'/studio/plans/{self.plan.pk}/edit/')
        self.assertNotContains(response, 'first-sprint-draft-panel')

        self._create_draft()
        response = self.client.get(f'/studio/plans/{self.plan.pk}/edit/')
        self.assertContains(response, 'first-sprint-draft-panel')
        self.assertContains(response, 'nothing has been applied')
        self.assertContains(response, 'Ship first app')

    def test_apply_writes_plan_and_removes_draft_without_sharing(self):
        self._create_draft()
        response = self.client.post(
            f'/studio/plans/{self.plan.pk}/draft-first-sprint/apply/',
        )

        self.assertRedirects(response, f'/studio/plans/{self.plan.pk}/edit/')
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.goal, 'Ship first app')
        self.assertIsNone(self.plan.shared_at)
        self.assertEqual(FirstSprintPlanDraft.objects.filter(plan=self.plan).count(), 0)

    def test_dismiss_removes_only_draft(self):
        self._create_draft()
        response = self.client.post(
            f'/studio/plans/{self.plan.pk}/draft-first-sprint/dismiss/',
        )

        self.assertRedirects(response, f'/studio/plans/{self.plan.pk}/edit/')
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.goal, '')
        self.assertEqual(FirstSprintPlanDraft.objects.filter(plan=self.plan).count(), 0)

    def test_visibility_update_accepts_cohort_and_rejects_invalid(self):
        response = self.client.post(
            f'/studio/plans/{self.plan.pk}/visibility/',
            {'visibility': 'cohort'},
        )
        self.assertRedirects(response, f'/studio/plans/{self.plan.pk}/')
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.visibility, 'cohort')

        response = self.client.post(
            f'/studio/plans/{self.plan.pk}/visibility/',
            {'visibility': 'public'},
        )
        self.assertRedirects(response, f'/studio/plans/{self.plan.pk}/')
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.visibility, 'cohort')

    def test_plans_list_member_filter_uses_people_picker_and_prefills(self):
        other = User.objects.create_user(email='other@test.com', password='pw')
        _make_plan(other, slug='july-studio-other')

        response = self.client.get(f'/studio/plans/?member={self.member.pk}')

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="plan-list-member-search"')
        self.assertContains(response, f"hidden.value = '{self.member.pk}'")
        self.assertContains(response, 'Member One')
        self.assertContains(response, 'member@test.com')
        self.assertNotContains(response, 'other@test.com')

    def test_editor_helper_copy_has_no_internal_todo(self):
        response = self.client.get(f'/studio/plans/{self.plan.pk}/edit/')
        self.assertNotContains(response, '#433')
        self.assertNotContains(response, 'TODO')
        self.assertNotContains(response, 'API in')
