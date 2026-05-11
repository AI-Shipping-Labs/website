"""Tests for the Stripe deep-link on /studio/users/<id>/ (issue #566).

When a user has a ``stripe_customer_id`` the Profile card on the user
detail page renders a ``Stripe`` row. With ``STRIPE_DASHBOARD_ACCOUNT_ID``
configured the value is wrapped in an ``<a>`` that deep-links to the
customer page on the Stripe dashboard; without it the value is rendered
as plain text so the operator can still copy the ``cus_*`` ID.

Layering mirrors ``test_user_list_stripe_indicator.py`` (issue #441):
each test pops the env var and ``IntegrationSetting`` row in ``setUp``
so a developer's local ``.env`` cannot accidentally satisfy the
"not configured" assertions.
"""

import os
import re

from django.contrib.auth import get_user_model
from django.test import TestCase

from integrations.config import clear_config_cache, get_config
from integrations.models import IntegrationSetting

User = get_user_model()

ENV_KEY = 'STRIPE_DASHBOARD_ACCOUNT_ID'


class StudioUserDetailStripeLinkTest(TestCase):
    """The Stripe row anchors out to the dashboard when the account is set."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='testpass', is_staff=True,
        )
        cls.user_with_stripe = User.objects.create_user(
            email='paid@test.com', password='testpass',
            stripe_customer_id='cus_ABC',
        )
        cls.user_without_stripe = User.objects.create_user(
            email='free@test.com', password='testpass',
            stripe_customer_id='',
        )

    def setUp(self):
        self.client.login(email='staff@test.com', password='testpass')
        # Start every test from a known-clean state regardless of what the
        # previous test, the dev shell's .env, or another suite left
        # behind. clear_config_cache() drops the in-process snapshot so
        # IntegrationSetting writes inside the test take effect.
        IntegrationSetting.objects.filter(key=ENV_KEY).delete()
        clear_config_cache()
        self.addCleanup(clear_config_cache)
        self._saved_env = os.environ.pop(ENV_KEY, None)
        self.addCleanup(self._restore_env)

    def _restore_env(self):
        if self._saved_env is not None:
            os.environ[ENV_KEY] = self._saved_env
        else:
            os.environ.pop(ENV_KEY, None)

    def _set_account_id(self, value):
        """Persist STRIPE_DASHBOARD_ACCOUNT_ID via the same mechanism the
        Studio settings save view uses, then clear the in-process cache
        so subsequent ``get_config`` calls see the new value."""
        IntegrationSetting.objects.update_or_create(
            key=ENV_KEY,
            defaults={
                'value': value,
                'is_secret': False,
                'group': 'stripe',
                'description': '',
            },
        )
        clear_config_cache()

    # ------------------------------------------------------------------
    # Linking: <a> when account configured
    # ------------------------------------------------------------------

    def test_stripe_link_rendered_when_account_configured(self):
        self._set_account_id('acct_TEST123')
        # Sanity: confirm the helper actually plumbed the value into config.
        self.assertEqual(get_config(ENV_KEY), 'acct_TEST123')

        response = self.client.get(
            f'/studio/users/{self.user_with_stripe.pk}/',
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response, 'data-testid="user-detail-stripe-link"',
        )
        # Exact URL with both placeholders interpolated correctly.
        self.assertContains(
            response,
            'href="https://dashboard.stripe.com/acct_TEST123/customers/cus_ABC"',
        )

    def test_stripe_link_uses_target_blank_and_noopener(self):
        self._set_account_id('acct_TEST123')

        response = self.client.get(
            f'/studio/users/{self.user_with_stripe.pk}/',
        )
        # Both attributes must be on the same element. Two separate
        # ``assertContains`` checks would pass on a page that had a
        # different unrelated <a target="_blank"> link (the Django Admin
        # button, the impersonate forms, etc.).
        anchor_match = re.search(
            r'<a([^>]*data-testid="user-detail-stripe-link"[^>]*)>',
            response.content.decode(),
            re.DOTALL,
        )
        self.assertIsNotNone(
            anchor_match,
            'Stripe anchor not found when account ID is configured.',
        )
        attrs = anchor_match.group(1)
        self.assertIn('target="_blank"', attrs)
        self.assertIn('rel="noopener"', attrs)

    def test_stripe_link_text_is_the_cus_id(self):
        self._set_account_id('acct_TEST123')

        response = self.client.get(
            f'/studio/users/{self.user_with_stripe.pk}/',
        )
        # The visible label inside the anchor is the cus_* ID itself —
        # no "Open in Stripe" text or extra chrome.
        anchor_match = re.search(
            r'<a[^>]*data-testid="user-detail-stripe-link"[^>]*>'
            r'\s*(?P<text>.*?)\s*</a>',
            response.content.decode(),
            re.DOTALL,
        )
        self.assertIsNotNone(anchor_match)
        self.assertEqual(anchor_match.group('text'), 'cus_ABC')

    def test_test_mode_account_id_round_trips_into_href(self):
        # Test/live mode is encoded in the operator-set account ID; we
        # do NOT introduce a separate code path. Pasting a test-mode
        # account ID just produces a different URL.
        self._set_account_id('acct_TESTLIVE')

        response = self.client.get(
            f'/studio/users/{self.user_with_stripe.pk}/',
        )
        self.assertContains(
            response,
            'href="https://dashboard.stripe.com/acct_TESTLIVE/customers/cus_ABC"',
        )

    # ------------------------------------------------------------------
    # Fallback: no anchor when account is not configured
    # ------------------------------------------------------------------

    def test_stripe_value_rendered_as_plain_text_when_account_blank(self):
        # No IntegrationSetting row, no env var (popped in setUp). Confirm.
        self.assertEqual(get_config(ENV_KEY), '')

        response = self.client.get(
            f'/studio/users/{self.user_with_stripe.pk}/',
        )
        # No anchor exists with the test-id when the account is blank —
        # nothing pretends to be a link that would 404 in the dashboard.
        self.assertNotContains(
            response, 'data-testid="user-detail-stripe-link"',
        )
        # The cus_* value is still rendered so the operator can copy it.
        self.assertContains(response, 'cus_ABC')

    # ------------------------------------------------------------------
    # Empty: no Stripe row when the user has no stripe_customer_id
    # ------------------------------------------------------------------

    def test_no_stripe_row_when_user_has_no_customer_id(self):
        self._set_account_id('acct_TEST123')

        response = self.client.get(
            f'/studio/users/{self.user_without_stripe.pk}/',
        )
        # The existing {% if detail_user.stripe_customer_id %} guard means
        # the whole Stripe row is omitted. No anchor and no plain-text
        # placeholder should appear for this user.
        self.assertNotContains(
            response, 'data-testid="user-detail-stripe-link"',
        )
        # And we should not see the muted-foreground "Stripe" label that
        # only renders inside that row.
        body = response.content.decode()
        # Look for the row label inside a <dt> next to the empty user's
        # Profile dl. A regex is overkill — the guard means the literal
        # row markup is gone. We assert on the unique <dt> text wrapped
        # in the muted-foreground class to avoid false positives from
        # the "Stripe" placeholder text elsewhere on the page.
        self.assertNotIn(
            '<dt class="text-muted-foreground">Stripe</dt>', body,
        )
