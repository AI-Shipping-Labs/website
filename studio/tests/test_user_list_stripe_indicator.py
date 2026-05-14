"""Tests for the per-row Stripe indicator on /studio/users/ (issue #441).

When a user has a non-empty ``stripe_customer_id`` the listing renders an
inline Stripe glyph in the Membership cell. When the operator has set
``STRIPE_DASHBOARD_ACCOUNT_ID`` in Studio the glyph is wrapped in an
``<a>`` that deep-links to the matching customer page on the Stripe
dashboard; otherwise it renders as a non-interactive ``<span>`` with the
``cus_*`` ID in a ``title`` tooltip so the operator can still copy it.

Each test scopes its assertions to the per-row container
(``data-testid="user-row-<pk>"``) so coincidental matches elsewhere on
the page (Slack badge, search placeholder, etc.) cannot satisfy the
assertion.
"""

import os
import re

from django.contrib.auth import get_user_model
from django.test import TestCase

from integrations.config import clear_config_cache, get_config
from integrations.models import IntegrationSetting
from payments.models import Tier

User = get_user_model()

# The single env key this suite cares about. We pop it in setUp so a
# developer's `.env` cannot accidentally satisfy the "not configured"
# assertions.
ENV_KEY = 'STRIPE_DASHBOARD_ACCOUNT_ID'


def _row_html(html, user_pk):
    """Return the inner HTML of the ``<tr data-testid="user-row-<pk>">`` row.

    Scoping every assertion to the row prevents coincidental matches
    elsewhere on the page (search-input placeholder text, the Slack
    badge, etc.) from satisfying the test when the indicator is actually
    missing or mis-rendered.
    """
    pattern = (
        r'<tr[^>]*data-testid="user-row-' + str(user_pk) + r'"[^>]*>'
        r'(.*?)'
        r'</tr>'
    )
    match = re.search(pattern, html, re.DOTALL)
    if not match:
        raise AssertionError(
            f'Could not locate row data-testid="user-row-{user_pk}" in '
            f'rendered HTML. Did the row markup change?'
        )
    return match.group(0)


class StudioUserListStripeIndicatorTest(TestCase):
    """The Stripe glyph appears (and links) based on per-row + per-config state."""

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
        cls.imported_paid_user = User.objects.create_user(
            email='imported-paid@test.com', password='testpass',
            stripe_customer_id='cus_PAID',
            tier=Tier.objects.get(slug='main'),
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
    # Visibility: shown when stripe_customer_id present, hidden otherwise
    # ------------------------------------------------------------------

    def test_stripe_icon_shown_when_user_has_stripe_customer_id(self):
        response = self.client.get('/studio/users/')
        self.assertEqual(response.status_code, 200)

        row_html = _row_html(response.content.decode(), self.user_with_stripe.pk)
        self.assertIn('data-testid="stripe-indicator"', row_html)
        self.assertIn('aria-label="Stripe customer"', row_html)

    def test_stripe_icon_hidden_when_user_has_no_stripe_customer_id(self):
        response = self.client.get('/studio/users/')
        self.assertEqual(response.status_code, 200)

        row_html = _row_html(response.content.decode(), self.user_without_stripe.pk)
        self.assertNotIn('data-testid="stripe-indicator"', row_html)

    def test_stripe_imported_paid_user_shows_base_tier_without_override_badge(self):
        response = self.client.get('/studio/users/', {'q': 'imported-paid@test.com'})
        self.assertEqual(response.status_code, 200)

        row_html = _row_html(response.content.decode(), self.imported_paid_user.pk)
        self.assertIn('Main', row_html)
        self.assertNotIn('(override)', row_html)

    # ------------------------------------------------------------------
    # Linking: <a> when account configured, <span> when not
    # ------------------------------------------------------------------

    def test_stripe_icon_links_to_dashboard_when_account_configured(self):
        self._set_account_id('acct_TEST123')
        # Sanity check: the value really did make it through the cache.
        self.assertEqual(get_config(ENV_KEY), 'acct_TEST123')

        response = self.client.get('/studio/users/')
        row_html = _row_html(response.content.decode(), self.user_with_stripe.pk)

        # Exact URL with both placeholders interpolated correctly.
        self.assertIn(
            'href="https://dashboard.stripe.com/acct_TEST123/customers/cus_ABC"',
            row_html,
        )

    def test_stripe_icon_no_link_when_account_not_configured(self):
        # No IntegrationSetting row, no env var (popped in setUp). Confirm.
        self.assertEqual(get_config(ENV_KEY), '')

        response = self.client.get('/studio/users/')
        row_html = _row_html(response.content.decode(), self.user_with_stripe.pk)

        # The indicator is rendered, but as a <span>, not an <a>. We assert
        # both: the indicator exists, and the host element is a span with a
        # title tooltip showing the cus_id.
        self.assertIn('data-testid="stripe-indicator"', row_html)
        # ``re.DOTALL`` so the regex can span the multi-line tag attributes
        # the template renders for readability.
        self.assertIsNone(
            re.search(
                r'<a[^>]*data-testid="stripe-indicator"',
                row_html,
                re.DOTALL,
            ),
            'Indicator must NOT be wrapped in <a> when account is unset.',
        )
        self.assertIsNotNone(
            re.search(
                r'<span[^>]*data-testid="stripe-indicator"',
                row_html,
                re.DOTALL,
            ),
            'Without an account ID the indicator host must be a <span>.',
        )
        # Title tooltip on the span must show the exact cus_id so the
        # operator can copy it out of the DOM.
        self.assertIn('title="cus_ABC"', row_html)

    def test_dashboard_link_uses_target_blank_and_noopener(self):
        self._set_account_id('acct_TEST123')

        response = self.client.get('/studio/users/')
        row_html = _row_html(response.content.decode(), self.user_with_stripe.pk)

        # Find the indicator anchor and verify both attributes are present
        # on the SAME element. Two separate ``assertIn`` checks would pass
        # on a row that had a different unrelated <a target="_blank"> link.
        anchor_match = re.search(
            r'<a([^>]*data-testid="stripe-indicator"[^>]*)>',
            row_html,
            re.DOTALL,
        )
        self.assertIsNotNone(
            anchor_match,
            'Stripe indicator anchor not found when account ID is configured.',
        )
        anchor_attrs = anchor_match.group(1)
        self.assertIn('target="_blank"', anchor_attrs)
        self.assertIn('rel="noopener"', anchor_attrs)

    # ------------------------------------------------------------------
    # Settings round-trip: the new key really is editable in Studio
    # ------------------------------------------------------------------

    def test_settings_save_round_trip_for_dashboard_account_id(self):
        # Posting to the Stripe group save endpoint must upsert the row and
        # clear the cache so subsequent reads see the new value. We use a
        # superuser because high-risk groups in studio are gated for staff
        # and the same staff fixture is used everywhere else; the staff
        # decorator on the save view accepts any is_staff user.
        # Include the other Stripe keys with empty values so the save view
        # does not try to delete pre-existing rows (there are none) — the
        # endpoint iterates the whole group on every save.
        post_data = {
            'STRIPE_SECRET_KEY': '',
            'STRIPE_WEBHOOK_SECRET': '',
            'STRIPE_CUSTOMER_PORTAL_URL': '',
            'STRIPE_DASHBOARD_ACCOUNT_ID': 'acct_NEW',
        }
        response = self.client.post('/studio/settings/stripe/save/', post_data)
        # 302 redirect back to settings dashboard on success.
        self.assertEqual(response.status_code, 302)

        row = IntegrationSetting.objects.get(key=ENV_KEY)
        self.assertEqual(row.value, 'acct_NEW')
        self.assertFalse(row.is_secret)
        self.assertEqual(row.group, 'stripe')

        # Cache must have been cleared by the save view, so a fresh read
        # returns the new value without us calling clear_config_cache here.
        self.assertEqual(get_config(ENV_KEY), 'acct_NEW')
