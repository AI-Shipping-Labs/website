"""Studio course-edit coverage for access_mode/enroll_url/program_label
(issue #1658): source-managed courses show the fields read-only,
local-only courses can edit them.
"""

from django.test import TestCase

from content.models import Course
from tests.fixtures import StaffUserMixin


class StudioCourseEntitlementFieldsTest(StaffUserMixin, TestCase):
    def setUp(self):
        self.client.login(**self.staff_credentials)

    def test_local_course_shows_access_mode_field_enabled(self):
        course = Course.objects.create(
            title='Local Course', slug='local-course-1658', status='draft',
        )
        response = self.client.get(f'/studio/courses/{course.pk}/edit')
        self.assertContains(response, 'Access mode')
        self.assertContains(response, 'name="access_mode"')
        self.assertNotContains(response, 'name="access_mode" disabled')

    def test_synced_course_shows_fields_disabled_with_guidance(self):
        course = Course.objects.create(
            title='Synced Course', slug='synced-course-1658', status='published',
            access_mode='entitlement',
            enroll_url='https://maven.com/alexey-grigorev/from-rag-to-agents',
            program_label='Maven',
            source_repo='AI-Shipping-Labs/content',
            source_path='courses/synced-course-1658/course.yaml',
        )
        response = self.client.get(f'/studio/courses/{course.pk}/edit')
        body = response.content.decode()
        self.assertContains(response, 'name="access_mode" disabled')

        def _input_tag(name):
            start = body.index(f'name="{name}"')
            tag_start = body.rindex('<input', 0, start)
            tag_end = body.index('>', start)
            return body[tag_start:tag_end]

        self.assertIn('disabled', _input_tag('enroll_url'))
        self.assertIn('disabled', _input_tag('program_label'))
        self.assertContains(response, 'Edit in GitHub, then re-sync.')
        self.assertContains(
            response,
            'value="https://maven.com/alexey-grigorev/from-rag-to-agents"',
        )
        self.assertContains(response, 'value="Maven"')

    def test_post_updates_access_mode_for_local_course(self):
        course = Course.objects.create(
            title='Local Course', slug='local-course-1658-post', status='draft',
        )
        response = self.client.post(
            f'/studio/courses/{course.pk}/edit',
            {
                'title': 'Local Course',
                'slug': 'local-course-1658-post',
                'status': 'draft',
                'required_level': '20',
                'access_mode': 'entitlement',
                'enroll_url': 'https://maven.com/x',
                'program_label': 'Maven',
                'tags': '',
            },
        )
        self.assertEqual(response.status_code, 302)
        course.refresh_from_db()
        self.assertEqual(course.access_mode, 'entitlement')
        self.assertEqual(course.enroll_url, 'https://maven.com/x')
        self.assertEqual(course.program_label, 'Maven')

    def test_post_rejects_synced_course(self):
        course = Course.objects.create(
            title='Synced Course', slug='synced-course-1658-post', status='published',
            source_repo='AI-Shipping-Labs/content',
            source_path='courses/synced-course-1658-post/course.yaml',
        )
        response = self.client.post(
            f'/studio/courses/{course.pk}/edit',
            {'access_mode': 'entitlement'},
        )
        self.assertEqual(response.status_code, 403)
        course.refresh_from_db()
        self.assertEqual(course.access_mode, 'tier')

    def test_invalid_access_mode_post_falls_back_to_tier(self):
        course = Course.objects.create(
            title='Local Course', slug='local-course-1658-invalid', status='draft',
        )
        self.client.post(
            f'/studio/courses/{course.pk}/edit',
            {
                'title': 'Local Course',
                'slug': 'local-course-1658-invalid',
                'status': 'draft',
                'required_level': '0',
                'access_mode': 'bogus',
                'tags': '',
            },
        )
        course.refresh_from_db()
        self.assertEqual(course.access_mode, 'tier')
