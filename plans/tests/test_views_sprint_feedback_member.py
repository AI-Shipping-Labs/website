"""Tests for member-facing sprint feedback fill-in / submit (issue #803)."""

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


class _FeedbackFixtureMixin:
    @classmethod
    def setUpTestData(cls):
        cls.sprint = Sprint.objects.create(
            name='May 2026', slug='may-2026',
            start_date=datetime.date(2026, 5, 1), status='active',
        )
        cls.questionnaire = Questionnaire.objects.create(
            title='May Sprint Feedback', slug='may-feedback', purpose='feedback',
        )
        cls.q_required = Question.objects.create(
            questionnaire=cls.questionnaire, question_type='long_text',
            prompt='How did this sprint go for you?', order=0, is_required=True,
        )
        cls.q_optional = Question.objects.create(
            questionnaire=cls.questionnaire, question_type='text',
            prompt='Anything else?', order=1, is_required=False,
        )
        cls.member = User.objects.create_user(email='member@test.com', password='pw')
        cls.other = User.objects.create_user(email='other@test.com', password='pw')
        SprintEnrollment.objects.create(sprint=cls.sprint, user=cls.member)
        SprintEnrollment.objects.create(sprint=cls.sprint, user=cls.other)
        cls.feedback_request = SprintFeedbackRequest.objects.create(
            sprint=cls.sprint, questionnaire=cls.questionnaire,
        )
        distribute_sprint_feedback(cls.feedback_request)
        cls.member_response = Response.objects.get(
            questionnaire=cls.questionnaire, respondent=cls.member,
        )
        cls.other_response = Response.objects.get(
            questionnaire=cls.questionnaire, respondent=cls.other,
        )
        cls.rq_required = cls.member_response.response_questions.get(
            source_question=cls.q_required,
        )
        cls.rq_optional = cls.member_response.response_questions.get(
            source_question=cls.q_optional,
        )

    def _fill_url(self, response):
        return reverse('sprint_feedback_fill', kwargs={
            'sprint_slug': self.sprint.slug, 'response_id': response.pk,
        })

    def _submit_url(self, response):
        return reverse('sprint_feedback_submit', kwargs={
            'sprint_slug': self.sprint.slug, 'response_id': response.pk,
        })


class SprintFeedbackAccessTest(_FeedbackFixtureMixin, TestCase):
    def test_anonymous_redirected_to_login(self):
        response = self.client.get(self._fill_url(self.member_response))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response['Location'])

    def test_member_cannot_open_another_members_response(self):
        self.client.force_login(self.member)
        response = self.client.get(self._fill_url(self.other_response))
        self.assertEqual(response.status_code, 404)
        self.assertNotContains(response, 'other@test.com', status_code=404)

    def test_member_can_open_own_response(self):
        self.client.force_login(self.member)
        response = self.client.get(self._fill_url(self.member_response))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'How did this sprint go for you?')


class SprintFeedbackFillTest(_FeedbackFixtureMixin, TestCase):
    def test_save_draft_persists_and_can_repeat(self):
        self.client.force_login(self.member)
        self.client.post(self._fill_url(self.member_response), {
            f'question_{self.rq_required.pk}': 'Going well',
        })
        self.member_response.refresh_from_db()
        self.assertEqual(self.member_response.status, 'draft')
        answer = self.member_response.answers.get(question=self.rq_required)
        self.assertEqual(answer.text_value, 'Going well')

        # Reopen: previously answered question is pre-filled.
        page = self.client.get(self._fill_url(self.member_response))
        self.assertContains(page, 'Going well')

    def test_submit_with_required_blank_re_renders_400_and_stays_draft(self):
        self.client.force_login(self.member)
        response = self.client.post(self._submit_url(self.member_response), {
            f'question_{self.rq_required.pk}': '',
        })
        self.assertEqual(response.status_code, 400)
        self.assertContains(
            response, 'How did this sprint go for you?', status_code=400,
        )
        self.member_response.refresh_from_db()
        self.assertEqual(self.member_response.status, 'draft')
        self.assertIsNone(self.member_response.submitted_at)

    def test_submit_answered_marks_submitted_and_redirects(self):
        self.client.force_login(self.member)
        response = self.client.post(self._submit_url(self.member_response), {
            f'question_{self.rq_required.pk}': 'It went great',
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response['Location'],
            reverse('sprint_detail', kwargs={'sprint_slug': self.sprint.slug}),
        )
        self.member_response.refresh_from_db()
        self.assertEqual(self.member_response.status, 'submitted')
        self.assertIsNotNone(self.member_response.submitted_at)

    def test_submitted_response_is_read_only(self):
        self.member_response.mark_submitted()
        self.client.force_login(self.member)
        page = self.client.get(self._fill_url(self.member_response))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'Feedback submitted')
        # The editable form is not rendered for a submitted response.
        self.assertNotContains(page, 'data-testid="questionnaire-submit-button"')


class SprintFeedbackCtaTest(_FeedbackFixtureMixin, TestCase):
    def test_enrolled_member_sees_feedback_cta_with_next_sprint_copy(self):
        self.client.force_login(self.member)
        page = self.client.get(
            reverse('sprint_detail', kwargs={'sprint_slug': self.sprint.slug}),
        )
        self.assertContains(page, 'data-testid="sprint-feedback-cta-link"')
        self.assertContains(page, 'shape the next one')

    def test_submitted_member_sees_confirmation_instead_of_cta(self):
        self.member_response.mark_submitted()
        self.client.force_login(self.member)
        page = self.client.get(
            reverse('sprint_detail', kwargs={'sprint_slug': self.sprint.slug}),
        )
        self.assertContains(page, 'data-testid="sprint-feedback-cta-submitted"')
        self.assertNotContains(page, 'data-testid="sprint-feedback-cta-link"')

    def test_no_cta_before_distribution(self):
        # A second sprint with an attached-but-undistributed questionnaire.
        sprint2 = Sprint.objects.create(
            name='June 2026', slug='june-2026',
            start_date=datetime.date(2026, 6, 1), status='active',
        )
        SprintEnrollment.objects.create(sprint=sprint2, user=self.member)
        SprintFeedbackRequest.objects.create(
            sprint=sprint2, questionnaire=self.questionnaire,
        )
        self.client.force_login(self.member)
        page = self.client.get(
            reverse('sprint_detail', kwargs={'sprint_slug': sprint2.slug}),
        )
        self.assertNotContains(page, 'data-testid="sprint-feedback-cta-section"')
