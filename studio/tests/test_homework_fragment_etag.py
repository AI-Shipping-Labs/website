"""The studio submissions poll answers 304 on unchanged pages (perf lane).

The submissions page polls its fragment every five seconds; the fragment
used to rebuild and re-render every submission and answer on each poll
(~990 KB responses at one hundred submissions). The ETag is derived from
submission/answer/question row identities, so an unchanged page costs
three aggregates and no rendering.
"""

from django.test import TestCase, tag

from content.services.homework_submissions import save_submission
from studio.tests.test_homework_studio import HomeworkStudioSetupMixin


@tag('core')
class HomeworkFragmentEtagTest(HomeworkStudioSetupMixin, TestCase):
    def url(self):
        return f'/studio/homeworks/{self.homework.pk}/submissions?fragment=submissions'

    def test_first_request_renders_with_etag_and_unchanged_polls_304(self):
        self.client.login(email='staff@test.com', password='testpass')

        first = self.client.get(self.url())
        self.assertContains(first, 'data-testid="homework-submissions-fragment"')
        etag = first['ETag']
        self.assertTrue(etag)

        cached = self.client.get(self.url(), HTTP_IF_NONE_MATCH=etag)
        self.assertEqual(cached.status_code, 304)

    def test_new_submission_changes_the_etag_and_rerenders(self):
        self.client.login(email='staff@test.com', password='testpass')
        first = self.client.get(self.url())
        etag = first['ETag']

        save_submission(
            self.homework, self.student,
            homework_link='', answers_by_question_id={self.question.pk: '4'},
        )
        second = self.client.get(self.url(), HTTP_IF_NONE_MATCH=etag)
        self.assertContains(second, 'data-submission-count="1"')
        self.assertContains(second, 'student-studio@test.com')
        self.assertNotEqual(second['ETag'], etag)

    def test_changed_answer_revises_the_etag(self):
        self.client.login(email='staff@test.com', password='testpass')
        save_submission(
            self.homework, self.student,
            homework_link='', answers_by_question_id={self.question.pk: '4'},
        )
        first = self.client.get(self.url())
        etag = first['ETag']

        save_submission(
            self.homework, self.student,
            homework_link='', answers_by_question_id={self.question.pk: '5'},
        )
        second = self.client.get(self.url(), HTTP_IF_NONE_MATCH=etag)
        self.assertContains(second, '<p>5</p>')
        self.assertNotEqual(second['ETag'], etag)
