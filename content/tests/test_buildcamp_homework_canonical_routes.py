"""Canonical first-level URLs for Buildcamp homework and capstone units."""

import uuid

from django.test import TestCase

from content.models import Course, Module, Unit


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
        self.assertEqual(response.status_code, 200)
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
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, colliding_unit.title)
