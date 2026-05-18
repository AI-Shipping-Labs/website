"""Tests for the integration-docs (?) help-icon links (issue #641, #664).

The Studio settings page renders a small (?) link next to each
integration setting whose registry entry carries a ``docs_url``. The
link target is the markdown file on GitHub
(``https://github.com/AI-Shipping-Labs/website/blob/main/_docs/integrations/<group>.md#<anchor>``)
which GitHub renders natively. This avoids shipping ``_docs/`` into the
container (``.dockerignore`` excludes it) and keeps the (?) icons
working in production (issue #664).

Covered here:
- The (?) icon renders only for keys that have a ``docs_url``, and the
  href points at the GitHub blob URL with the per-key anchor.
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
        """The STRIPE_WEBHOOK_SECRET row carries a (?) link at the GitHub docs anchor."""
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

        # The href is the GitHub blob URL with the per-key anchor — not
        # the raw ``_docs/`` path stored in the registry. Linking to
        # GitHub avoids shipping ``_docs/`` into the container (which
        # ``.dockerignore`` excludes) and keeps the (?) icons working
        # in production (issue #664).
        self.assertIn(
            'href="https://github.com/AI-Shipping-Labs/website/blob/main/'
            '_docs/integrations/stripe.md#stripe_webhook_secret"',
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

        Issue #649 wired every key in the live registry to a docs URL,
        so this scenario no longer occurs in production. We still
        cover the negative branch by injecting a synthetic registry
        whose entries are intentionally missing ``docs_url`` — the
        Studio template must not render dead (?) icons for keys whose
        docs page does not exist.
        """
        from unittest.mock import patch

        synthetic_registry = [
            {
                'name': 'phantom',
                'label': 'Phantom',
                'keys': [
                    {
                        'key': 'PHANTOM_NO_DOCS_KEY',
                        'is_secret': False,
                        'description': 'Synthetic key without docs_url.',
                    },
                ],
            },
        ]
        with patch('studio.views.settings.INTEGRATION_GROUPS', synthetic_registry):
            response = self.client.get('/studio/settings/')
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()

        # The (?) anchor uses data-docs-link="<KEY>". The synthetic key
        # has no docs_url so the anchor must not render.
        self.assertNotRegex(
            body,
            r'data-docs-link="PHANTOM_NO_DOCS_KEY"',
        )

    def test_internal_docs_route_is_removed(self):
        """``/studio/docs/integrations/<group>`` no longer exists (issue #664).

        The internal doc-serving view was removed because
        ``.dockerignore`` excluded ``_docs/`` from the container image,
        causing every (?) click to 404 in production. The (?) icons now
        link straight at GitHub instead.
        """
        self.client.login(email='docs-admin@test.com', password='testpass')
        response = self.client.get('/studio/docs/integrations/stripe')
        self.assertEqual(response.status_code, 404)
