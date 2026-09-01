"""Tests for Course Admin CRUD - issue #80.

Covers:
- Admin CRUD operations for courses, modules, units
- Retired reorder APIs fail closed without changing course structure
- Status transitions (draft -> published, published -> draft)
"""

import json

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import Resolver404, resolve

from content.models import Course, Module, Unit

User = get_user_model()


# ============================================================
# Admin Functional Tests (via Django admin views)
# ============================================================


class CourseAdminCRUDTest(TestCase):
    """Test admin CRUD operations for courses."""

    def setUp(self):
        self.client = Client()
        self.admin_user = User.objects.create_superuser(
            email='admin@test.com', password='testpass',
        )
        self.client.login(email='admin@test.com', password='testpass')

    def test_course_add_page_loads(self):
        response = self.client.get('/admin/content/course/add/')
        self.assertEqual(response.status_code, 200)

    def test_course_list_shows_courses(self):
        Course.objects.create(
            title='Admin Test Course', slug='admin-test',
            status='published',
        )
        response = self.client.get('/admin/content/course/')
        self.assertContains(response, 'Admin Test Course')

    def test_course_list_filterable_by_status(self):
        Course.objects.create(
            title='Draft Course', slug='draft-filter',
            status='draft',
        )
        Course.objects.create(
            title='Published Course', slug='pub-filter',
            status='published',
        )
        # Filter by draft status
        response = self.client.get('/admin/content/course/?status__exact=draft')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Draft Course')

    def test_course_edit_page_loads(self):
        course = Course.objects.create(
            title='Edit Me', slug='edit-me', status='draft',
        )
        response = self.client.get(f'/admin/content/course/{course.pk}/change/')
        self.assertEqual(response.status_code, 200)

    def test_course_edit_has_module_inline(self):
        course = Course.objects.create(
            title='With Modules', slug='with-modules',
        )
        response = self.client.get(f'/admin/content/course/{course.pk}/change/')
        self.assertContains(response, 'modules-')

    def test_admin_create_course_via_post(self):
        """Test creating a course via admin form POST."""
        response = self.client.post('/admin/content/course/add/', {
            'title': 'New Course',
            'slug': 'new-course',
            'description': 'A new course description.',
            'cover_image_url': 'https://example.com/cover.jpg',
            'tags': '["python", "django"]',
            'required_level': 0,
            'status': 'draft',
            'discussion_url': 'https://github.com/test',
            # Module inline management form
            'modules-TOTAL_FORMS': '0',
            'modules-INITIAL_FORMS': '0',
            'modules-MIN_NUM_FORMS': '0',
            'modules-MAX_NUM_FORMS': '1000',
            # Cohort inline management form
            'cohorts-TOTAL_FORMS': '0',
            'cohorts-INITIAL_FORMS': '0',
            'cohorts-MIN_NUM_FORMS': '0',
            'cohorts-MAX_NUM_FORMS': '1000',
            # CourseInstructor through-model inline (issue #308)
            'courseinstructor_set-TOTAL_FORMS': '0',
            'courseinstructor_set-INITIAL_FORMS': '0',
            'courseinstructor_set-MIN_NUM_FORMS': '0',
            'courseinstructor_set-MAX_NUM_FORMS': '1000',
            # Peer review fields
            'peer_review_enabled': '',
            'peer_review_count': '3',
            'peer_review_deadline_days': '7',
            'peer_review_criteria': '',
        })
        # Should redirect after successful creation
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Course.objects.filter(slug='new-course').exists())
        course = Course.objects.get(slug='new-course')
        self.assertEqual(course.title, 'New Course')
        self.assertEqual(course.required_level, 0)

    def test_admin_status_change_draft_to_published(self):
        course = Course.objects.create(
            title='Status Test', slug='status-test', status='draft',
        )
        self.assertEqual(course.status, 'draft')
        # Use the publish action
        self.client.post('/admin/content/course/', {
            'action': 'publish_courses',
            '_selected_action': [course.pk],
        })
        course.refresh_from_db()
        self.assertEqual(course.status, 'published')

    def test_admin_status_change_published_to_draft(self):
        course = Course.objects.create(
            title='Unpub Test', slug='unpub-test', status='published',
        )
        self.client.post('/admin/content/course/', {
            'action': 'unpublish_courses',
            '_selected_action': [course.pk],
        })
        course.refresh_from_db()
        self.assertEqual(course.status, 'draft')

    def test_admin_delete_course_cascades(self):
        """Deleting a course cascade-deletes its modules and units."""
        course = Course.objects.create(
            title='Cascade Test', slug='cascade-test',
        )
        module = Module.objects.create(
            course=course, title='Module 1', slug='module-1', sort_order=1,
        )
        Unit.objects.create(
            module=module, title='Unit 1', slug='unit-1', sort_order=1,
        )
        Unit.objects.create(
            module=module, title='Unit 2', slug='unit-2', sort_order=2,
        )

        self.assertEqual(Module.objects.filter(course=course).count(), 1)
        self.assertEqual(Unit.objects.filter(module__course=course).count(), 2)

        # Delete via admin
        self.client.post(f'/admin/content/course/{course.pk}/delete/', {
            'post': 'yes',
        })

        self.assertEqual(Course.objects.filter(pk=course.pk).count(), 0)
        self.assertEqual(Module.objects.filter(course=course).count(), 0)
        self.assertEqual(Unit.objects.filter(module=module).count(), 0)


class ModuleAdminCRUDTest(TestCase):
    """Test admin CRUD operations for modules."""

    def setUp(self):
        self.client = Client()
        self.admin_user = User.objects.create_superuser(
            email='admin@test.com', password='testpass',
        )
        self.client.login(email='admin@test.com', password='testpass')
        self.course = Course.objects.create(
            title='Module CRUD Course', slug='mod-crud',
        )

    def test_module_add_page_loads(self):
        response = self.client.get('/admin/content/module/add/')
        self.assertEqual(response.status_code, 200)

    def test_module_edit_page_loads(self):
        module = Module.objects.create(
            course=self.course, title='Edit Module', slug='edit-module', sort_order=1,
        )
        response = self.client.get(f'/admin/content/module/{module.pk}/change/')
        self.assertEqual(response.status_code, 200)

    def test_module_edit_has_unit_inline(self):
        module = Module.objects.create(
            course=self.course, title='With Units', slug='with-units', sort_order=1,
        )
        response = self.client.get(f'/admin/content/module/{module.pk}/change/')
        self.assertContains(response, 'units-')


class UnitAdminCRUDTest(TestCase):
    """Test admin CRUD operations for units."""

    def setUp(self):
        self.client = Client()
        self.admin_user = User.objects.create_superuser(
            email='admin@test.com', password='testpass',
        )
        self.client.login(email='admin@test.com', password='testpass')
        self.course = Course.objects.create(
            title='Unit CRUD Course', slug='unit-crud',
        )
        self.module = Module.objects.create(
            course=self.course, title='Module', slug='module', sort_order=1,
        )

    def test_unit_list_page_loads(self):
        response = self.client.get('/admin/content/unit/')
        self.assertEqual(response.status_code, 200)

    def test_unit_add_page_loads(self):
        response = self.client.get('/admin/content/unit/add/')
        self.assertEqual(response.status_code, 200)

    def test_unit_edit_page_loads(self):
        unit = Unit.objects.create(
            module=self.module, title='Edit Unit', slug='edit-unit', sort_order=1,
        )
        response = self.client.get(f'/admin/content/unit/{unit.pk}/change/')
        self.assertEqual(response.status_code, 200)

    def test_unit_edit_page_has_timestamps_field(self):
        unit = Unit.objects.create(
            module=self.module, title='TS Unit', slug='ts-unit', sort_order=1,
            timestamps=[{'time_seconds': 120, 'label': 'Intro'}],
        )
        response = self.client.get(f'/admin/content/unit/{unit.pk}/change/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'timestamp-editor')

    def test_unit_edit_page_has_body_field(self):
        unit = Unit.objects.create(
            module=self.module, title='Body Unit', slug='body-unit', sort_order=1,
        )
        response = self.client.get(f'/admin/content/unit/{unit.pk}/change/')
        self.assertContains(response, 'id_body')

    def test_unit_edit_page_has_homework_field(self):
        unit = Unit.objects.create(
            module=self.module, title='HW Unit', slug='hw-unit', sort_order=1,
        )
        response = self.client.get(f'/admin/content/unit/{unit.pk}/change/')
        self.assertContains(response, 'id_homework')

    def test_unit_edit_page_has_video_url_field(self):
        unit = Unit.objects.create(
            module=self.module, title='Video Unit', slug='video-unit', sort_order=1,
        )
        response = self.client.get(f'/admin/content/unit/{unit.pk}/change/')
        self.assertContains(response, 'id_video_url')

    def test_unit_edit_page_has_is_preview_field(self):
        unit = Unit.objects.create(
            module=self.module, title='Preview Unit', slug='preview-unit', sort_order=1,
        )
        response = self.client.get(f'/admin/content/unit/{unit.pk}/change/')
        self.assertContains(response, 'id_is_preview')


# ============================================================
# Retired Reorder API Tests
# ============================================================


class RetiredReorderApiTest(TestCase):
    """Legacy browser-era reorder routes fail closed without mutation."""

    @classmethod
    def setUpTestData(cls):
        cls.course = Course.objects.create(
            title='Private Reorder Course', slug='private-reorder',
        )
        cls.module = Module.objects.create(
            course=cls.course,
            title='Private Module',
            slug='private-module',
            sort_order=7,
        )
        cls.unit = Unit.objects.create(
            module=cls.module,
            title='Private Unit',
            slug='private-unit',
            sort_order=9,
        )
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        self.client.force_login(self.staff)

    def test_retired_reorder_routes_are_unhandled_404_without_mutation_or_leakage(self):
        cases = [
            ('/api/admin/modules/reorder', self.module, 7),
            ('/api/admin/units/reorder', self.unit, 9),
        ]
        for path, obj, original_order in cases:
            with self.subTest(path=path):
                with self.assertRaises(Resolver404):
                    resolve(path)
                response = self.client.put(
                    path,
                    json.dumps([{'id': obj.pk, 'sort_order': 0}]),
                    content_type='application/json',
                )
                self.assertEqual(response.status_code, 404)
                self.assertNotContains(
                    response, 'Private Reorder Course', status_code=404,
                )
                obj.refresh_from_db()
                self.assertEqual(obj.sort_order, original_order)
