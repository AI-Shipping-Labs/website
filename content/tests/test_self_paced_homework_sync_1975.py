"""Multi-target homework sync coverage for issue #1975."""

import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase

from content.models import Cohort, Course, Module, Unit
from content.models.cohort import CohortEnrollment
from content.models.homework import Answer, Homework, HomeworkState, Submission
from content.models.peer_review import CourseProject
from content.sync_parsers.families.homework import sync_unit_homework
from integrations.tests.sync_fixtures import make_sync_repo, sync_repo


def _metadata(title='How many lines?'):
    return {
        'due_date': '2026-10-31T21:59:00Z',
        'questions': [{
            'id': 'q1-lines',
            'text': title,
            'type': 'multiple_choice',
            'options': ['12', '14'],
            'correct': '2',
        }],
        'homework_steps': False,
    }


class SelfPacedHomeworkTargetTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(title='Buildcamp', slug='buildcamp-1975')
        module = Module.objects.create(course=cls.course, title='Module 1', slug='module-1')
        cls.unit = Unit.objects.create(
            module=module,
            title='Module 1 Homework',
            slug='homework-1',
            content_id='19750000-0000-0000-0000-000000000001',
            homework='Homework instructions.',
        )

    def _dated(self, name='Cohort 4', *, current=True):
        today = datetime.date.today()
        start = today - datetime.timedelta(days=10)
        end = today + datetime.timedelta(days=10)
        if not current:
            start = today - datetime.timedelta(days=100)
            end = today - datetime.timedelta(days=50)
        return Cohort.objects.create(
            course=self.course,
            name=name,
            external_key=name.lower().replace(' ', '-'),
            start_date=start,
            end_date=end,
        )

    def _self_paced(self, *, active=True):
        return Cohort.objects.create(
            course=self.course,
            name='Self-paced',
            external_key='self-paced',
            mode='self_paced',
            start_date=None,
            end_date=None,
            is_active=active,
        )

    def _sync(self, metadata=None):
        stats = {'errors': []}
        sync_unit_homework(
            self.unit,
            self.course,
            metadata or _metadata(),
            'course/01-module/02-homework.md',
            stats,
        )
        return stats

    def _dated_learner_graph(self, homework, dated):
        question = homework.questions.get()
        student = get_user_model().objects.create_user(
            email='dated-1975@example.com', password='x',
        )
        enrollment = CohortEnrollment.objects.create(cohort=dated, user=student)
        submission = Submission.objects.create(
            homework=homework, student=student, enrollment=enrollment,
        )
        answer = Answer.objects.create(
            submission=submission, question=question, answer_text='14',
        )
        return enrollment, submission, question, answer

    def test_current_dated_and_active_self_paced_get_independent_rows(self):
        dated = self._dated()
        self_paced = self._self_paced()

        self.assertEqual(self._sync()['errors'], [])

        dated_homework = Homework.objects.get(cohort=dated)
        self_paced_homework = Homework.objects.get(cohort=self_paced)
        self.assertIsNotNone(dated_homework.due_date)
        self.assertIsNone(self_paced_homework.due_date)
        self.assertEqual(dated_homework.questions.count(), 1)
        self.assertEqual(self_paced_homework.questions.count(), 1)
        self.assertEqual(self_paced_homework.state, HomeworkState.OPEN)

        first_ids = set(Homework.objects.values_list('pk', flat=True))
        self._sync()
        self.assertSetEqual(
            set(Homework.objects.values_list('pk', flat=True)), first_ids,
        )
        self.assertEqual(self_paced_homework.questions.count(), 1)

    def test_adding_self_paced_target_preserves_dated_learner_graph(self):
        dated = self._dated()
        self._sync()
        homework = Homework.objects.get(cohort=dated)
        homework.state = HomeworkState.CLOSED
        homework.save(update_fields=['state'])
        enrollment, submission, question, answer = self._dated_learner_graph(
            homework, dated,
        )
        self_paced = self._self_paced()

        self._sync()

        homework.refresh_from_db()
        submission.refresh_from_db()
        answer.refresh_from_db()
        question.refresh_from_db()
        enrollment.refresh_from_db()
        self.assertEqual(homework.state, HomeworkState.CLOSED)
        self.assertEqual(homework.pk, submission.homework_id)
        self.assertEqual(homework.cohort_id, dated.pk)
        self.assertEqual(question.homework_id, homework.pk)
        self.assertEqual(answer.submission_id, submission.pk)
        self.assertEqual(answer.question_id, question.pk)
        self.assertEqual(enrollment.cohort_id, dated.pk)
        self.assertEqual(Homework.objects.filter(cohort=self_paced).count(), 1)

    def test_self_paced_updates_after_dated_target_is_ambiguous(self):
        old = self._dated('Old cohort', current=False)
        today = datetime.date.today()
        Cohort.objects.create(
            course=self.course,
            name='Future cohort',
            external_key='future',
            start_date=today + datetime.timedelta(days=50),
            end_date=today + datetime.timedelta(days=100),
        )
        self_paced = self._self_paced()
        Homework.objects.create(
            cohort=old,
            slug=self.unit.slug,
            title='Historical title',
            content_id=self.unit.content_id,
        )

        self._sync(_metadata(title='Updated question'))

        self.assertEqual(Homework.objects.get(cohort=old).title, 'Historical title')
        current = Homework.objects.get(cohort=self_paced)
        self.assertEqual(current.questions.get().text, 'Updated question')

    def test_inactive_self_paced_is_not_targeted_or_reactivated(self):
        self._dated()
        self_paced = self._self_paced(active=False)

        self._sync()

        self_paced.refresh_from_db()
        self.assertFalse(self_paced.is_active)
        self.assertFalse(Homework.objects.filter(cohort=self_paced).exists())

    def test_sole_inactive_self_paced_is_not_a_fallback_target(self):
        self_paced = self._self_paced(active=False)

        stats = self._sync()

        self.assertTrue(any(row.get('severity') == 'info' for row in stats['errors']))
        self.assertFalse(Homework.objects.filter(cohort=self_paced).exists())


class SelfPacedHomeworkFirstSyncTest(TestCase):
    def _write_course(self, repo, *, include_cohorts):
        data = {
            'title': 'Buildcamp',
            'slug': 'buildcamp-first-sync-1975',
            'content_id': '19750000-0000-0000-0000-000000000002',
        }
        if include_cohorts:
            today = datetime.date.today()
            data['cohorts'] = [
                {
                    'key': 'current', 'name': 'Current',
                    'start_date': today - datetime.timedelta(days=1),
                    'end_date': today + datetime.timedelta(days=30),
                },
                {'key': 'self-paced', 'name': 'Self-paced', 'mode': 'self_paced'},
            ]
            data['peer_review_enabled'] = True
            data['projects'] = [{
                'slug': 'self-paced-project',
                'title': 'Self-paced project',
                'module_path': 'module',
                'cohort_key': 'self-paced',
            }]
        repo.write_yaml('course/course.yaml', data)

    def _write_homework(self, repo):
        repo.write_yaml('course/01-module/module.yaml', {'title': 'Module 1'})
        repo.write_markdown(
            'course/01-module/02-homework.md',
            {
                'content_id': '19750000-0000-0000-0000-000000000003',
                'title': 'Homework', 'kind': 'homework', **_metadata(),
            },
            'Homework instructions.', ensure_content_id=False,
        )

    def test_declared_cohorts_and_homework_materialize_in_one_sync(self):
        source, repo = make_sync_repo(
            self, repo_name='AI-Shipping-Labs/self-paced-homework-1975',
        )
        self._write_course(repo, include_cohorts=True)
        self._write_homework(repo)

        sync_repo(source, repo)

        rows = Homework.objects.filter(
            cohort__course__slug='buildcamp-first-sync-1975',
        ).select_related('cohort')
        self.assertEqual(rows.count(), 2)
        self.assertEqual({row.cohort.mode for row in rows}, {'cohort', 'self_paced'})
        self.assertIsNone(rows.get(cohort__mode='self_paced').due_date)
        project = CourseProject.objects.get(course__slug='buildcamp-first-sync-1975')
        self.assertEqual(project.cohort.mode, 'self_paced')
        self.assertIsNone(project.submission_due_at)
        self.assertIsNone(project.review_due_at)

        self._write_course(repo, include_cohorts=False)
        sync_repo(source, repo)
        self.assertEqual(rows.count(), 2)
        self.assertEqual(rows.get(cohort__mode='self_paced').questions.count(), 1)
