"""Course Home: search first, the Current module card, and the next live session."""

import datetime
import uuid
from html.parser import HTMLParser

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

_VOID_TAGS = {
    'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link',
    'meta', 'source', 'track', 'wbr',
}


class _AncestryParser(HTMLParser):
    """Record each element's ancestor chain so tests can compare nesting."""

    def __init__(self):
        super().__init__()
        self.stack = []
        self.counter = 0
        self.found = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.counter += 1
        node = (self.counter, attrs.get('data-testid', ''))
        key = attrs.get('data-testid') or (
            'progressbar' if attrs.get('role') == 'progressbar' else None
        )
        if key and key not in self.found:
            self.found[key] = self.stack + [node]
        if tag not in _VOID_TAGS:
            self.stack.append(node)

    def handle_endtag(self, tag):
        if tag not in _VOID_TAGS and self.stack:
            self.stack.pop()


class CurrentModuleFixture(TestCase):
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



class CurrentModuleHomeTests(CurrentModuleFixture):
    """The enrolled learner's Home."""

    def test_home_order_is_search_card_due_next_next_session_checklist_without_help_links(self):
        self._session(3, days=6, title='Session three event')
        response = self._home()
        html = response.content.decode()
        positions = [
            html.index('id="course-home-syllabus-search"'),
            html.index('data-testid="course-home-current-module"'),
            html.index('data-testid="course-home-due-next"'),
            html.index('data-testid="course-home-next-session"'),
            html.index('data-testid="course-home-checklist"'),
        ]
        self.assertEqual(positions, sorted(positions))
        self.assertNotContains(response, 'data-testid="course-home-help-links"')
        self.assertNotContains(response, 'Need help?')
        self.assertNotContains(response, 'data-testid="course-home-focus"')
        self.assertNotContains(response, 'data-testid="course-home-deadlines"')
        # A linked cohort needs no picker.
        self.assertNotContains(response, 'data-testid="course-home-no-cohort"')

    def test_primary_button_sits_below_progress_not_beside_it(self):
        parser = _AncestryParser()
        parser.feed(self._home().content.decode())
        progress = parser.found['progressbar']
        button = parser.found['course-home-primary-action']
        # Vertical reading order: progress first, then the button.
        self.assertLess(progress[-1][0], button[-1][0])
        # No shared row wrapper: the closest common ancestor is the card.
        common = [a for a, b in zip(progress, button) if a == b]
        self.assertEqual(common[-1][1], 'course-home-current-module')

    def test_card_names_the_cohort_week_and_links_the_module_home(self):
        response = self._home()
        card = response.context['current_module']
        self.assertEqual(card['module'], self.second)
        self.assertEqual(card['week_label'], 'Week 2')
        self.assertEqual(card['eyebrow'], 'Current module')
        self.assertContains(
            response,
            f'href="/courses/{self.course.slug}/retrieval?cohort=4"',
        )
        self.assertNotContains(response, 'data-testid="course-home-earlier-unfinished"')
        self.assertNotContains(response, 'unfinished lesson')

    def _due_in(self, homework, days):
        homework.due_date = timezone.now() + datetime.timedelta(days=days)
        homework.save(update_fields=['due_date'])

    def test_deliverables_are_scoped_to_the_current_module(self):
        # Neither is due soon, so "Due next" falls forward to the earlier
        # homework's week and the card keeps only its own module's work.
        self._due_in(self.homework, 40)
        self._due_in(self.earlier_homework, 20)
        response = self._home()
        rows = response.context['current_module']['deliverables']
        self.assertEqual([row['title'] for row in rows], ['Retrieval homework'])
        # Kind, due date, question count and status on every row.
        self.assertEqual(rows[0]['question_label'], '3 questions')
        self.assertEqual(rows[0]['status'], 'Not started')
        self.assertEqual(rows[0]['action'], 'Start homework')

    def test_due_next_lists_this_weeks_work_from_another_module(self):
        self._due_in(self.homework, 40)
        # The Foundations homework is due within minutes, so it is this week's.
        self.earlier_homework.due_date = timezone.now() + datetime.timedelta(minutes=5)
        self.earlier_homework.save(update_fields=['due_date'])
        response = self._home()
        due_next = response.context['due_next']
        self.assertEqual(due_next['title'], 'Due this week')
        self.assertEqual(
            [(row['title'], row['module_title']) for row in due_next['rows']],
            [('Earlier homework', 'Foundations')],
        )
        self.assertContains(
            response,
            'id="course-home-due-next-heading" class="text-lg font-semibold text-foreground">'
            'Due this week</h2>',
        )

    def test_due_next_falls_forward_to_the_next_week_with_every_item(self):
        from zoneinfo import ZoneInfo
        zone = ZoneInfo(self._home().context['commitment_timezone'])
        today = timezone.now().astimezone(zone).date()
        monday = today - datetime.timedelta(days=today.weekday()) + datetime.timedelta(weeks=3)
        for homework, day in ((self.earlier_homework, monday),
                              (self.homework, monday + datetime.timedelta(days=2))):
            homework.due_date = datetime.datetime.combine(
                day, datetime.time(12), tzinfo=zone,
            )
            homework.save(update_fields=['due_date'])
        due_next = self._home().context['due_next']
        self.assertEqual(due_next['title'], f'Due next · week of {monday:%b} {monday.day}')
        self.assertEqual(
            [row['title'] for row in due_next['rows']],
            ['Earlier homework', 'Retrieval homework'],
        )

    def test_due_next_excludes_submitted_work_and_the_card_does_not_repeat_it(self):
        self._due_in(self.homework, 1)
        self._due_in(self.earlier_homework, 2)
        Submission.objects.create(
            homework=self.earlier_homework, student=self.user, enrollment=self.enrollment,
        )
        response = self._home()
        self.assertEqual(
            [row['title'] for row in response.context['due_next']['rows']],
            ['Retrieval homework'],
        )
        # Shown under "Due next", so the card does not list it again.
        self.assertEqual(response.context['current_module']['deliverables'], [])
        self.assertContains(response, 'Retrieval homework</h4>', count=1)

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
            response, 'data-testid="course-home-module-progress">1 of 5 done</span>',
        )

    def test_submitted_homework_counts_every_question(self):
        Submission.objects.create(
            homework=self.homework, student=self.user, enrollment=self.enrollment,
        )
        progress = module_progress(self.second, self.user, self.cohort, self._completed_ids())
        self.assertEqual((progress['questions_done'], progress['questions_total']), (3, 3))

    def test_continue_lesson_never_targets_a_session_unit(self):
        # A session authored before the lessons must not become the target.
        Unit.objects.create(
            module=self.second, title='Kickoff session', slug='kickoff', kind='event',
            sort_order=0, session_position=9,
        )
        action = self._home().context['current_module']['action']
        self.assertEqual(action['label'], 'Continue lesson')
        self.assertEqual(action['url'], f'{self.lesson_a.get_absolute_url()}?cohort=4')

    def test_unscheduled_session_says_date_to_be_announced_without_a_link(self):
        response = self._home()
        sessions = response.context['current_module']['sessions']
        self.assertEqual(len(sessions), 1)
        self.assertTrue(sessions[0]['date_to_be_announced'])
        self.assertEqual(sessions[0]['actions'], [])
        self.assertContains(response, 'data-testid="course-home-live-session-tba"')
        self.assertContains(response, 'Date to be announced')
        self.assertNotContains(response, 'Not scheduled')
        self.assertNotContains(response, 'Open session')

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

    def test_next_session_block_shows_the_cohorts_next_session_even_in_this_module(self):
        self._session(2, days=2, title='Session two event')
        self._session(3, days=9, title='Session three event')
        response = self._home()
        self.assertEqual(response.context['home_next_session']['display_title'], 'Session 2')
        self.assertContains(response, 'data-testid="course-home-next-session"')
        sessions = response.context['current_module']['sessions']
        self.assertEqual(
            [action['label'] for action in sessions[0]['actions']], ['Open session'],
        )

    def test_homework_tab_rows_name_the_module_with_due_date_and_status(self):
        self.client.force_login(self.user)
        response = self.client.get(f'/courses/{self.course.slug}/home/homework?cohort=4')

        rows = response.context['homework_rows']
        self.assertEqual(
            {row['title'] for row in rows}, {'Retrieval homework', 'Earlier homework'},
        )
        self.assertEqual({row['status'] for row in rows}, {'Not submitted'})
        self.assertContains(response, 'data-testid="course-home-deadline-row"', count=2)
        # The tab already says Homework; each row names its module instead.
        self.assertNotContains(response, 'data-testid="course-home-deadline-kind"')
        self.assertContains(
            response,
            'data-testid="course-home-deadline-module">Retrieval in practice</span>',
        )
        # Due date and status share the meta line.
        self.assertContains(response, 'data-testid="course-home-deadline-status"', count=2)
        self.assertContains(
            response, '<h2 id="course-homework-heading" class="sr-only">Homework</h2>',
            html=True,
        )

    def test_sessions_tab_course_session_card_has_status_badge_not_tier_or_tags(self):
        self._session(
            2, days=-1, title='Session 2', tags=['office-hours'],
            recording_url='https://www.youtube.com/watch?v=abc123',
        )
        self.client.force_login(self.user)
        response = self.client.get(f'/courses/{self.course.slug}/home/sessions?cohort=4')

        self.assertContains(response, 'data-testid="past-recording-card"', count=1)
        self.assertContains(response, 'data-testid="course-home-live-session-status"')
        self.assertNotContains(response, 'data-testid="past-card-recording-tier"')
        self.assertNotContains(response, 'data-testid="event-card-tags"')
        self.assertNotContains(response, 'Session 2 · Past')

    def test_unlinked_learner_sessions_projects_and_homework_tabs_show_empty_states(self):
        CourseProject.objects.create(
            course=self.course, cohort=None, slug='preview-attempt', title='Preview attempt',
            submission_due_at=timezone.now() + datetime.timedelta(days=20),
            review_due_at=timezone.now() + datetime.timedelta(days=27),
        )
        learner = User.objects.create_user(
            email='current-module-unlinked@example.com', email_verified=True,
        )
        self.client.force_login(learner)

        sessions = self.client.get(f'/courses/{self.course.slug}/home/sessions')
        projects = self.client.get(f'/courses/{self.course.slug}/home/projects')
        homework = self.client.get(f'/courses/{self.course.slug}/home/homework')

        self.assertIsNone(sessions.context['cohort'])
        # Authored session units exist, but no placeholder cards render.
        self.assertTrue(sessions.context['live_session_schedule'])
        self.assertContains(sessions, 'data-testid="course-home-sessions-no-cohort"')
        self.assertNotContains(sessions, 'data-testid="course-home-live-session-row"')
        self.assertTrue(projects.context['project_rows'])
        self.assertContains(projects, 'data-testid="course-home-projects-no-cohort"')
        self.assertNotContains(projects, 'data-testid="course-home-deadline-row"')
        self.assertNotContains(projects, 'Preview attempt')
        self.assertContains(homework, 'data-testid="course-home-homework-no-cohort"')


class NoCohortHomeTests(CurrentModuleFixture):
    """A learner with course access but no cohort, on the same course."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.learner = User.objects.create_user(
            email='no-cohort-home@example.com', email_verified=True,
        )

    def _home(self):
        self.client.force_login(self.learner)
        return self.client.get(f'/courses/{self.course.slug}/home')

    def test_cohort_picker_comes_first_and_next_session_is_left_to_it(self):
        start = timezone.now() + datetime.timedelta(days=3)
        Event.objects.create(
            event_series=self.series, slug='no-cohort-session', title='Session 2',
            status='upcoming', start_datetime=start,
            end_datetime=start + datetime.timedelta(hours=1), series_position=2,
        )
        response = self._home()
        html = response.content.decode()
        self.assertLess(
            html.index('data-testid="course-home-no-cohort"'),
            html.index('id="course-home-syllabus-search"'),
        )
        self.assertEqual(
            [cohort.name for cohort in response.context['pickable_cohorts']], ['Cohort 4'],
        )
        self.assertContains(response, 'data-testid="course-home-cohort-picker"')
        self.assertContains(response, 'data-testid="course-home-cohort-support"')
        # No cohort: no dated "Due next" and no next-session block.
        self.assertIsNone(response.context['due_next'])
        self.assertIsNone(response.context['home_next_session'])
        self.assertNotContains(response, 'data-testid="course-home-next-session"')

    def test_card_says_your_next_module_and_rows_carry_counts_status_and_action(self):
        response = self._home()
        card = response.context['current_module']
        self.assertEqual(card['eyebrow'], 'Your next module')
        self.assertContains(
            response, 'data-testid="course-home-current-module-eyebrow">Your next module',
        )
        rows = {row['title']: row for row in card['deliverables']}
        row = rows['Earlier homework'] if card['module'] == self.first else rows['Retrieval homework']
        self.assertEqual(row['status'], 'Not started')
        self.assertEqual(row['action'], 'Start homework')
        self.assertIsNone(row['when'])

    def test_choosing_a_cohort_links_it_and_home_shows_its_sessions(self):
        start = timezone.now() + datetime.timedelta(days=3)
        Event.objects.create(
            event_series=self.series, slug='picked-cohort-session', title='Session 2',
            status='upcoming', start_datetime=start,
            end_datetime=start + datetime.timedelta(hours=1), series_position=2,
        )
        self.client.force_login(self.learner)
        response = self.client.post(
            f'/api/courses/{self.course.slug}/cohorts/{self.cohort.pk}/enroll',
        )
        self.assertEqual(response.json(), {'enrolled': True, 'cohort_id': self.cohort.pk})
        self.assertTrue(
            CohortEnrollment.objects.filter(user=self.learner, cohort=self.cohort).exists()
        )
        home = self.client.get(f'/courses/{self.course.slug}/home?cohort=4')
        self.assertEqual(home.context['cohort'], self.cohort)
        self.assertNotContains(home, 'data-testid="course-home-no-cohort"')
        self.assertEqual(home.context['home_next_session']['display_title'], 'Session 2')


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
