"""Content-sync coverage for `questions:`/`due_date:` frontmatter -- issue #1683.

Covers: parsing into Homework/Question rows, cohort resolution, the naive
due_date rejection, question removal cleanup, and re-sync idempotency. A
malformed homework file must record a per-file sync error without aborting
the rest of the sync run (see `_sync_module_units`'s per-file exception
handling, which every case here relies on).
"""

from django.test import TestCase

from content.models import Cohort, Course, Unit
from content.models.homework import AnswerType, Homework, Question, QuestionType
from integrations.tests.sync_fixtures import make_sync_repo, sync_repo


class HomeworkSyncTestBase(TestCase):
    def setUp(self):
        self.source, self.repo = make_sync_repo(
            self, repo_name='AI-Shipping-Labs/buildcamp-homework-1683',
        )
        self.repo.write_yaml('course/course.yaml', {
            'title': 'Buildcamp',
            'slug': 'buildcamp-hw-1683',
            'content_id': '55555555-5555-5555-5555-555555555555',
        })
        self.repo.write_yaml('course/01-module/module.yaml', {'title': 'Module 1'})
        # First sync creates the Course row so a Cohort can be attached to
        # it before the homework unit (which needs a resolvable cohort) is
        # synced.
        sync_repo(self.source, self.repo)
        self.course = Course.objects.get(slug='buildcamp-hw-1683')
        self.cohort = Cohort.objects.create(
            course=self.course, name='Cohort 4',
            start_date='2026-09-21', end_date='2026-11-22',
        )

    def _write_homework_unit(self, questions, *, due_date="'2026-09-27T21:59:00Z'", extra=''):
        text = (
            '---\n'
            'content_id: 66666666-6666-6666-6666-666666666666\n'
            'title: Module 1 Homework\n'
            'is_homework: true\n'
            f'due_date: {due_date}\n'
            f'{questions}'
            f'{extra}'
            '---\n'
            'Homework instructions.\n'
        )
        self.repo.write_text('course/01-module/02-homework.md', text)


QUESTIONS_YAML = (
    'questions:\n'
    '  - id: q1-lines\n'
    "    text: 'How many lines?'\n"
    '    type: multiple_choice\n'
    "    options: ['A', 'B', 'C']\n"
    "    correct: '2'\n"
    '    score: 1\n'
    '  - id: q2-reflect\n'
    "    text: 'What was hardest?'\n"
    '    type: free_form\n'
    '    answer_type: any\n'
)


class HomeworkSyncCreatesRowsTest(HomeworkSyncTestBase):
    def test_questions_and_due_date_sync_into_homework_and_question_rows(self):
        self._write_homework_unit(QUESTIONS_YAML)
        log = sync_repo(self.source, self.repo)
        self.assertEqual(log.errors, [])

        unit = Unit.objects.get(slug='homework')
        homework = Homework.objects.get(content_id=unit.content_id, cohort=self.cohort)
        self.assertEqual(homework.title, 'Module 1 Homework')
        self.assertEqual(str(homework.due_date.date()), '2026-09-27')

        mc_question = Question.objects.get(homework=homework, source_question_id='q1-lines')
        self.assertEqual(mc_question.question_type, QuestionType.MULTIPLE_CHOICE)
        self.assertEqual(mc_question.options_list, ['A', 'B', 'C'])
        self.assertEqual(mc_question.correct_answer, '2')

        ff_question = Question.objects.get(homework=homework, source_question_id='q2-reflect')
        self.assertEqual(ff_question.question_type, QuestionType.FREE_FORM)
        self.assertEqual(ff_question.answer_type, AnswerType.ANY)

    def test_resync_is_idempotent_and_updates_changed_fields(self):
        self._write_homework_unit(QUESTIONS_YAML)
        sync_repo(self.source, self.repo)

        self._write_homework_unit(QUESTIONS_YAML, due_date="'2026-10-04T21:59:00Z'")
        log = sync_repo(self.source, self.repo)
        self.assertEqual(log.errors, [])

        unit = Unit.objects.get(slug='homework')
        homework = Homework.objects.get(content_id=unit.content_id, cohort=self.cohort)
        self.assertEqual(str(homework.due_date.date()), '2026-10-04')
        self.assertEqual(
            Homework.objects.filter(content_id=unit.content_id, cohort=self.cohort).count(), 1,
        )

    def test_removed_question_entry_is_deleted_on_next_sync(self):
        self._write_homework_unit(QUESTIONS_YAML)
        sync_repo(self.source, self.repo)
        unit = Unit.objects.get(slug='homework')
        homework = Homework.objects.get(content_id=unit.content_id, cohort=self.cohort)
        self.assertEqual(homework.questions.count(), 2)

        # Drop q2-reflect from the frontmatter.
        self._write_homework_unit(
            'questions:\n'
            '  - id: q1-lines\n'
            "    text: 'How many lines?'\n"
            '    type: multiple_choice\n'
            "    options: ['A', 'B', 'C']\n"
            "    correct: '2'\n"
        )
        log = sync_repo(self.source, self.repo)
        self.assertEqual(log.errors, [])

        homework.refresh_from_db()
        self.assertEqual(homework.questions.count(), 1)
        self.assertFalse(
            Question.objects.filter(homework=homework, source_question_id='q2-reflect').exists()
        )


class HomeworkSyncValidationTest(HomeworkSyncTestBase):
    def test_naive_due_date_is_rejected_without_failing_the_whole_sync(self):
        self._write_homework_unit(QUESTIONS_YAML, due_date="'2026-09-27T21:59:00'")
        log = sync_repo(self.source, self.repo)

        self.assertTrue(log.errors)
        self.assertTrue(
            any('due_date' in str(e.get('error', '')) for e in log.errors)
        )
        # The course itself still synced (the per-file error did not abort
        # the run).
        self.assertTrue(Course.objects.filter(slug='buildcamp-hw-1683').exists())
        self.assertFalse(
            Homework.objects.filter(cohort=self.cohort).exists()
        )

    def test_questions_without_deadline_sync_into_open_homework(self):
        # A homework may intentionally have no due_date; its OPEN/CLOSED
        # state then controls whether it accepts submissions.
        text = (
            '---\n'
            'content_id: 66666666-6666-6666-6666-666666666666\n'
            'title: Module 1 Homework\n'
            'is_homework: true\n'
            f'{QUESTIONS_YAML}'
            '---\n'
            'Homework instructions.\n'
        )
        self.repo.write_text('course/01-module/02-homework.md', text)
        log = sync_repo(self.source, self.repo)

        self.assertEqual(log.errors, [])
        unit = Unit.objects.get(slug='homework')
        homework = Homework.objects.get(content_id=unit.content_id, cohort=self.cohort)
        self.assertIsNone(homework.due_date)
        self.assertTrue(homework.is_accepting_submissions)

    def test_explicit_null_deadline_syncs_into_homework(self):
        self._write_homework_unit(QUESTIONS_YAML, due_date='null')
        log = sync_repo(self.source, self.repo)

        self.assertEqual(log.errors, [])
        unit = Unit.objects.get(slug='homework')
        homework = Homework.objects.get(content_id=unit.content_id, cohort=self.cohort)
        self.assertIsNone(homework.due_date)

    def test_homework_unit_without_questions_or_due_date_syncs_with_no_homework_row(self):
        """Backward compatibility: a plain `is_homework: true` unit with no
        `questions:`/`due_date:` keeps syncing exactly as before -- no
        Homework row, no error."""
        text = (
            '---\n'
            'content_id: 66666666-6666-6666-6666-666666666666\n'
            'title: Module 1 Homework\n'
            'is_homework: true\n'
            '---\n'
            'Homework instructions, prose only.\n'
        )
        self.repo.write_text('course/01-module/02-homework.md', text)
        log = sync_repo(self.source, self.repo)

        self.assertEqual(log.errors, [])
        unit = Unit.objects.get(slug='homework')
        self.assertIn('Homework instructions, prose only.', unit.homework)
        self.assertFalse(Homework.objects.filter(content_id=unit.content_id).exists())


class HomeworkCohortResolutionTest(TestCase):
    def setUp(self):
        self.source, self.repo = make_sync_repo(
            self, repo_name='AI-Shipping-Labs/buildcamp-homework-cohort-1683',
        )
        self.repo.write_yaml('course/course.yaml', {
            'title': 'Buildcamp',
            'slug': 'buildcamp-hw-cohort-1683',
            'content_id': '77777777-7777-7777-7777-777777777777',
        })
        self.repo.write_yaml('course/01-module/module.yaml', {'title': 'Module 1'})
        sync_repo(self.source, self.repo)
        self.course = Course.objects.get(slug='buildcamp-hw-cohort-1683')

    def _write_homework_unit(self):
        text = (
            '---\n'
            'content_id: 88888888-8888-8888-8888-888888888888\n'
            'title: Module 1 Homework\n'
            'is_homework: true\n'
            "due_date: '2026-09-27T21:59:00Z'\n"
            f'{QUESTIONS_YAML}'
            '---\n'
            'Homework instructions.\n'
        )
        self.repo.write_text('course/01-module/02-homework.md', text)

    def test_default_self_paced_cohort_is_used_as_the_fallback_target(self):
        self._write_homework_unit()
        log = sync_repo(self.source, self.repo)

        # Course sync creates one default self-paced cohort when the source
        # defines no cohorts, so the homework can resolve to that cohort.
        self.assertTrue(Unit.objects.filter(slug='homework').exists())
        self.assertEqual(log.errors, [])
        unit = Unit.objects.get(slug='homework')
        homework = Homework.objects.get(content_id=unit.content_id)
        self.assertEqual(homework.cohort.mode, 'self_paced')

    def test_multiple_cohorts_without_an_in_range_match_are_skipped(self):
        Cohort.objects.create(
            course=self.course, name='Cohort 4',
            start_date='2000-01-01', end_date='2000-02-01',  # not in range
        )
        self._write_homework_unit()
        log = sync_repo(self.source, self.repo)

        self.assertTrue(Unit.objects.filter(slug='homework').exists())
        self.assertFalse(Homework.objects.filter(cohort__course=self.course).exists())
        self.assertTrue(any(e.get('severity') == 'info' for e in log.errors))
        self.assertFalse(any(e.get('severity') != 'info' for e in log.errors))
