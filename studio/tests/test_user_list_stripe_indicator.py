"""Tests for surfacing Stripe customer data on /studio/users/ (issue #441).

Issue #441 introduced an inline Stripe glyph on each row that carried
a ``cus_*`` ID; issue #451 removed the per-row Membership column and
moved the Stripe deep-link to the user detail page. On the listing the
``cus_*`` ID now appears in the row-level ``<tr title="...">`` hover
tooltip alongside Slack ID, Newsletter state, and Slack workspace
state.

Tests in this module verify:

- the row tooltip carries ``Stripe customer: <cus_id>`` when the user
  has a stripe_customer_id;
- the tooltip omits the Stripe line when the user has no
  stripe_customer_id;
- the imported paid user's tier rendering is not corrupted (regression
  guard from #441's original spec);
- the Stripe settings save view still upserts STRIPE_DASHBOARD_ACCOUNT_ID
  (config round-trip unrelated to the row layout).

Each test scopes its assertions to the per-row container
(``data-testid="user-row-<pk>"``) so coincidental matches elsewhere on
the page cannot satisfy the assertion.
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
    # Row tooltip: Stripe customer ID surfaces in the <tr title="..."> only
    # when the user has a stripe_customer_id (issue #451)
    # ------------------------------------------------------------------

    def _tr_attrs(self, html, user_pk):
        """Return the ``<tr ...>`` open-tag attribute string for a row."""
        match = re.search(
            r'<tr([^>]*data-testid="user-row-' + str(user_pk)
            + r'"[^>]*)>',
            html,
        )
        self.assertIsNotNone(
            match,
            f'Could not find <tr data-testid="user-row-{user_pk}">.',
        )
        return match.group(1)

    def test_stripe_customer_id_appears_in_row_tooltip_when_set(self):
        response = self.client.get('/studio/users/')
        self.assertEqual(response.status_code, 200)
        attrs = self._tr_attrs(
            response.content.decode(), self.user_with_stripe.pk,
        )
        # Tooltip carries the documented "Stripe customer: <cus_*>" line.
        self.assertIn('Stripe customer: cus_ABC', attrs)

    def test_stripe_customer_omitted_from_row_tooltip_when_unset(self):
        response = self.client.get('/studio/users/')
        self.assertEqual(response.status_code, 200)
        attrs = self._tr_attrs(
            response.content.decode(), self.user_without_stripe.pk,
        )
        # No Stripe line at all when the user has no customer ID.
        self.assertNotIn('Stripe customer:', attrs)

    def test_per_row_stripe_indicator_glyph_is_removed_from_listing(self):
        # Issue #451 regression guard: the inline glyph anchor / span
        # both disappear from the row; the Stripe deep-link lives on the
        # user detail page instead.
        response = self.client.get('/studio/users/')
        row_html = _row_html(
            response.content.decode(), self.user_with_stripe.pk,
        )
        self.assertNotIn('data-testid="stripe-indicator"', row_html)

    def test_stripe_imported_paid_user_shows_base_tier_without_override_badge(self):
        response = self.client.get('/studio/users/', {'q': 'imported-paid@test.com'})
        self.assertEqual(response.status_code, 200)

        row_html = _row_html(response.content.decode(), self.imported_paid_user.pk)
        self.assertIn('Main', row_html)
        self.assertNotIn('(override)', row_html)

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
