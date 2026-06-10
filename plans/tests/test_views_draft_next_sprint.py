"""Studio view tests for the next-sprint draft action + dismiss (#891).

Covers the carry-over flash, the editor draft panel rendering, dismiss,
and access control (non-staff 403, GET rejected). The LLM is stubbed at
the service boundary.
"""

import datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from plans.models import (
    Checkpoint,
    NextSprintPlanDraft,
    Plan,
    Sprint,
    Week,
)
from plans.services.next_sprint_draft import NextSprintDraftResult

User = get_user_model()


def _make_plan(member, sprint, weeks=4):
    plan = Plan.objects.create(member=member, sprint=sprint)
    for n in range(1, weeks + 1):
        Week.objects.create(plan=plan, week_number=n, position=n - 1)
    return plan


def _draft_result():
    return NextSprintDraftResult(
        summary_current_situation='Prototype shipped',
        summary_goal='Evaluate it',
        summary_main_gap='No eval set',
        summary_weekly_hours='~6h',
        goal='Ship an evaluated pipeline',
        suggested_next_steps=['Build an eval set'],
        rationale='Updates show quality is unmeasured.',
    )


class DraftNextSprintViewTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.member = User.objects.create_user(email='m@test.com', password='pw')
        cls.s_may = Sprint.objects.create(
            name='May', slug='may',
            start_date=datetime.date(2026, 5, 1), duration_weeks=4,
        )
        cls.s_jun = Sprint.objects.create(
            name='Jun', slug='jun',
            start_date=datetime.date(2026, 6, 1), duration_weeks=4,
        )

    def setUp(self):
        self.client.force_login(self.staff)

    def _url(self, plan):
        return reverse('studio_plan_draft_next_sprint', kwargs={'plan_id': plan.pk})

    def test_carry_over_runs_and_flash_reports_count(self):
        source = _make_plan(self.member, self.s_may)
        Checkpoint.objects.create(
            week=source.weeks.get(week_number=1), description='Cp', position=0,
        )
        dest = _make_plan(self.member, self.s_jun)

        with patch(
            'plans.services.next_sprint_draft_service.llm.is_enabled',
            return_value=True,
        ), patch(
            'plans.services.next_sprint_draft_service.draft_next_sprint',
            return_value=_draft_result(),
        ):
            resp = self.client.post(self._url(dest), follow=True)

        self.assertEqual(resp.status_code, 200)
        # Redirected to the editor.
        self.assertEqual(resp.request['PATH_INFO'], reverse(
            'studio_plan_edit', kwargs={'plan_id': dest.pk},
        ))
        self.assertEqual(Checkpoint.objects.filter(week__plan=dest).count(), 1)
        body = resp.content.decode()
        self.assertIn('Carried over 1 task', body)

    def test_editor_renders_draft_panel_with_proposed_content(self):
        dest = _make_plan(self.member, self.s_jun)
        with patch(
            'plans.services.next_sprint_draft_service.llm.is_enabled',
            return_value=True,
        ), patch(
            'plans.services.next_sprint_draft_service.draft_next_sprint',
            return_value=_draft_result(),
        ):
            resp = self.client.post(self._url(dest), follow=True)

        self.assertContains(resp, 'next-sprint-draft-panel')
        self.assertContains(resp, 'AI draft for the next sprint')
        self.assertContains(resp, 'Ship an evaluated pipeline')
        self.assertContains(resp, 'Build an eval set')
        self.assertContains(resp, 'Updates show quality is unmeasured.')

    def test_llm_off_flash_says_skipped_and_no_panel(self):
        source = _make_plan(self.member, self.s_may)
        Checkpoint.objects.create(
            week=source.weeks.get(week_number=1), description='Cp', position=0,
        )
        dest = _make_plan(self.member, self.s_jun)
        with patch(
            'plans.services.next_sprint_draft_service.llm.is_enabled',
            return_value=False,
        ):
            resp = self.client.post(self._url(dest), follow=True)

        self.assertContains(resp, 'AI draft was skipped because AI is off')
        self.assertNotContains(resp, 'next-sprint-draft-panel')
        self.assertEqual(NextSprintPlanDraft.objects.filter(plan=dest).count(), 0)

    def test_no_prior_plan_flash_and_draft_from_state(self):
        dest = _make_plan(self.member, self.s_may)
        with patch(
            'plans.services.next_sprint_draft_service.llm.is_enabled',
            return_value=True,
        ), patch(
            'plans.services.next_sprint_draft_service.draft_next_sprint',
            return_value=_draft_result(),
        ):
            resp = self.client.post(self._url(dest), follow=True)

        self.assertContains(resp, 'No previous plan to carry over from')
        self.assertContains(resp, 'next-sprint-draft-panel')

    def test_get_rejected(self):
        dest = _make_plan(self.member, self.s_jun)
        resp = self.client.get(self._url(dest))
        self.assertEqual(resp.status_code, 405)

    def test_non_staff_denied_and_no_draft_created(self):
        self.client.force_login(self.member)
        dest = _make_plan(self.member, self.s_jun)
        resp = self.client.post(self._url(dest))
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(NextSprintPlanDraft.objects.filter(plan=dest).count(), 0)


class DraftDismissViewTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.member = User.objects.create_user(email='m@test.com', password='pw')
        cls.sprint = Sprint.objects.create(
            name='Jun', slug='jun',
            start_date=datetime.date(2026, 6, 1), duration_weeks=4,
        )

    def setUp(self):
        self.client.force_login(self.staff)
        self.plan = _make_plan(self.member, self.sprint)
        NextSprintPlanDraft.objects.create(
            plan=self.plan,
            result_json=_draft_result().model_dump(),
            generated_at=timezone.now(),
        )

    def _url(self):
        return reverse(
            'studio_plan_draft_next_sprint_dismiss',
            kwargs={'plan_id': self.plan.pk},
        )

    def test_dismiss_deletes_draft_and_panel_gone(self):
        resp = self.client.post(self._url(), follow=True)
        self.assertEqual(
            NextSprintPlanDraft.objects.filter(plan=self.plan).count(), 0,
        )
        self.assertNotContains(resp, 'next-sprint-draft-panel')

    def test_dismiss_get_rejected(self):
        resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, 405)

    def test_dismiss_non_staff_denied(self):
        self.client.force_login(self.member)
        resp = self.client.post(self._url())
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(
            NextSprintPlanDraft.objects.filter(plan=self.plan).count(), 1,
        )
