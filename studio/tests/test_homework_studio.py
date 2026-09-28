"""Studio read-only homework/submissions views -- issue #1683, tranche 1.

Covers: staff-only access (403 for non-staff, redirect-to-login for
anonymous), the homework list shows due date/state/submission count, the
submissions list shows every student's answers and correctness with no
scoring controls anywhere on the page. Syllabus order, the cohort filter,
and the submissions privacy toggle are covered below.
"""

import datetime
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
