"""The course's Evaluation criteria page, generated from course.yaml."""

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from accounts.models import User
from content.models import Cohort, CohortEnrollment, Course, Module, ProjectSubmission, Unit
from content.models.peer_review import CourseProject

CRITERIA = [
    {
        'id': 'testing', 'title': 'Testing', 'description': 'Your testing strategy.',
        'type': 'scale',
        'options': [{'points': 0, 'text': 'No tests'}, {'points': 2, 'text': 'Judge tests'}],
    },
    {
        'id': 'extra', 'title': 'Extra', 'description': '', 'type': 'checklist',
        'options': [{'points': 1, 'text': 'UI'}],
    },
]
PAGE_URL = '/courses/criteria-course/project-criteria'


def _between(content, marker, end='</ul>'):
    start = content.index(marker)
    return content[start:content.index(end, start)]


class ProjectCriteriaPageTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.learner = User.objects.create_user(
            email='criteria-page@example.com', password='pw', email_verified=True,
        )
        cls.course = Course.objects.create(
            title='Criteria Course', slug='criteria-course', status='published',
            required_level=0, peer_review_enabled=True, peer_review_count=2,
            reader_navigation_scope='module',
            project_criteria_intro='All criteria are *optional*.',
            project_criteria_outro='## Final Notes\n\nOutro prose.',
        )
        cls.module = Module.objects.create(
            course=cls.course, title='Capstone', slug='capstone', sort_order=1,
        )
        Unit.objects.create(
            module=cls.module, title='Project Guidelines', slug='guidelines',
            sort_order=1, body='Guidelines prose.',
        )
        today = timezone.localdate()
        cohort = Cohort.objects.create(
            course=cls.course, name='Criteria cohort', external_key='crit',
            start_date=today - timedelta(days=1), end_date=today + timedelta(days=90),
        )
        CohortEnrollment.objects.create(cohort=cohort, user=cls.learner)
        cls.project = CourseProject.objects.create(
            course=cls.course, cohort=cohort, module=cls.module,
            slug='attempt-1', title='Capstone Attempt 1',
            submission_due_at=timezone.now() + timedelta(days=3),
            review_due_at=timezone.now() + timedelta(days=10),
            evaluation_criteria=CRITERIA,
        )

    def test_page_renders_yaml_intro_totals_criteria_outro_and_cta_in_order(self):
        self.client.force_login(self.learner)
        response = self.client.get(PAGE_URL)
        self.assertContains(response, 'data-testid="criteria-page-title">Evaluation criteria</h1>')
        content = response.content.decode()
        body = content[content.index('data-testid="criteria-page-body"'):]
        order = [
            body.index('All criteria are <em>optional</em>.'),
            body.index('Up to 2 points, plus up to 1 bonus points.'),
            body.index('<h2 id="testing">Testing</h2>'),
            body.index('<li>Judge tests: 2 points</li>'),
            body.index('Outro prose.'),
            body.index('data-testid="criteria-page-cta"'),
        ]
        self.assertEqual(order, sorted(order))
        cta = _between(body, 'data-testid="criteria-page-cta"', '</p>')
        self.assertIn('href="/courses/criteria-course/projects/attempt-1/submit"', cta)
        self.assertIn('Open Capstone Attempt 1</a>, due', cta)

    def test_breadcrumb_names_module_and_page(self):
        self.client.force_login(self.learner)
        content = self.client.get(PAGE_URL).content.decode()
        crumb = _between(content, 'aria-label="Breadcrumb"', '</nav>')
        self.assertIn('Home', crumb)
        self.assertIn('>Capstone</a>', crumb)
        self.assertIn('aria-current="page">Evaluation criteria</span>', crumb)

    def test_no_cta_once_the_attempt_is_closed(self):
        CourseProject.objects.filter(pk=self.project.pk).update(state='PR')
        self.client.force_login(self.learner)
        response = self.client.get(PAGE_URL)
        self.assertContains(response, '<h2 id="testing">Testing</h2>')
        self.assertNotContains(response, 'criteria-page-cta')

    def test_no_cta_once_the_learner_submitted(self):
        ProjectSubmission.objects.create(
            user=self.learner, course=self.course, course_project=self.project,
            project_url='https://github.com/x/y',
        )
        self.client.force_login(self.learner)
        response = self.client.get(PAGE_URL)
        self.assertContains(response, '<h2 id="testing">Testing</h2>')
        self.assertNotContains(response, 'criteria-page-cta')

    def test_course_without_criteria_is_404(self):
        CourseProject.objects.filter(course=self.course).update(evaluation_criteria=[])
        self.client.force_login(self.learner)
        self.assertEqual(self.client.get(PAGE_URL).status_code, 404)

    def test_anonymous_visitor_is_sent_to_login(self):
        response = self.client.get(PAGE_URL)
        self.assertEqual(response.status_code, 302)
        self.assertIn('login', response['Location'])

    def test_course_without_peer_review_is_404(self):
        self.course.peer_review_enabled = False
        self.course.save()
        self.client.force_login(self.learner)
        self.assertEqual(self.client.get(PAGE_URL).status_code, 404)

    def test_lesson_sidebar_lists_criteria_row_after_attempt_rows(self):
        self.client.force_login(self.learner)
        content = self.client.get('/courses/criteria-course/capstone/guidelines').content.decode()
        self.assertLess(
            content.index('data-testid="reader-project-rows"'),
            content.index('data-testid="reader-criteria-row"'),
        )
        row = _between(content, 'data-testid="reader-criteria-row"', '</a>')
        self.assertIn('Evaluation criteria', row)
        self.assertNotIn('aria-current', row)
        anchor_start = content.rindex('<a', 0, content.index('data-testid="reader-criteria-row"'))
        self.assertIn(f'href="{PAGE_URL}"', content[anchor_start:content.index('data-testid="reader-criteria-row"')])

    def test_criteria_row_is_current_on_the_page_and_attempt_row_is_not(self):
        self.client.force_login(self.learner)
        content = self.client.get(PAGE_URL).content.decode()
        row = _between(content, 'data-testid="reader-criteria-row"', '</a>')
        self.assertIn('aria-current="page"', row)
        attempts = _between(content, 'data-testid="reader-project-rows"')
        self.assertNotIn('aria-current', attempts)

    def test_no_criteria_row_without_criteria(self):
        CourseProject.objects.filter(course=self.course).update(evaluation_criteria=[])
        self.client.force_login(self.learner)
        response = self.client.get('/courses/criteria-course/capstone/guidelines')
        self.assertContains(response, 'data-testid="reader-project-rows"')
        self.assertNotContains(response, 'reader-criteria-row')

    def test_submit_page_links_to_the_criteria_page(self):
        self.client.force_login(self.learner)
        response = self.client.get('/courses/criteria-course/projects/attempt-1/submit')
        self.assertContains(
            response,
            f'<a href="{PAGE_URL}" class="text-accent underline underline-offset-2 '
            'hover:text-foreground" data-testid="criteria-lesson-link">'
            'Read the evaluation criteria</a>',
        )
