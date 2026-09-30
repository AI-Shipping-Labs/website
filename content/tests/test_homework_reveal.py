"""Homework results reveal on submit -- issue #1696 phase 1.

A self-paced learner sees Correct/Incorrect plus the correct answer right
after submitting, and cannot submit again. A dated cohort reveals nothing
until the homework is ``SCORED``. Nothing is revealed before a submission.
"""

import datetime

from community_base.homework_steps.models import HomeworkDraft
from community_base.homework_steps.types import QuestionResult
from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase
from django.utils import timezone

from content.models import Course, Module, Unit
from content.models.cohort import Cohort, CohortEnrollment
from content.models.homework import (
    AnswerType,
    Homework,
    HomeworkState,
    Question,
    QuestionType,
    Submission,
)
from content.services.homework_step_reader import (
    AISLHomeworkAdapter,
    build_assignment,
)
from content.services.homework_submissions import save_submission

User = get_user_model()


def _add_questions(homework, prefix):
    choice = Question.objects.create(
        homework=homework, source_question_id=f'{prefix}-choice',
        text='How many lines?', question_type=QuestionType.MULTIPLE_CHOICE,
        possible_answers='12\n14\n16', correct_answer='2',
    )
    checkbox = Question.objects.create(
        homework=homework, source_question_id=f'{prefix}-checkbox',
        text='Which are vector stores?', question_type=QuestionType.CHECKBOXES,
        possible_answers='Qdrant\nPandas\nLanceDB', correct_answer='1,3',
    )
    typed = Question.objects.create(
        homework=homework, source_question_id=f'{prefix}-typed',
        text='Name the primary key.', question_type=QuestionType.FREE_FORM,
        answer_type=AnswerType.EXACT_STRING, correct_answer='reveal-canary-5b2e',
    )
    return choice, checkbox, typed


class RevealFixtureMixin:
    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(
            title='Reveal course', slug='reveal-course-1696',
            status='published', required_level=0,
        )
        cls.module = Module.objects.create(course=cls.course, title='Module 1', slug='module-1')
        cls.unit = Unit.objects.create(
            module=cls.module, title='Module 1 Homework', slug='hw1', sort_order=1,
            homework='Answer the questions below.',
            content_id='16961696-1696-1696-1696-169616961696', kind='homework',
        )
        cls.self_paced = Cohort.objects.create(
            course=cls.course, name='Self-paced', mode='self_paced',
        )
        cls.homework = Homework.objects.create(
            cohort=cls.self_paced, slug='hw1', title='Module 1 Homework',
            content_id=cls.unit.content_id, stepper_enabled=True,
            homework_url_field=False,
        )
        cls.choice, cls.checkbox, cls.typed = _add_questions(cls.homework, 'sp')
        today = timezone.now().date()
        cls.dated = Cohort.objects.create(
            course=cls.course, name='Cohort 4',
            start_date=today - datetime.timedelta(days=5),
            end_date=today + datetime.timedelta(days=60),
        )
        cls.dated_homework = Homework.objects.create(
            cohort=cls.dated, slug='hw1', title='Module 1 Homework',
            content_id=cls.unit.content_id,
            due_date=timezone.now() + datetime.timedelta(days=7),
        )
        cls.dated_choice, cls.dated_checkbox, cls.dated_typed = _add_questions(
            cls.dated_homework, 'dated',
        )
        cls.unit_url = cls.unit.get_absolute_url()

    def setUp(self):
        self.student = User.objects.create_user(email=f'reveal-{id(self)}@test.com')

    def submit(self, homework, choice, checkbox):
        """Right single choice, wrong checkbox set, typed question left blank."""
        return save_submission(
            homework, self.student, homework_link='',
            answers_by_question_id={choice.pk: '2', checkbox.pk: '1,2'},
        )


class ReaderRevealTest(RevealFixtureMixin, TestCase):
    def test_self_paced_reveals_nothing_before_submit(self):
        assignment = build_assignment(self.homework, self.unit, self.student)

        self.assertIsNone(assignment.question_results)
        self.assertEqual(assignment.availability, 'open')

    def test_self_paced_submit_reveals_results_for_every_question_type(self):
        self.submit(self.homework, self.choice, self.checkbox)

        assignment = build_assignment(self.homework, self.unit, self.student)

        self.assertEqual(assignment.availability, 'scored')
        self.assertEqual(assignment.question_results, {
            'sp-choice': QuestionResult(correct=True, correct_answer='14'),
            'sp-checkbox': QuestionResult(correct=False, correct_answer='Qdrant, LanceDB'),
            'sp-typed': QuestionResult(correct=False, correct_answer='reveal-canary-5b2e'),
        })

    def test_dated_cohort_reveals_only_once_scored(self):
        self.submit(self.dated_homework, self.dated_choice, self.dated_checkbox)

        open_assignment = build_assignment(self.dated_homework, self.unit, self.student)
        self.assertIsNone(open_assignment.question_results)
        self.assertEqual(open_assignment.availability, 'open')

        self.dated_homework.state = HomeworkState.SCORED
        self.dated_homework.save(update_fields=['state'])
        scored = build_assignment(self.dated_homework, self.unit, self.student)

        self.assertEqual(scored.availability, 'scored')
        self.assertEqual(scored.question_results, {
            'dated-choice': QuestionResult(correct=True, correct_answer='14'),
            'dated-checkbox': QuestionResult(correct=False, correct_answer='Qdrant, LanceDB'),
            'dated-typed': QuestionResult(correct=False, correct_answer='reveal-canary-5b2e'),
        })

    def test_scored_dated_homework_reveals_nothing_without_a_submission(self):
        self.dated_homework.state = HomeworkState.SCORED
        self.dated_homework.save(update_fields=['state'])

        assignment = build_assignment(self.dated_homework, self.unit, self.student)

        self.assertIsNone(assignment.question_results)


class AdapterLockTest(RevealFixtureMixin, TestCase):
    def eligibility(self, homework, cohort):
        request = RequestFactory().get(self.unit_url)
        request.user = self.student
        CohortEnrollment.objects.get_or_create(user=self.student, cohort=cohort)
        return AISLHomeworkAdapter(homework, self.unit, cohort=cohort).eligibility(request, None)

    def test_self_paced_submission_locks_further_writes_and_submits(self):
        self.assertTrue(self.eligibility(self.homework, self.self_paced).submit)

        self.submit(self.homework, self.choice, self.checkbox)
        locked = self.eligibility(self.homework, self.self_paced)

        self.assertEqual((locked.read, locked.write, locked.submit), (True, False, False))
        self.assertIn('Your results are shown', locked.reason)

    def test_dated_submission_can_still_be_updated_while_open(self):
        self.submit(self.dated_homework, self.dated_choice, self.dated_checkbox)

        eligibility = self.eligibility(self.dated_homework, self.dated)

        self.assertTrue(eligibility.submit)


class SelfPacedStepperRevealViewTest(RevealFixtureMixin, TestCase):
    def setUp(self):
        super().setUp()
        CohortEnrollment.objects.create(user=self.student, cohort=self.self_paced)
        self.client.force_login(self.student)

    def submit_review(self):
        self.client.get(self.unit_url)
        draft = HomeworkDraft.objects.get(user=self.student)
        return self.client.post(self.unit_url, {
            'assignment_key': f'aisl:homework:{self.homework.pk}',
            'draft_token': str(draft.token),
            'homework_step': 'review', 'revision': str(draft.revision),
            'intent': 'submit',
        })

    def test_question_and_review_steps_hide_results_before_submit(self):
        question = self.client.get(f'{self.unit_url}/sp-typed')
        review = self.client.get(f'{self.unit_url}/review')

        for response in (question, review):
            self.assertNotContains(response, 'data-correct=')
            self.assertNotContains(response, 'reveal-canary-5b2e')

    def test_submitted_learner_sees_results_on_question_and_review_steps(self):
        self.submit(self.homework, self.choice, self.checkbox)

        choice = self.client.get(f'{self.unit_url}/sp-choice')
        checkbox = self.client.get(f'{self.unit_url}/sp-checkbox')
        review = self.client.get(f'{self.unit_url}/review')

        self.assertContains(choice, 'data-correct="true"', count=1)
        self.assertContains(choice, 'Correct answer: <span class="text-foreground">14</span>')
        self.assertContains(checkbox, 'data-correct="false"', count=1)
        self.assertContains(
            checkbox, 'Correct answer: <span class="text-foreground">Qdrant, LanceDB</span>',
        )
        self.assertContains(review, 'data-correct="true"', count=1)
        self.assertContains(review, 'data-correct="false"', count=2)
        self.assertContains(review, 'reveal-canary-5b2e')
        self.assertNotContains(review, 'homework-submit-button')

    def test_stepper_submit_reveals_results_and_refuses_a_second_submit(self):
        first = self.submit_review()
        self.assertEqual(first.status_code, 302)
        review = self.client.get(f'{self.unit_url}/review')
        self.assertContains(review, 'data-testid="homework-question-result"', count=3)

        second = self.submit_review()

        self.assertEqual(second.status_code, 403)
        self.assertEqual(Submission.objects.filter(homework=self.homework).count(), 1)

    def test_draft_autosave_is_refused_after_submit(self):
        self.submit_review()
        self.client.get(self.unit_url)
        draft = HomeworkDraft.objects.get(user=self.student)

        response = self.client.post(
            f'/api/homework-reader/drafts/{self.homework.pk}/questions/sp-typed',
            {'draft_token': str(draft.token), 'revision': str(draft.revision), 'answer': 'x'},
        )

        self.assertEqual(response.status_code, 403)


class SelfPacedLegacyFormRevealTest(RevealFixtureMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.homework.stepper_enabled = False
        self.homework.save(update_fields=['stepper_enabled'])
        CohortEnrollment.objects.create(user=self.student, cohort=self.self_paced)
        self.client.force_login(self.student)

    def test_form_hides_results_before_submit(self):
        response = self.client.get(self.unit_url)

        self.assertContains(response, 'homework-submit-button')
        self.assertNotContains(response, 'data-correct=')
        self.assertNotContains(response, 'reveal-canary-5b2e')

    def test_submit_reveals_results_and_locks_the_form(self):
        response = self.client.post(self.unit_url, {
            f'answer_{self.choice.pk}': '1',
        }, follow=True)

        self.assertContains(response, 'data-testid="homework-submitted-status"')
        self.assertContains(response, 'data-correct="false"', count=3)
        self.assertContains(response, 'Correct answer: <span class="text-foreground">14</span>')
        self.assertNotContains(response, 'homework-submit-button')

    def test_second_post_is_refused_and_keeps_the_first_answer(self):
        self.client.post(self.unit_url, {f'answer_{self.choice.pk}': '1'})

        response = self.client.post(self.unit_url, {
            f'answer_{self.choice.pk}': '2',
        }, follow=True)

        self.assertContains(response, 'You have already submitted this homework')
        submission = Submission.objects.get(homework=self.homework, student=self.student)
        self.assertEqual(submission.answers.get(question=self.choice).answer_text, '1')
