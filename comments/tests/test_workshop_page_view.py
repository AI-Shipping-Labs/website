"""Test that the workshop page detail view renders the Q&A section.

Mirrors comments/tests/test_unit_view.py — verifies the shared
`comments/_qa_section.html` partial is wired up correctly on the
workshop tutorial surface.
"""
import datetime
import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase

from content.models import Workshop, WorkshopPage
from tests.fixtures import TierSetupMixin

User = get_user_model()


class WorkshopPageQASectionTest(TierSetupMixin, TestCase):
    """Verify the Q&A section is rendered on workshop pages."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        # Ungated workshop so anonymous + free can both read the body.
        cls.workshop_open = Workshop.objects.create(
            slug='open-ws',
            title='Open Workshop',
            status='published',
            date=datetime.date(2026, 4, 21),
            landing_required_level=0,
            pages_required_level=0,
            recording_required_level=0,
            description='desc',
            instructor_name='Author',
        )
        cls.page_with_id = WorkshopPage.objects.create(
            workshop=cls.workshop_open,
            slug='intro',
            title='Intro',
            sort_order=1,
            body='# Welcome',
            content_id=uuid.uuid4(),
        )
        cls.page_without_id = WorkshopPage.objects.create(
            workshop=cls.workshop_open,
            slug='no-id',
            title='No ID',
            sort_order=2,
            body='no id body',
            content_id=None,
        )

        # Pages-gated workshop: only Basic+ can see the body / Q&A section.
        cls.workshop_paid = Workshop.objects.create(
            slug='paid-ws',
            title='Paid Workshop',
            status='published',
            date=datetime.date(2026, 4, 21),
            landing_required_level=0,
            pages_required_level=10,
            recording_required_level=20,
            description='desc',
            instructor_name='Author',
        )
        cls.paid_page = WorkshopPage.objects.create(
            workshop=cls.workshop_paid,
            slug='intro',
            title='Intro',
            sort_order=1,
            body='# Hidden',
            content_id=uuid.uuid4(),
        )

        cls.basic_user = User.objects.create_user(
            email='basic@x.com', password='pw', tier=cls.basic_tier,
        )
        cls.free_user = User.objects.create_user(
            email='free@x.com', password='pw', tier=cls.free_tier,
        )

    def test_page_with_content_id_renders_qa_section(self):
        response = self.client.get('/workshops/open-ws/tutorial/intro')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="qa-section"')
        self.assertContains(
            response,
            f'data-content-id="{self.page_with_id.content_id}"',
        )
        self.assertContains(response, 'Questions &amp; Answers')

    def test_page_without_content_id_does_not_render_qa_section(self):
        response = self.client.get('/workshops/open-ws/tutorial/no-id')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'id="qa-section"')

    def test_anonymous_user_sees_sign_in_link(self):
        response = self.client.get('/workshops/open-ws/tutorial/intro')
        self.assertContains(response, 'id="qa-section"')
        # The sign-in branch in the partial.
        self.assertContains(response, '/accounts/login/')
        self.assertContains(response, 'to ask questions')
        # The textarea/post-button must NOT render for anonymous users.
        self.assertNotContains(response, 'id="qa-new-question"')
        self.assertNotContains(response, 'id="qa-post-btn"')

    def test_authenticated_user_sees_textarea_and_post_button(self):
        self.client.force_login(self.basic_user)
        response = self.client.get('/workshops/open-ws/tutorial/intro')
        self.assertContains(response, 'id="qa-new-question"')
        self.assertContains(response, 'id="qa-post-btn"')
        self.assertContains(response, 'Post Question')

    def test_gated_user_does_not_see_qa_section(self):
        # Free user is below pages_required_level=10. The view sets
        # is_gated=True and the upgrade card replaces the body — the
        # Q&A include is in the {% else %} branch and must not render.
        self.client.force_login(self.free_user)
        response = self.client.get('/workshops/paid-ws/tutorial/intro')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="page-paywall"')
        self.assertNotContains(response, 'id="qa-section"')
        self.assertNotContains(response, 'Questions &amp; Answers')

    def test_qa_script_loaded_on_workshop_page(self):
        # The script include lives in {% block extra_scripts %} on the
        # workshop page template. Without it the markup would render but
        # nothing would fetch the comments — confirm the IIFE is on the
        # page when the section is.
        response = self.client.get('/workshops/open-ws/tutorial/intro')
        self.assertContains(response, "document.getElementById('qa-section')")
