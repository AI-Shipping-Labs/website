"""Tests for ``questionnaires.services.build_response_questions`` (issue #800)."""

from django.contrib.auth import get_user_model
from django.test import TestCase

from questionnaires.models import (
    Question,
    Questionnaire,
    QuestionOption,
    Response,
)
from questionnaires.services import build_response_questions

User = get_user_model()


class BuildResponseQuestionsTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.questionnaire = Questionnaire.objects.create(title='Onboarding')
        cls.member = User.objects.create_user(email='m@test.com', password='pw')

        cls.text_q = Question.objects.create(
            questionnaire=cls.questionnaire, question_type='long_text',
            prompt='Goals?', help_text='Be specific', is_required=True, order=0,
        )
        cls.scale_q = Question.objects.create(
            questionnaire=cls.questionnaire, question_type='scale',
            prompt='Confidence', order=1, scale_min=1, scale_max=5,
        )
        cls.choice_q = Question.objects.create(
            questionnaire=cls.questionnaire, question_type='multiple_choice',
            prompt='Focus areas', order=2,
        )
        QuestionOption.objects.create(question=cls.choice_q, label='RAG', order=0)
        QuestionOption.objects.create(question=cls.choice_q, label='Agents', order=1)

    def _make_response(self):
        return Response.objects.create(
            questionnaire=self.questionnaire, respondent=self.member,
        )

    def test_copies_all_base_questions_with_snapshot_fields(self):
        response = self._make_response()
        build_response_questions(response)

        rqs = list(response.response_questions.all())
        self.assertEqual(len(rqs), 3)

        text_rq = rqs[0]
        self.assertEqual(text_rq.source_question, self.text_q)
        self.assertEqual(text_rq.question_type, 'long_text')
        self.assertEqual(text_rq.prompt, 'Goals?')
        self.assertEqual(text_rq.help_text, 'Be specific')
        self.assertTrue(text_rq.is_required)

        scale_rq = rqs[1]
        self.assertEqual(scale_rq.scale_min, 1)
        self.assertEqual(scale_rq.scale_max, 5)

    def test_copies_choice_options(self):
        response = self._make_response()
        build_response_questions(response)

        choice_rq = response.response_questions.get(source_question=self.choice_q)
        labels = list(choice_rq.options.values_list('label', flat=True))
        self.assertEqual(labels, ['RAG', 'Agents'])
        for opt in choice_rq.options.all():
            self.assertIsNotNone(opt.source_option)

    def test_idempotent_does_not_duplicate(self):
        response = self._make_response()
        build_response_questions(response)
        created_again = build_response_questions(response)

        self.assertEqual(created_again, [])
        self.assertEqual(response.response_questions.count(), 3)

    def test_returns_created_response_questions(self):
        response = self._make_response()
        created = build_response_questions(response)
        self.assertEqual(len(created), 3)

    def test_later_base_edit_does_not_rewrite_existing_snapshot(self):
        response = self._make_response()
        build_response_questions(response)

        self.text_q.prompt = 'Changed prompt'
        self.text_q.save()

        snapshot = response.response_questions.get(source_question=self.text_q)
        self.assertEqual(snapshot.prompt, 'Goals?')
