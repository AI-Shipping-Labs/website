"""Model tests for the questionnaire system (issue #800)."""

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from questionnaires.models import (
    Answer,
    Question,
    Questionnaire,
    QuestionOption,
    Response,
    ResponseQuestion,
    ResponseQuestionOption,
)
from questionnaires.response_workflows import transition_response_review

User = get_user_model()


class QuestionnaireModelTest(TestCase):
    def test_slug_derived_from_title_when_blank(self):
        q = Questionnaire.objects.create(title='May Sprint Feedback')
        self.assertEqual(q.slug, 'may-sprint-feedback')

    def test_explicit_slug_is_preserved(self):
        q = Questionnaire.objects.create(title='May Sprint Feedback', slug='custom-slug')
        self.assertEqual(q.slug, 'custom-slug')

    def test_default_purpose_is_general(self):
        q = Questionnaire.objects.create(title='Untitled')
        self.assertEqual(q.purpose, 'general')

    def test_question_and_response_counts(self):
        q = Questionnaire.objects.create(title='Counts')
        Question.objects.create(questionnaire=q, question_type='text', prompt='A')
        Question.objects.create(questionnaire=q, question_type='text', prompt='B')
        member = User.objects.create_user(email='m@test.com', password='pw')
        Response.objects.create(questionnaire=q, respondent=member)
        self.assertEqual(q.question_count, 2)
        self.assertEqual(q.response_count, 1)


class QuestionModelTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.questionnaire = Questionnaire.objects.create(title='Q')

    def test_all_six_types_are_valid_choices(self):
        valid = {value for value, _ in Question._meta.get_field('question_type').choices}
        self.assertEqual(
            valid,
            {'text', 'long_text', 'single_choice', 'multiple_choice', 'scale', 'number'},
        )

    def test_is_choice_type(self):
        single = Question.objects.create(
            questionnaire=self.questionnaire, question_type='single_choice', prompt='A',
        )
        text = Question.objects.create(
            questionnaire=self.questionnaire, question_type='text', prompt='B',
        )
        self.assertTrue(single.is_choice_type)
        self.assertFalse(text.is_choice_type)

    def test_questions_ordered_by_order_then_id(self):
        q3 = Question.objects.create(
            questionnaire=self.questionnaire, question_type='text', prompt='third', order=3,
        )
        q1 = Question.objects.create(
            questionnaire=self.questionnaire, question_type='text', prompt='first', order=1,
        )
        ordered = list(self.questionnaire.questions.all())
        self.assertEqual(ordered, [q1, q3])

    def test_options_ordered_by_order(self):
        question = Question.objects.create(
            questionnaire=self.questionnaire, question_type='multiple_choice', prompt='A',
        )
        b = QuestionOption.objects.create(question=question, label='B', order=2)
        a = QuestionOption.objects.create(question=question, label='A', order=1)
        self.assertEqual(list(question.options.all()), [a, b])


class ResponseModelTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.questionnaire = Questionnaire.objects.create(title='Q')
        cls.member = User.objects.create_user(email='m@test.com', password='pw')

    def test_unique_response_per_respondent(self):
        Response.objects.create(questionnaire=self.questionnaire, respondent=self.member)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Response.objects.create(
                    questionnaire=self.questionnaire, respondent=self.member,
                )

    def test_mark_submitted_sets_status_and_timestamp(self):
        response = Response.objects.create(
            questionnaire=self.questionnaire, respondent=self.member,
        )
        self.assertEqual(response.status, 'draft')
        self.assertIsNone(response.submitted_at)
        response.mark_submitted()
        response.refresh_from_db()
        self.assertEqual(response.status, 'submitted')
        self.assertIsNotNone(response.submitted_at)


class ResponseQuestionModelTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.questionnaire = Questionnaire.objects.create(title='Q')
        cls.member = User.objects.create_user(email='m@test.com', password='pw')
        cls.response = Response.objects.create(
            questionnaire=cls.questionnaire, respondent=cls.member,
        )

    def test_snapshot_links_back_to_source_question(self):
        base = Question.objects.create(
            questionnaire=self.questionnaire, question_type='long_text', prompt='Base',
        )
        rq = ResponseQuestion.objects.create(
            response=self.response, source_question=base,
            question_type='long_text', prompt='Base',
        )
        self.assertEqual(rq.source_question, base)
        self.assertFalse(rq.is_custom)

    def test_custom_question_has_no_source(self):
        rq = ResponseQuestion.objects.create(
            response=self.response, source_question=None,
            question_type='text', prompt='Custom one-off',
        )
        self.assertTrue(rq.is_custom)

    def test_deleting_base_question_nulls_source_keeps_snapshot(self):
        base = Question.objects.create(
            questionnaire=self.questionnaire, question_type='text', prompt='Base',
        )
        rq = ResponseQuestion.objects.create(
            response=self.response, source_question=base,
            question_type='text', prompt='Snapshot text',
        )
        base.delete()
        rq.refresh_from_db()
        self.assertIsNone(rq.source_question)
        self.assertEqual(rq.prompt, 'Snapshot text')


class AnswerModelTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.questionnaire = Questionnaire.objects.create(title='Q')
        cls.member = User.objects.create_user(email='m@test.com', password='pw')
        cls.response = Response.objects.create(
            questionnaire=cls.questionnaire, respondent=cls.member,
        )

    def _rq(self, question_type, prompt='Q'):
        return ResponseQuestion.objects.create(
            response=self.response, question_type=question_type, prompt=prompt,
        )

    def test_unique_answer_per_question(self):
        rq = self._rq('text')
        Answer.objects.create(response=self.response, question=rq, text_value='one')
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Answer.objects.create(response=self.response, question=rq, text_value='two')

    def test_display_value_text(self):
        rq = self._rq('long_text')
        answer = Answer.objects.create(response=self.response, question=rq, text_value='Hello')
        self.assertEqual(answer.display_value, 'Hello')

    def test_display_value_number(self):
        rq = self._rq('number')
        answer = Answer.objects.create(response=self.response, question=rq, number_value=12)
        self.assertEqual(answer.display_value, '12')

    def test_display_value_choices(self):
        rq = self._rq('multiple_choice')
        opt_a = ResponseQuestionOption.objects.create(response_question=rq, label='RAG', order=0)
        opt_b = ResponseQuestionOption.objects.create(response_question=rq, label='Agents', order=1)
        answer = Answer.objects.create(response=self.response, question=rq)
        answer.selected_options.add(opt_a, opt_b)
        self.assertEqual(answer.display_value, 'RAG, Agents')

    def test_display_value_blank_when_empty(self):
        rq = self._rq('text')
        answer = Answer.objects.create(response=self.response, question=rq)
        self.assertEqual(answer.display_value, '')


class ResponseReviewStateTest(TestCase):
    def setUp(self):
        self.questionnaire = Questionnaire.objects.create(title='Review state')
        self.member = User.objects.create_user(email='review-member@test.com')
        self.staff = User.objects.create_user(
            email='review-staff@test.com', is_staff=True,
        )
        self.response = Response.objects.create(
            questionnaire=self.questionnaire, respondent=self.member,
        )

    def test_mark_submitted_always_requeues(self):
        self.response.status = 'submitted'
        self.response.reviewed_at = timezone.now()
        self.response.reviewed_by = self.staff
        self.response.save()
        self.response.mark_submitted()
        self.assertEqual(self.response.review_state, 'awaiting')
        self.assertIsNone(self.response.reviewed_at)
        self.assertIsNone(self.response.reviewed_by)

    def test_database_rejects_review_fields_on_draft(self):
        self.response.reviewed_at = timezone.now()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self.response.save()

    def test_database_rejects_reviewer_without_timestamp(self):
        self.response.status = 'submitted'
        self.response.reviewed_by = self.staff
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self.response.save()

    def test_deleted_reviewer_preserves_review_timestamp_and_historical_label(self):
        self.response.mark_submitted()
        transition_response_review(
            response_id=self.response.pk, reviewed=True, actor=self.staff,
        )
        reviewed_at = Response.objects.get(pk=self.response.pk).reviewed_at
        self.staff.delete()
        self.response.refresh_from_db()
        self.assertEqual(self.response.reviewed_at, reviewed_at)
        self.assertIsNone(self.response.reviewed_by)
        self.assertEqual(self.response.review_label, 'Reviewed before queue launch')

    def test_queue_index_and_review_state_semantics(self):
        index = next(
            item for item in Response._meta.indexes
            if item.name == 'response_review_queue_idx'
        )
        self.assertEqual(index.fields, ['status', 'reviewed_at', '-submitted_at'])
        self.assertEqual(self.response.review_state, 'not_applicable')
        self.response.mark_submitted()
        self.assertEqual(self.response.review_state, 'awaiting')
        transition_response_review(
            response_id=self.response.pk, reviewed=True, actor=self.staff,
        )
        self.response.refresh_from_db()
        self.assertEqual(self.response.review_state, 'reviewed')
