"""Server-rendered syllabus states for issue #1770."""

from django.test import TestCase

from content.models import Course, Module, Unit


class CourseSyllabusEmptyAndFlatTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.empty_course = Course.objects.create(
            title='No curriculum yet', slug='empty-syllabus-1770',
            status='published', required_level=0,
        )
        cls.flat_course = Course.objects.create(
            title='Flat curriculum', slug='flat-syllabus-1770',
            status='published', required_level=0,
        )
        cls.flat_module = Module.objects.create(
            course=cls.flat_course, title='Getting started',
            slug='getting-started', sort_order=1,
        )
        cls.flat_unit = Unit.objects.create(
            module=cls.flat_module, title='First lesson',
            slug='first-lesson', sort_order=1,
        )

    def test_empty_course_explains_missing_syllabus(self):
        response = self.client.get('/courses/empty-syllabus-1770')
        self.assertContains(response, 'data-testid="syllabus-empty-state"')
        self.assertContains(response, 'Syllabus coming soon')
        self.assertNotContains(response, 'data-testid="syllabus-module-summary"')

    def test_flat_module_keeps_its_direct_lesson_link(self):
        response = self.client.get('/courses/flat-syllabus-1770')
        self.assertContains(response, 'data-testid="syllabus-module-summary"')
        self.assertContains(response, '1 lesson</span>')
        self.assertContains(response, f'href="{self.flat_unit.get_absolute_url()}"')


class CourseSyllabusParentSummaryTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        course = Course.objects.create(
            title='Nested curriculum', slug='nested-counts-1770',
            status='published', required_level=0,
        )
        week = Module.objects.create(
            course=course, title='Week one', slug='week-one', sort_order=1,
        )
        main_topic = Module.objects.create(
            course=course, parent=week, title='Main topic',
            slug='main-topic', sort_order=1,
        )
        bonus_topic = Module.objects.create(
            course=course, parent=week, title='Bonus topic',
            slug='bonus-topic', sort_order=2, is_bonus=True,
        )
        Unit.objects.create(module=main_topic, title='Lesson one', slug='one', sort_order=1)
        Unit.objects.create(module=main_topic, title='Lesson two', slug='two', sort_order=2)
        Unit.objects.create(module=bonus_topic, title='Bonus lesson', slug='bonus', sort_order=1)

    def test_week_summary_counts_topics_and_all_leaf_lessons(self):
        response = self.client.get('/courses/nested-counts-1770')
        self.assertContains(
            response,
            'data-testid="module-submodule-count">2 topics &middot; 3 lessons</span>',
        )
