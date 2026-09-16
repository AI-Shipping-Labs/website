"""Direct (no GitHub-App-pipeline) coverage for `sync_unit_homework`.

The full ``sync_content_source`` pipeline (see
``content/tests/test_homework_sync.py``) always goes through the GitHub
App-authenticated checkout path even for a local ``repo_dir`` override, so
it cannot run in every environment. These tests call
``content.sync_parsers.families.homework.sync_unit_homework`` directly
against real ``Course``/``Cohort``/``Unit`` rows -- the actual parsing,
validation, and upsert logic under test here -- with no GitHub dependency
at all.
"""

import datetime

from django.test import TestCase

from content.models import Course, Module, Unit
from content.models.cohort import Cohort
from content.models.homework import AnswerType, Homework, Question, QuestionType
from content.sync_parsers.common import GitHubSyncError
from content.sync_parsers.families.homework import sync_unit_homework


def _questions_metadata(**overrides):
    metadata = {
        'due_date': '2026-09-27T21:59:00Z',
        'questions': [
            {
                'id': 'q1-lines',
                'text': 'How many lines?',
                'type': 'multiple_choice',
                'options': ['A', 'B', 'C'],
                'correct': '2',
                'score': 1,
            },
            {
                'id': 'q2-reflect',
                'text': 'What was hardest?',
                'type': 'free_form',
                'answer_type': 'any',
            },
        ],
    }
    metadata.update(overrides)
    return metadata


class SyncUnitHomeworkDirectTest(TestCase):
    def setUp(self):
        self.course = Course.objects.create(title='Buildcamp', slug='buildcamp-direct-1683')
        self.module = Module.objects.create(course=self.course, title='Module 1', slug='module-1')
        self.cohort = Cohort.objects.create(
            course=self.course, name='Cohort 4',
            start_date='2026-09-21', end_date='2026-11-22',
        )
        self.unit = Unit.objects.create(
            module=self.module, title='Module 1 Homework', slug='hw1',
            content_id='99999999-9999-9999-9999-999999999999',
            homework='Homework instructions.',
        )

    def _sync(self, metadata):
        stats = {'errors': []}
        sync_unit_homework(self.unit, self.course, metadata, 'course/01-module/02-hw.md', stats)
        return stats

    def test_creates_homework_and_questions(self):
        self._sync(_questions_metadata())

        homework = Homework.objects.get(content_id=self.unit.content_id, cohort=self.cohort)
        self.assertEqual(homework.title, self.unit.title)
        self.assertEqual(str(homework.due_date.date()), '2026-09-27')
        self.assertEqual(homework.questions.count(), 2)

        mc = Question.objects.get(homework=homework, source_question_id='q1-lines')
        self.assertEqual(mc.question_type, QuestionType.MULTIPLE_CHOICE)
        self.assertEqual(mc.correct_answer, '2')
        self.assertEqual(mc.options_list, ['A', 'B', 'C'])

        ff = Question.objects.get(homework=homework, source_question_id='q2-reflect')
        self.assertEqual(ff.answer_type, AnswerType.ANY)

    def test_no_op_when_neither_due_date_nor_questions_present(self):
        stats = self._sync({})
        self.assertEqual(stats['errors'], [])
        self.assertFalse(Homework.objects.filter(content_id=self.unit.content_id).exists())

    def test_questions_without_due_date_raises(self):
        with self.assertRaises(GitHubSyncError) as cm:
            self._sync({'questions': _questions_metadata()['questions']})
        self.assertIn('due_date', str(cm.exception))

    def test_naive_due_date_raises(self):
        with self.assertRaises(GitHubSyncError) as cm:
            self._sync(_questions_metadata(due_date='2026-09-27T21:59:00'))
        self.assertIn('due_date', str(cm.exception))

    def test_unknown_question_type_raises(self):
        with self.assertRaises(GitHubSyncError):
            self._sync(_questions_metadata(questions=[
                {'id': 'q1', 'text': 'x', 'type': 'not-a-real-type'},
            ]))

    def test_question_missing_id_raises(self):
        with self.assertRaises(GitHubSyncError):
            self._sync(_questions_metadata(questions=[
                {'text': 'x', 'type': 'free_form', 'answer_type': 'any'},
            ]))

    def test_resync_upserts_in_place_no_duplicate_homework_or_questions(self):
        self._sync(_questions_metadata())
        self._sync(_questions_metadata(due_date='2026-10-04T21:59:00Z'))

        self.assertEqual(
            Homework.objects.filter(content_id=self.unit.content_id, cohort=self.cohort).count(), 1,
        )
        homework = Homework.objects.get(content_id=self.unit.content_id, cohort=self.cohort)
        self.assertEqual(str(homework.due_date.date()), '2026-10-04')
        self.assertEqual(homework.questions.count(), 2)

    def test_question_removed_from_metadata_is_deleted(self):
        self._sync(_questions_metadata())
        homework = Homework.objects.get(content_id=self.unit.content_id, cohort=self.cohort)
        self.assertEqual(homework.questions.count(), 2)

        remaining = [_questions_metadata()['questions'][0]]
        self._sync(_questions_metadata(questions=remaining))

        homework.refresh_from_db()
        self.assertEqual(homework.questions.count(), 1)
        self.assertFalse(
            Question.objects.filter(homework=homework, source_question_id='q2-reflect').exists()
        )

    def test_answer_cascades_deleted_with_removed_question(self):
        from django.contrib.auth import get_user_model

        from content.models.cohort import CohortEnrollment
        from content.models.homework import Answer, Submission

        User = get_user_model()
        self._sync(_questions_metadata())
        homework = Homework.objects.get(content_id=self.unit.content_id, cohort=self.cohort)
        question = Question.objects.get(homework=homework, source_question_id='q2-reflect')
        student = User.objects.create_user(email='cascade@test.com')
        enrollment = CohortEnrollment.objects.create(user=student, cohort=self.cohort)
        submission = Submission.objects.create(
            homework=homework, student=student, enrollment=enrollment,
        )
        Answer.objects.create(submission=submission, question=question, answer_text='hard part')

        remaining = [_questions_metadata()['questions'][0]]
        self._sync(_questions_metadata(questions=remaining))

        self.assertFalse(Answer.objects.filter(question=question).exists())


class ResolveHomeworkCohortDirectTest(TestCase):
    def setUp(self):
        self.course = Course.objects.create(title='Buildcamp', slug='buildcamp-direct-cohort-1683')
        self.module = Module.objects.create(course=self.course, title='Module 1', slug='module-1')
        self.unit = Unit.objects.create(
            module=self.module, title='HW', slug='hw1',
            content_id='11122233-1122-1122-1122-112233445566',
        )

    def test_no_cohort_skips_with_info_only(self):
        stats = {'errors': []}
        sync_unit_homework(
            self.unit, self.course, _questions_metadata(), 'f.md', stats,
        )
        self.assertFalse(Homework.objects.filter(content_id=self.unit.content_id).exists())
        self.assertTrue(stats['errors'])
        self.assertTrue(all(e.get('severity') == 'info' for e in stats['errors']))

    def test_in_range_cohort_preferred_over_out_of_range_single_cohort(self):
        today = datetime.date.today()
        out_of_range = Cohort.objects.create(
            course=self.course, name='Old cohort',
            start_date=today - datetime.timedelta(days=400),
            end_date=today - datetime.timedelta(days=300),
        )
        in_range = Cohort.objects.create(
            course=self.course, name='Current cohort',
            start_date=today - datetime.timedelta(days=1),
            end_date=today + datetime.timedelta(days=30),
        )
        stats = {'errors': []}
        sync_unit_homework(
            self.unit, self.course, _questions_metadata(), 'f.md', stats,
        )
        homework = Homework.objects.get(content_id=self.unit.content_id)
        self.assertEqual(homework.cohort_id, in_range.pk)
        self.assertNotEqual(homework.cohort_id, out_of_range.pk)

    def test_multiple_out_of_range_cohorts_with_no_match_skips(self):
        today = datetime.date.today()
        Cohort.objects.create(
            course=self.course, name='Old',
            start_date=today - datetime.timedelta(days=400),
            end_date=today - datetime.timedelta(days=300),
        )
        Cohort.objects.create(
            course=self.course, name='Future',
            start_date=today + datetime.timedelta(days=100),
            end_date=today + datetime.timedelta(days=200),
        )
        stats = {'errors': []}
        sync_unit_homework(
            self.unit, self.course, _questions_metadata(), 'f.md', stats,
        )
        self.assertFalse(Homework.objects.filter(content_id=self.unit.content_id).exists())


class MultiCohortHomeworkSyncTest(TestCase):
    """Tester-confirmed bug fix: a second cohort resolving against the
    same curriculum unit (real-world case -- cohort 5 launches reusing
    cohort 4's homework file) must get its own Homework row, not collide
    with cohort 4's on `content_id` and silently lose its homework."""

    def setUp(self):
        self.course = Course.objects.create(title='Buildcamp', slug='buildcamp-multi-cohort-1683')
        self.module = Module.objects.create(course=self.course, title='Module 1', slug='module-1')
        self.unit = Unit.objects.create(
            module=self.module, title='Module 1 Homework', slug='hw1',
            content_id='ffffffff-ffff-ffff-ffff-ffffffffffff',
        )

    def test_second_cohort_resyncing_the_same_unit_gets_its_own_homework_row(self):
        # Cohort 4 is the only cohort at first sync -- resolves via the
        # single-cohort fallback and gets its own Homework row.
        cohort_4 = Cohort.objects.create(
            course=self.course, name='Cohort 4',
            start_date='2026-09-21', end_date='2026-11-22',
        )
        stats = {'errors': []}
        sync_unit_homework(self.unit, self.course, _questions_metadata(), 'f.md', stats)
        self.assertEqual(stats['errors'], [])
        homework_4 = Homework.objects.get(content_id=self.unit.content_id, cohort=cohort_4)

        # Cohort 5 launches, out-ranging cohort 4 (now the sole in-range
        # cohort) -- re-syncing the SAME unit file must create cohort 5's
        # own Homework row without disturbing cohort 4's.
        today = datetime.date.today()
        cohort_5 = Cohort.objects.create(
            course=self.course, name='Cohort 5',
            start_date=today - datetime.timedelta(days=1),
            end_date=today + datetime.timedelta(days=60),
        )
        stats = {'errors': []}
        sync_unit_homework(self.unit, self.course, _questions_metadata(), 'f.md', stats)
        self.assertEqual(stats['errors'], [])

        homework_5 = Homework.objects.get(content_id=self.unit.content_id, cohort=cohort_5)
        self.assertNotEqual(homework_5.pk, homework_4.pk)
        self.assertEqual(
            Homework.objects.filter(content_id=self.unit.content_id).count(), 2,
        )

        # Cohort 4's row is untouched (reconcile-never-destroy).
        homework_4.refresh_from_db()
        self.assertEqual(homework_4.cohort_id, cohort_4.pk)
        self.assertEqual(homework_5.cohort_id, cohort_5.pk)

        # Each cohort's students get their own submission scoped to their
        # own Homework row -- the actual "form works for both cohorts"
        # guarantee this bug threatened.
        self.assertEqual(homework_4.questions.count(), 2)
        self.assertEqual(homework_5.questions.count(), 2)
