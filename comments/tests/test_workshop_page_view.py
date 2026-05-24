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
        response = self.client.get('/workshops/2026-04-21-open-ws/tutorial/intro')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="qa-section"')
        self.assertContains(
            response,
            f'data-content-id="{self.page_with_id.content_id}"',
        )
        self.assertContains(response, 'Questions &amp; Answers')

    def test_page_without_content_id_does_not_render_qa_section(self):
        response = self.client.get('/workshops/2026-04-21-open-ws/tutorial/no-id')
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'id="qa-section"')

    def test_anonymous_visitor_sees_signup_cta(self):
        """Issue #792: anonymous visitor on a workshop tutorial page
        sees a primary "Sign up" CTA, the new expanded subtitle, and
        a secondary "Already have an account? Sign in" link. Both
        links carry a ``?next=`` pointing back at the current path
        so the visitor lands on the tutorial after auth.
        """
        tutorial_path = '/workshops/2026-04-21-open-ws/tutorial/intro'
        response = self.client.get(tutorial_path)
        self.assertContains(response, 'id="qa-section"')

        # Primary CTA: "Sign up" pointing at /accounts/signup/?next=<path>.
        self.assertContains(
            response,
            f'<a href="/accounts/signup/?next={tutorial_path}"',
        )
        self.assertContains(response, '>Sign up</a>')

        # New default subtitle copy (the workshop default — plan callers
        # override it). Use a specific substring so this test would
        # fail if the copy regressed.
        self.assertContains(
            response,
            'to ask questions, track your progress, and get access to other workshops',
        )

        # Secondary "Already have an account? Sign in" link with
        # the same next param.
        self.assertContains(
            response,
            f'<a href="/accounts/login/?next={tutorial_path}"',
        )
        self.assertContains(response, 'Already have an account? Sign in')

        # The textarea/post-button must NOT render for anonymous users.
        self.assertNotContains(response, 'id="qa-new-question"')
        self.assertNotContains(response, 'id="qa-post-btn"')

    def test_authenticated_visitor_does_not_see_signup_cta(self):
        """Issue #792: signed-in visitors must see the composer,
        not the anonymous CTA. Guards against the new "Sign up" link
        leaking into the authenticated branch.
        """
        self.client.force_login(self.basic_user)
        response = self.client.get('/workshops/2026-04-21-open-ws/tutorial/intro')
        # Authenticated branch is rendered.
        self.assertContains(response, 'id="qa-new-question"')
        self.assertContains(response, 'id="qa-post-btn"')
        self.assertContains(response, 'Post Question')
        # The anonymous-only CTA elements must NOT appear.
        self.assertNotContains(response, '/accounts/signup/?next=')
        self.assertNotContains(response, 'Already have an account? Sign in')

    def test_gated_user_does_not_see_qa_section(self):
        # Free user is below pages_required_level=10. The view sets
        # is_gated=True and the upgrade card replaces the body — the
        # Q&A include is in the {% else %} branch and must not render.
        self.client.force_login(self.free_user)
        response = self.client.get('/workshops/2026-04-21-paid-ws/tutorial/intro')
        self.assertEqual(response.status_code, 403)
        self.assertContains(response, 'data-testid="page-paywall"', status_code=403)
        self.assertNotContains(response, 'id="qa-section"', status_code=403)
        self.assertNotContains(response, 'Questions &amp; Answers', status_code=403)

    def test_qa_script_loaded_on_workshop_page(self):
        # The script include lives in {% block extra_scripts %} on the
        # workshop page template. Without it the markup would render but
        # nothing would fetch the comments — confirm the IIFE is on the
        # page when the section is.
        response = self.client.get('/workshops/2026-04-21-open-ws/tutorial/intro')
        self.assertContains(response, "document.getElementById('qa-section')")
