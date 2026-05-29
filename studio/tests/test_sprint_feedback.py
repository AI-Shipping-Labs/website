"""Studio sprint feedback attach / distribute / completion (issue #803)."""

import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from plans.models import (
    Sprint,
    SprintEnrollment,
    SprintFeedbackRequest,
)
from plans.services import distribute_sprint_feedback
from questionnaires.models import Question, Questionnaire, Response

User = get_user_model()


class _FeedbackStudioMixin:
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )
        cls.member = User.objects.create_user(email='m1@test.com', password='pw')
        cls.sprint = Sprint.objects.create(
            name='May 2026', slug='may-2026',
            start_date=datetime.date(2026, 5, 1), status='active',
        )
        SprintEnrollment.objects.create(sprint=cls.sprint, user=cls.member)
        cls.questionnaire = Questionnaire.objects.create(
            title='May Sprint Feedback', slug='may-feedback', purpose='feedback',
        )
        cls.q = Question.objects.create(
            questionnaire=cls.questionnaire, question_type='long_text',
            prompt='How did it go?', order=0,
        )

    def _detail_url(self):
        return reverse('studio_sprint_detail', kwargs={'sprint_id': self.sprint.pk})

    def _attach_url(self):
        return reverse('studio_sprint_feedback_attach', kwargs={
            'sprint_id': self.sprint.pk,
        })


class FeedbackAttachTest(_FeedbackStudioMixin, TestCase):
    def test_empty_state_offers_attach_when_none_attached(self):
        self.client.force_login(self.staff)
        page = self.client.get(self._detail_url())
        self.assertContains(page, 'data-testid="sprint-feedback-empty"')
        self.assertContains(page, 'data-testid="sprint-feedback-attach-button"')

    def test_attach_creates_feedback_request(self):
        self.client.force_login(self.staff)
        response = self.client.post(self._attach_url(), {
            'questionnaire': self.questionnaire.pk,
        })
        self.assertEqual(response.status_code, 302)
        fr = SprintFeedbackRequest.objects.get(sprint=self.sprint)
        self.assertEqual(fr.questionnaire, self.questionnaire)
        self.assertEqual(fr.created_by, self.staff)

    def test_attach_picker_only_offers_active_feedback_questionnaires(self):
        Questionnaire.objects.create(
            title='Onboarding', slug='onb', purpose='onboarding',
        )
        Questionnaire.objects.create(
            title='Inactive FB', slug='inactive-fb', purpose='feedback',
            is_active=False,
        )
        self.client.force_login(self.staff)
        page = self.client.get(self._detail_url())
        self.assertContains(page, 'May Sprint Feedback')
        self.assertNotContains(page, 'Onboarding')
        self.assertNotContains(page, 'Inactive FB')

    def test_attach_non_feedback_questionnaire_rejected_400_no_row(self):
        onboarding = Questionnaire.objects.create(
            title='Onboarding', slug='onb', purpose='onboarding',
        )
        self.client.force_login(self.staff)
        response = self.client.post(self._attach_url(), {
            'questionnaire': onboarding.pk,
        })
        self.assertEqual(response.status_code, 400)
        self.assertContains(
            response, 'not an active feedback questionnaire', status_code=400,
        )
        self.assertFalse(SprintFeedbackRequest.objects.exists())

    def test_attach_missing_questionnaire_rejected_400(self):
        self.client.force_login(self.staff)
        response = self.client.post(self._attach_url(), {})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(SprintFeedbackRequest.objects.exists())


class FeedbackDistributeAndCompletionTest(_FeedbackStudioMixin, TestCase):
    def setUp(self):
        self.fr = SprintFeedbackRequest.objects.create(
            sprint=self.sprint, questionnaire=self.questionnaire,
            created_by=self.staff,
        )

    def _distribute_url(self):
        return reverse('studio_sprint_feedback_distribute', kwargs={
            'sprint_id': self.sprint.pk, 'feedback_request_id': self.fr.pk,
        })

    def test_distribute_creates_responses_and_shows_completion(self):
        self.client.force_login(self.staff)
        response = self.client.post(self._distribute_url(), follow=True)
        self.assertEqual(Response.objects.count(), 1)
        self.assertContains(response, '0 of 1 submitted')
        self.assertContains(response, 'data-testid="sprint-feedback-completion-row"')

    def test_completion_reflects_submitted_member_with_link(self):
        distribute_sprint_feedback(self.fr)
        member_response = Response.objects.get(respondent=self.member)
        member_response.mark_submitted()
        self.client.force_login(self.staff)
        page = self.client.get(self._detail_url())
        self.assertContains(page, '1 of 1 submitted')
        self.assertContains(page, 'data-testid="sprint-feedback-response-link"')
        link = reverse('studio_questionnaire_response_detail', kwargs={
            'questionnaire_id': self.questionnaire.pk,
            'response_id': member_response.pk,
        })
        self.assertContains(page, link)


class FeedbackStudioAccessControlTest(_FeedbackStudioMixin, TestCase):
    def setUp(self):
        self.fr = SprintFeedbackRequest.objects.create(
            sprint=self.sprint, questionnaire=self.questionnaire,
        )

    def test_anonymous_attach_redirects_to_login(self):
        response = self.client.post(self._attach_url(), {
            'questionnaire': self.questionnaire.pk,
        })
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response['Location'])
        self.assertFalse(SprintFeedbackRequest.objects.filter(
            distributed_at__isnull=False,
        ).exists())

    def test_non_staff_distribute_forbidden_no_responses(self):
        self.client.force_login(self.member)
        url = reverse('studio_sprint_feedback_distribute', kwargs={
            'sprint_id': self.sprint.pk, 'feedback_request_id': self.fr.pk,
        })
        response = self.client.post(url)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(Response.objects.count(), 0)
