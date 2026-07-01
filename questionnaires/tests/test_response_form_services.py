"""Tests for the shared member fill-in service helpers (issue #803).

These cover the reusable seam (#802 onboarding will reuse it):
``build_response_form_rows``, ``save_response_answers``,
``find_unanswered_required``.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase

from questionnaires.models import (
    Answer,
    AnswerOptionText,
    Question,
    Questionnaire,
    QuestionOption,
    Response,
)
from questionnaires.services import (
    AnswerSaveError,
    build_response_form_rows,
    build_response_questions,
    find_unanswered_required,
    save_response_answers,
)

User = get_user_model()


class _FakePost(dict):
    """Minimal QueryDict-like stand-in supporting getlist()."""

    def getlist(self, key):
        value = self.get(key)
        if value is None:
            return []
        if isinstance(value, list):
            return value
        return [value]


class SaveResponseAnswersTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(email='m@test.com', password='pw')
        cls.questionnaire = Questionnaire.objects.create(
            title='Feedback', slug='fb', purpose='feedback',
        )
        cls.q_text = Question.objects.create(
            questionnaire=cls.questionnaire, question_type='long_text',
            prompt='How did it go?', order=0, is_required=True,
        )
        cls.q_scale = Question.objects.create(
            questionnaire=cls.questionnaire, question_type='scale',
            prompt='Rate 1-5', order=1, scale_min=1, scale_max=5,
        )
        cls.q_choice = Question.objects.create(
            questionnaire=cls.questionnaire, question_type='single_choice',
            prompt='Join next?', order=2,
        )
        cls.opt_yes = QuestionOption.objects.create(
            question=cls.q_choice, label='Yes', order=0,
        )
        cls.opt_no = QuestionOption.objects.create(
            question=cls.q_choice, label='No', order=1,
        )
        cls.opt_other = QuestionOption.objects.create(
            question=cls.q_choice,
            label='Other',
            allows_free_text=True,
            order=2,
        )

    def setUp(self):
        self.response = Response.objects.create(
            questionnaire=self.questionnaire, respondent=self.user,
        )
        build_response_questions(self.response)
        self.rq_text = self.response.response_questions.get(
            source_question=self.q_text,
        )
        self.rq_scale = self.response.response_questions.get(
            source_question=self.q_scale,
        )
        self.rq_choice = self.response.response_questions.get(
            source_question=self.q_choice,
        )
        self.opt_yes_rq = self.rq_choice.options.get(label='Yes')
        self.opt_other_rq = self.rq_choice.options.get(label='Other')

    def test_saves_text_number_and_choice_answers(self):
        post = _FakePost({
            f'question_{self.rq_text.pk}': 'Great sprint',
            f'question_{self.rq_scale.pk}': '4',
            f'question_{self.rq_choice.pk}': str(self.opt_yes_rq.pk),
        })
        save_response_answers(self.response, post)
        text_answer = Answer.objects.get(response=self.response, question=self.rq_text)
        self.assertEqual(text_answer.text_value, 'Great sprint')
        scale_answer = Answer.objects.get(response=self.response, question=self.rq_scale)
        self.assertEqual(scale_answer.number_value, 4)
        choice_answer = Answer.objects.get(response=self.response, question=self.rq_choice)
        self.assertEqual(
            list(choice_answer.selected_options.values_list('label', flat=True)),
            ['Yes'],
        )

    def test_saves_choice_option_free_text_on_draft(self):
        post = _FakePost({
            f'question_{self.rq_choice.pk}': str(self.opt_other_rq.pk),
            (
                f'question_{self.rq_choice.pk}_option_'
                f'{self.opt_other_rq.pk}_text'
            ): 'Something else',
        })
        save_response_answers(self.response, post)
        answer = Answer.objects.get(response=self.response, question=self.rq_choice)
        self.assertEqual(answer.display_value, 'Other: Something else')
        self.assertEqual(
            AnswerOptionText.objects.get(answer=answer).text_value,
            'Something else',
        )

    def test_submit_requires_selected_free_text_option_text(self):
        post = _FakePost({
            f'question_{self.rq_choice.pk}': str(self.opt_other_rq.pk),
        })
        with self.assertRaises(AnswerSaveError) as ctx:
            save_response_answers(
                self.response, post, require_choice_free_text=True,
            )
        self.assertEqual(
            ctx.exception.field_errors[self.rq_choice.pk],
            'Describe your "Other" answer.',
        )
        self.assertFalse(Answer.objects.filter(response=self.response).exists())

    def test_save_is_repeatable_and_overwrites(self):
        save_response_answers(self.response, _FakePost({
            f'question_{self.rq_text.pk}': 'First',
        }))
        save_response_answers(self.response, _FakePost({
            f'question_{self.rq_text.pk}': 'Second',
        }))
        self.assertEqual(
            Answer.objects.filter(
                response=self.response, question=self.rq_text,
            ).count(),
            1,
        )
        answer = Answer.objects.get(response=self.response, question=self.rq_text)
        self.assertEqual(answer.text_value, 'Second')

    def test_out_of_range_scale_raises_and_persists_nothing(self):
        post = _FakePost({
            f'question_{self.rq_text.pk}': 'ok',
            f'question_{self.rq_scale.pk}': '9',
        })
        with self.assertRaises(AnswerSaveError) as ctx:
            save_response_answers(self.response, post)
        self.assertIn(self.rq_scale.pk, ctx.exception.field_errors)
        # Nothing persisted, not even the valid text field.
        self.assertEqual(Answer.objects.filter(response=self.response).count(), 0)

    def test_non_integer_scale_raises(self):
        post = _FakePost({f'question_{self.rq_scale.pk}': 'abc'})
        with self.assertRaises(AnswerSaveError) as ctx:
            save_response_answers(self.response, post)
        self.assertIn(self.rq_scale.pk, ctx.exception.field_errors)


class FindUnansweredRequiredTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(email='m@test.com', password='pw')
        cls.questionnaire = Questionnaire.objects.create(
            title='Feedback', slug='fb', purpose='feedback',
        )
        cls.q_required = Question.objects.create(
            questionnaire=cls.questionnaire, question_type='long_text',
            prompt='Required one', order=0, is_required=True,
        )
        cls.q_optional = Question.objects.create(
            questionnaire=cls.questionnaire, question_type='text',
            prompt='Optional one', order=1, is_required=False,
        )

    def setUp(self):
        self.response = Response.objects.create(
            questionnaire=self.questionnaire, respondent=self.user,
        )
        build_response_questions(self.response)
        self.rq_required = self.response.response_questions.get(
            source_question=self.q_required,
        )

    def test_required_unanswered_is_reported(self):
        missing = find_unanswered_required(self.response)
        self.assertEqual([rq.pk for rq in missing], [self.rq_required.pk])

    def test_required_answered_is_satisfied(self):
        save_response_answers(self.response, _FakePost({
            f'question_{self.rq_required.pk}': 'done',
        }))
        self.assertEqual(find_unanswered_required(self.response), [])

    def test_blank_whitespace_does_not_satisfy_required(self):
        save_response_answers(self.response, _FakePost({
            f'question_{self.rq_required.pk}': '   ',
        }))
        missing = find_unanswered_required(self.response)
        self.assertEqual([rq.pk for rq in missing], [self.rq_required.pk])


class BuildResponseFormRowsTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(email='m@test.com', password='pw')
        cls.questionnaire = Questionnaire.objects.create(
            title='Feedback', slug='fb', purpose='feedback',
        )
        cls.q_text = Question.objects.create(
            questionnaire=cls.questionnaire, question_type='text',
            prompt='Name?', order=0,
        )
        cls.q_choice = Question.objects.create(
            questionnaire=cls.questionnaire,
            question_type='single_choice',
            prompt='Path?',
            order=1,
        )
        QuestionOption.objects.create(
            question=cls.q_choice,
            label='Other',
            allows_free_text=True,
            order=0,
        )

    def setUp(self):
        self.response = Response.objects.create(
            questionnaire=self.questionnaire, respondent=self.user,
        )
        build_response_questions(self.response)
        self.rq_text = self.response.response_questions.get(
            source_question=self.q_text,
        )

    def test_prefills_existing_answer(self):
        save_response_answers(self.response, _FakePost({
            f'question_{self.rq_text.pk}': 'Alice',
        }))
        rows = build_response_form_rows(self.response)
        self.assertEqual(rows[0]['text_value'], 'Alice')

    def test_prefills_choice_option_free_text(self):
        rq_choice = self.response.response_questions.get(
            source_question=self.q_choice,
        )
        opt_other = rq_choice.options.get(label='Other')
        save_response_answers(self.response, _FakePost({
            f'question_{rq_choice.pk}': str(opt_other.pk),
            f'question_{rq_choice.pk}_option_{opt_other.pk}_text': 'Custom path',
        }))
        rows = build_response_form_rows(self.response)
        choice_row = next(row for row in rows if row['question'] == rq_choice)
        self.assertEqual(choice_row['options'][0]['free_text_value'], 'Custom path')
