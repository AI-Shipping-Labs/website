"""Canonical first-level URLs for Buildcamp homework and capstone units."""

import datetime
import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from content.models import Course, Module, Unit
from content.models.cohort import Cohort, CohortEnrollment
from content.models.homework import Homework, Question, QuestionType

User = get_user_model()


class BuildcampHomeworkCanonicalRoutesTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(
            title='AI Engineering Buildcamp', slug='ai-buildcamp',
            status='published', required_level=0,
        )
        cls.foundation = Module.objects.create(
            course=cls.course, title='Foundations', slug='foundation', sort_order=1,
        )
        cls.agents = Module.objects.create(
            course=cls.course, title='Agents', slug='agents', sort_order=2,
        )
        cls.foundation_homework, cls.foundation_main, cls.foundation_capstone = (
            cls._make_homework_group(cls.foundation, 1)
        )
        cls.agents_homework, cls.agents_main, cls.agents_capstone = (
            cls._make_homework_group(cls.agents, 3)
        )

    @classmethod
    def _make_homework_group(cls, parent, week):
        homework = Module.objects.create(
            course=cls.course, parent=parent, title='Homework', slug='homework',
            sort_order=90,
        )
        main = Unit.objects.create(
            module=homework,
            title=f'Week {week} Homework: Build an Agent',
            slug='build-an-agent' if week > 1 else 'document-processing',
            kind='homework',
            sort_order=1,
            content_id=uuid.uuid4(),
            is_preview=True,
            homework='## Question 1. First\nDescribe your approach.',
        )
        capstone = Unit.objects.create(
            module=homework,
            title=f'Week {week} Capstone: Extend Your Project',
            slug='capstone-ai-project' if week == 1 else 'capstone-building-agents',
            kind='homework',
            sort_order=2,
            content_id=uuid.uuid4(),
            is_preview=True,
            homework='## Question 1. First\nDescribe the capstone step.',
        )
        return homework, main, capstone

    def _assert_route_resolves_to(self, url, expected_unit):
        response = self.client.get(url)
        self.assertContains(response, expected_unit.title)
        self.assertEqual(response.context['unit'].pk, expected_unit.pk)

    def test_module_one_assignment_uses_the_homework_module_path(self):
        expected = '/courses/ai-buildcamp/foundation/homework'
        self.assertEqual(self.foundation_main.get_absolute_url(), expected)
        self._assert_route_resolves_to(expected, self.foundation_main)

    def test_capstone_uses_a_sibling_path_and_not_the_old_duplicate_path(self):
        expected = '/courses/ai-buildcamp/foundation/homework-capstone'
        self.assertEqual(self.foundation_capstone.get_absolute_url(), expected)
        self._assert_route_resolves_to(expected, self.foundation_capstone)
        old_response = self.client.get(
            '/courses/ai-buildcamp/foundation/homework/capstone-ai-project',
        )
        self.assertEqual(old_response.status_code, 404, old_response.get('Location'))

    def test_weekly_homework_and_capstone_use_the_same_canonical_pattern(self):
        homework_url = '/courses/ai-buildcamp/agents/homework'
        capstone_url = '/courses/ai-buildcamp/agents/homework-capstone'
        self.assertEqual(self.agents_main.get_absolute_url(), homework_url)
        self.assertEqual(self.agents_capstone.get_absolute_url(), capstone_url)
        self._assert_route_resolves_to(homework_url, self.agents_main)
        self._assert_route_resolves_to(capstone_url, self.agents_capstone)

    def test_old_duplicate_homework_path_is_not_redirected(self):
        old_url = '/courses/ai-buildcamp/foundation/homework/document-processing'
        response = self.client.get(old_url)
        self.assertEqual(response.status_code, 404, response.get('Location'))

    def test_syllabus_and_week_overview_show_homework_units_as_sibling_rows(self):
        for url, testid in (
            ('/courses/ai-buildcamp', 'syllabus-unit-row'),
            ('/courses/ai-buildcamp/foundation', 'module-lesson-link'),
        ):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertContains(response, f'data-testid="{testid}"')
                self.assertContains(
                    response,
                    f'href="{self.foundation_main.get_absolute_url()}"',
                )
                self.assertContains(
                    response,
                    f'href="{self.foundation_capstone.get_absolute_url()}"',
                )

        syllabus = self.client.get('/courses/ai-buildcamp')
        self.assertNotContains(syllabus, 'data-testid="syllabus-module"')
        api_week = self.client.get('/api/courses/ai-buildcamp').json()['syllabus'][0]
        self.assertEqual(
            [unit['title'] for unit in api_week['units']],
            [self.foundation_main.title, self.foundation_capstone.title],
        )
        self.assertEqual(api_week['modules'], [])

    def test_reader_navigation_shows_homework_units_in_full_and_scoped_modes(self):
        user = User.objects.create_user(email='buildcamp-inline-reader@test.com', password='pw')
        self.client.force_login(user)

        for scope in ('course', 'module'):
            with self.subTest(scope=scope):
                self.course.reader_navigation_scope = scope
                self.course.save(update_fields=['reader_navigation_scope'])
                response = self.client.get(self.foundation_main.get_absolute_url())
                sidebar = response.content.decode().split(
                    '<nav id="sidebar-nav"', 1,
                )[1].split('</nav>', 1)[0]

                self.assertIn(self.foundation_main.get_absolute_url(), sidebar)
                self.assertIn(self.foundation_main.title, sidebar)
                self.assertIn(self.foundation_capstone.get_absolute_url(), sidebar)
                self.assertIn(self.foundation_capstone.title, sidebar)

    def test_inline_homework_reader_keeps_step_navigation(self):
        user = User.objects.create_user(email='buildcamp-homework-reader@test.com', password='pw')
        self.client.force_login(user)
        today = timezone.localdate()
        cohort = Cohort.objects.create(
            course=self.course,
            name='Reader test cohort',
            start_date=today - datetime.timedelta(days=1),
            end_date=today + datetime.timedelta(days=30),
        )
        CohortEnrollment.objects.create(cohort=cohort, user=user)
        self.course.reader_navigation_scope = 'module'
        self.course.save(update_fields=['reader_navigation_scope'])
        self.foundation_main.homework = '## Question 1. First step\nDescribe your approach.'
        self.foundation_main.save(update_fields=['homework'])
        homework = Homework.objects.create(
            content_id=self.foundation_main.content_id,
            cohort=cohort,
            slug='foundation-homework',
            title=self.foundation_main.title,
            stepper_enabled=True,
        )
        Question.objects.create(
            homework=homework,
            source_question_id='q1-first-step',
            text='Describe your approach.',
            question_type=QuestionType.FREE_FORM,
        )

        response = self.client.get(self.foundation_main.get_absolute_url())
        self.assertContains(response, 'data-testid="homework-step-nav"')
        self.assertContains(response, 'data-testid="homework-step-current"')
        sidebar = response.content.decode().split(
            '<nav id="sidebar-nav"', 1,
        )[1].split('</nav>', 1)[0]
        self.assertIn(self.foundation_capstone.get_absolute_url(), sidebar)

    def test_real_submodule_slug_takes_precedence_over_capstone_alias(self):
        colliding_module = Module.objects.create(
            course=self.course, parent=self.foundation,
            title='Separate capstone materials', slug='homework-capstone',
            sort_order=91,
        )
        colliding_unit = Unit.objects.create(
            module=colliding_module, title='Capstone instructions', slug='instructions',
            kind='lesson', sort_order=1, content_id=uuid.uuid4(), body='Read this.'
        )

        self.assertEqual(
            self.foundation_capstone.get_absolute_url(),
            '/courses/ai-buildcamp/foundation/homework/capstone-ai-project',
        )
        response = self.client.get('/courses/ai-buildcamp/foundation/homework-capstone')
        self.assertContains(response, colliding_unit.title)
