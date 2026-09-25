"""Source-authored section labels for optional curriculum groups."""

from django.test import TestCase

from content.models import Course, Module, Unit


class OptionalSyllabusSectionRenderingTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(
            title='Portable optional sections', slug='portable-optional-sections',
            status='published', required_level=0,
        )
        foundations = Module.objects.create(
            course=cls.course, title='Foundations', slug='foundations',
            sort_order=1,
        )
        Unit.objects.create(
            module=foundations, title='Required lesson', slug='required-lesson',
            sort_order=1,
        )
        cls.optional = Module.objects.create(
            course=cls.course, title='Bonus', slug='bonus', sort_order=2,
            is_bonus=True,
        )
        cls.week_one_first = Module.objects.create(
            course=cls.course, parent=cls.optional, title='Search basics',
            slug='search-basics', sort_order=1, is_bonus=True,
            syllabus_section='Week 1',
        )
        Module.objects.create(
            course=cls.course, parent=cls.optional, title='Chunking patterns',
            slug='chunking-patterns', sort_order=2, is_bonus=True,
        )
        Module.objects.create(
            course=cls.course, parent=cls.optional, title='Agent tools',
            slug='agent-tools', sort_order=3, is_bonus=True,
            syllabus_section='Week 2',
        )
        Module.objects.create(
            course=cls.course, parent=cls.optional,
            title='Week 3: Legacy title only', slug='legacy-week-title',
            sort_order=4, is_bonus=True,
        )
        cls.optional_unit = Unit.objects.create(
            module=cls.week_one_first, title='Search lesson',
            slug='search-lesson', sort_order=1, is_preview=True,
        )

    def _assert_section_labels(self, response, testid):
        html = response.content.decode()
        self.assertEqual(
            html.count(f'data-testid="{testid}">Week 1</'), 1,
        )
        self.assertEqual(
            html.count(f'data-testid="{testid}">Week 2</'), 1,
        )
        self.assertNotIn(f'data-testid="{testid}">Week 3</', html)

    def test_course_syllabus_uses_source_sections_for_non_buildcamp_courses(self):
        response = self.client.get(self.course.get_absolute_url())

        self.assertEqual(response.status_code, 200)
        self._assert_section_labels(response, 'syllabus-module-section')

    def test_optional_module_overview_uses_source_sections(self):
        response = self.client.get(self.optional.get_absolute_url())

        self.assertEqual(response.status_code, 200)
        self._assert_section_labels(response, 'module-syllabus-section')

    def test_course_reader_sidebar_uses_source_sections(self):
        response = self.client.get(self.optional_unit.get_absolute_url())

        self.assertEqual(response.status_code, 200)
        self._assert_section_labels(response, 'reader-syllabus-section')
