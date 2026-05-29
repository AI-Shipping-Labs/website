"""Tests for sprint feedback distribution service (issue #803)."""

import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase

from plans.models import (
    Sprint,
    SprintEnrollment,
    SprintFeedbackRequest,
)
from plans.services import distribute_sprint_feedback
from questionnaires.models import (
    Question,
    Questionnaire,
    Response,
    ResponseQuestion,
)

User = get_user_model()


class DistributeSprintFeedbackTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.sprint = Sprint.objects.create(
            name='May 2026', slug='may-2026',
            start_date=datetime.date(2026, 5, 1), status='active',
        )
        cls.questionnaire = Questionnaire.objects.create(
            title='May Sprint Feedback', slug='may-feedback', purpose='feedback',
        )
        cls.q1 = Question.objects.create(
            questionnaire=cls.questionnaire, question_type='long_text',
            prompt='How did this sprint go for you?', order=0,
        )
        cls.members = [
            User.objects.create_user(email=f'm{i}@test.com', password='pw')
            for i in range(3)
        ]
        for m in cls.members:
            SprintEnrollment.objects.create(sprint=cls.sprint, user=m)
        cls.feedback_request = SprintFeedbackRequest.objects.create(
            sprint=cls.sprint, questionnaire=cls.questionnaire,
        )

    def test_creates_one_draft_response_per_enrolled_member(self):
        summary = distribute_sprint_feedback(self.feedback_request)
        self.assertEqual(summary['created'], 3)
        self.assertEqual(summary['existing'], 0)
        responses = Response.objects.filter(questionnaire=self.questionnaire)
        self.assertEqual(responses.count(), 3)
        self.assertTrue(all(r.status == 'draft' for r in responses))

    def test_materializes_questions_for_each_response(self):
        distribute_sprint_feedback(self.feedback_request)
        for member in self.members:
            response = Response.objects.get(
                questionnaire=self.questionnaire, respondent=member,
            )
            self.assertEqual(
                ResponseQuestion.objects.filter(response=response).count(), 1,
            )

    def test_stamps_distributed_at_on_first_run(self):
        self.assertIsNone(self.feedback_request.distributed_at)
        distribute_sprint_feedback(self.feedback_request)
        self.feedback_request.refresh_from_db()
        self.assertIsNotNone(self.feedback_request.distributed_at)

    def test_idempotent_no_duplicate_responses_or_questions(self):
        distribute_sprint_feedback(self.feedback_request)
        second = distribute_sprint_feedback(self.feedback_request)
        self.assertEqual(second['created'], 0)
        self.assertEqual(second['existing'], 3)
        self.assertEqual(
            Response.objects.filter(questionnaire=self.questionnaire).count(), 3,
        )
        # No duplicate ResponseQuestion rows (1 base question x 3 members).
        self.assertEqual(
            ResponseQuestion.objects.filter(
                response__questionnaire=self.questionnaire,
            ).count(),
            3,
        )

    def test_distributed_at_unchanged_on_rerun(self):
        distribute_sprint_feedback(self.feedback_request)
        self.feedback_request.refresh_from_db()
        first_stamp = self.feedback_request.distributed_at
        distribute_sprint_feedback(self.feedback_request)
        self.feedback_request.refresh_from_db()
        self.assertEqual(self.feedback_request.distributed_at, first_stamp)

    def test_late_enrollee_picked_up_on_rerun_without_disturbing_others(self):
        distribute_sprint_feedback(self.feedback_request)
        # An existing member answers their draft so we can confirm it's
        # untouched after the second run.
        existing_response = Response.objects.get(
            questionnaire=self.questionnaire, respondent=self.members[0],
        )

        late = User.objects.create_user(email='late@test.com', password='pw')
        SprintEnrollment.objects.create(sprint=self.sprint, user=late)

        summary = distribute_sprint_feedback(self.feedback_request)
        self.assertEqual(summary['created'], 1)
        self.assertEqual(summary['existing'], 3)
        # Late member now has a materialized response.
        late_response = Response.objects.get(
            questionnaire=self.questionnaire, respondent=late,
        )
        self.assertEqual(
            ResponseQuestion.objects.filter(response=late_response).count(), 1,
        )
        # The pre-existing response row is the same row (not recreated).
        existing_response.refresh_from_db()
        self.assertEqual(existing_response.status, 'draft')
        self.assertEqual(
            Response.objects.filter(
                questionnaire=self.questionnaire, respondent=self.members[0],
            ).count(),
            1,
        )
