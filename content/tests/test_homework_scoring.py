"""Auto-scoring coverage -- issue #1683.

Ported from community_base.coursework.answer_checks with unchanged logic:
MC/checkboxes compare 1-based option indices, typed free-form answers are
checked per answer_type.
"""

import datetime
from types import SimpleNamespace

from django.test import TestCase
from django.utils import timezone

from content.models import Course
from content.models.cohort import Cohort
from content.models.homework import AnswerType, Homework, Question, QuestionType
from content.services.homework_scoring import is_answer_correct, score_answer


def _question(**kwargs):
    """Build an unsaved Question-like object for pure scoring-function tests."""
    defaults = {
        'question_type': QuestionType.MULTIPLE_CHOICE,
        'answer_type': '',
        'correct_answer': '',
        'scores_for_correct_answer': 1,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


class MultipleChoiceScoringTest(TestCase):
    def test_matching_index_is_correct(self):
        q = _question(question_type=QuestionType.MULTIPLE_CHOICE, correct_answer='3')
        self.assertTrue(is_answer_correct(q, '3'))

    def test_non_matching_index_is_incorrect(self):
        q = _question(question_type=QuestionType.MULTIPLE_CHOICE, correct_answer='3')
        self.assertFalse(is_answer_correct(q, '2'))

    def test_blank_answer_is_incorrect(self):
        q = _question(question_type=QuestionType.MULTIPLE_CHOICE, correct_answer='3')
        self.assertFalse(is_answer_correct(q, ''))
        self.assertFalse(is_answer_correct(q, None))


class CheckboxesScoringTest(TestCase):
    def test_exact_set_match_is_correct_regardless_of_order(self):
        q = _question(question_type=QuestionType.CHECKBOXES, correct_answer='1,3')
        self.assertTrue(is_answer_correct(q, '3,1'))

    def test_partial_set_is_incorrect(self):
        q = _question(question_type=QuestionType.CHECKBOXES, correct_answer='1,3')
        self.assertFalse(is_answer_correct(q, '1'))

    def test_extra_selection_is_incorrect(self):
        q = _question(question_type=QuestionType.CHECKBOXES, correct_answer='1,3')
        self.assertFalse(is_answer_correct(q, '1,2,3'))


class TypedFreeFormScoringTest(TestCase):
    def test_answer_type_any_scores_any_non_empty_answer_correct(self):
        q = _question(
            question_type=QuestionType.FREE_FORM, answer_type=AnswerType.ANY,
            correct_answer='',
        )
        self.assertTrue(is_answer_correct(q, 'anything at all'))
        self.assertFalse(is_answer_correct(q, ''))

    def test_answer_type_float_compares_numerically(self):
        q = _question(
            question_type=QuestionType.FREE_FORM, answer_type=AnswerType.FLOAT,
            correct_answer='3.14',
        )
        self.assertTrue(is_answer_correct(q, '3.140'))
        self.assertFalse(is_answer_correct(q, '3.15'))
        self.assertFalse(is_answer_correct(q, 'not a number'))

    def test_answer_type_integer_compares_numerically(self):
        q = _question(
            question_type=QuestionType.FREE_FORM, answer_type=AnswerType.INTEGER,
            correct_answer='42',
        )
        self.assertTrue(is_answer_correct(q, '42'))
        self.assertFalse(is_answer_correct(q, '43'))

    def test_answer_type_exact_string_is_case_sensitive(self):
        q = _question(
            question_type=QuestionType.FREE_FORM, answer_type=AnswerType.EXACT_STRING,
            correct_answer='Django',
        )
        self.assertTrue(is_answer_correct(q, 'Django'))
        self.assertFalse(is_answer_correct(q, 'django'))

    def test_answer_type_contains_string_is_case_insensitive_substring(self):
        q = _question(
            question_type=QuestionType.FREE_FORM, answer_type=AnswerType.CONTAINS_STRING,
            correct_answer='vector',
        )
        self.assertTrue(is_answer_correct(q, 'We used a VECTOR database.'))
        self.assertFalse(is_answer_correct(q, 'We used a graph database.'))


class ScoreAnswerTest(TestCase):
    def test_correct_answer_earns_scores_for_correct_answer(self):
        q = _question(
            question_type=QuestionType.MULTIPLE_CHOICE, correct_answer='1',
            scores_for_correct_answer=5,
        )
        self.assertEqual(score_answer(q, '1'), 5)

    def test_incorrect_answer_earns_zero(self):
        q = _question(
            question_type=QuestionType.MULTIPLE_CHOICE, correct_answer='1',
            scores_for_correct_answer=5,
        )
        self.assertEqual(score_answer(q, '2'), 0)


class ScoringWithRealQuestionRowsTest(TestCase):
    """Sanity check the pure-function tests above against a saved Question row."""

    def setUp(self):
        course = Course.objects.create(title='Buildcamp', slug='buildcamp-scoring')
        cohort = Cohort.objects.create(
            course=course, name='Cohort 4',
            start_date='2026-09-21', end_date='2026-11-22',
        )
        self.homework = Homework.objects.create(
            cohort=cohort, slug='hw1', title='HW1',
            due_date=timezone.now() + datetime.timedelta(days=7),
        )

    def test_saved_question_scores_the_same_as_the_pure_function(self):
        question = Question.objects.create(
            homework=self.homework, text='2+2?',
            question_type=QuestionType.FREE_FORM,
            answer_type=AnswerType.INTEGER, correct_answer='4',
            scores_for_correct_answer=2,
        )
        self.assertTrue(is_answer_correct(question, '4'))
        self.assertEqual(score_answer(question, '4'), 2)
        self.assertEqual(score_answer(question, '5'), 0)
