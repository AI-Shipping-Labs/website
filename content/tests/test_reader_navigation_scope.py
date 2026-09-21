"""A source-managed course can keep the reader focused on its submodule."""

import datetime
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from content.models import Course, Module, Unit
from content.models.peer_review import CourseProject
from content.sync_parsers.common import GitHubSyncError
from content.sync_parsers.families.courses import _build_course_defaults


class ReaderNavigationScopeTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            email='scoped-reader@example.com', password='pw', email_verified=True,
        )
        cls.course = Course.objects.create(
            title='Scoped course', slug='scoped-reader', status='published',
            required_level=0, reader_navigation_scope='module',
        )
        week1 = Module.objects.create(
            course=cls.course, title='Week 1', slug='week-1', sort_order=1,
        )
        cls.first = Module.objects.create(
            course=cls.course, parent=week1, title='First topic',
            slug='first', sort_order=1,
        )
        cls.middle = Module.objects.create(
            course=cls.course, parent=week1, title='Middle topic',
            slug='middle', sort_order=2,
        )
        week2 = Module.objects.create(
            course=cls.course, title='Week 2', slug='week-2', sort_order=2,
        )
        cls.last = Module.objects.create(
            course=cls.course, parent=week2, title='Last topic',
            slug='last', sort_order=1,
        )
        for topic in (cls.first, cls.middle, cls.last):
            Unit.objects.create(
                module=topic, title=f'{topic.title} lesson',
                slug='lesson', sort_order=1,
            )

    def setUp(self):
        self.client.force_login(self.user)

    def reader(self, module):
        return self.client.get(
            f'/courses/scoped-reader/{module.parent.slug}/{module.slug}/lesson'
        )

    def test_current_week_includes_all_its_topics_but_not_other_weeks(self):
        response = self.reader(self.middle)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="reader-scoped-module"')
        sidebar = response.content.decode().split('<nav id="sidebar-nav"', 1)[1].split('</nav>', 1)[0]
        self.assertIn('Middle topic lesson', sidebar)
        self.assertIn('First topic lesson', sidebar)
        self.assertNotIn('Last topic lesson', sidebar)
        self.assertLess(sidebar.index('First topic lesson'), sidebar.index('Middle topic lesson'))
        self.assertIn('aria-current="page"', sidebar)
        self.assertIn('data-testid="reader-view-syllabus"', sidebar)
        self.assertEqual(response.context['prev_unit'].title, 'First topic lesson')
        self.assertEqual(response.context['next_unit'].title, 'Last topic lesson')

    def test_adjacent_module_links_follow_top_level_weeks(self):
        response = self.reader(self.middle)
        sidebar = response.content.decode().split('<nav id="sidebar-nav"', 1)[1].split('</nav>', 1)[0]
        self.assertNotIn('data-testid="reader-previous-module"', sidebar)
        self.assertIn('href="/courses/scoped-reader/week-2"', sidebar)
        self.assertIn('href="/courses/scoped-reader#syllabus"', sidebar)
        first = self.reader(self.first)
        self.assertIsNone(first.context['previous_module'])
        last = self.reader(self.last)
        self.assertEqual(last.context['previous_module'].slug, 'week-1')
        self.assertIsNone(last.context['next_module'])

    def test_adjacent_module_navigation_preserves_explicit_cohort(self):
        response = self.client.get(
            '/courses/scoped-reader/week-1/middle/lesson?cohort=4',
        )
        sidebar = response.content.decode().split('<nav id="sidebar-nav"', 1)[1].split('</nav>', 1)[0]
        self.assertIn('href="/courses/scoped-reader/week-2?cohort=4"', sidebar)
        self.assertIn('href="/courses/scoped-reader/week-1/first/lesson?cohort=4"', sidebar)
        self.assertIn('href="/courses/scoped-reader?cohort=4#syllabus"', sidebar)
        self.assertEqual(response.context['prev_item_url'], '/courses/scoped-reader/week-1/first/lesson?cohort=4')
        redirected = self.client.get('/courses/scoped-reader/week-2?cohort=4')
        self.assertRedirects(
            redirected, '/courses/scoped-reader/week-2/last/lesson?cohort=4',
            fetch_redirect_response=False,
        )

    def test_legacy_submodule_value_uses_current_week(self):
        self.course.reader_navigation_scope = 'submodule'
        self.course.save(update_fields=['reader_navigation_scope'])
        response = self.reader(self.middle)
        sidebar = response.content.decode().split('<nav id="sidebar-nav"', 1)[1].split('</nav>', 1)[0]
        self.assertIn('First topic lesson', sidebar)
        self.assertNotIn('Last topic lesson', sidebar)

    def test_default_course_scope_preserves_full_outline(self):
        self.course.reader_navigation_scope = 'course'
        self.course.save(update_fields=['reader_navigation_scope'])
        response = self.reader(self.middle)
        sidebar = response.content.decode().split('<nav id="sidebar-nav"', 1)[1].split('</nav>', 1)[0]
        self.assertNotIn('data-testid="reader-scoped-module"', sidebar)
        self.assertIn('First topic lesson', sidebar)
        self.assertIn('Last topic lesson', sidebar)

    def test_source_scope_is_validated(self):
        source = SimpleNamespace(repo_name='content')
        args = (
            'scoped-reader', 'f876ab29-1f46-45f2-8284-ab86182b76aa',
            '/nonexistent/course', 'course', source, 'a' * 40, [],
        )
        defaults = _build_course_defaults(
            {'title': 'Scoped', 'description': 'A course', 'reader_navigation_scope': 'module'}, *args,
        )
        self.assertEqual(defaults['reader_navigation_scope'], 'module')
        self.assertEqual(_build_course_defaults({'title': 'Default', 'description': 'A course'}, *args)['reader_navigation_scope'], 'course')
        with self.assertRaises(GitHubSyncError):
            _build_course_defaults(
                {'title': 'Invalid', 'description': 'A course', 'reader_navigation_scope': 'lessons'}, *args,
            )

    def test_scoped_module_overviews_open_first_lesson(self):
        top = self.client.get('/courses/scoped-reader/week-1')
        self.assertRedirects(
            top, '/courses/scoped-reader/week-1/first/lesson',
            fetch_redirect_response=False,
        )
        topic = self.client.get('/courses/scoped-reader/week-1/middle')
        self.assertRedirects(
            topic, '/courses/scoped-reader/week-1/middle/lesson',
            fetch_redirect_response=False,
        )

    def test_parent_overview_skips_event_for_first_lesson_page(self):
        week = Module.objects.create(
            course=self.course, title='Event then lesson',
            slug='event-then-lesson', sort_order=4,
        )
        session = Module.objects.create(
            course=self.course, parent=week, title='Session',
            slug='session', sort_order=1,
        )
        event = Unit.objects.create(
            module=session, title='Live session', slug='live-session',
            sort_order=1, kind='event', session_position=1,
        )
        lesson_module = Module.objects.create(
            course=self.course, parent=week, title='Overview',
            slug='overview', sort_order=2,
        )
        lesson = Unit.objects.create(
            module=lesson_module, title='Week overview',
            slug='week-overview', sort_order=1, kind='lesson',
        )
        response = self.client.get('/courses/scoped-reader/event-then-lesson')
        self.assertRedirects(response, lesson.get_absolute_url(), fetch_redirect_response=False)
        # A module containing only events still has a usable destination.
        lesson_module.delete()
        response = self.client.get('/courses/scoped-reader/event-then-lesson')
        self.assertRedirects(response, event.get_absolute_url(), fetch_redirect_response=False)

    def test_empty_scoped_modules_keep_overview(self):
        empty = Module.objects.create(
            course=self.course, title='Empty', slug='empty', sort_order=3,
        )
        response = self.client.get('/courses/scoped-reader/empty')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['module'], empty)

    def test_course_scope_keeps_module_overviews(self):
        self.course.reader_navigation_scope = 'course'
        self.course.save(update_fields=['reader_navigation_scope'])
        response = self.client.get('/courses/scoped-reader/week-1')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['module'].slug, 'week-1')

    def test_project_attempt_module_keeps_overview_and_attempt_cards(self):
        now = timezone.now()
        CourseProject.objects.create(
            course=self.course, module=self.middle,
            slug='attempt-1', title='Project attempt 1',
            submission_due_at=now + datetime.timedelta(days=7),
            review_due_at=now + datetime.timedelta(days=14),
        )
        response = self.client.get('/courses/scoped-reader/week-1/middle')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['module'], self.middle)
        self.assertContains(response, 'Project attempt 1')
