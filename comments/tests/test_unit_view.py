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
            title='Test Course', slug='test-course', status='published',
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
        response = self.client.get('/courses/test-course/module-1/unit-1')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context['unit_content_id'],
            str(self.unit_with_id.content_id),
        )

    def test_unit_without_content_id_passes_empty_string(self):
        response = self.client.get('/courses/test-course/module-1/unit-2')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['unit_content_id'], '')

    def test_qa_section_rendered_when_content_id_present(self):
        response = self.client.get('/courses/test-course/module-1/unit-1')
        self.assertContains(response, 'id="qa-section"')
        self.assertContains(response, 'Questions &amp; Answers')

    def test_qa_section_not_rendered_when_no_content_id(self):
        response = self.client.get('/courses/test-course/module-1/unit-2')
        self.assertNotContains(response, 'id="qa-section"')

    def test_anonymous_visitor_sees_signup_cta_on_course_unit(self):
        """Issue #792: anonymous visitor on a course unit page sees
        the new "Sign up" primary CTA, the expanded default subtitle,
        and a secondary "Already have an account? Sign in" link.
        Mirrors the workshop tutorial test.
        """
        unit_path = '/courses/test-course/module-1/unit-1'
        response = self.client.get(unit_path)

        # Primary CTA: Sign up with ?next pointing back to the unit.
        self.assertContains(
            response,
            f'<a href="/accounts/signup/?next={unit_path}"',
        )
        self.assertContains(response, '>Sign up</a>')

        # Expanded subtitle copy is the new default for non-plan callers.
        self.assertContains(
            response,
            'to ask questions, track your progress, and get access to other workshops',
        )

        # Secondary "Already have an account? Sign in" with same next.
        self.assertContains(
            response,
            f'<a href="/accounts/login/?next={unit_path}"',
        )
        self.assertContains(response, 'Already have an account? Sign in')

    def test_secondary_signin_link_is_visually_demoted(self):
        """Issue #793: the secondary "Already have an account? Sign in"
        anchor must read as a muted secondary link (text-muted-foreground),
        not as the primary accent action, while the primary "Sign up"
        anchor keeps text-accent. Guards against a revert that restores
        text-accent on the secondary link.
        """
        unit_path = '/courses/test-course/module-1/unit-1'
        response = self.client.get(unit_path)
        self.assertEqual(response.status_code, 200)

        # Primary "Sign up" anchor still carries the accent treatment.
        self.assertContains(
            response,
            f'<a href="/accounts/signup/?next={unit_path}" '
            'class="text-accent hover:underline">Sign up</a>',
            html=False,
        )

        # Secondary "Sign in" anchor is demoted: muted, with a clear
        # hover affordance, and explicitly NOT the accent treatment.
        self.assertContains(
            response,
            f'<a href="/accounts/login/?next={unit_path}" '
            'class="text-muted-foreground hover:text-foreground hover:underline">'
            'Already have an account? Sign in</a>',
            html=False,
        )
        self.assertNotContains(
            response,
            f'<a href="/accounts/login/?next={unit_path}" '
            'class="text-accent hover:underline">',
            html=False,
        )

    def test_authenticated_visitor_does_not_see_signup_cta(self):
        """Issue #792: signed-in visitor on a course unit page sees the
        textarea + Post Question composer, never the anonymous CTA.
        """
        self.client.login(email='test@test.com', password='pass')
        response = self.client.get('/courses/test-course/module-1/unit-1')
        self.assertContains(response, 'id="qa-new-question"')
        self.assertContains(response, 'Post Question')
        self.assertNotContains(response, '/accounts/signup/?next=')
        self.assertNotContains(response, 'Already have an account? Sign in')
