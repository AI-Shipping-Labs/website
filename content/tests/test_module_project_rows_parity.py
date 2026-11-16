import re
from datetime import timedelta

from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from accounts.models import User
from content.models import (
    Cohort,
    CohortEnrollment,
    Course,
    Module,
    PeerReview,
    ProjectSubmission,
    Unit,
)
from content.models.peer_review import CourseProject
from content.services.project_reader import (
    criteria_row,
    module_project_rows,
    project_rows,
)
from content.services.syllabus_titles import section_heading_for, title_under_heading

CRITERIA = [{
    'id': 'problem', 'title': 'Problem', 'description': '', 'type': 'scale',
    'options': [{'points': 0, 'text': 'None'}, {'points': 2, 'text': 'Clear'}],
}]
BADGE = re.compile(r'data-homework-state="([a-z_]+)">([^<]+)<')


class ModuleProjectRowsParityTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            email='parity@example.com', password='pw', email_verified=True,
        )
        cls.peer = User.objects.create_user(email='parity-peer@example.com', password='pw')
        cls.course = Course.objects.create(
            title='Parity Course', slug='parity-course', status='published',
            required_level=0, peer_review_enabled=True, peer_review_count=1,
            reader_navigation_scope='module',
        )
        cls.module = Module.objects.create(
            course=cls.course, title='Capstone', slug='capstone', sort_order=1,
        )
        Unit.objects.create(module=cls.module, title='Lesson', slug='lesson', sort_order=1)
        today = timezone.localdate()
        cls.cohort = Cohort.objects.create(
            course=cls.course, name='Parity cohort', external_key='parity',
            start_date=today - timedelta(days=10), end_date=today + timedelta(days=90),
        )
        CohortEnrollment.objects.create(cohort=cls.cohort, user=cls.user)
        CohortEnrollment.objects.create(cohort=cls.cohort, user=cls.peer)
        now = timezone.now()
        cls.first = CourseProject.objects.create(
            course=cls.course, cohort=cls.cohort, module=cls.module,
            slug='attempt-1', title='Capstone Project Attempt 1', state='PR',
            submission_due_at=now - timedelta(days=1),
            review_due_at=now + timedelta(days=6),
            evaluation_criteria=CRITERIA,
        )
        cls.second = CourseProject.objects.create(
            course=cls.course, cohort=cls.cohort, module=cls.module,
            slug='attempt-2', title='Capstone Project Attempt 2',
            submission_due_at=now + timedelta(days=14),
            review_due_at=now + timedelta(days=21),
            evaluation_criteria=CRITERIA,
        )
        for author in (cls.user, cls.peer):
            ProjectSubmission.objects.create(
                user=author, course=cls.course, cohort=cls.cohort,
                course_project=cls.first, project_url='https://github.com/x/y',
                status='in_review',
            )
        peer_submission = ProjectSubmission.objects.get(user=cls.peer)
        PeerReview.objects.create(
            submission=peer_submission, reviewer=cls.user, is_complete=True,
        )
        ProjectSubmission.objects.create(
            user=cls.user, course=cls.course, cohort=cls.cohort,
            course_project=cls.second, project_url='https://github.com/x/z',
        )

    def _sidebar(self):
        return self.client.get('/courses/parity-course/capstone/lesson').content.decode()

    def _syllabus_rows(self, content):
        return re.findall(
            r'data-testid="(syllabus-project-attempt|syllabus-criteria-row)"(.*?)</div>',
            content, re.S,
        )

    def test_service_rows_match_the_sidebar_rows(self):
        sidebar = project_rows(self.user, self.course, self.module)
        criteria = criteria_row(self.course, self.module)
        syllabus = module_project_rows(
            self.user, self.course, [self.first, self.second],
        )[self.module.pk]
        self.assertEqual(
            [(r.kind, r.title, r.url, r.status and r.status.label) for r in syllabus],
            [(r.kind, r.title, r.url, r.status and r.status.label)
             for r in [*sidebar, criteria]],
        )

    def test_syllabus_lists_the_sidebar_badges_and_criteria_row(self):
        self.client.force_login(self.user)
        sidebar = self._sidebar()
        for path in ('/courses/parity-course/home/syllabus', '/courses/parity-course'):
            with self.subTest(path=path):
                content = self.client.get(path).content.decode()
                rows = self._syllabus_rows(content)
                self.assertEqual(
                    [kind for kind, _ in rows],
                    ['syllabus-project-attempt'] * 2 + ['syllabus-criteria-row'],
                )
                self.assertIn('href="/courses/parity-course/projects/attempt-1/reviews"', rows[0][1])
                self.assertIn('href="/courses/parity-course/projects/attempt-2/submit"', rows[1][1])
                self.assertIn('href="/courses/parity-course/project-criteria"', rows[2][1])
                self.assertIn('Evaluation criteria', rows[2][1])
                syllabus_badges = BADGE.findall(''.join(body for _, body in rows))
                self.assertEqual(syllabus_badges, BADGE.findall(
                    sidebar[sidebar.index('data-testid="reader-project-rows"'):],
                ))
                self.assertEqual(len(syllabus_badges), 2)
                self.assertEqual(syllabus_badges[1], ('submitted', 'Submitted'))
                self.assertNotIn('Peer reviews for', content)

    def test_visitor_sees_dates_only_rows_and_unlinked_criteria(self):
        content = self.client.get('/courses/parity-course').content.decode()
        self.assertEqual(self._syllabus_rows(content), [])
        content = self.client.get(
            '/courses/parity-course?cohort=parity',
        ).content.decode()
        rows = self._syllabus_rows(content)
        self.assertEqual(
            [kind for kind, _ in rows],
            ['syllabus-project-attempt'] * 2 + ['syllabus-criteria-row'],
        )
        body = ''.join(b for _, b in rows)
        self.assertNotIn('href=', body)
        self.assertNotIn('data-homework-state', body)
        self.assertIn('data-testid="syllabus-project-preview"', body)

    def test_no_criteria_row_without_criteria_or_peer_review(self):
        self.client.force_login(self.user)
        CourseProject.objects.filter(course=self.course).update(evaluation_criteria=[])
        content = self.client.get('/courses/parity-course/home/syllabus').content.decode()
        self.assertNotIn('syllabus-criteria-row', content)
        self.assertNotIn('reader-criteria-row', self._sidebar())


class SyllabusWeekTitleTest(SimpleTestCase):
    def test_strips_the_week_the_heading_shows(self):
        for title in ('Week 8 · Deployment', 'Week 8 - Deployment', 'week 8: Deployment',
                      'Week 8 — Deployment'):
            with self.subTest(title=title):
                self.assertEqual(title_under_heading(title, 'Week 8'), 'Deployment')

    def test_keeps_title_for_another_week_or_no_heading(self):
        self.assertEqual(title_under_heading('Week 8 · Deployment', 'Week 7'), 'Week 8 · Deployment')
        self.assertEqual(title_under_heading('Week 8 · Deployment', ''), 'Week 8 · Deployment')
        self.assertEqual(title_under_heading('Week 8 · Deployment', 'Main content'), 'Week 8 · Deployment')
        self.assertEqual(title_under_heading('Week 80 · Deployment', 'Week 8'), 'Week 80 · Deployment')
        self.assertEqual(title_under_heading('Week 8 Overview', 'Week 8'), 'Week 8 Overview')

    def test_never_returns_an_empty_title(self):
        self.assertEqual(title_under_heading('Week 8 ·', 'Week 8'), 'Week 8 ·')

    def test_heading_carries_over_to_following_siblings(self):
        class M:
            def __init__(self, pk, section=''):
                self.pk, self.syllabus_section = pk, section

        siblings = [M(1), M(2, 'Week 7'), M(3), M(4, 'Week 8'), M(5)]
        self.assertEqual(
            [section_heading_for(m, siblings) for m in siblings],
            ['', 'Week 7', 'Week 7', 'Week 8', 'Week 8'],
        )


class SyllabusWeekTitleRenderTest(TestCase):
    def test_syllabus_drops_the_repeated_week_label(self):
        course = Course.objects.create(
            title='Weeks Course', slug='weeks-course', status='published', required_level=0,
        )
        optional = Module.objects.create(
            course=course, title='Optional', slug='optional', sort_order=1, is_bonus=True,
        )
        for order, (title, section) in enumerate([
            ('Build a Coding Agent', 'Week 8'),
            ('Week 8 · Deployment', ''),
            ('Week 9 · Wrap-up', ''),
        ], start=1):
            child = Module.objects.create(
                course=course, parent=optional, title=title, slug=f'child-{order}',
                sort_order=order, is_bonus=True, syllabus_section=section,
            )
            Unit.objects.create(module=child, title=f'Lesson {order}', slug='lesson', sort_order=1)
        content = self.client.get('/courses/weeks-course').content.decode()
        self.assertIn('>Deployment</span>', content)
        self.assertNotIn('>Week 8 · Deployment</span>', content)
        self.assertIn('>Week 9 · Wrap-up</span>', content)
