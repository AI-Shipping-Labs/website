"""Test that the course unit detail view passes unit_content_id to the template."""
import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase

from content.models import Course, Module, Unit

User = get_user_model()


class CourseUnitDetailContentIdTest(TestCase):
    """Verify unit_content_id is available in the template context."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(email='test@test.com', password='pass')
        cls.course = Course.objects.create(
            title='Test Course', slug='test-course', status='published', is_free=True,
        )
        cls.module = Module.objects.create(
            course=cls.course, title='Module 1', slug='module-1', sort_order=1,
        )
        cls.unit_with_id = Unit.objects.create(
            module=cls.module, title='Unit 1', slug='unit-1', sort_order=1,
            content_id=uuid.uuid4(), is_preview=True,
        )
        cls.unit_without_id = Unit.objects.create(
            module=cls.module, title='Unit 2', slug='unit-2', sort_order=2,
            is_preview=True,
        )

    def test_unit_with_content_id_passes_it_to_context(self):
        response = self.client.get(f'/courses/test-course/module-1/unit-1')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context['unit_content_id'],
            str(self.unit_with_id.content_id),
        )

    def test_unit_without_content_id_passes_empty_string(self):
        response = self.client.get(f'/courses/test-course/module-1/unit-2')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['unit_content_id'], '')

    def test_qa_section_rendered_when_content_id_present(self):
        response = self.client.get(f'/courses/test-course/module-1/unit-1')
        self.assertContains(response, 'id="qa-section"')
        self.assertContains(response, 'Questions &amp; Answers')

    def test_qa_section_not_rendered_when_no_content_id(self):
        response = self.client.get(f'/courses/test-course/module-1/unit-2')
        self.assertNotContains(response, 'id="qa-section"')

    def test_anonymous_user_sees_sign_in_link(self):
        response = self.client.get(f'/courses/test-course/module-1/unit-1')
        self.assertContains(response, '/accounts/login/')
        self.assertContains(response, 'Sign in')

    def test_authenticated_user_sees_textarea(self):
        self.client.login(email='test@test.com', password='pass')
        response = self.client.get(f'/courses/test-course/module-1/unit-1')
        self.assertContains(response, 'id="qa-new-question"')
        self.assertContains(response, 'Post Question')
