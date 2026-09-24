"""Model-level coverage for Homework/Question/Submission/Answer -- issue #1683."""

import datetime

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from content.models import Course
from content.models.cohort import Cohort, CohortEnrollment
from content.models.homework import (
    Answer,
    AnswerType,
    Homework,
    HomeworkState,
    Question,
    QuestionType,
    Submission,
)

User = get_user_model()


class HomeworkModelTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(title='Buildcamp', slug='buildcamp-1683')
        cls.cohort = Cohort.objects.create(
            course=cls.course, name='Cohort 4',
            start_date='2026-09-21', end_date='2026-11-22',
        )

    def test_state_defaults_open(self):
        homework = Homework.objects.create(
            cohort=self.cohort, slug='hw1', title='HW1',
            due_date=timezone.now() + datetime.timedelta(days=7),
        )
        self.assertEqual(homework.state, HomeworkState.OPEN)

    def test_duplicate_slug_within_cohort_is_rejected(self):
        Homework.objects.create(
            cohort=self.cohort, slug='hw1', title='HW1',
            due_date=timezone.now() + datetime.timedelta(days=7),
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Homework.objects.create(
                    cohort=self.cohort, slug='hw1', title='HW1 dup',
                    due_date=timezone.now() + datetime.timedelta(days=7),
                )

    def test_is_accepting_submissions_true_when_open_and_before_deadline(self):
        homework = Homework.objects.create(
            cohort=self.cohort, slug='hw1', title='HW1',
            due_date=timezone.now() + datetime.timedelta(days=1),
        )
        self.assertTrue(homework.is_accepting_submissions)

    def test_is_accepting_submissions_false_after_deadline(self):
        """The absolute deadline-enforcement requirement: due_date alone,
        with no operator action, must close submissions -- tranche 1 has
        no Studio surface to flip `state` manually."""
        homework = Homework.objects.create(
            cohort=self.cohort, slug='hw1', title='HW1',
            due_date=timezone.now() - datetime.timedelta(days=1),
        )
        self.assertFalse(homework.is_accepting_submissions)
        self.assertTrue(homework.is_past_due)
        self.assertFalse(homework.is_self_paced)

    def test_is_accepting_submissions_false_when_state_closed_even_before_deadline(self):
        homework = Homework.objects.create(
            cohort=self.cohort, slug='hw1', title='HW1',
            due_date=timezone.now() + datetime.timedelta(days=1),
            state=HomeworkState.CLOSED,
        )
        self.assertFalse(homework.is_accepting_submissions)

    def test_no_deadline_stays_open_until_state_is_closed(self):
        homework = Homework.objects.create(
            cohort=self.cohort, slug='hw-no-deadline', title='HW without deadline',
            due_date=None,
        )
        self.assertTrue(homework.is_accepting_submissions)
        self.assertFalse(homework.is_past_due)
        self.assertFalse(homework.is_self_paced)

        homework.state = HomeworkState.CLOSED
        self.assertFalse(homework.is_accepting_submissions)
        self.assertFalse(homework.is_past_due)


class SelfPacedHomeworkDeadlineTest(TestCase):
    """Tester-confirmed bug fix: a self-paced cohort has no dates, so
    there is no deadline to be late against. `due_date` (which sync still
    stamps on every Homework row, dated cohort or not, since the content
    frontmatter always carries one) must not lock a self-paced learner
    out -- only `state` can."""

    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(title='Buildcamp', slug='buildcamp-1683-sp')
        cls.self_paced_cohort = Cohort.objects.create(
            course=cls.course, name='Self-paced', mode='self_paced',
        )

    def test_is_accepting_submissions_true_past_the_stamped_due_date(self):
        homework = Homework.objects.create(
            cohort=self.self_paced_cohort, slug='hw1', title='HW1',
            due_date=timezone.now() - datetime.timedelta(days=365),
        )
        self.assertTrue(homework.is_accepting_submissions)
        self.assertFalse(homework.is_past_due)
        self.assertTrue(homework.is_self_paced)

    def test_is_accepting_submissions_false_when_state_closed(self):
        homework = Homework.objects.create(
            cohort=self.self_paced_cohort, slug='hw1', title='HW1',
            due_date=timezone.now() - datetime.timedelta(days=365),
            state=HomeworkState.CLOSED,
        )
        self.assertFalse(homework.is_accepting_submissions)


class HomeworkContentIdScopingTest(TestCase):
    """Tester-confirmed bug fix: content_id must be scoped to (cohort,
    content_id), not globally unique, so a second cohort reusing the same
    curriculum unit gets its own Homework row instead of colliding."""

    def test_two_cohorts_can_each_carry_homework_for_the_same_content_id(self):
        course = Course.objects.create(title='Buildcamp', slug='buildcamp-1683-multi')
        cohort_a = Cohort.objects.create(
            course=course, name='Cohort 4',
            start_date='2026-09-21', end_date='2026-11-22',
        )
        cohort_b = Cohort.objects.create(
            course=course, name='Cohort 5',
            start_date='2027-01-12', end_date='2027-03-15',
        )
        shared_content_id = 'dddddddd-dddd-dddd-dddd-dddddddddddd'

        Homework.objects.create(
            cohort=cohort_a, slug='hw1', title='HW1', content_id=shared_content_id,
            due_date=timezone.now() + datetime.timedelta(days=7),
        )
        # Must not raise IntegrityError.
        Homework.objects.create(
            cohort=cohort_b, slug='hw1', title='HW1', content_id=shared_content_id,
            due_date=timezone.now() + datetime.timedelta(days=100),
        )

        self.assertEqual(
            Homework.objects.filter(content_id=shared_content_id).count(), 2,
        )
        self.assertTrue(Homework.objects.filter(cohort=cohort_a, content_id=shared_content_id).exists())
        self.assertTrue(Homework.objects.filter(cohort=cohort_b, content_id=shared_content_id).exists())

    def test_duplicate_content_id_within_the_same_cohort_is_rejected(self):
        course = Course.objects.create(title='Buildcamp', slug='buildcamp-1683-dup-cid')
        cohort = Cohort.objects.create(
            course=course, name='Cohort 4',
            start_date='2026-09-21', end_date='2026-11-22',
        )
        shared_content_id = 'eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee'
        Homework.objects.create(
            cohort=cohort, slug='hw1', title='HW1', content_id=shared_content_id,
            due_date=timezone.now() + datetime.timedelta(days=7),
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Homework.objects.create(
                    cohort=cohort, slug='hw2', title='HW1 dup', content_id=shared_content_id,
                    due_date=timezone.now() + datetime.timedelta(days=7),
                )


class QuestionModelTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        course = Course.objects.create(title='Buildcamp', slug='buildcamp-1683-q')
        cohort = Cohort.objects.create(
            course=course, name='Cohort 4',
            start_date='2026-09-21', end_date='2026-11-22',
        )
        cls.homework = Homework.objects.create(
            cohort=cohort, slug='hw1', title='HW1',
            due_date=timezone.now() + datetime.timedelta(days=7),
        )

    def test_options_list_splits_possible_answers_on_newlines(self):
        question = Question.objects.create(
            homework=self.homework, text='Pick one',
            question_type=QuestionType.MULTIPLE_CHOICE,
            possible_answers='A\nB\nC',
        )
        self.assertEqual(question.options_list, ['A', 'B', 'C'])

    def test_options_list_empty_for_blank_possible_answers(self):
        question = Question.objects.create(
            homework=self.homework, text='Reflect',
            question_type=QuestionType.FREE_FORM,
            answer_type=AnswerType.ANY,
        )
        self.assertEqual(question.options_list, [])


class SubmissionAnswerModelTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(title='Buildcamp', slug='buildcamp-1683-s')
        cls.cohort = Cohort.objects.create(
            course=cls.course, name='Cohort 4',
            start_date='2026-09-21', end_date='2026-11-22',
        )
        cls.homework = Homework.objects.create(
            cohort=cls.cohort, slug='hw1', title='HW1',
            due_date=timezone.now() + datetime.timedelta(days=7),
        )
        cls.question = Question.objects.create(
            homework=cls.homework, text='2+2?',
            question_type=QuestionType.FREE_FORM,
            answer_type=AnswerType.INTEGER, correct_answer='4',
        )
        cls.student = User.objects.create_user(email='student@test.com')
        cls.enrollment = CohortEnrollment.objects.create(
            user=cls.student, cohort=cls.cohort,
        )

    def test_one_submission_per_homework_student(self):
        Submission.objects.create(
            homework=self.homework, student=self.student, enrollment=self.enrollment,
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Submission.objects.create(
                    homework=self.homework, student=self.student, enrollment=self.enrollment,
                )

    def test_one_answer_per_submission_question(self):
        submission = Submission.objects.create(
            homework=self.homework, student=self.student, enrollment=self.enrollment,
        )
        Answer.objects.create(submission=submission, question=self.question, answer_text='4')
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Answer.objects.create(
                    submission=submission, question=self.question, answer_text='5',
                )
