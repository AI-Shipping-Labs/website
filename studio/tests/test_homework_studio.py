"""Studio read-only homework/submissions views -- issue #1683, tranche 1.

Covers: staff-only access (403 for non-staff, redirect-to-login for
anonymous), the homework list shows due date/state/submission count, the
submissions list shows every student's answers and correctness with no
scoring controls anywhere on the page.
"""

import datetime

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.utils import timezone

from content.models import Cohort, Course
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
