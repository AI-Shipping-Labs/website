"""Studio homework presentation mode, fragment poll, and markdown -- issue #1944."""

import datetime

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.utils import timezone

from content.models.homework import (
    AnswerType,
    Homework,
    Question,
    QuestionType,
)
from content.services.homework_submissions import save_submission
from studio.tests.test_homework_studio import HomeworkStudioSetupMixin

User = get_user_model()


class HomeworkPresentationStudioTest(HomeworkStudioSetupMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.client.login(email='staff@test.com', password='testpass')
        self.url = f'/studio/homeworks/{self.homework.pk}/submissions'

    def test_header_has_presentation_mode_and_one_primary(self):
        response = self.client.get(self.url)

        self.assertContains(response, 'data-testid="homework-presentation-toggle"')
        self.assertContains(response, 'Presentation mode')
        self.assertContains(response, 'data-testid="studio-header-actions"')
        header = response.content.decode().split('data-testid="studio-header-actions"', 1)[1]
        header = header.split('</div>', 1)[0]
        self.assertEqual(header.count('bg-accent text-accent-foreground'), 1)
        self.assertNotContains(response, 'Re-score')
        self.assertNotContains(response, 'Export')
        self.assertNotContains(response, '<button type="submit"', html=False)

    def test_empty_homework_keeps_fresh_empty_state_and_can_enter_presentation(self):
        response = self.client.get(self.url)

        self.assertContains(response, 'No submissions for this homework yet.')
        self.assertContains(response, 'data-testid="studio-empty-state-fresh"')
        self.assertContains(response, 'No free-text answers to present yet.')
        self.assertContains(response, 'data-testid="homework-presentation-toggle"')
        self.assertContains(response, '?fragment=submissions')

    def test_anonymous_is_redirected_with_or_without_fragment(self):
        anon = Client()
        full = anon.get(self.url)
        fragment = anon.get(self.url + '?fragment=submissions')

        self.assertEqual(full.status_code, 302)
        self.assertIn('/accounts/login/', full.url)
        self.assertEqual(fragment.status_code, 302)
        self.assertIn('/accounts/login/', fragment.url)

    def test_non_staff_is_denied_with_or_without_fragment(self):
        self.client.logout()
        self.client.login(email='student-studio@test.com', password='testpass')

        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(
            self.client.get(self.url + '?fragment=submissions').status_code, 403,
        )

    def test_fragment_returns_list_partial_without_chrome(self):
        save_submission(
            self.homework, self.student,
            homework_link='', answers_by_question_id={self.question.pk: '4'},
        )

        response = self.client.get(self.url + '?fragment=submissions')

        self.assertTemplateUsed(response, 'studio/courses/_homework_submissions_list.html')
        self.assertContains(response, 'student-studio@test.com')
        self.assertContains(response, 'data-testid="homework-submissions-fragment"')
        self.assertNotContains(response, 'id="studio-sidebar"')
        self.assertNotContains(response, 'Presentation mode')
        self.assertNotContains(response, 'homework-submissions-title')

    def test_fragment_empty_returns_fresh_empty_state_only(self):
        response = self.client.get(self.url + '?fragment=submissions')

        self.assertContains(response, 'No submissions for this homework yet.')
        self.assertContains(response, 'data-testid="studio-empty-state-fresh"')
        self.assertNotContains(response, 'id="studio-sidebar"')
        self.assertNotContains(response, 'Presentation mode')

    def test_free_text_is_presentable_and_rendered_as_markdown(self):
        long_q = Question.objects.create(
            homework=self.homework, source_question_id='q2-writeup',
            text='Write it up', question_type=QuestionType.FREE_FORM_LONG,
            answer_type=AnswerType.ANY,
        )
        save_submission(
            self.homework, self.student, homework_link='',
            answers_by_question_id={
                self.question.pk: '4',
                long_q.pk: (
                    'I **shipped** it.\n\n- demo\n- notes\n\n'
                    '[site](https://example.com)\n\n```\ncode\n```'
                ),
            },
        )

        response = self.client.get(self.url)
        items = response.context['submission_items']
        rows = {row['question'].pk: row for row in items[0]['review_rows']}

        self.assertTrue(rows[self.question.pk]['presentable'])
        self.assertTrue(rows[long_q.pk]['presentable'])
        self.assertContains(response, 'data-presentable="true"')
        self.assertContains(response, '<strong>shipped</strong>')
        self.assertContains(response, '<ul>')
        self.assertContains(response, 'href="https://example.com"')
        self.assertContains(response, '<pre>')
        self.assertContains(response, 'class="prose')
        self.assertContains(response, 'studio-homework-talked:')
        self.assertContains(response, "Talked about")

    def test_script_and_widgets_do_not_run_on_student_answers(self):
        save_submission(
            self.homework, self.student, homework_link='',
            answers_by_question_id={
                self.question.pk: (
                    'Hi <script>alert(1)</script>\n\n'
                    '```mermaid\ngraph TD\nA-->B\n```\n\n'
                    '```eventwidget\nslug: v0-claim\n```'
                ),
            },
        )

        body = self.client.get(self.url).content.decode().lower()
        fragment = self.client.get(self.url + '?fragment=submissions').content.decode().lower()

        for html in (body, fragment):
            self.assertNotIn('<script>alert(1)</script>', html)
            self.assertNotIn('class="mermaid"', html)
            self.assertNotIn('data-event-widget', html)

    def test_multiple_choice_is_not_a_presentation_item(self):
        homework = Homework.objects.create(
            cohort=self.cohort, slug='mc-only-1944', title='MC only',
            due_date=timezone.now() + datetime.timedelta(days=3),
        )
        mc = Question.objects.create(
            homework=homework, source_question_id='q1-pick',
            text='Pick one', question_type=QuestionType.MULTIPLE_CHOICE,
            possible_answers='Alpha\nBeta', correct_answer='1',
        )
        save_submission(
            homework, self.student, homework_link='',
            answers_by_question_id={mc.pk: '1'},
        )

        response = self.client.get(f'/studio/homeworks/{homework.pk}/submissions')
        row = response.context['submission_items'][0]['review_rows'][0]

        self.assertFalse(row['presentable'])
        self.assertEqual(row['answer'].answer_text, '1')
        self.assertContains(response, 'student-studio@test.com')
        self.assertContains(response, 'No free-text answers to present yet.')
        fragment = self.client.get(
            f'/studio/homeworks/{homework.pk}/submissions?fragment=submissions',
        )
        self.assertNotContains(fragment, 'data-presentable')
        self.assertNotContains(response, 'Re-score')
        self.assertNotContains(response, 'Export')

    def test_unanswered_free_text_is_not_presentable(self):
        Question.objects.create(
            homework=self.homework, source_question_id='q2-empty',
            text='Optional writeup', question_type=QuestionType.FREE_FORM_LONG,
            answer_type=AnswerType.ANY,
        )
        save_submission(
            self.homework, self.student, homework_link='',
            answers_by_question_id={self.question.pk: '4'},
        )

        rows = self.client.get(self.url).context['submission_items'][0]['review_rows']
        by_id = {row['question'].source_question_id: row for row in rows}

        self.assertTrue(by_id['q1']['presentable'])
        self.assertFalse(by_id['q2-empty']['presentable'])
        self.assertTrue(by_id['q2-empty']['unanswered'])
