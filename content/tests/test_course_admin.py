"""Tests for Course Admin CRUD - issue #80.

Covers:
- Admin CRUD operations for courses, modules, units
- Reorder API: PUT /api/admin/modules/reorder
- Reorder API: PUT /api/admin/units/reorder
- Status transitions (draft -> published, published -> draft)
"""

import json

from django.contrib.auth import get_user_model
from django.test import TestCase, Client

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
            'instructor_name': 'Test Author',
            'instructor_bio': 'An expert.',
            'tags': '["python", "django"]',
            'required_level': 0,
            'is_free': 'on',
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
        self.assertEqual(course.instructor_name, 'Test Author')
        self.assertTrue(course.is_free)

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
            course=course, title='Module 1', sort_order=1,
        )
        Unit.objects.create(
            module=module, title='Unit 1', sort_order=1,
        )
        Unit.objects.create(
            module=module, title='Unit 2', sort_order=2,
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
            course=self.course, title='Edit Module', sort_order=1,
        )
        response = self.client.get(f'/admin/content/module/{module.pk}/change/')
        self.assertEqual(response.status_code, 200)

    def test_module_edit_has_unit_inline(self):
        module = Module.objects.create(
            course=self.course, title='With Units', sort_order=1,
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
            course=self.course, title='Module', sort_order=1,
        )

    def test_unit_list_page_loads(self):
        response = self.client.get('/admin/content/unit/')
        self.assertEqual(response.status_code, 200)

    def test_unit_add_page_loads(self):
        response = self.client.get('/admin/content/unit/add/')
        self.assertEqual(response.status_code, 200)

    def test_unit_edit_page_loads(self):
        unit = Unit.objects.create(
            module=self.module, title='Edit Unit', sort_order=1,
        )
        response = self.client.get(f'/admin/content/unit/{unit.pk}/change/')
        self.assertEqual(response.status_code, 200)

    def test_unit_edit_page_has_timestamps_field(self):
        unit = Unit.objects.create(
            module=self.module, title='TS Unit', sort_order=1,
            timestamps=[{'time_seconds': 120, 'label': 'Intro'}],
        )
        response = self.client.get(f'/admin/content/unit/{unit.pk}/change/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'timestamp-editor')

    def test_unit_edit_page_has_body_field(self):
        unit = Unit.objects.create(
            module=self.module, title='Body Unit', sort_order=1,
        )
        response = self.client.get(f'/admin/content/unit/{unit.pk}/change/')
        self.assertContains(response, 'id_body')

    def test_unit_edit_page_has_homework_field(self):
        unit = Unit.objects.create(
            module=self.module, title='HW Unit', sort_order=1,
        )
        response = self.client.get(f'/admin/content/unit/{unit.pk}/change/')
        self.assertContains(response, 'id_homework')

    def test_unit_edit_page_has_video_url_field(self):
        unit = Unit.objects.create(
            module=self.module, title='Video Unit', sort_order=1,
        )
        response = self.client.get(f'/admin/content/unit/{unit.pk}/change/')
        self.assertContains(response, 'id_video_url')

    def test_unit_edit_page_has_is_preview_field(self):
        unit = Unit.objects.create(
            module=self.module, title='Preview Unit', sort_order=1,
        )
        response = self.client.get(f'/admin/content/unit/{unit.pk}/change/')
        self.assertContains(response, 'id_is_preview')


# ============================================================
# Reorder API Tests
# ============================================================


class ReorderModulesApiTest(TestCase):
    """Test PUT /api/admin/modules/reorder."""

    def setUp(self):
        self.client = Client()
        self.admin_user = User.objects.create_superuser(
            email='admin@test.com', password='testpass',
        )
        self.staff_user = User.objects.create_user(
            email='staff@test.com', password='testpass',
            is_staff=True,
        )
        self.regular_user = User.objects.create_user(
            email='user@test.com', password='testpass',
        )
        self.course = Course.objects.create(
            title='Reorder Course', slug='reorder',
        )
        self.mod1 = Module.objects.create(
            course=self.course, title='Module 1', sort_order=0,
        )
        self.mod2 = Module.objects.create(
            course=self.course, title='Module 2', sort_order=1,
        )
        self.mod3 = Module.objects.create(
            course=self.course, title='Module 3', sort_order=2,
        )

    def test_reorder_succeeds_for_staff(self):
        self.client.login(email='staff@test.com', password='testpass')
        data = [
            {'id': self.mod1.pk, 'sort_order': 2},
            {'id': self.mod2.pk, 'sort_order': 0},
            {'id': self.mod3.pk, 'sort_order': 1},
        ]
        response = self.client.put(
            '/api/admin/modules/reorder',
            json.dumps(data),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        result = json.loads(response.content)
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['updated'], 3)

        self.mod1.refresh_from_db()
        self.mod2.refresh_from_db()
        self.mod3.refresh_from_db()
        self.assertEqual(self.mod1.sort_order, 2)
        self.assertEqual(self.mod2.sort_order, 0)
        self.assertEqual(self.mod3.sort_order, 1)

    def test_reorder_succeeds_for_superuser(self):
        self.client.login(email='admin@test.com', password='testpass')
        data = [
            {'id': self.mod1.pk, 'sort_order': 1},
            {'id': self.mod2.pk, 'sort_order': 2},
        ]
        response = self.client.put(
            '/api/admin/modules/reorder',
            json.dumps(data),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)

    def test_reorder_requires_authentication(self):
        data = [{'id': self.mod1.pk, 'sort_order': 1}]
        response = self.client.put(
            '/api/admin/modules/reorder',
            json.dumps(data),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 401)

    def test_reorder_requires_staff(self):
        self.client.login(email='user@test.com', password='testpass')
        data = [{'id': self.mod1.pk, 'sort_order': 1}]
        response = self.client.put(
            '/api/admin/modules/reorder',
            json.dumps(data),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 403)

    def test_reorder_rejects_non_put(self):
        self.client.login(email='admin@test.com', password='testpass')
        response = self.client.post(
            '/api/admin/modules/reorder',
            json.dumps([]),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 405)

    def test_reorder_rejects_invalid_json(self):
        self.client.login(email='admin@test.com', password='testpass')
        response = self.client.put(
            '/api/admin/modules/reorder',
            'not json',
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)

    def test_reorder_rejects_non_list(self):
        self.client.login(email='admin@test.com', password='testpass')
        response = self.client.put(
            '/api/admin/modules/reorder',
            json.dumps({'id': 1, 'sort_order': 0}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)

    def test_reorder_rejects_missing_id(self):
        self.client.login(email='admin@test.com', password='testpass')
        data = [{'sort_order': 0}]
        response = self.client.put(
            '/api/admin/modules/reorder',
            json.dumps(data),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)

    def test_reorder_rejects_missing_sort_order(self):
        self.client.login(email='admin@test.com', password='testpass')
        data = [{'id': self.mod1.pk}]
        response = self.client.put(
            '/api/admin/modules/reorder',
            json.dumps(data),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)

    def test_reorder_rejects_non_integer_values(self):
        self.client.login(email='admin@test.com', password='testpass')
        data = [{'id': 'abc', 'sort_order': 0}]
        response = self.client.put(
            '/api/admin/modules/reorder',
            json.dumps(data),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)

    def test_reorder_with_empty_list(self):
        self.client.login(email='admin@test.com', password='testpass')
        response = self.client.put(
            '/api/admin/modules/reorder',
            json.dumps([]),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        result = json.loads(response.content)
        self.assertEqual(result['updated'], 0)

    def test_reorder_nonexistent_module(self):
        """Reorder with a nonexistent ID is not an error, just updates 0."""
        self.client.login(email='admin@test.com', password='testpass')
        data = [{'id': 99999, 'sort_order': 0}]
        response = self.client.put(
            '/api/admin/modules/reorder',
            json.dumps(data),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        result = json.loads(response.content)
        self.assertEqual(result['updated'], 0)

    def test_reorder_persists_correctly(self):
        """Verify reordering is actually persisted in DB."""
        self.client.login(email='admin@test.com', password='testpass')
        # Reverse the order
        data = [
            {'id': self.mod1.pk, 'sort_order': 2},
            {'id': self.mod2.pk, 'sort_order': 1},
            {'id': self.mod3.pk, 'sort_order': 0},
        ]
        self.client.put(
            '/api/admin/modules/reorder',
            json.dumps(data),
            content_type='application/json',
        )
        # Fetch modules in sort order
        modules = list(
            Module.objects.filter(course=self.course).order_by('sort_order')
        )
        self.assertEqual(modules[0].title, 'Module 3')
        self.assertEqual(modules[1].title, 'Module 2')
        self.assertEqual(modules[2].title, 'Module 1')


class ReorderUnitsApiTest(TestCase):
    """Test PUT /api/admin/units/reorder."""

    def setUp(self):
        self.client = Client()
        self.admin_user = User.objects.create_superuser(
            email='admin@test.com', password='testpass',
        )
        self.staff_user = User.objects.create_user(
            email='staff@test.com', password='testpass',
            is_staff=True,
        )
        self.regular_user = User.objects.create_user(
            email='user@test.com', password='testpass',
        )
        self.course = Course.objects.create(
            title='Unit Reorder Course', slug='unit-reorder',
        )
        self.module = Module.objects.create(
            course=self.course, title='Module', sort_order=1,
        )
        self.unit1 = Unit.objects.create(
            module=self.module, title='Unit A', sort_order=0,
        )
        self.unit2 = Unit.objects.create(
            module=self.module, title='Unit B', sort_order=1,
        )
        self.unit3 = Unit.objects.create(
            module=self.module, title='Unit C', sort_order=2,
        )

    def test_reorder_succeeds_for_staff(self):
        self.client.login(email='staff@test.com', password='testpass')
        data = [
            {'id': self.unit1.pk, 'sort_order': 2},
            {'id': self.unit2.pk, 'sort_order': 0},
            {'id': self.unit3.pk, 'sort_order': 1},
        ]
        response = self.client.put(
            '/api/admin/units/reorder',
            json.dumps(data),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        result = json.loads(response.content)
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['updated'], 3)

        self.unit1.refresh_from_db()
        self.unit2.refresh_from_db()
        self.unit3.refresh_from_db()
        self.assertEqual(self.unit1.sort_order, 2)
        self.assertEqual(self.unit2.sort_order, 0)
        self.assertEqual(self.unit3.sort_order, 1)

    def test_reorder_succeeds_for_superuser(self):
        self.client.login(email='admin@test.com', password='testpass')
        data = [
            {'id': self.unit1.pk, 'sort_order': 1},
        ]
        response = self.client.put(
            '/api/admin/units/reorder',
            json.dumps(data),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)

    def test_reorder_requires_authentication(self):
        data = [{'id': self.unit1.pk, 'sort_order': 1}]
        response = self.client.put(
            '/api/admin/units/reorder',
            json.dumps(data),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 401)

    def test_reorder_requires_staff(self):
        self.client.login(email='user@test.com', password='testpass')
        data = [{'id': self.unit1.pk, 'sort_order': 1}]
        response = self.client.put(
            '/api/admin/units/reorder',
            json.dumps(data),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 403)

    def test_reorder_rejects_non_put(self):
        self.client.login(email='admin@test.com', password='testpass')
        response = self.client.post(
            '/api/admin/units/reorder',
            json.dumps([]),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 405)

    def test_reorder_rejects_invalid_json(self):
        self.client.login(email='admin@test.com', password='testpass')
        response = self.client.put(
            '/api/admin/units/reorder',
            'not json',
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)

    def test_reorder_rejects_non_list(self):
        self.client.login(email='admin@test.com', password='testpass')
        response = self.client.put(
            '/api/admin/units/reorder',
            json.dumps({'id': 1, 'sort_order': 0}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)

    def test_reorder_rejects_missing_fields(self):
        self.client.login(email='admin@test.com', password='testpass')
        data = [{'id': self.unit1.pk}]
        response = self.client.put(
            '/api/admin/units/reorder',
            json.dumps(data),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)

    def test_reorder_with_empty_list(self):
        self.client.login(email='admin@test.com', password='testpass')
        response = self.client.put(
            '/api/admin/units/reorder',
            json.dumps([]),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        result = json.loads(response.content)
        self.assertEqual(result['updated'], 0)

    def test_reorder_persists_correctly(self):
        """Verify reordering is actually persisted in DB."""
        self.client.login(email='admin@test.com', password='testpass')
        data = [
            {'id': self.unit1.pk, 'sort_order': 2},
            {'id': self.unit2.pk, 'sort_order': 1},
            {'id': self.unit3.pk, 'sort_order': 0},
        ]
        self.client.put(
            '/api/admin/units/reorder',
            json.dumps(data),
            content_type='application/json',
        )
        units = list(
            Unit.objects.filter(module=self.module).order_by('sort_order')
        )
        self.assertEqual(units[0].title, 'Unit C')
        self.assertEqual(units[1].title, 'Unit B')
        self.assertEqual(units[2].title, 'Unit A')
