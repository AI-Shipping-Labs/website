"""Course Home: search first, the Current module card, and the next live session."""

import datetime
import uuid

from community_base.homework_steps.models import HomeworkDraft
from django.test import TestCase
from django.utils import timezone

from accounts.models import User
from content.models import (
    Cohort,
    CohortEnrollment,
    Course,
    Module,
    Unit,
    UserCourseProgress,
)
from content.models.homework import Homework, Question, Submission
from content.models.peer_review import CourseProject
from content.services.course_home import build_course_home
from content.services.course_schedule import cohort_projects
from content.services.current_module import module_progress
from events.models import Event, EventSeries


class CurrentModuleHomeTests(TestCase):
    """A dated cohort in its second week: ``second`` is the current module."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            email='current-module@example.com', password='testpass',
            email_verified=True,
        )
        cls.course = Course.objects.create(
            title='Current module course', slug='current-module-course',
            status='published', required_level=0,
            discussion_url='https://example.org/course-discussion',
        )
        cls.first = Module.objects.create(
            course=cls.course, title='Foundations', slug='foundations', sort_order=1,
            available_after_days=0,
        )
        cls.second = Module.objects.create(
            course=cls.course, title='Retrieval in practice', slug='retrieval', sort_order=2,
            available_after_days=7,
        )
        cls.third = Module.objects.create(
            course=cls.course, title='Agentic flows', slug='agents', sort_order=3,
            available_after_days=14,
        )
        cls.earlier_lesson = Unit.objects.create(
            module=cls.first, title='Earlier lesson', slug='earlier-lesson', sort_order=1,
        )
        cls.earlier_homework_unit = Unit.objects.create(
            module=cls.first, title='Earlier homework', slug='earlier-homework',
            kind='homework', sort_order=2, content_id=uuid.uuid4(),
        )
        cls.lesson_a = Unit.objects.create(
            module=cls.second, title='Retrieval basics', slug='retrieval-basics', sort_order=1,
        )
        cls.lesson_b = Unit.objects.create(
            module=cls.second, title='Retrieval evaluation', slug='retrieval-eval', sort_order=2,
        )
        cls.optional_lesson = Unit.objects.create(
            module=cls.second, title='Optional deep dive', slug='optional-deep-dive',
            sort_order=3, is_bonus=True,
        )
        cls.session_two_unit = Unit.objects.create(
            module=cls.second, title='Session 2', slug='session', kind='event',
            sort_order=4, session_position=2,
        )
        cls.homework_unit = Unit.objects.create(
            module=cls.second, title='Retrieval homework', slug='retrieval-homework',
            kind='homework', sort_order=5, content_id=uuid.uuid4(),
        )
        cls.next_lesson = Unit.objects.create(
            module=cls.third, title='Agent basics', slug='agent-basics', sort_order=1,
        )
        cls.session_three_unit = Unit.objects.create(
            module=cls.third, title='Session 3', slug='session', kind='event',
            sort_order=2, session_position=3,
        )
        today = timezone.localdate()
        cls.series = EventSeries.objects.create(name='Cohort sessions', slug='cohort-sessions')
        cls.cohort = Cohort.objects.create(
            course=cls.course, name='Cohort 4', external_key='4',
            start_date=today - datetime.timedelta(days=8),
            end_date=today + datetime.timedelta(days=30),
            event_series=cls.series,
        )
        cls.enrollment = CohortEnrollment.objects.create(cohort=cls.cohort, user=cls.user)
        cls.homework = Homework.objects.create(
            cohort=cls.cohort, content_id=cls.homework_unit.content_id,
            slug='retrieval-homework', title='Retrieval homework',
            due_date=timezone.now() + datetime.timedelta(days=5),
        )
        cls.questions = [
            Question.objects.create(
                homework=cls.homework, text=f'Question {index}', question_type='FF',
                source_question_id=f'q{index}',
            )
            for index in range(1, 4)
        ]
        cls.earlier_homework = Homework.objects.create(
            cohort=cls.cohort, content_id=cls.earlier_homework_unit.content_id,
            slug='earlier-homework', title='Earlier homework',
            due_date=timezone.now() + datetime.timedelta(days=5),
        )
        cls.home_url = f'/courses/{cls.course.slug}/home?cohort=4'

    def _session(self, position, *, days, title, **extra):
        start = timezone.now() + datetime.timedelta(days=days)
        return Event.objects.create(
            event_series=self.series, slug=f'session-{position}', title=title,
            status='completed' if days < 0 else 'upcoming',
            start_datetime=start, end_datetime=start + datetime.timedelta(hours=1),
            series_position=position,
            description='We review questions from the week.', **extra,
        )

    def _complete(self, *units):
        for unit in units:
            UserCourseProgress.objects.create(
                user=self.user, unit=unit, completed_at=timezone.now(),
            )

    def _home(self):
        self.client.force_login(self.user)
        return self.client.get(self.home_url)

    def _completed_ids(self):
        return set(UserCourseProgress.objects.filter(
            user=self.user, completed_at__isnull=False,
        ).values_list('unit_id', flat=True))

    def test_home_order_is_search_card_next_session_checklist_without_help_links(self):
        self._session(3, days=6, title='Session three event')
        response = self._home()
        html = response.content.decode()
        positions = [
            html.index('id="course-home-syllabus-search"'),
            html.index('data-testid="course-home-current-module"'),
            html.index('data-testid="course-home-next-session"'),
            html.index('data-testid="course-home-checklist"'),
        ]
        self.assertEqual(positions, sorted(positions))
        self.assertNotContains(response, 'data-testid="course-home-help-links"')
        self.assertNotContains(response, 'Need help?')
        self.assertNotContains(response, 'data-testid="course-home-focus"')
        self.assertNotContains(response, 'data-testid="course-home-deadlines"')

    def test_card_names_the_cohort_week_and_links_the_module_home(self):
        response = self._home()
        card = response.context['current_module']
        self.assertEqual(card['module'], self.second)
        self.assertEqual(card['week_label'], 'Week 2')
        self.assertContains(
            response,
            f'href="/courses/{self.course.slug}/retrieval?cohort=4"',
        )
        self.assertNotContains(response, 'data-testid="course-home-earlier-unfinished"')
        self.assertNotContains(response, 'unfinished lesson')

    def test_deliverables_are_scoped_to_the_current_module(self):
        response = self._home()
        titles = [row['title'] for row in response.context['current_module']['deliverables']]
        self.assertEqual(titles, ['Retrieval homework'])
        self.assertNotContains(response, 'Earlier homework</h4>')

    def test_progress_counts_units_and_each_homework_question_but_not_optional_or_sessions(self):
        self._complete(self.lesson_a, self.optional_lesson, self.session_two_unit)
        progress = module_progress(self.second, self.user, self.cohort, self._completed_ids())
        # Two core lessons and three homework questions; the optional lesson
        # and the session unit are not counted even though completed.
        self.assertEqual((progress['done'], progress['total']), (1, 5))

    def test_partially_answered_homework_draft_moves_progress(self):
        HomeworkDraft.objects.create(
            user=self.user, assignment_key=f'aisl:homework:{self.homework.pk}',
            answers={'q1': 'An answer', 'q2': '', 'q3': []},
        )
        progress = module_progress(self.second, self.user, self.cohort, self._completed_ids())
        self.assertEqual(progress['questions_done'], 1)
        self.assertEqual(progress['done'], 1)
        response = self._home()
        self.assertContains(
            response, 'data-testid="course-home-module-progress">1 of 5 done</p>',
        )

    def test_submitted_homework_counts_every_question(self):
        Submission.objects.create(
            homework=self.homework, student=self.user, enrollment=self.enrollment,
        )
        progress = module_progress(self.second, self.user, self.cohort, self._completed_ids())
        self.assertEqual((progress['questions_done'], progress['questions_total']), (3, 3))

    def test_button_continues_lesson_then_open_homework_then_next_module(self):
        response = self._home()
        self.assertEqual(response.context['current_module']['action'], {
            'label': 'Continue lesson',
            'url': f'{self.lesson_a.get_absolute_url()}?cohort=4',
        })

        self._complete(self.lesson_a, self.lesson_b)
        response = self._home()
        self.assertEqual(response.context['current_module']['action'], {
            'label': 'Start homework',
            'url': f'{self.homework_unit.get_absolute_url()}?cohort=4',
        })

        Submission.objects.create(
            homework=self.homework, student=self.user, enrollment=self.enrollment,
        )
        response = self._home()
        self.assertEqual(response.context['current_module']['action'], {
            'label': 'Next module: Agentic flows',
            'url': f'{self.next_lesson.get_absolute_url()}?cohort=4',
        })

    def test_ended_session_is_never_the_recommendation(self):
        self._session(2, days=-1, title='Session two event')
        self._complete(self.lesson_a, self.lesson_b)
        model = build_course_home(self.course, self.user, self.cohort)
        # Without the rule, the finished week's ended session is next.
        self.assertNotEqual(model['recommended_unit'], self.session_two_unit)
        self.assertEqual(model['recommended_unit'].kind, 'lesson')
        response = self._home()
        self.assertNotEqual(
            response.context['current_module']['action']['url'],
            f'{self.session_two_unit.get_absolute_url()}?cohort=4',
        )

    def test_next_module_session_shows_when_the_current_one_is_past(self):
        self._session(
            2, days=-1, title='Session two event',
            recap_notes='What we covered.', recording_url='https://www.youtube.com/watch?v=abc123',
        )
        self._session(3, days=6, title='Session three event')
        response = self._home()

        sessions = response.context['current_module']['sessions']
        self.assertEqual([row['display_title'] for row in sessions], ['Session 2'])
        self.assertEqual(
            [action['label'] for action in sessions[0]['actions']],
            ['Watch recording', 'Read recap'],
        )
        next_session = response.context['home_next_session']
        self.assertEqual(next_session['display_title'], 'Session 3')
        self.assertEqual(next_session['module_label'], 'Week 3 · Agentic flows')
        self.assertContains(
            response,
            'data-testid="course-home-next-session-module">Week 3 · Agentic flows</span>',
        )
        self.assertContains(response, 'We review questions from the week.')

    def test_next_session_block_is_omitted_when_it_is_the_cards_own_session(self):
        self._session(2, days=2, title='Session two event')
        self._session(3, days=9, title='Session three event')
        response = self._home()
        self.assertIsNone(response.context['home_next_session'])
        self.assertNotContains(response, 'data-testid="course-home-next-session"')
        sessions = response.context['current_module']['sessions']
        self.assertEqual(
            [action['label'] for action in sessions[0]['actions']], ['Open session'],
        )


class CohortProjectScopingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(
            title='Projects course', slug='projects-course', status='published',
        )
        today = timezone.localdate()
        cls.cohort = Cohort.objects.create(
            course=cls.course, name='Cohort 4', external_key='4',
            start_date=today, end_date=today + datetime.timedelta(days=60),
        )
        cls.other_cohort = Cohort.objects.create(
            course=cls.course, name='Cohort 5', external_key='5',
            start_date=today, end_date=today + datetime.timedelta(days=60),
        )
        due = timezone.now() + datetime.timedelta(days=30)

        def project(slug, cohort):
            return CourseProject.objects.create(
                course=cls.course, cohort=cohort, slug=slug, title=slug,
                submission_due_at=due, review_due_at=due + datetime.timedelta(days=7),
            )

        cls.preview = project('preview-attempt-1', None)
        cls.attempt = project('attempt-1', cls.cohort)

    def test_cohort_attempts_replace_unscoped_preview_attempts(self):
        self.assertEqual(list(cohort_projects(self.course, self.cohort)), [self.attempt])

    def test_unscoped_attempts_apply_when_the_cohort_has_none(self):
        self.assertEqual(list(cohort_projects(self.course, self.other_cohort)), [self.preview])
        self.assertEqual(list(cohort_projects(self.course, None)), [self.preview])
