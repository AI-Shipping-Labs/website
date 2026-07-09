"""Dashboard plan-preparation waiting state tests (issue #1199)."""

import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.urls import reverse
from django.utils import timezone

from plans.models import Plan, Sprint, SprintEnrollment
from questionnaires.models import Questionnaire, Response
from tests.fixtures import TierSetupMixin

User = get_user_model()


@tag("core")
class DashboardPlanPreparationStateTest(TierSetupMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.questionnaire = Questionnaire.objects.create(
            title="Onboarding",
            slug="dashboard-plan-prep-onboarding",
            purpose="onboarding",
        )

    def _member(self, email="member@test.com"):
        return User.objects.create_user(
            email=email,
            password="pw",
            tier=self.main_tier,
            email_verified=True,
        )

    def _submit_onboarding(self, user):
        return Response.objects.create(
            respondent=user,
            questionnaire=self.questionnaire,
            status="submitted",
            submitted_at=timezone.now(),
        )

    def _sprint(self, slug, *, status="active"):
        return Sprint.objects.create(
            name=slug.replace("-", " ").title(),
            slug=slug,
            start_date=datetime.date.today() - datetime.timedelta(days=7),
            duration_weeks=6,
            status=status,
            min_tier_level=20,
        )

    def test_submitted_onboarding_without_plan_shows_preparing_card(self):
        user = self._member("submitted-no-plan@test.com")
        self._submit_onboarding(user)
        self.client.force_login(user)

        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response, 'data-testid="dashboard-plan-preparing-card"',
        )
        self.assertContains(response, "Your plan is being prepared")
        self.assertContains(response, "Alexey and Valeria")
        self.assertContains(response, "1-2 business days")
        self.assertContains(response, "bell")
        self.assertContains(response, "email")
        self.assertContains(response, reverse("onboarding_start"))
        self.assertContains(response, "Review onboarding answers")
        self.assertNotContains(response, 'data-testid="onboarding-prompt"')
        self.assertNotContains(
            response, 'data-testid="account-sprint-plan-card"',
        )
        self.assertNotContains(response, "Open my plan")

    def test_unshared_staff_draft_shows_preparing_without_plan_cta(self):
        user = self._member("submitted-draft@test.com")
        self._submit_onboarding(user)
        draft_sprint = self._sprint("internal-draft-sprint", status="completed")
        Plan.objects.create(
            member=user,
            sprint=draft_sprint,
            goal="Internal staff draft goal",
        )
        self.client.force_login(user)

        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["plan"])
        self.assertTrue(response.context["has_any_plan"])
        self.assertContains(
            response, 'data-testid="dashboard-plan-preparing-card"',
        )
        self.assertNotContains(
            response, 'data-testid="account-sprint-plan-card"',
        )
        self.assertNotContains(response, "Open my plan")
        self.assertNotContains(response, "Internal staff draft goal")
        self.assertNotContains(response, "Internal Draft Sprint")

    def test_unshared_active_draft_cohort_is_hidden_from_sprint_opportunities(self):
        user = self._member("submitted-active-draft@test.com")
        self._submit_onboarding(user)
        draft_sprint = self._sprint("draft-1199", status="active")
        SprintEnrollment.objects.create(user=user, sprint=draft_sprint)
        Plan.objects.create(
            member=user,
            sprint=draft_sprint,
            goal="Draft cohort that should stay hidden",
        )
        self.client.force_login(user)

        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response, 'data-testid="dashboard-plan-preparing-card"',
        )
        self.assertEqual(response.context["active_sprint_opportunities"], [])
        self.assertNotContains(response, "Draft 1199")
        self.assertNotContains(
            response,
            reverse("cohort_board", kwargs={"sprint_slug": draft_sprint.slug}),
        )
        self.assertNotContains(response, "View cohort")

    def test_shared_plan_wins_over_newer_unshared_draft(self):
        user = self._member("shared-plus-draft@test.com")
        self._submit_onboarding(user)
        shared_sprint = self._sprint("shared-member-sprint")
        draft_sprint = self._sprint("newer-staff-draft", status="completed")
        shared_plan = Plan.objects.create(
            member=user,
            sprint=shared_sprint,
            shared_at=timezone.now(),
        )
        Plan.objects.create(
            member=user,
            sprint=draft_sprint,
            goal="New unshared staff draft",
        )
        self.client.force_login(user)

        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["plan"].pk, shared_plan.pk)
        self.assertContains(
            response, 'data-testid="account-sprint-plan-card"',
        )
        self.assertContains(response, "Your sprint plan")
        self.assertContains(response, "Open my plan")
        self.assertNotContains(
            response, 'data-testid="dashboard-plan-preparing-card"',
        )
        self.assertNotContains(response, 'data-testid="onboarding-prompt"')
        self.assertNotContains(response, "New unshared staff draft")

    def test_any_existing_plan_suppresses_onboarding_prompt_before_submit(self):
        user = self._member("draft-before-onboarding@test.com")
        sprint = self._sprint("draft-before-onboarding", status="completed")
        Plan.objects.create(member=user, sprint=sprint)
        self.client.force_login(user)

        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["has_any_plan"])
        self.assertNotContains(response, 'data-testid="onboarding-prompt"')
        self.assertNotContains(
            response, 'data-testid="dashboard-plan-preparing-card"',
        )

    def test_eligible_paid_member_without_onboarding_or_plan_keeps_prompt(self):
        user = self._member("needs-onboarding@test.com")
        self.client.force_login(user)

        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["has_any_plan"])
        self.assertContains(response, 'data-testid="onboarding-prompt"')
        self.assertContains(response, "Tell us a bit about you")
        self.assertNotContains(
            response, 'data-testid="dashboard-plan-preparing-card"',
        )
