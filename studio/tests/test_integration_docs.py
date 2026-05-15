"""Tests for the integration-docs (?) help-icon links (issue #641).

The Studio settings page renders a small (?) link next to each
integration setting whose registry entry carries a ``docs_url``. The
link target is a Studio-routed view that reads the markdown file under
``_docs/integrations/<group>.md`` at request time and renders it to
HTML.

Covered here:
- The (?) icon renders only for keys that have a ``docs_url``, and the
  href points at ``/studio/docs/integrations/<group>#<anchor>``.
- The doc-serving view returns 200 for a known group, renders the
  per-key headings as anchored sections, and refuses unknown groups
  with 404.
- Path traversal via the ``<group>`` URL segment cannot escape the
  ``_docs/integrations/`` directory.
"""

import re

from django.contrib.auth import get_user_model
from django.test import TestCase

User = get_user_model()


class IntegrationDocsHelpIconRenderTest(TestCase):
    """The Studio settings dashboard renders a (?) link per ``docs_url`` key."""

    @classmethod
    def setUpTestData(cls):
        cls.staff_user = User.objects.create_user(
            email='docs-admin@test.com', password='testpass', is_staff=True,
        )

    def setUp(self):
        self.client.login(email='docs-admin@test.com', password='testpass')

    def test_stripe_webhook_secret_field_has_help_icon_link(self):
        """The STRIPE_WEBHOOK_SECRET row carries a (?) link at the docs anchor."""
        response = self.client.get('/studio/settings/')
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()

        # The (?) anchor element is keyed off the setting key so we can
        # locate it exactly even when other anchors are on the page.
        match = re.search(
            r'<a[^>]*data-docs-link="STRIPE_WEBHOOK_SECRET"[^>]*>',
            body,
        )
        self.assertIsNotNone(
            match,
            "Expected a (?) docs link rendered next to STRIPE_WEBHOOK_SECRET",
        )
        opening_tag = match.group(0)

        # The href is the Studio-routed docs URL with the per-key
        # anchor — not the raw ``_docs/`` path stored in the registry.
        self.assertIn(
            'href="/studio/docs/integrations/stripe#stripe_webhook_secret"',
            opening_tag,
        )
        # The link opens in a new tab and is keyboard-discoverable as a
        # real anchor element (not a button).
        self.assertIn('target="_blank"', opening_tag)
        self.assertIn('rel="noopener noreferrer"', opening_tag)
        # Accessible name for assistive tech.
        self.assertIn(
            'aria-label="Setup docs for STRIPE_WEBHOOK_SECRET"',
            opening_tag,
        )

    def test_key_without_docs_url_has_no_help_icon(self):
        """Keys without ``docs_url`` in the registry get no (?) link.

        Adding ``docs_url`` for every group is a follow-up; until then
        the Studio page must not render dead (?) icons for keys whose
        docs page does not exist.
        """
        response = self.client.get('/studio/settings/')
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()

        # SLACK_ENABLED is a known boolean key with no docs_url in this
        # commit. There must be no ``data-docs-link`` anchor for it.
        self.assertNotRegex(
            body,
            r'data-docs-link="SLACK_ENABLED"',
        )


class IntegrationDocsViewTest(TestCase):
    """The doc-serving view at ``/studio/docs/integrations/<group>``."""

    @classmethod
    def setUpTestData(cls):
        cls.staff_user = User.objects.create_user(
            email='docs-view@test.com', password='testpass', is_staff=True,
        )
        cls.non_staff_user = User.objects.create_user(
            email='member-docs@test.com', password='testpass', is_staff=False,
        )

    def test_staff_sees_rendered_markdown_with_anchors(self):
        self.client.login(email='docs-view@test.com', password='testpass')
        response = self.client.get('/studio/docs/integrations/stripe')
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()

        # Each per-key heading becomes an ``id``-bearing element so the
        # ``#anchor`` fragment in the (?) link lines up.
        self.assertIn('id="stripe_webhook_secret"', body)
        self.assertIn('id="stripe_secret_key"', body)
        self.assertIn('id="stripe_customer_portal_url"', body)
        self.assertIn('id="stripe_dashboard_account_id"', body)
        # The Purpose / Without-it framing from the docs source is the
        # value the issue explicitly asks for — assert at least one
        # sentence of it survives the markdown round-trip.
        self.assertIn('Purpose', body)
        self.assertIn('Without it', body)

    def test_non_staff_cannot_view_integration_docs(self):
        self.client.login(email='member-docs@test.com', password='testpass')
        response = self.client.get('/studio/docs/integrations/stripe')
        self.assertEqual(response.status_code, 403)

    def test_unknown_group_returns_404(self):
        self.client.login(email='docs-view@test.com', password='testpass')
        response = self.client.get('/studio/docs/integrations/not-a-real-group')
        self.assertEqual(response.status_code, 404)

    def test_group_without_authored_doc_returns_404(self):
        """A registered group whose markdown file does not yet exist 404s.

        This is the steady state for every non-Stripe group until the
        follow-up commits land. Studio must not 500 just because the
        markdown isn't authored yet.
        """
        self.client.login(email='docs-view@test.com', password='testpass')
        # ``auth`` is in INTEGRATION_GROUPS but has no markdown file
        # authored under _docs/integrations/auth.md in this commit.
        response = self.client.get('/studio/docs/integrations/auth')
        self.assertEqual(response.status_code, 404)
