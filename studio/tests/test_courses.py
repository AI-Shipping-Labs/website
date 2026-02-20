"""Tests for studio course CRUD views.

Verifies:
- Course list view with search and status filter
- Course create form (GET and POST)
- Course edit form (GET and POST)
- Module creation
- Unit creation and editing
- Module reorder API
"""

from django.contrib.auth import get_user_model
from django.test import TestCase, Client

from content.models import Course, Module, Unit

User = get_user_model()


class StudioCourseListTest(TestCase):
    """Test course list view."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='testpass')

    def test_list_returns_200(self):
        response = self.client.get('/studio/courses/')
        self.assertEqual(response.status_code, 200)

    def test_list_uses_correct_template(self):
        response = self.client.get('/studio/courses/')
        self.assertTemplateUsed(response, 'studio/courses/list.html')

    def test_list_shows_courses(self):
        Course.objects.create(title='Test Course', slug='test-course')
        response = self.client.get('/studio/courses/')
        self.assertContains(response, 'Test Course')

    def test_list_filter_by_status(self):
        Course.objects.create(title='DraftCourseXYZ', slug='draft', status='draft')
        Course.objects.create(title='PublishedCourseXYZ', slug='pub', status='published')
        response = self.client.get('/studio/courses/?status=draft')
        self.assertContains(response, 'DraftCourseXYZ')
        self.assertNotContains(response, 'PublishedCourseXYZ')

    def test_list_search(self):
        Course.objects.create(title='Python Course', slug='python')
        Course.objects.create(title='Java Course', slug='java')
        response = self.client.get('/studio/courses/?q=Python')
        self.assertContains(response, 'Python Course')
        self.assertNotContains(response, 'Java Course')

    def test_list_empty_state(self):
        response = self.client.get('/studio/courses/')
        self.assertContains(response, 'No courses found')


class StudioCourseCreateTest(TestCase):
    """Test course creation form."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='testpass')

    def test_create_form_returns_200(self):
        response = self.client.get('/studio/courses/new')
        self.assertEqual(response.status_code, 200)

    def test_create_form_uses_correct_template(self):
        response = self.client.get('/studio/courses/new')
        self.assertTemplateUsed(response, 'studio/courses/form.html')

    def test_create_course_post(self):
        response = self.client.post('/studio/courses/new', {
            'title': 'New Course',
            'slug': 'new-course',
            'description': 'A test course',
            'status': 'draft',
            'required_level': '0',
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Course.objects.filter(slug='new-course').exists())
        course = Course.objects.get(slug='new-course')
        self.assertEqual(course.title, 'New Course')
        self.assertEqual(course.status, 'draft')

    def test_create_course_auto_slug(self):
        """If no slug provided, it's auto-generated from title."""
        response = self.client.post('/studio/courses/new', {
            'title': 'Auto Slug Course',
            'description': '',
            'status': 'draft',
            'required_level': '0',
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Course.objects.filter(slug='auto-slug-course').exists())

    def test_create_course_with_tags(self):
        self.client.post('/studio/courses/new', {
            'title': 'Tagged Course',
            'slug': 'tagged',
            'status': 'draft',
            'required_level': '0',
            'tags': 'python, django, web',
        })
        course = Course.objects.get(slug='tagged')
        self.assertEqual(len(course.tags), 3)
        self.assertIn('python', course.tags)

    def test_create_course_redirects_to_edit(self):
        response = self.client.post('/studio/courses/new', {
            'title': 'Redirect Test',
            'slug': 'redirect-test',
            'status': 'draft',
            'required_level': '0',
        })
        course = Course.objects.get(slug='redirect-test')
        self.assertRedirects(
            response, f'/studio/courses/{course.pk}/edit',
            fetch_redirect_response=False,
        )

    def test_create_free_course(self):
        self.client.post('/studio/courses/new', {
            'title': 'Free Course',
            'slug': 'free-course',
            'status': 'draft',
            'required_level': '0',
            'is_free': 'on',
        })
        course = Course.objects.get(slug='free-course')
        self.assertTrue(course.is_free)


class StudioCourseEditTest(TestCase):
    """Test course edit form."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='testpass')
        self.course = Course.objects.create(
            title='Edit Course', slug='edit-course', status='draft',
        )

    def test_edit_form_returns_200(self):
        response = self.client.get(f'/studio/courses/{self.course.pk}/edit')
        self.assertEqual(response.status_code, 200)

    def test_edit_form_shows_course_data(self):
        response = self.client.get(f'/studio/courses/{self.course.pk}/edit')
        self.assertContains(response, 'Edit Course')

    def test_edit_course_post(self):
        response = self.client.post(f'/studio/courses/{self.course.pk}/edit', {
            'title': 'Updated Course',
            'slug': 'edit-course',
            'status': 'published',
            'required_level': '10',
        })
        self.assertEqual(response.status_code, 302)
        self.course.refresh_from_db()
        self.assertEqual(self.course.title, 'Updated Course')
        self.assertEqual(self.course.status, 'published')
        self.assertEqual(self.course.required_level, 10)

    def test_edit_shows_modules(self):
        Module.objects.create(course=self.course, title='Module 1', sort_order=0)
        response = self.client.get(f'/studio/courses/{self.course.pk}/edit')
        self.assertContains(response, 'Module 1')

    def test_edit_nonexistent_course_returns_404(self):
        response = self.client.get('/studio/courses/99999/edit')
        self.assertEqual(response.status_code, 404)


class StudioModuleCreateTest(TestCase):
    """Test module creation within a course."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='testpass')
        self.course = Course.objects.create(
            title='Course', slug='module-test', status='draft',
        )

    def test_create_module(self):
        response = self.client.post(
            f'/studio/courses/{self.course.pk}/modules/add',
            {'title': 'New Module'},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            Module.objects.filter(course=self.course, title='New Module').exists()
        )

    def test_create_module_increments_sort_order(self):
        Module.objects.create(course=self.course, title='M1', sort_order=0)
        self.client.post(
            f'/studio/courses/{self.course.pk}/modules/add',
            {'title': 'M2'},
        )
        m2 = Module.objects.get(course=self.course, title='M2')
        self.assertEqual(m2.sort_order, 1)

    def test_create_module_redirects_to_course_edit(self):
        response = self.client.post(
            f'/studio/courses/{self.course.pk}/modules/add',
            {'title': 'Redirect Module'},
        )
        self.assertRedirects(
            response, f'/studio/courses/{self.course.pk}/edit',
            fetch_redirect_response=False,
        )


class StudioUnitCreateTest(TestCase):
    """Test unit creation within a module."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='testpass')
        self.course = Course.objects.create(
            title='Course', slug='unit-test', status='draft',
        )
        self.module = Module.objects.create(
            course=self.course, title='Module', sort_order=0,
        )

    def test_create_unit(self):
        self.client.post(
            f'/studio/modules/{self.module.pk}/units/add',
            {'title': 'New Unit'},
        )
        self.assertTrue(
            Unit.objects.filter(module=self.module, title='New Unit').exists()
        )

    def test_create_unit_redirects_to_course_edit(self):
        response = self.client.post(
            f'/studio/modules/{self.module.pk}/units/add',
            {'title': 'Redirect Unit'},
        )
        self.assertRedirects(
            response, f'/studio/courses/{self.course.pk}/edit',
            fetch_redirect_response=False,
        )


class StudioUnitEditTest(TestCase):
    """Test unit editing."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='testpass')
        self.course = Course.objects.create(
            title='Course', slug='unit-edit', status='draft',
        )
        self.module = Module.objects.create(
            course=self.course, title='Module', sort_order=0,
        )
        self.unit = Unit.objects.create(
            module=self.module, title='Unit', sort_order=0,
        )

    def test_edit_unit_form_returns_200(self):
        response = self.client.get(f'/studio/units/{self.unit.pk}/edit')
        self.assertEqual(response.status_code, 200)

    def test_edit_unit_form_shows_data(self):
        response = self.client.get(f'/studio/units/{self.unit.pk}/edit')
        self.assertContains(response, 'Unit')

    def test_edit_unit_post(self):
        response = self.client.post(f'/studio/units/{self.unit.pk}/edit', {
            'title': 'Updated Unit',
            'video_url': 'https://youtube.com/test',
            'body': '# Lesson',
            'homework': '# Homework',
        })
        self.assertEqual(response.status_code, 302)
        self.unit.refresh_from_db()
        self.assertEqual(self.unit.title, 'Updated Unit')
        self.assertEqual(self.unit.video_url, 'https://youtube.com/test')
        self.assertIn('<h1>Lesson</h1>', self.unit.body_html)

    def test_edit_unit_is_preview(self):
        self.client.post(f'/studio/units/{self.unit.pk}/edit', {
            'title': 'Preview Unit',
            'is_preview': 'on',
        })
        self.unit.refresh_from_db()
        self.assertTrue(self.unit.is_preview)

    def test_edit_unit_redirects_to_course(self):
        response = self.client.post(f'/studio/units/{self.unit.pk}/edit', {
            'title': 'Redirect Test',
        })
        self.assertRedirects(
            response, f'/studio/courses/{self.course.pk}/edit',
            fetch_redirect_response=False,
        )


class StudioModuleReorderTest(TestCase):
    """Test module reorder API."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        self.client.login(email='staff@test.com', password='testpass')
        self.course = Course.objects.create(
            title='Reorder', slug='reorder', status='draft',
        )
        self.m1 = Module.objects.create(
            course=self.course, title='M1', sort_order=0,
        )
        self.m2 = Module.objects.create(
            course=self.course, title='M2', sort_order=1,
        )

    def test_reorder_modules(self):
        import json
        response = self.client.post(
            f'/studio/courses/{self.course.pk}/modules/reorder',
            json.dumps([
                {'id': self.m1.pk, 'sort_order': 1},
                {'id': self.m2.pk, 'sort_order': 0},
            ]),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        self.m1.refresh_from_db()
        self.m2.refresh_from_db()
        self.assertEqual(self.m1.sort_order, 1)
        self.assertEqual(self.m2.sort_order, 0)

    def test_reorder_requires_post(self):
        response = self.client.get(
            f'/studio/courses/{self.course.pk}/modules/reorder',
        )
        self.assertEqual(response.status_code, 405)
