"""The studio submissions poll answers 304 on unchanged pages (perf lane).

The submissions page polls its fragment every five seconds; the fragment
used to rebuild and re-render every submission and answer on each poll
(~990 KB responses at one hundred submissions). The ETag is derived from
submission/answer/question row identities, so an unchanged page costs
three aggregates and no rendering.
"""

import uuid

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, tag
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from content.models import CohortEnrollment, Module, Unit
from content.models.homework import Answer, AnswerType, Question, QuestionType, Submission
from content.services.homework_submissions import save_submission
from integrations.middleware import clear_redirect_cache, get_active_redirects
from studio.tests.test_homework_studio import HomeworkStudioSetupMixin

User = get_user_model()


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


@tag('core')
class HomeworkFragmentQueryBudgetTest(HomeworkStudioSetupMixin, TestCase):
    """The poll cost stays bounded as submission and answer rows grow."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.content_id = uuid.uuid4()
        cls.homework.content_id = cls.content_id
        cls.homework.save(update_fields=['content_id'])
        module = Module.objects.create(
            course=cls.course, title='Performance module', slug='performance-module',
        )
        Unit.objects.create(
            module=module, title='Performance homework', slug='performance-homework',
            sort_order=1, source_content_id=cls.content_id,
        )
        cls.questions = [cls.question]
        cls.questions.extend(Question.objects.bulk_create([
            Question(
                homework=cls.homework,
                source_question_id=f'query-budget-{index}',
                text=f'Question {index}',
                question_type=QuestionType.FREE_FORM,
                answer_type=AnswerType.ANY,
            )
            for index in range(2, 6)
        ]))

    def setUp(self):
        super().setUp()
        self.client.force_login(self.staff)
        # RedirectMiddleware has a process-local first-request read. Prime only
        # that unrelated middleware cache so both first-view and grown-row
        # measurements cover the same request path and remain order-independent.
        clear_redirect_cache()
        get_active_redirects()
        self.addCleanup(clear_redirect_cache)

    def url(self):
        return f'/studio/homeworks/{self.homework.pk}/submissions?fragment=submissions'

    def _add_submissions(self, start, stop):
        users = User.objects.bulk_create([
            User(email=f'query-budget-{index}@test.com')
            for index in range(start, stop)
        ])
        enrollments = CohortEnrollment.objects.bulk_create([
            CohortEnrollment(cohort=self.cohort, user=user)
            for user in users
        ])
        submissions = Submission.objects.bulk_create([
            Submission(
                homework=self.homework,
                student=user,
                enrollment=enrollment,
                submitted_at=timezone.now(),
            )
            for user, enrollment in zip(users, enrollments, strict=True)
        ])
        Answer.objects.bulk_create([
            Answer(
                submission=submission,
                question=question,
                answer_text=f'Answer {submission.pk}-{question.pk}',
                is_correct=False,
            )
            for submission in submissions
            for question in self.questions
        ])

    def _captured_get(self, *, etag=None):
        headers = {'HTTP_IF_NONE_MATCH': etag} if etag else {}
        with CaptureQueriesContext(connection) as captured:
            response = self.client.get(self.url(), **headers)
        return response, [query['sql'] for query in captured.captured_queries]

    def test_changed_and_unchanged_query_counts_do_not_grow_with_rows(self):
        self._add_submissions(1, 2)

        small_changed, small_changed_sql = self._captured_get()
        self.assertContains(small_changed, 'data-submission-count="1"')
        small_cached, small_cached_sql = self._captured_get(
            etag=small_changed['ETag'],
        )
        self.assertEqual(small_cached.status_code, 304)

        self._add_submissions(2, 18)

        large_changed, large_changed_sql = self._captured_get()
        self.assertContains(large_changed, 'data-submission-count="17"')
        large_cached, large_cached_sql = self._captured_get(
            etag=large_changed['ETag'],
        )
        self.assertEqual(large_cached.status_code, 304)

        self.assertEqual(len(small_changed_sql), len(large_changed_sql))
        self.assertEqual(len(small_cached_sql), len(large_cached_sql))
        self.assertLess(len(large_cached_sql), len(large_changed_sql))

        changed_sql = '\n'.join(small_changed_sql + large_changed_sql).lower()
        self.assertNotIn('integrations_redirect', changed_sql)
        cached_sql = '\n'.join(large_cached_sql).lower()
        self.assertNotIn('content_unit', cached_sql)
        self.assertNotIn('"content_question"."text"', cached_sql)
        self.assertNotIn('"content_answer"."answer_text"', cached_sql)
        self.assertNotIn('"content_submission"."student_id"', cached_sql)
        revision_queries = [
            sql.lower() for sql in large_cached_sql
            if any(
                table in sql.lower()
                for table in (
                    'from "content_submission"',
                    'from "content_answer"',
                    'from "content_question"',
                )
            )
        ]
        self.assertEqual(len(revision_queries), 3)
        self.assertTrue(all('count(' in sql and 'max(' in sql for sql in revision_queries))
        self.assertTrue(all('order by' not in sql for sql in revision_queries))
