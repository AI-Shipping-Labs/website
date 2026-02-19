"""Tests for Course Admin CRUD - issue #80.

Covers:
- CourseAdmin configuration: list_display, list_filter, fieldsets, inlines, actions
- ModuleAdmin with UnitInline (nested editing)
- UnitAdmin with all fields including timestamps widget
- Reorder API: PUT /api/admin/modules/reorder
- Reorder API: PUT /api/admin/units/reorder
- Cascade delete behavior
- Status transitions (draft -> published, published -> draft)
"""

import json

from django.contrib.auth import get_user_model
from django.test import TestCase, Client

from content.models import Course, Module, Unit

User = get_user_model()


# ============================================================
# Admin Configuration Tests
# ============================================================


class CourseAdminConfigTest(TestCase):
    """Test CourseAdmin configuration matches issue requirements."""

    def test_list_display_includes_required_columns(self):
        from content.admin.course import CourseAdmin
        expected = [
            'title', 'slug', 'status', 'instructor_name',
            'required_level', 'is_free', 'created_at', 'updated_at',
        ]
        self.assertEqual(CourseAdmin.list_display, expected)

    def test_list_filter_includes_status(self):
        from content.admin.course import CourseAdmin
        self.assertIn('status', CourseAdmin.list_filter)

    def test_list_filter_includes_required_level(self):
        from content.admin.course import CourseAdmin
        self.assertIn('required_level', CourseAdmin.list_filter)

    def test_list_filter_includes_is_free(self):
        from content.admin.course import CourseAdmin
        self.assertIn('is_free', CourseAdmin.list_filter)

    def test_ordering_by_created_at_desc(self):
        from content.admin.course import CourseAdmin
        self.assertEqual(CourseAdmin.ordering, ['-created_at'])

    def test_prepopulated_slug(self):
        from content.admin.course import CourseAdmin
        self.assertEqual(CourseAdmin.prepopulated_fields, {'slug': ('title',)})

    def test_search_fields(self):
        from content.admin.course import CourseAdmin
        self.assertIn('title', CourseAdmin.search_fields)
        self.assertIn('description', CourseAdmin.search_fields)
        self.assertIn('instructor_name', CourseAdmin.search_fields)

    def test_has_module_inline(self):
        from content.admin.course import CourseAdmin, ModuleInline
        self.assertIn(ModuleInline, CourseAdmin.inlines)

    def test_fieldsets_include_all_required_fields(self):
        from content.admin.course import CourseAdmin
        # Flatten all fields from fieldsets
        all_fields = []
        for name, opts in CourseAdmin.fieldsets:
            all_fields.extend(opts['fields'])
        # Check all required fields are present
        required = [
            'title', 'slug', 'description', 'cover_image_url',
            'instructor_name', 'instructor_bio', 'tags',
            'required_level', 'is_free', 'status', 'discussion_url',
        ]
        for field in required:
            self.assertIn(field, all_fields, f'{field} missing from fieldsets')

    def test_has_publish_action(self):
        from content.admin.course import CourseAdmin, publish_courses
        self.assertIn(publish_courses, CourseAdmin.actions)

    def test_has_unpublish_action(self):
        from content.admin.course import CourseAdmin, unpublish_courses
        self.assertIn(unpublish_courses, CourseAdmin.actions)

    def test_has_date_hierarchy(self):
        from content.admin.course import CourseAdmin
        self.assertEqual(CourseAdmin.date_hierarchy, 'created_at')

    def test_readonly_fields_include_timestamps(self):
        from content.admin.course import CourseAdmin
        self.assertIn('created_at', CourseAdmin.readonly_fields)
        self.assertIn('updated_at', CourseAdmin.readonly_fields)


class ModuleAdminConfigTest(TestCase):
    """Test ModuleAdmin configuration."""

    def test_has_unit_inline(self):
        from content.admin.course import ModuleAdmin, UnitInline
        self.assertIn(UnitInline, ModuleAdmin.inlines)

    def test_list_display(self):
        from content.admin.course import ModuleAdmin
        self.assertEqual(ModuleAdmin.list_display, ['title', 'course', 'sort_order'])

    def test_list_filter_by_course(self):
        from content.admin.course import ModuleAdmin
        self.assertIn('course', ModuleAdmin.list_filter)

    def test_search_fields(self):
        from content.admin.course import ModuleAdmin
        self.assertIn('title', ModuleAdmin.search_fields)
        self.assertIn('course__title', ModuleAdmin.search_fields)


class UnitAdminConfigTest(TestCase):
    """Test UnitAdmin configuration."""

    def test_list_display_includes_video_url(self):
        from content.admin.course import UnitAdmin
        self.assertIn('video_url', UnitAdmin.list_display)

    def test_list_display_includes_is_preview(self):
        from content.admin.course import UnitAdmin
        self.assertIn('is_preview', UnitAdmin.list_display)

    def test_has_fieldsets_with_all_fields(self):
        from content.admin.course import UnitAdmin
        all_fields = []
        for name, opts in UnitAdmin.fieldsets:
            all_fields.extend(opts['fields'])
        required = [
            'module', 'title', 'sort_order', 'is_preview',
            'video_url', 'timestamps', 'body', 'homework',
        ]
        for field in required:
            self.assertIn(field, all_fields, f'{field} missing from UnitAdmin fieldsets')

    def test_uses_custom_form_with_timestamp_widget(self):
        from content.admin.course import UnitAdmin, UnitAdminForm
        self.assertEqual(UnitAdmin.form, UnitAdminForm)

    def test_unit_admin_form_has_timestamp_widget(self):
        from content.admin.course import UnitAdminForm
        from content.admin.widgets import TimestampEditorWidget
        form = UnitAdminForm()
        self.assertIsInstance(
            form.fields['timestamps'].widget,
            TimestampEditorWidget,
        )


class UnitInlineConfigTest(TestCase):
    """Test UnitInline configuration within ModuleAdmin."""

    def test_unit_inline_fields_include_all_required(self):
        from content.admin.course import UnitInline
        required = [
            'title', 'sort_order', 'video_url', 'is_preview',
            'body', 'homework', 'timestamps',
        ]
        for field in required:
            self.assertIn(
                field, UnitInline.fields,
                f'{field} missing from UnitInline fields',
            )

    def test_unit_inline_uses_stacked_layout(self):
        from django.contrib.admin import StackedInline
        from content.admin.course import UnitInline
        self.assertTrue(issubclass(UnitInline, StackedInline))

    def test_unit_inline_uses_custom_form(self):
        from content.admin.course import UnitInline, UnitAdminForm
        self.assertEqual(UnitInline.form, UnitAdminForm)


class ModuleInlineConfigTest(TestCase):
    """Test ModuleInline configuration within CourseAdmin."""

    def test_module_inline_has_show_change_link(self):
        from content.admin.course import ModuleInline
        self.assertTrue(ModuleInline.show_change_link)

    def test_module_inline_fields(self):
        from content.admin.course import ModuleInline
        self.assertEqual(ModuleInline.fields, ['title', 'sort_order'])


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

    def test_course_list_page_loads(self):
        response = self.client.get('/admin/content/course/')
        self.assertEqual(response.status_code, 200)

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

    def test_module_list_page_loads(self):
        response = self.client.get('/admin/content/module/')
        self.assertEqual(response.status_code, 200)

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


# ============================================================
# Cascade Delete Tests
# ============================================================


class CascadeDeleteTest(TestCase):
    """Test that deleting a course cascade-deletes modules and units."""

    def test_delete_course_deletes_modules(self):
        course = Course.objects.create(title='Del', slug='del-cascade')
        Module.objects.create(course=course, title='M1', sort_order=1)
        Module.objects.create(course=course, title='M2', sort_order=2)
        self.assertEqual(Module.objects.filter(course=course).count(), 2)
        course.delete()
        self.assertEqual(Module.objects.count(), 0)

    def test_delete_course_deletes_units(self):
        course = Course.objects.create(title='Del', slug='del-units')
        module = Module.objects.create(course=course, title='M', sort_order=1)
        Unit.objects.create(module=module, title='U1', sort_order=1)
        Unit.objects.create(module=module, title='U2', sort_order=2)
        self.assertEqual(Unit.objects.filter(module__course=course).count(), 2)
        course.delete()
        self.assertEqual(Unit.objects.count(), 0)

    def test_delete_module_deletes_units(self):
        course = Course.objects.create(title='Del', slug='del-mod-units')
        module = Module.objects.create(course=course, title='M', sort_order=1)
        Unit.objects.create(module=module, title='U1', sort_order=1)
        module.delete()
        self.assertEqual(Unit.objects.count(), 0)

    def test_deep_cascade_three_modules_each_with_units(self):
        course = Course.objects.create(title='Deep', slug='deep-cascade')
        for i in range(3):
            module = Module.objects.create(
                course=course, title=f'Module {i}', sort_order=i,
            )
            for j in range(2):
                Unit.objects.create(
                    module=module, title=f'Unit {i}-{j}', sort_order=j,
                )
        self.assertEqual(Module.objects.filter(course=course).count(), 3)
        self.assertEqual(Unit.objects.filter(module__course=course).count(), 6)
        course.delete()
        self.assertEqual(Module.objects.count(), 0)
        self.assertEqual(Unit.objects.count(), 0)
