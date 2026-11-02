from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from accounts.models import User
from content.models import Course, Module, Unit


class CourseHomeSearchIndexTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            email='course-home-index@example.com', password='testpass',
            email_verified=True,
        )
        cls.course = Course.objects.create(
            title='Searchable course', slug='course-home-index',
            status='published', required_level=0,
        )
        module = Module.objects.create(
            course=cls.course, title='Retrieval basics', slug='retrieval-basics',
            sort_order=1,
        )
        cls.unit = Unit.objects.create(
            module=module, title='Vector embeddings explained',
            slug='vector-embeddings', sort_order=1,
        )

    def setUp(self):
        self.client.force_login(self.user)

    def _course_home_query_count(self):
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get('/courses/course-home-index/home')
        self.assertEqual(response.status_code, 200)
        return len(queries)

    def test_home_uses_compact_syllabus_search_index(self):
        response = self.client.get('/courses/course-home-index/home')

        self.assertContains(response, self.unit.get_absolute_url())
        self.assertContains(response, 'Vector embeddings explained')
        self.assertContains(response, 'data-cb-syllabus-search-item')
        self.assertNotContains(response, 'data-testid="syllabus-module-content"')
        self.assertNotContains(response, 'data-testid="syllabus-unit-right-meta"')

    def test_home_search_index_query_count_is_constant_across_modules(self):
        self.client.get('/courses/course-home-index/home')
        small_count = self._course_home_query_count()
        for index in range(2, 7):
            module = Module.objects.create(
                course=self.course,
                title=f'Module {index}',
                slug=f'module-{index}',
                sort_order=index,
            )
            Unit.objects.create(
                module=module,
                title=f'Lesson {index}',
                slug=f'lesson-{index}',
                sort_order=1,
            )

        large_count = self._course_home_query_count()

        self.assertEqual(
            small_count,
            large_count,
            msg=(
                'Course-home search index queries grew with curriculum size '
                f'({small_count} -> {large_count})'
            ),
        )
        self.assertLessEqual(large_count, 40)
