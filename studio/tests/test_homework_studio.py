"""Studio read-only homework/submissions views -- issue #1683, tranche 1.

Covers: staff-only access (403 for non-staff, redirect-to-login for
anonymous), the homework list shows due date/state/submission count, the
submissions list shows every student's answers and correctness with no
scoring controls anywhere on the page. Syllabus order, the cohort filter,
and the submissions privacy toggle are covered below. Issue #1893 adds
authored Question N order and titles on the submissions page.
"""

import datetime
import re
import uuid

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.utils import timezone

from content.models import Cohort, Course, Module, Unit
from content.models.homework import (
    AnswerType,
    Homework,
    Question,
    QuestionType,
)
from content.services.homework_submissions import save_submission

User = get_user_model()


class HomeworkStudioSetupMixin:
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        cls.student = User.objects.create_user(
            email='student-studio@test.com', password='testpass',
        )
        cls.course = Course.objects.create(title='Buildcamp', slug='buildcamp-studio-1683')
        cls.cohort = Cohort.objects.create(
            course=cls.course, name='Cohort 4',
            start_date='2026-09-21', end_date='2026-11-22',
        )
        cls.homework = Homework.objects.create(
            cohort=cls.cohort, slug='hw1', title='Module 1 Homework',
            due_date=timezone.now() + datetime.timedelta(days=7),
        )
        cls.question = Question.objects.create(
            homework=cls.homework, source_question_id='q1',
            text='2+2?', question_type=QuestionType.FREE_FORM,
            answer_type=AnswerType.INTEGER, correct_answer='4',
        )

    def setUp(self):
        self.client = Client()


class HomeworkListStudioTest(HomeworkStudioSetupMixin, TestCase):
    def test_staff_sees_homework_list_with_due_date_state_and_submission_count(self):
        save_submission(
            self.homework, self.student,
            homework_link='', answers_by_question_id={self.question.pk: '4'},
        )
        self.client.login(email='staff@test.com', password='testpass')

        response = self.client.get(f'/studio/courses/{self.course.pk}/homeworks')

        self.assertTemplateUsed(response, 'studio/courses/homeworks.html')
        self.assertContains(response, 'Module 1 Homework')
        self.assertContains(response, 'Cohort 4')
        self.assertContains(response, 'Open')
        homework_rows = response.context['homework_rows']
        self.assertEqual(len(homework_rows), 1)
        self.assertEqual(homework_rows[0]['submission_count'], 1)

    def test_non_staff_authenticated_user_is_denied(self):
        self.client.login(email='student-studio@test.com', password='testpass')
        response = self.client.get(f'/studio/courses/{self.course.pk}/homeworks')
        self.assertEqual(response.status_code, 403)

    def test_anonymous_is_redirected_to_login(self):
        response = self.client.get(f'/studio/courses/{self.course.pk}/homeworks')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)


class HomeworkSubmissionsStudioTest(HomeworkStudioSetupMixin, TestCase):
    def test_staff_sees_student_answers_and_correctness_no_scoring_controls(self):
        save_submission(
            self.homework, self.student,
            homework_link='https://github.com/student/hw1',
            answers_by_question_id={self.question.pk: '4'},
        )
        self.client.login(email='staff@test.com', password='testpass')

        response = self.client.get(f'/studio/homeworks/{self.homework.pk}/submissions')

        self.assertTemplateUsed(response, 'studio/courses/homework_submissions.html')
        self.assertContains(response, 'student-studio@test.com')
        self.assertContains(response, 'https://github.com/student/hw1')
        self.assertContains(response, '2+2?')
        self.assertContains(response, 'homework-submission-answer-correct')
        self.assertNotContains(response, 'Re-score')
        self.assertNotContains(response, 'Export')
        self.assertNotContains(response, '<button type="submit"', html=False)

    def test_incorrect_answer_shown_as_incorrect(self):
        save_submission(
            self.homework, self.student,
            homework_link='', answers_by_question_id={self.question.pk: '5'},
        )
        self.client.login(email='staff@test.com', password='testpass')

        response = self.client.get(f'/studio/homeworks/{self.homework.pk}/submissions')
        self.assertContains(response, 'homework-submission-answer-incorrect')

    def test_non_staff_authenticated_user_is_denied(self):
        self.client.login(email='student-studio@test.com', password='testpass')
        response = self.client.get(f'/studio/homeworks/{self.homework.pk}/submissions')
        self.assertEqual(response.status_code, 403)

    def test_anonymous_is_redirected_to_login(self):
        response = self.client.get(f'/studio/homeworks/{self.homework.pk}/submissions')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response.url)

    def test_privacy_toggle_blurs_the_student_email_element(self):
        save_submission(
            self.homework, self.student,
            homework_link='', answers_by_question_id={self.question.pk: '4'},
        )
        self.client.login(email='staff@test.com', password='testpass')

        response = self.client.get(f'/studio/homeworks/{self.homework.pk}/submissions')

        body = response.content.decode()
        self.assertContains(response, 'data-testid="homework-privacy-toggle"')
        self.assertContains(response, 'Hide emails')
        self.assertIn(
            'data-testid="homework-submission-student">student-studio@test.com</span>',
            body,
        )
        self.assertIn("localStorage.getItem('studio-homework-privacy')", body)
        self.assertIn("localStorage.setItem('studio-homework-privacy'", body)
        self.assertRegex(
            body,
            r'\[data-testid="homework-submission-student"\]\s*\{[^}]*filter:\s*blur\(6px\)',
        )
        self.assertRegex(
            body,
            r'\[data-testid="homework-submission-student"\]\s*\{[^}]*user-select:\s*none',
        )
        self.assertIn(
            "querySelectorAll('[data-testid=\"homework-submission-student\"]')",
            body,
        )
        self.assertIn("setAttribute('aria-hidden', 'true')", body)
        self.assertNotIn('homework-submission-student" aria-hidden', body)


class HomeworkSubmissionQuestionOrderStudioTest(HomeworkStudioSetupMixin, TestCase):
    """Issue #1893: authored public Question N order and titles."""

    def setUp(self):
        super().setUp()
        self.client.login(email='staff@test.com', password='testpass')

    def _headings(self, response):
        return re.findall(
            r'data-testid="homework-submission-answer-heading"[^>]*>(.*?)</p>',
            response.content.decode(),
        )

    def _answer_blocks(self, response):
        html = response.content.decode()
        parts = html.split('data-testid="homework-submission-answer"')
        return parts[1:]

    def _capstone_homework(self):
        module = Module.objects.create(
            course=self.course, title='Foundation', slug='foundation-1893',
        )
        unit = Unit.objects.create(
            module=module, title='Capstone', slug='homework-capstone-1893',
            sort_order=1, source_content_id=uuid.uuid4(), kind='homework',
            homework=(
                'Intro\n\n'
                '## Question 1. Your Project Idea\n'
                'What project would you like to build?\n'
                '## Question 2. Starter Project\n'
                'Customize the starter project.\n'
            ),
        )
        homework = Homework.objects.create(
            cohort=self.cohort, slug='capstone-1893', title='Capstone homework',
            content_id=unit.source_content_id,
        )
        question_two = Question.objects.create(
            homework=homework, source_question_id='q2-starter',
            text='Customize the starter project.',
            question_type=QuestionType.FREE_FORM, answer_type=AnswerType.ANY,
            scores_for_correct_answer=1,
        )
        question_one = Question.objects.create(
            homework=homework, source_question_id='q1-project-idea',
            text='What project would you like to build?',
            question_type=QuestionType.FREE_FORM, answer_type=AnswerType.ANY,
            scores_for_correct_answer=2,
        )
        return homework, question_one, question_two

    def test_authored_order_and_heading_even_when_answers_saved_in_reverse(self):
        homework, question_one, question_two = self._capstone_homework()
        second_student = User.objects.create_user(
            email='student-two-studio@test.com', password='testpass',
        )
        save_submission(
            homework, self.student,
            homework_link='https://github.com/student/capstone',
            answers_by_question_id={
                question_two.pk: 'starter later',
                question_one.pk: 'idea first',
            },
        )
        save_submission(
            homework, second_student,
            homework_link='',
            answers_by_question_id={
                question_one.pk: 'idea in order',
                question_two.pk: 'starter in order',
            },
        )

        response = self.client.get(f'/studio/homeworks/{homework.pk}/submissions')

        self.assertEqual(
            self._headings(response),
            [
                'Question 1: Your Project Idea (2 points)',
                'Question 2: Starter Project (1 point)',
                'Question 1: Your Project Idea (2 points)',
                'Question 2: Starter Project (1 point)',
            ],
        )
        self.assertContains(response, 'What project would you like to build?')
        self.assertContains(response, 'Customize the starter project.')
        self.assertContains(response, 'https://github.com/student/capstone')
        self.assertContains(response, 'idea first')
        self.assertContains(response, 'starter later')
        for item in response.context['submission_items']:
            self.assertEqual(
                [row['heading'] for row in item['review_rows']],
                [
                    'Question 1: Your Project Idea (2 points)',
                    'Question 2: Starter Project (1 point)',
                ],
            )
            self.assertEqual(
                [row['question'].source_question_id for row in item['review_rows']],
                ['q1-project-idea', 'q2-starter'],
            )

    def test_unanswered_question_stays_in_place_with_no_answer_and_no_badge(self):
        homework, question_one, question_two = self._capstone_homework()
        save_submission(
            homework, self.student, homework_link='',
            answers_by_question_id={question_two.pk: 'only the second'},
        )

        response = self.client.get(f'/studio/homeworks/{homework.pk}/submissions')
        rows = response.context['submission_items'][0]['review_rows']
        first_block, second_block = self._answer_blocks(response)

        self.assertEqual(
            [row['heading'] for row in rows],
            [
                'Question 1: Your Project Idea (2 points)',
                'Question 2: Starter Project (1 point)',
            ],
        )
        self.assertTrue(rows[0]['unanswered'])
        self.assertIsNone(rows[0]['answer'])
        self.assertFalse(rows[1]['unanswered'])
        self.assertIn('No answer', first_block)
        self.assertNotIn('homework-submission-answer-correct', first_block)
        self.assertNotIn('homework-submission-answer-incorrect', first_block)
        self.assertIn('only the second', second_block)

    def test_zero_answers_lists_every_question_not_empty_copy(self):
        homework, _question_one, _question_two = self._capstone_homework()
        save_submission(
            homework, self.student, homework_link='', answers_by_question_id={},
        )

        response = self.client.get(f'/studio/homeworks/{homework.pk}/submissions')
        rows = response.context['submission_items'][0]['review_rows']

        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row['unanswered'] for row in rows))
        self.assertEqual(
            self._headings(response),
            [
                'Question 1: Your Project Idea (2 points)',
                'Question 2: Starter Project (1 point)',
            ],
        )
        self.assertContains(response, 'No answer')
        self.assertNotContains(response, 'No answers on record.')

    def test_legacy_question_uses_stored_text_once(self):
        homework = Homework.objects.create(
            cohort=self.cohort, slug='legacy-1893', title='Legacy homework',
        )
        Question.objects.create(
            homework=homework, source_question_id='q1-lines',
            text='How many lines?', question_type=QuestionType.FREE_FORM,
            answer_type=AnswerType.INTEGER, correct_answer='4',
            scores_for_correct_answer=1,
        )
        save_submission(
            homework, self.student, homework_link='',
            answers_by_question_id={
                homework.questions.get().pk: '4',
            },
        )

        response = self.client.get(f'/studio/homeworks/{homework.pk}/submissions')
        rows = response.context['submission_items'][0]['review_rows']

        self.assertEqual(rows[0]['heading'], 'Question 1: How many lines? (1 point)')
        self.assertEqual(rows[0]['supporting_text'], '')
        self.assertContains(response, 'Question 1: How many lines? (1 point)')
        self.assertContains(response, 'How many lines?', count=1)
        self.assertNotContains(response, 'data-testid="homework-submission-answer-prompt"')

    def test_homework_with_no_questions_shows_no_answers_on_record(self):
        homework = Homework.objects.create(
            cohort=self.cohort, slug='no-questions-1893', title='Empty homework',
        )
        save_submission(
            homework, self.student, homework_link='', answers_by_question_id={},
        )

        response = self.client.get(f'/studio/homeworks/{homework.pk}/submissions')

        self.assertEqual(response.context['submission_items'][0]['review_rows'], [])
        self.assertContains(response, 'No answers on record.')
        self.assertNotContains(response, 'homework-submission-answer-heading')

    def test_homework_with_no_submissions_keeps_fresh_empty_state(self):
        homework, _question_one, _question_two = self._capstone_homework()

        response = self.client.get(f'/studio/homeworks/{homework.pk}/submissions')

        self.assertEqual(response.context['submission_items'], [])
        self.assertContains(response, 'No submissions for this homework yet.')
        self.assertNotContains(response, 'homework-submission-row')
        self.assertNotContains(response, 'Question 1:')

    def test_learning_in_public_is_not_a_numbered_question(self):
        homework, question_one, _question_two = self._capstone_homework()
        homework.learning_in_public_cap = 3
        homework.time_spent_lectures_field = True
        homework.save(update_fields=[
            'learning_in_public_cap', 'time_spent_lectures_field',
        ])
        save_submission(
            homework, self.student,
            homework_link='https://github.com/student/lip',
            answers_by_question_id={question_one.pk: 'idea'},
            learning_in_public_links=['https://example.com/post'],
            time_spent_lectures=2,
        )

        response = self.client.get(f'/studio/homeworks/{homework.pk}/submissions')
        headings = self._headings(response)

        self.assertEqual(headings[0], 'Question 1: Your Project Idea (2 points)')
        self.assertNotIn('Learning in Public', ' '.join(headings))
        self.assertContains(response, 'https://github.com/student/lip')
        self.assertNotContains(response, 'Time spent')


class HomeworkListOrderAndCohortTest(HomeworkStudioSetupMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.client.login(email='staff@test.com', password='testpass')
        self.today = timezone.localdate()

    def _course(self, slug):
        return Course.objects.create(title=slug, slug=slug)

    def _unit(self, module, title, sort_order):
        return Unit.objects.create(
            module=module, title=title, slug=title.lower().replace(' ', '-'),
            sort_order=sort_order, source_content_id=uuid.uuid4(),
        )

    def _homework(self, cohort, title, content_id, due_days):
        return Homework.objects.create(
            cohort=cohort, slug=title.lower().replace(' ', '-'), title=title,
            content_id=content_id,
            due_date=timezone.now() + datetime.timedelta(days=due_days),
        )

    def _titles(self, course, query=''):
        response = self.client.get(f'/studio/courses/{course.pk}/homeworks{query}')
        return response, [
            row['homework'].title for row in response.context['homework_rows']
        ]

    def test_earlier_module_sorts_first_even_when_another_homework_is_due_later(self):
        course = self._course('hw-sort-modules')
        cohort = Cohort.objects.create(
            course=course, name='Only cohort',
            start_date=self.today - datetime.timedelta(days=3),
            end_date=self.today + datetime.timedelta(days=30),
        )
        early = Module.objects.create(
            course=course, title='Week 1', slug='week-1', sort_order=1,
        )
        late = Module.objects.create(
            course=course, title='Week 9', slug='week-9', sort_order=9,
        )
        early_unit = self._unit(early, 'Week 1 unit', 1)
        late_unit = self._unit(late, 'Week 9 unit', 1)
        self._homework(cohort, 'Week 1 homework', early_unit.source_content_id, due_days=1)
        self._homework(cohort, 'Week 9 homework', late_unit.source_content_id, due_days=40)

        _response, titles = self._titles(course)

        self.assertEqual(titles, ['Week 1 homework', 'Week 9 homework'])

    def test_nested_module_label_renders_parent_and_child(self):
        course = self._course('hw-nested-module')
        cohort = Cohort.objects.create(
            course=course, name='Only cohort',
            start_date=self.today - datetime.timedelta(days=3),
            end_date=self.today + datetime.timedelta(days=30),
        )
        parent = Module.objects.create(
            course=course, title='Foundations', slug='foundations', sort_order=1,
        )
        child = Module.objects.create(
            course=course, title='Retrieval', slug='retrieval', sort_order=2,
            parent=parent,
        )
        unit = self._unit(child, 'Nested unit', 1)
        self._homework(cohort, 'Nested homework', unit.source_content_id, due_days=3)

        response, titles = self._titles(course)

        self.assertEqual(titles, ['Nested homework'])
        self.assertEqual(
            response.context['homework_rows'][0]['module_label'],
            'Foundations / Retrieval',
        )
        self.assertContains(response, 'data-testid="homework-module"')
        self.assertContains(response, 'Foundations / Retrieval')

    def test_unmatched_homework_sorts_last_and_shows_an_em_dash(self):
        course = self._course('hw-unmatched')
        cohort = Cohort.objects.create(
            course=course, name='Only cohort',
            start_date=self.today - datetime.timedelta(days=3),
            end_date=self.today + datetime.timedelta(days=30),
        )
        module = Module.objects.create(
            course=course, title='Week 1', slug='week-1', sort_order=1,
        )
        unit = self._unit(module, 'Matched unit', 1)
        self._homework(cohort, 'Matched homework', unit.source_content_id, due_days=1)
        self._homework(cohort, 'Unmatched homework', None, due_days=40)

        response, titles = self._titles(course)

        self.assertEqual(titles, ['Matched homework', 'Unmatched homework'])
        labels = [
            row['module_label'] for row in response.context['homework_rows']
        ]
        self.assertEqual(labels, ['Week 1', ''])
        self.assertContains(response, 'data-testid="homework-module">—')

    def test_same_module_orders_by_unit_then_cohort(self):
        course = self._course('hw-unit-then-cohort')
        earlier_cohort = Cohort.objects.create(
            course=course, name='Earlier cohort',
            start_date=self.today - datetime.timedelta(days=20),
            end_date=self.today - datetime.timedelta(days=10),
        )
        later_cohort = Cohort.objects.create(
            course=course, name='Later cohort',
            start_date=self.today - datetime.timedelta(days=5),
            end_date=self.today + datetime.timedelta(days=20),
        )
        module = Module.objects.create(
            course=course, title='Week 2', slug='week-2', sort_order=1,
        )
        first_unit = self._unit(module, 'First unit', 1)
        second_unit = self._unit(module, 'Second unit', 2)
        self._homework(
            earlier_cohort, 'Second unit earlier cohort',
            second_unit.source_content_id, due_days=1,
        )
        self._homework(
            later_cohort, 'First unit later cohort',
            first_unit.source_content_id, due_days=2,
        )
        self._homework(
            earlier_cohort, 'First unit earlier cohort',
            first_unit.source_content_id, due_days=30,
        )

        _response, titles = self._titles(course, '?cohort=all')

        self.assertEqual(titles, [
            'First unit earlier cohort',
            'First unit later cohort',
            'Second unit earlier cohort',
        ])

    def test_default_cohort_is_the_current_dated_cohort(self):
        course = self._course('hw-default-cohort')
        current = Cohort.objects.create(
            course=course, name='Current cohort',
            start_date=self.today - datetime.timedelta(days=7),
            end_date=self.today + datetime.timedelta(days=21),
        )
        other = Cohort.objects.create(
            course=course, name='Past cohort',
            start_date=self.today - datetime.timedelta(days=400),
            end_date=self.today - datetime.timedelta(days=200),
        )
        module = Module.objects.create(
            course=course, title='Week 1', slug='week-1', sort_order=1,
        )
        unit = self._unit(module, 'Shared unit', 1)
        self._homework(current, 'Current homework', unit.source_content_id, due_days=4)
        self._homework(other, 'Other homework', unit.source_content_id, due_days=4)

        response, titles = self._titles(course)

        self.assertEqual(titles, ['Current homework'])
        self.assertEqual(response.context['selected_cohort'].pk, current.pk)
        self.assertContains(
            response,
            f'<option value="{current.pk}" selected>Current cohort</option>',
            html=True,
        )
        self.assertNotContains(response, 'Other homework')
        self.assertNotContains(response, 'No homeworks synced for this course yet.')

    def test_cohort_query_filters_one_cohort_and_all_shows_every_cohort(self):
        course = self._course('hw-cohort-query')
        current = Cohort.objects.create(
            course=course, name='Current cohort',
            start_date=self.today - datetime.timedelta(days=7),
            end_date=self.today + datetime.timedelta(days=21),
        )
        other = Cohort.objects.create(
            course=course, name='Past cohort',
            start_date=self.today - datetime.timedelta(days=400),
            end_date=self.today - datetime.timedelta(days=200),
        )
        module = Module.objects.create(
            course=course, title='Week 1', slug='week-1', sort_order=1,
        )
        unit = self._unit(module, 'Shared unit', 1)
        self._homework(current, 'Current homework', unit.source_content_id, due_days=4)
        self._homework(other, 'Other homework', unit.source_content_id, due_days=4)

        response, titles = self._titles(course, f'?cohort={other.pk}')
        self.assertEqual(titles, ['Other homework'])
        self.assertContains(
            response,
            f'<option value="{other.pk}" selected>Past cohort</option>',
            html=True,
        )
        self.assertNotContains(response, 'Current homework')

        response, titles = self._titles(course, '?cohort=all')
        self.assertEqual(titles, ['Other homework', 'Current homework'])
        self.assertContains(
            response,
            '<option value="all" selected>All cohorts</option>',
            html=True,
        )

    def test_invalid_cohort_param_uses_the_default_cohort(self):
        course = self._course('hw-invalid-cohort')
        current = Cohort.objects.create(
            course=course, name='Current cohort',
            start_date=self.today - datetime.timedelta(days=7),
            end_date=self.today + datetime.timedelta(days=21),
        )
        module = Module.objects.create(
            course=course, title='Week 1', slug='week-1', sort_order=1,
        )
        unit = self._unit(module, 'Shared unit', 1)
        self._homework(current, 'Current homework', unit.source_content_id, due_days=4)

        response, titles = self._titles(course, '?cohort=not-a-cohort')

        self.assertEqual(titles, ['Current homework'])
        self.assertEqual(response.context['selected_cohort'].pk, current.pk)

    def test_selected_cohort_with_no_rows_is_not_the_unsynced_empty_state(self):
        course = self._course('hw-empty-cohort')
        current = Cohort.objects.create(
            course=course, name='Current cohort',
            start_date=self.today - datetime.timedelta(days=7),
            end_date=self.today + datetime.timedelta(days=21),
        )
        empty = Cohort.objects.create(
            course=course, name='Empty cohort',
            start_date=self.today - datetime.timedelta(days=400),
            end_date=self.today - datetime.timedelta(days=200),
        )
        module = Module.objects.create(
            course=course, title='Week 1', slug='week-1', sort_order=1,
        )
        unit = self._unit(module, 'Shared unit', 1)
        self._homework(current, 'Current homework', unit.source_content_id, due_days=4)

        response, titles = self._titles(course, f'?cohort={empty.pk}')

        self.assertEqual(titles, [])
        self.assertContains(response, 'data-testid="studio-empty-state-filter"')
        self.assertContains(response, 'No homeworks match your filters.')
        self.assertContains(response, 'Clear filters')
        self.assertContains(
            response, f'href="/studio/courses/{course.pk}/homeworks"',
        )
        self.assertContains(response, 'data-testid="homework-cohort-filter"')
        self.assertNotContains(response, 'No homeworks synced for this course yet.')
        self.assertNotContains(response, 'Current homework')

    def test_unknown_cohort_id_uses_the_default_cohort(self):
        course = self._course('hw-unknown-cohort-id')
        current = Cohort.objects.create(
            course=course, name='Current cohort',
            start_date=self.today - datetime.timedelta(days=7),
            end_date=self.today + datetime.timedelta(days=21),
        )
        module = Module.objects.create(
            course=course, title='Week 1', slug='week-1', sort_order=1,
        )
        unit = self._unit(module, 'Shared unit', 1)
        self._homework(current, 'Current homework', unit.source_content_id, due_days=4)

        response, titles = self._titles(course, '?cohort=999999')

        self.assertEqual(titles, ['Current homework'])
        self.assertEqual(response.context['selected_cohort'].pk, current.pk)

    def test_latest_dated_cohort_is_default_when_none_covers_today(self):
        course = self._course('hw-latest-dated')
        older = Cohort.objects.create(
            course=course, name='Older cohort',
            start_date=self.today - datetime.timedelta(days=400),
            end_date=self.today - datetime.timedelta(days=300),
        )
        newer = Cohort.objects.create(
            course=course, name='Newer cohort',
            start_date=self.today - datetime.timedelta(days=200),
            end_date=self.today - datetime.timedelta(days=100),
        )
        module = Module.objects.create(
            course=course, title='Week 1', slug='week-1', sort_order=1,
        )
        unit = self._unit(module, 'Shared unit', 1)
        self._homework(older, 'Older homework', unit.source_content_id, due_days=4)
        self._homework(newer, 'Newer homework', unit.source_content_id, due_days=4)

        response, titles = self._titles(course)

        self.assertEqual(titles, ['Newer homework'])
        self.assertEqual(response.context['selected_cohort'].pk, newer.pk)


class HomeworkViewOnSiteStudioTest(TestCase):
    """Studio View on site from homework submissions and list rows -- #1892."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff-view-1892@test.com', password='testpass', is_staff=True,
        )
        cls.today = timezone.localdate()

    def setUp(self):
        self.client = Client()
        self.client.login(email='staff-view-1892@test.com', password='testpass')

    def _course(self, slug, status='published'):
        return Course.objects.create(title=slug, slug=slug, status=status)

    def _cohort(self, course, name='Cohort 4', external_key=''):
        return Cohort.objects.create(
            course=course, name=name, external_key=external_key,
            start_date=self.today - datetime.timedelta(days=3),
            end_date=self.today + datetime.timedelta(days=30),
        )

    def _unit(self, module, title, sort_order, content_id=None, kind='homework', slug=None):
        return Unit.objects.create(
            module=module,
            title=title,
            slug=slug or title.lower().replace(' ', '-'),
            sort_order=sort_order,
            source_content_id=content_id or uuid.uuid4(),
            kind=kind,
        )

    def _homework(self, cohort, title, content_id, slug=None):
        return Homework.objects.create(
            cohort=cohort,
            slug=slug or title.lower().replace(' ', '-'),
            title=title,
            content_id=content_id,
            due_date=timezone.now() + datetime.timedelta(days=7),
        )

    def _submissions(self, homework):
        return self.client.get(f'/studio/homeworks/{homework.pk}/submissions')

    def _list(self, course, query=''):
        return self.client.get(f'/studio/courses/{course.pk}/homeworks{query}')

    def _header(self, response, testid):
        body = response.content.decode()
        start = body.index(f'data-testid="{testid}"')
        return body[start:body.index('</header>', start)]

    def _row_html(self, response, title):
        body = response.content.decode()
        start = body.index(f'data-testid="homework-title">{title}')
        return body[start:body.index('</tr>', start)]

    def test_submissions_header_links_to_the_published_unit(self):
        course = self._course('buildcamp-view-1892')
        cohort = self._cohort(course)
        module = Module.objects.create(
            course=course, title='Week 1', slug='week-1', sort_order=1,
        )
        unit = self._unit(module, 'Hw one', 1, slug='hw-one')
        homework = self._homework(
            cohort, 'Module 1 Homework', unit.source_content_id,
        )

        response = self._submissions(homework)

        expected = '/courses/buildcamp-view-1892/week-1/hw-one'
        self.assertEqual(response.context['public_url'], expected)
        self.assertContains(response, 'No submissions for this homework yet.')
        self.assertContains(response, 'data-testid="homework-privacy-toggle"')
        self.assertContains(response, 'Hide emails')
        self.assertNotContains(response, 'Re-score')
        self.assertNotContains(response, 'Export')
        header = self._header(response, 'homework-submissions-title')
        self.assertIn('data-testid="view-on-site"', header)
        self.assertIn(f'href="{expected}"', header)
        self.assertIn('target="_blank"', header)
        self.assertIn('rel="noopener noreferrer"', header)
        self.assertIn('data-lucide="external-link"', header)
        self.assertIn('View on site', header)
        self.assertIn('Back to homeworks', header)
        self.assertIn(f'href="/studio/courses/{course.pk}/homeworks"', header)
        self.assertNotIn(f'href="{expected}">Module 1 Homework', header)
        self.assertNotIn('homework_step', header)

    def test_submissions_view_on_site_includes_cohort_preview_key(self):
        course = self._course('hw-view-cohort-1892')
        cohort = self._cohort(course, external_key='cohort-4')
        module = Module.objects.create(
            course=course, title='Week 1', slug='week-1', sort_order=1,
        )
        unit = self._unit(module, 'Hw one', 1, slug='hw-one')
        homework = self._homework(
            cohort, 'Cohort homework', unit.source_content_id,
        )

        response = self._submissions(homework)

        expected = '/courses/hw-view-cohort-1892/week-1/hw-one?cohort=cohort-4'
        self.assertEqual(response.context['public_url'], expected)
        header = self._header(response, 'homework-submissions-title')
        self.assertIn(f'href="{expected}"', header)

    def test_view_on_site_is_hidden_when_no_public_unit_resolves(self):
        def assert_hidden(homework, course, *, module_label):
            submissions = self._submissions(homework)
            self.assertEqual(submissions.context['public_url'], '')
            self.assertNotContains(submissions, 'data-testid="view-on-site"')
            self.assertNotContains(submissions, 'View on site')
            self.assertContains(submissions, 'Back to homeworks')
            listing = self._list(course)
            match = next(
                row for row in listing.context['homework_rows']
                if row['homework'].pk == homework.pk
            )
            self.assertEqual(match['public_url'], '')
            self.assertEqual(match['module_label'], module_label)
            row_html = self._row_html(listing, homework.title)
            self.assertIn('View submissions', row_html)
            self.assertNotIn('View on site', row_html)
            header = self._header(listing, 'homework-list-title')
            self.assertNotIn('View on site', header)

        with self.subTest('empty_content_id'):
            course = self._course('hw-view-empty-1892')
            cohort = self._cohort(course)
            module = Module.objects.create(
                course=course, title='Week 1', slug='week-1', sort_order=1,
            )
            self._unit(module, 'Hw one', 1)
            homework = self._homework(cohort, 'Unmatched homework', None)
            assert_hidden(homework, course, module_label='')

        with self.subTest('no_unit_on_this_course'):
            course = self._course('hw-view-orphan-1892')
            cohort = self._cohort(course)
            homework = self._homework(cohort, 'Orphan homework', uuid.uuid4())
            assert_hidden(homework, course, module_label='')

        with self.subTest('only_match_on_another_course'):
            course_a = self._course('hw-view-a-1892')
            course_b = self._course('hw-view-b-1892')
            content_id = uuid.uuid4()
            cohort_a = self._cohort(course_a)
            module_b = Module.objects.create(
                course=course_b, title='Week 1', slug='week-1', sort_order=1,
            )
            self._unit(module_b, 'Other course unit', 1, content_id=content_id)
            homework = self._homework(
                cohort_a, 'Cross-course homework', content_id,
            )
            assert_hidden(homework, course_a, module_label='')

        with self.subTest('unpublished_course'):
            course = self._course('hw-view-draft-1892', status='draft')
            cohort = self._cohort(course)
            module = Module.objects.create(
                course=course, title='Week 1', slug='week-1', sort_order=1,
            )
            unit = self._unit(module, 'Hw one', 1)
            homework = self._homework(
                cohort, 'Draft course homework', unit.source_content_id,
            )
            assert_hidden(homework, course, module_label='Week 1')

    def test_list_row_adds_view_on_site_only_when_matched(self):
        course = self._course('hw-view-list-1892')
        cohort = self._cohort(course, external_key='cohort-4')
        module = Module.objects.create(
            course=course, title='Week 1', slug='week-1', sort_order=1,
        )
        unit = self._unit(module, 'Hw one', 1, slug='hw-one')
        self._homework(cohort, 'Module 1 Homework', unit.source_content_id)
        self._homework(cohort, 'Unmatched homework', None)

        response = self._list(course, '?cohort=all')

        rows = {
            row['homework'].title: row
            for row in response.context['homework_rows']
        }
        expected = '/courses/hw-view-list-1892/week-1/hw-one?cohort=cohort-4'
        self.assertEqual(rows['Module 1 Homework']['public_url'], expected)
        self.assertEqual(rows['Unmatched homework']['public_url'], '')

        matched_html = self._row_html(response, 'Module 1 Homework')
        self.assertIn('View submissions', matched_html)
        self.assertIn('View on site', matched_html)
        self.assertLess(
            matched_html.index('View submissions'),
            matched_html.index('View on site'),
        )
        self.assertIn(f'href="{expected}"', matched_html)
        self.assertIn('target="_blank"', matched_html)
        self.assertIn('rel="noopener noreferrer"', matched_html)
        self.assertIn('data-testid="view-on-site"', matched_html)

        unmatched_html = self._row_html(response, 'Unmatched homework')
        self.assertIn('View submissions', unmatched_html)
        self.assertNotIn('View on site', unmatched_html)
        self.assertNotIn('view-on-site', unmatched_html)

        header = self._header(response, 'homework-list-title')
        self.assertNotIn('View on site', header)
        self.assertNotIn('view-on-site', header)
        self.assertContains(response, 'data-testid="view-on-site"', count=1)

    def test_duplicate_content_id_uses_earliest_syllabus_unit(self):
        course = self._course('hw-view-dup-1892')
        cohort = self._cohort(course)
        content_id = uuid.uuid4()
        week1 = Module.objects.create(
            course=course, title='Week 1', slug='week-1', sort_order=1,
        )
        week9 = Module.objects.create(
            course=course, title='Week 9', slug='week-9', sort_order=9,
        )
        self._unit(week1, 'Hw early', 1, content_id=content_id, slug='hw-early')
        self._unit(week9, 'Hw late', 1, content_id=content_id, slug='hw-late')
        homework = self._homework(cohort, 'Shared homework', content_id)

        expected = '/courses/hw-view-dup-1892/week-1/hw-early'
        submissions = self._submissions(homework)
        self.assertEqual(submissions.context['public_url'], expected)
        header = self._header(submissions, 'homework-submissions-title')
        self.assertIn(f'href="{expected}"', header)
        self.assertNotIn('hw-late', submissions.context['public_url'])

        listing = self._list(course)
        self.assertEqual(
            listing.context['homework_rows'][0]['public_url'], expected,
        )
        row_html = self._row_html(listing, 'Shared homework')
        self.assertIn(f'href="{expected}"', row_html)
        self.assertNotIn('hw-late', row_html)

    def test_nested_and_inline_homework_use_canonical_learner_urls(self):
        with self.subTest('nested_module'):
            course = self._course('hw-view-nested-1892')
            cohort = self._cohort(course)
            parent = Module.objects.create(
                course=course, title='Foundations', slug='foundations',
                sort_order=1,
            )
            child = Module.objects.create(
                course=course, title='Retrieval', slug='retrieval',
                sort_order=1, parent=parent,
            )
            unit = self._unit(child, 'Nested hw', 1, slug='nested-hw')
            homework = self._homework(
                cohort, 'Nested homework', unit.source_content_id,
            )
            expected = (
                '/courses/hw-view-nested-1892/foundations/retrieval/nested-hw'
            )
            self.assertEqual(unit.get_absolute_url(), expected)
            submissions = self._submissions(homework)
            self.assertEqual(submissions.context['public_url'], expected)
            listing = self._list(course)
            self.assertEqual(
                listing.context['homework_rows'][0]['public_url'], expected,
            )

        with self.subTest('buildcamp_inline'):
            course = self._course('ai-buildcamp')
            cohort = self._cohort(course)
            week = Module.objects.create(
                course=course, title='Week 1', slug='week-1', sort_order=1,
            )
            wrapper = Module.objects.create(
                course=course, title='Homework', slug='homework',
                sort_order=2, parent=week,
            )
            unit = self._unit(
                wrapper, 'Build the retrieval pipeline', 1,
                slug='retrieval-homework', kind='homework',
            )
            homework = self._homework(
                cohort, 'Inline homework', unit.source_content_id,
            )
            expected = '/courses/ai-buildcamp/week-1/homework'
            inner = '/courses/ai-buildcamp/week-1/homework/retrieval-homework'
            self.assertEqual(unit.get_absolute_url(), expected)
            self.assertNotEqual(expected, inner)
            submissions = self._submissions(homework)
            self.assertEqual(submissions.context['public_url'], expected)
            listing = self._list(course)
            self.assertEqual(
                listing.context['homework_rows'][0]['public_url'], expected,
            )
