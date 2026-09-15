"""Django admin coverage for Course.access_mode/enroll_url/program_label
(issue #1658).

Studio coverage lives in studio/tests/test_course_entitlement_1658.py.
"""

from django.contrib.auth import get_user_model
from django.test import Client, TestCase

from content.models import Course

User = get_user_model()


class CourseAdminAccessModeTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.admin_user = User.objects.create_superuser(
            email='admin-1658@test.com', password='testpass',
        )
        self.client.login(email='admin-1658@test.com', password='testpass')

    def test_change_list_shows_access_mode_column(self):
        Course.objects.create(
            title='Entitlement Admin Course', slug='entitlement-admin-course',
            status='published', access_mode='entitlement',
            enroll_url='https://maven.com/x', program_label='Maven',
        )
        response = self.client.get('/admin/content/course/')
        self.assertContains(response, 'entitlement')

    def test_change_list_filterable_by_access_mode(self):
        Course.objects.create(
            title='Tier Admin Course', slug='tier-admin-course',
            status='published',
        )
        Course.objects.create(
            title='Entitlement Admin Course', slug='entitlement-admin-course-2',
            status='published', access_mode='entitlement',
            enroll_url='https://maven.com/x',
        )
        response = self.client.get(
            '/admin/content/course/?access_mode__exact=entitlement',
        )
        self.assertContains(response, 'Entitlement Admin Course')
        self.assertNotContains(response, 'Tier Admin Course')

    def test_change_form_exposes_access_mode_enroll_url_program_label(self):
        course = Course.objects.create(
            title='Form Course', slug='form-course-1658',
            status='published', access_mode='entitlement',
            enroll_url='https://maven.com/x', program_label='Maven',
        )
        response = self.client.get(f'/admin/content/course/{course.pk}/change/')
        self.assertContains(response, 'id="id_access_mode"')
        self.assertContains(response, 'id="id_enroll_url"')
        self.assertContains(response, 'id="id_program_label"')
