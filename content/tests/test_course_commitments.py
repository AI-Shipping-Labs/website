"""Selected-cohort course home commitments from persisted learner records."""

import datetime
import uuid

from community_base.homework_steps.models import HomeworkDraft
from django.core.exceptions import PermissionDenied
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from accounts.models import User
from content.models import Cohort, CohortEnrollment, Course, Module, Unit, UserCourseProgress
from content.models.homework import Homework
from content.models.peer_review import CourseProject, PeerReview, ProjectSubmission
from content.services.course_commitments import build_course_commitments
from content.services.homework_submissions import save_submission
from events.models import Event, EventSeries


class CourseCommitmentsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(email='commitment-learner@example.test')
        cls.other = User.objects.create_user(email='commitment-other@example.test')
        cls.course = Course.objects.create(
            title='A real course', slug='commitment-course', status='published',
            required_level=0, peer_review_enabled=True, peer_review_count=3,
        )
        cls.module = Module.objects.create(
            course=cls.course, title='Course work', slug='course-work',
        )
        cls.lesson = Unit.objects.create(
            module=cls.module, title='Read this lesson', slug='read-lesson',
        )
        cls.content_id = uuid.uuid4()
        cls.homework_unit = Unit.objects.create(
            module=cls.module, title='Submit practice', slug='submit-practice',
            kind='homework', content_id=cls.content_id,
        )
        now = timezone.now()
        cls.now = now
        cls.series_one = EventSeries.objects.create(name='First meetings', slug='first-meetings')
        cls.series_two = EventSeries.objects.create(name='Second meetings', slug='second-meetings')
        cls.cohort_one = Cohort.objects.create(
            course=cls.course, name='First cohort', external_key='first',
            start_date=now.date() - datetime.timedelta(days=1),
            end_date=now.date() + datetime.timedelta(days=60),
            event_series=cls.series_one,
        )
        cls.cohort_two = Cohort.objects.create(
            course=cls.course, name='Second cohort', external_key='second',
            start_date=now.date() - datetime.timedelta(days=1),
            end_date=now.date() + datetime.timedelta(days=60),
            event_series=cls.series_two,
        )
        CohortEnrollment.objects.create(user=cls.user, cohort=cls.cohort_one)
        CohortEnrollment.objects.create(user=cls.user, cohort=cls.cohort_two)
        cls.homework_one = Homework.objects.create(
            cohort=cls.cohort_one, content_id=cls.content_id, slug='practice',
            title='First cohort homework', due_date=now + datetime.timedelta(days=2),
        )
        cls.homework_two = Homework.objects.create(
            cohort=cls.cohort_two, content_id=cls.content_id, slug='practice',
            title='Second cohort homework', due_date=now + datetime.timedelta(days=3),
        )
        cls.project_one = CourseProject.objects.create(
            course=cls.course, cohort=cls.cohort_one, slug='first-project',
            title='First cohort project',
            submission_due_at=now + datetime.timedelta(days=5),
            review_due_at=now + datetime.timedelta(days=12),
        )
        cls.project_two = CourseProject.objects.create(
            course=cls.course, cohort=cls.cohort_two, slug='second-project',
            title='Second cohort project',
            submission_due_at=now + datetime.timedelta(days=6),
            review_due_at=now + datetime.timedelta(days=13),
        )

    def _event(self, slug, series, *, start, status='upcoming', **extra):
        return Event.objects.create(
            title=slug.replace('-', ' ').title(), slug=slug,
            event_series=series, start_datetime=start,
            end_datetime=start + datetime.timedelta(hours=1),
            status=status, **extra,
        )

    def test_switching_owned_cohort_switches_every_scheduled_source(self):
        Unit.objects.create(
            module=self.module, title='Session 1', slug='session-1', kind='event',
            session_position=1,
        )
        event_one = self._event(
            'first-session', self.series_one,
            start=self.now + datetime.timedelta(days=1), series_position=1,
        )
        self._event(
            'second-session', self.series_two,
            start=self.now + datetime.timedelta(days=1), series_position=1,
        )
        self.client.force_login(self.user)
        first = self.client.get('/courses/commitment-course/home?cohort=first')
        self.assertContains(first, 'First cohort homework')
        self.assertContains(first, event_one.title)
        self.assertEqual(
            first.context['urgent_commitment']['title'], 'First cohort homework',
        )
        self.assertIn(
            'First cohort project',
            [row['title'] for row in first.context['open_assignments']],
        )
        self.assertNotContains(first, 'Second cohort homework')
        self.assertNotContains(first, 'Second cohort project')
        self.assertNotContains(first, 'Second Session')
        second = self.client.get('/courses/commitment-course/home?cohort=second')
        self.assertContains(second, 'Second cohort homework')
        self.assertContains(second, 'Second Session')
        self.assertEqual(
            second.context['urgent_commitment']['title'], 'Second cohort homework',
        )
        self.assertIn(
            'Second cohort project',
            [row['title'] for row in second.context['open_assignments']],
        )
        self.assertNotContains(second, 'First cohort homework')
        self.assertNotContains(second, 'First cohort project')
        self.assertNotContains(second, event_one.title)

        unowned = Cohort.objects.create(
            course=self.course, name='Other cohort', external_key='other',
            start_date=self.now.date(), end_date=self.now.date() + datetime.timedelta(days=30),
        )
        self.assertEqual(
            self.client.get('/courses/commitment-course/home?cohort=other').status_code, 404,
        )
        with self.assertRaises(PermissionDenied):
            build_course_commitments(self.course, self.user, unowned, now=self.now)

    def test_draft_submission_and_closed_homework_are_distinct_from_reading(self):
        HomeworkDraft.objects.create(
            user=self.user, assignment_key=f'aisl:homework:{self.homework_one.pk}',
            revision=1, answers={'q1': 'in progress'},
        )
        UserCourseProgress.objects.create(
            user=self.user, unit=self.lesson, completed_at=self.now,
        )
        model = build_course_commitments(
            self.course, self.user, self.cohort_one, now=self.now,
        )
        homework = next(row for row in model['open_assignments'] if row['kind'] == 'Homework')
        self.assertEqual(homework['status'], 'Draft saved')
        self.assertEqual(homework['action'], 'Continue homework')
        self.assertEqual(
            homework['url'], f'{self.homework_unit.get_absolute_url()}?cohort=first',
        )
        save_submission(self.homework_one, self.user, homework_link='', answers_by_question_id={})
        after_submit = build_course_commitments(
            self.course, self.user, self.cohort_one, now=self.now,
        )
        self.assertFalse(any(row['kind'] == 'Homework' for row in after_submit['open_assignments']))
        self.assertEqual(
            next(row for row in after_submit['completed_assignments']
                 if row['kind'] == 'Homework')['status'],
            'Submitted',
        )

        self.homework_two.state = 'CL'
        self.homework_two.save(update_fields=['state'])
        closed = build_course_commitments(
            self.course, self.user, self.cohort_two, now=self.now,
        )
        closed_homework = next(
            row for row in closed['open_assignments'] if row['kind'] == 'Homework'
        )
        self.assertEqual(closed_homework['status'], 'Closed')
        self.assertEqual(closed_homework['action'], '')
        self.assertFalse(any(row['kind'] == 'Homework' for row in closed['coming_up']))

    def test_submitted_project_keeps_one_of_three_review_action(self):
        ProjectSubmission.objects.create(
            user=self.user, course=self.course, course_project=self.project_one,
            cohort=self.cohort_one, project_url='https://example.com/mine',
        )
        waiting = build_course_commitments(
            self.course, self.user, self.cohort_one, now=self.now,
        )
        waiting_review = next(row for row in waiting['open_assignments']
                              if row['kind'] == 'Peer reviews')
        self.assertEqual(waiting_review['status'], 'Waiting for reviews')
        self.assertEqual(waiting_review['action'], '')
        for index in range(3):
            peer = User.objects.create_user(email=f'peer-{index}@example.test')
            submission = ProjectSubmission.objects.create(
                user=peer, course=self.course, course_project=self.project_one,
                cohort=self.cohort_one, project_url=f'https://example.com/{index}',
            )
            PeerReview.objects.create(
                submission=submission, reviewer=self.user,
                is_complete=index == 0,
                completed_at=self.now if index == 0 else None,
            )
        model = build_course_commitments(
            self.course, self.user, self.cohort_one, now=self.now,
        )
        review = next(row for row in model['open_assignments']
                      if row['kind'] == 'Peer reviews')
        self.assertEqual(review['status'], '1 of 3 complete')
        self.assertEqual(review['action'], 'Continue reviews')
        self.assertEqual(
            review['url'], '/courses/commitment-course/projects/first-project/reviews',
        )
        self.assertEqual(review['detail'], '1 of 3 reviews completed')
        self.assertTrue(any(row['kind'] == 'Project'
                            for row in model['completed_assignments']))
        self.assertFalse(any('second-project' in row['url']
                             for row in model['open_assignments']))

    def test_home_collapses_alternative_project_windows_to_one_deadline(self):
        self.project_one.submission_due_at = self.now + datetime.timedelta(days=5)
        self.project_one.review_due_at = self.now + datetime.timedelta(days=12)
        self.project_one.module = self.module
        self.project_one.save(update_fields=[
            'submission_due_at', 'review_due_at', 'module',
        ])
        self.project_two.submission_due_at = self.now + datetime.timedelta(days=6)
        self.project_two.review_due_at = self.now + datetime.timedelta(days=13)
        self.project_two.cohort = self.cohort_one
        self.project_two.module = self.module
        self.project_two.save(update_fields=[
            'submission_due_at', 'review_due_at', 'cohort', 'module',
        ])
        self.homework_one.due_date = self.now + datetime.timedelta(days=30)
        self.homework_one.save(update_fields=['due_date'])

        model = build_course_commitments(
            self.course, self.user, self.cohort_one, now=self.now,
        )

        urgent = model['urgent_commitment']
        self.assertEqual(urgent['kind'], 'Project')
        self.assertEqual(urgent['title'], self.module.title)
        self.assertEqual(urgent['url'], f'/courses/{self.course.slug}/projects/{self.project_one.slug}/submit')
        self.assertEqual(urgent['detail'], 'Alternative submission windows for one project.')

    def test_submitting_one_alternative_removes_sibling_submission_obligation(self):
        self.project_one.submission_due_at = self.now + datetime.timedelta(days=2)
        self.project_one.module = self.module
        self.project_one.save(update_fields=['submission_due_at', 'module'])
        self.project_two.submission_due_at = self.now + datetime.timedelta(days=3)
        self.project_two.cohort = self.cohort_one
        self.project_two.module = self.module
        self.project_two.save(update_fields=['submission_due_at', 'cohort', 'module'])
        self.homework_one.due_date = self.now + datetime.timedelta(days=30)
        self.homework_one.save(update_fields=['due_date'])
        ProjectSubmission.objects.create(
            user=self.user, course=self.course, course_project=self.project_one,
            cohort=self.cohort_one, project_url='https://example.com/my-project',
        )

        model = build_course_commitments(
            self.course, self.user, self.cohort_one, now=self.now,
        )

        self.assertIsNone(model['urgent_commitment'])

    def test_authored_capstone_step_is_visible_without_submission_metadata(self):
        self.module.available_after_days = 0
        self.module.save(update_fields=['available_after_days'])
        content_id = uuid.uuid4()
        step = Unit.objects.create(
            module=self.module, title='Module 1 Capstone: Your AI Project',
            slug='module-1-capstone', kind='homework', content_id=content_id,
        )

        model = build_course_commitments(
            self.course, self.user, self.cohort_one, now=self.now,
        )

        work = next(item for item in model['focus_work_items'] if item['unit'] == step)
        self.assertIsNone(work['commitment'])
        self.assertEqual(work['url'], f'{step.get_absolute_url()}?cohort=first')
        self.assertEqual(work['action'], 'Open project step')

    def test_live_future_past_and_cancelled_sessions_have_truthful_actions(self):
        self._event(
            'past-without-replay', self.series_one,
            start=self.now - datetime.timedelta(days=2), status='completed',
        )
        recap = self._event(
            'past-with-recap', self.series_one,
            start=self.now - datetime.timedelta(days=1), status='completed',
            recap_html='<p>Recap</p>',
        )
        live = self._event(
            'live-session', self.series_one,
            start=self.now - datetime.timedelta(minutes=1),
            zoom_join_url='https://example.com/join',
        )
        future = self._event(
            'future-session', self.series_one,
            start=self.now + datetime.timedelta(days=1),
        )
        self._event(
            'cancelled-session', self.series_one,
            start=self.now + datetime.timedelta(days=2), status='cancelled',
        )
        model = build_course_commitments(
            self.course, self.user, self.cohort_one, now=self.now,
        )
        self.assertEqual(model['coming_up'][0]['title'], live.title)
        self.assertEqual(model['coming_up'][0]['action'], 'Join now')
        self.assertEqual(model['coming_up'][0]['url'], live.get_join_url())
        future_row = next(row for row in model['schedule_rows']
                          if row['title'] == future.title)
        self.assertEqual(future_row['action'], 'View session')
        self.assertEqual(future_row['url'], future.get_absolute_url())
        self.assertIn(model['commitment_timezone'], future_row['when_label'])
        self.assertEqual([row['title'] for row in model['past_events']], [recap.title])
        self.assertEqual(model['past_events'][0]['url'], recap.get_recap_url())
        self.assertNotIn('Cancelled Session', [row['title'] for row in model['schedule_rows']])

    def test_self_paced_homework_has_no_fake_deadline_or_overdue_state(self):
        cohort = Cohort.objects.create(
            course=self.course, name='Self-paced', mode='self_paced',
        )
        CohortEnrollment.objects.create(user=self.user, cohort=cohort)
        Homework.objects.create(
            cohort=cohort, content_id=self.content_id, slug='self-paced-practice',
            title='Practice at your pace',
            due_date=self.now - datetime.timedelta(days=30),
        )
        model = build_course_commitments(self.course, self.user, cohort, now=self.now)
        homework = next(row for row in model['open_assignments'] if row['kind'] == 'Homework')
        self.assertIsNone(homework['when'])
        self.assertEqual(homework['status'], 'Not submitted')
        self.assertEqual(homework['action'], 'Start homework')
        self.assertFalse(model['coming_up'])

    def test_drip_locked_homework_has_no_action_until_reader_opens(self):
        self.homework_unit.available_after_days = 14
        self.homework_unit.save(update_fields=['available_after_days'])

        model = build_course_commitments(
            self.course, self.user, self.cohort_one, now=self.now,
        )
        homework = next(row for row in model['open_assignments']
                        if row['kind'] == 'Homework')
        self.assertEqual(homework['status'], 'Not submitted')
        self.assertEqual(homework['action'], '')
        self.assertEqual(homework['url'], '')
        self.assertIn('Available', homework['detail'])
        self.assertNotIn(homework, model['coming_up'])
        self.client.force_login(self.user)
        self.assertEqual(
            self.client.get(f'{self.homework_unit.get_absolute_url()}?cohort=first').status_code,
            403,
        )

    def test_homework_query_count_stays_flat_when_more_units_are_added(self):
        with CaptureQueriesContext(connection) as one_homework:
            build_course_commitments(self.course, self.user, self.cohort_one, now=self.now)
        for index in range(8):
            content_id = uuid.uuid4()
            Unit.objects.create(
                module=self.module, title=f'Practice {index}', slug=f'practice-{index}',
                kind='homework', content_id=content_id,
            )
            Homework.objects.create(
                cohort=self.cohort_one, content_id=content_id,
                slug=f'practice-{index}', title=f'Practice {index}',
                due_date=self.now + datetime.timedelta(days=7),
            )
        with CaptureQueriesContext(connection) as nine_homeworks:
            model = build_course_commitments(
                self.course, self.user, self.cohort_one, now=self.now,
            )
        self.assertEqual(
            len([row for row in model['open_assignments'] if row['kind'] == 'Homework']),
            9,
        )
        self.assertLessEqual(len(nine_homeworks), len(one_homework) + 2)

    def test_spring_dst_deadlines_show_the_selected_timezone_without_ambiguity(self):
        self.user.preferred_timezone = 'Europe/Berlin'
        self.user.save(update_fields=['preferred_timezone'])
        year = self.now.year + 1
        switch_day = next(
            day for day in range(31, 24, -1)
            if datetime.date(year, 3, day).weekday() == 6
        )
        before = datetime.datetime(year, 3, switch_day, 0, 30, tzinfo=datetime.UTC)
        after = before + datetime.timedelta(hours=1)
        self._event('before-clock-change', self.series_one, start=before)
        self._event('after-clock-change', self.series_one, start=after)

        model = build_course_commitments(
            self.course, self.user, self.cohort_one, now=self.now,
        )
        labels = {
            row['title']: row['when_label']
            for row in model['schedule_rows'] if row['kind'] == 'Live session'
        }
        self.assertIn('01:30 Europe/Berlin', labels['Before Clock Change'])
        self.assertIn('03:30 Europe/Berlin', labels['After Clock Change'])

    def test_ended_course_keeps_allowed_unfinished_project_action(self):
        self.cohort_one.end_date = self.now.date() - datetime.timedelta(days=1)
        self.cohort_one.save(update_fields=['end_date'])

        model = build_course_commitments(
            self.course, self.user, self.cohort_one, now=self.now,
        )
        project = next(
            row for row in model['open_assignments'] if row['kind'] == 'Project'
        )
        self.assertEqual(project['action'], 'Submit project')
        self.assertIn(project, model['coming_up'])

    def test_no_cohort_or_tasks_has_useful_empty_state_and_no_private_rows(self):
        Course.objects.create(
            title='No commitments', slug='no-commitments', status='published',
        )
        self.client.force_login(self.user)
        response = self.client.get('/courses/no-commitments/home')
        self.assertContains(response, 'data-testid="course-home-focus"')
        self.assertIsNone(response.context['urgent_commitment'])
        self.assertFalse(response.context['focus_work_items'])
        self.assertIsNone(response.context['next_live_session'])
        self.assertNotContains(response, 'data-testid="course-home-urgent"')
        self.assertNotContains(response, 'data-testid="course-home-next-session"')
        self.assertNotContains(response, 'data-testid="course-home-weekly-work"')
        self.assertNotContains(response, self.homework_one.title)
