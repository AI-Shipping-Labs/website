"""Visibility rules for the ``account_plan_state`` frame on /account/.

Issue #581 tightened the gate on the ``account_plan_state`` frame so it
only renders when it carries information the rest of the membership
card cannot already convey.

The frame is HIDDEN when:

- A pending downgrade or pending cancellation is in flight
  (the dedicated amber/red notice already says the same thing).
- The user is on the steady-state Free plan with no pending change,
  no override, no stale subscription.
- The user is on the steady-state paid Current plan with no pending
  change, no override, no stale subscription.

The frame is SHOWN when:

- The user has a stale subscription (subscription_id but free tier).
- The user has an active temporary tier override.
"""

import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from accounts.models import TierOverride
from accounts.views.account import (
    _STEADY_STATE_PAIRS,
    _suppress_steady_state_plan_state,
)
from payments.models import Tier

User = get_user_model()


class SuppressSteadyStatePlanStateUnitTest(TestCase):
    """Direct unit coverage of the suppression helper."""

    def test_steady_state_free_is_dropped(self):
        result = _suppress_steady_state_plan_state(
            {
                "badge": "Current free plan",
                "note": "You are on the free membership.",
                "action_label": "Current plan",
                "action_kind": "disabled",
            },
            is_pending_downgrade=False,
            is_pending_cancellation=False,
        )
        self.assertEqual(result, {})

    def test_steady_state_current_plan_is_dropped(self):
        result = _suppress_steady_state_plan_state(
            {
                "badge": "Current plan",
                "note": "",
                "action_label": "Current plan",
                "action_kind": "disabled",
            },
            is_pending_downgrade=False,
            is_pending_cancellation=False,
        )
        self.assertEqual(result, {})

    def test_pending_downgrade_drops_frame_even_with_useful_note(self):
        result = _suppress_steady_state_plan_state(
            {
                "badge": "Current plan",
                "note": "Your plan changes to Basic on May 29, 2026.",
                "action_label": "Current plan",
                "action_kind": "disabled",
            },
            is_pending_downgrade=True,
            is_pending_cancellation=False,
        )
        self.assertEqual(result, {})

    def test_pending_cancellation_drops_frame(self):
        result = _suppress_steady_state_plan_state(
            {
                "badge": "Access ending",
                "note": "Access ends on May 29, 2026.",
                "action_label": "Manage Subscription",
                "action_kind": "portal",
            },
            is_pending_downgrade=False,
            is_pending_cancellation=True,
        )
        self.assertEqual(result, {})

    def test_stale_subscription_warning_is_kept(self):
        state = {
            "badge": "Included",
            "note": "Your subscription needs review.",
            "action_label": "Manage Subscription",
            "action_kind": "portal",
        }
        result = _suppress_steady_state_plan_state(
            state,
            is_pending_downgrade=False,
            is_pending_cancellation=False,
        )
        self.assertEqual(result, state)

    def test_override_message_is_kept(self):
        """An override-active note keeps the frame even though the
        badge is ``Current plan`` (the note is non-empty)."""
        state = {
            "badge": "Current plan",
            "note": "Base subscription. Temporary Premium access is active.",
            "action_label": "Current plan",
            "action_kind": "disabled",
        }
        result = _suppress_steady_state_plan_state(
            state,
            is_pending_downgrade=False,
            is_pending_cancellation=False,
        )
        self.assertEqual(result, state)

    def test_empty_state_passes_through(self):
        self.assertEqual(
            _suppress_steady_state_plan_state(
                {},
                is_pending_downgrade=False,
                is_pending_cancellation=False,
            ),
            {},
        )

    def test_steady_state_pairs_membership(self):
        """Sanity check the constant the helper consults."""
        self.assertIn(
            ("Current free plan", "You are on the free membership."),
            _STEADY_STATE_PAIRS,
        )
        self.assertIn(("Current plan", ""), _STEADY_STATE_PAIRS)


class AccountPagePlanStateRenderingTest(TestCase):
    """End-to-end view + template rendering of the visibility rules."""

    @classmethod
    def setUpTestData(cls):
        cls.basic_tier = Tier.objects.get(slug="basic")
        cls.main_tier = Tier.objects.get(slug="main")
        cls.premium_tier = Tier.objects.get(slug="premium")
        cls.free_tier = Tier.objects.get(slug="free")

    def test_free_user_no_plan_state_frame(self):
        user = User.objects.create_user(email="free@example.com")
        self.client.force_login(user)

        response = self.client.get("/account/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["account_plan_state"], {})
        self.assertNotContains(response, 'id="account-plan-state"')

    def test_paid_current_plan_user_no_frame(self):
        user = User.objects.create_user(email="premium@example.com")
        user.tier = self.premium_tier
        user.subscription_id = "sub_premium_test_123"
        user.billing_period_end = timezone.make_aware(
            datetime.datetime(2026, 5, 1, 12, 0, 0)
        )
        user.save(
            update_fields=["tier", "subscription_id", "billing_period_end"]
        )
        self.client.force_login(user)

        response = self.client.get("/account/")

        self.assertEqual(response.context["account_plan_state"], {})
        self.assertNotContains(response, 'id="account-plan-state"')

    def test_pending_downgrade_user_no_frame_but_amber_notice_present(self):
        user = User.objects.create_user(email="dg@example.com")
        user.tier = self.main_tier
        user.subscription_id = "sub_main_dg_123"
        user.pending_tier = self.basic_tier
        user.billing_period_end = timezone.make_aware(
            datetime.datetime(2026, 4, 1, 12, 0, 0)
        )
        user.save()
        self.client.force_login(user)

        response = self.client.get("/account/")

        self.assertEqual(response.context["account_plan_state"], {})
        self.assertNotContains(response, 'id="account-plan-state"')
        # The amber pending-downgrade notice is still rendered.
        self.assertContains(response, 'id="pending-downgrade-notice"')
        self.assertContains(response, 'Basic')
        self.assertContains(response, '01/04/2026')

    def test_pending_cancellation_user_no_frame_but_red_notice_present(self):
        user = User.objects.create_user(email="cancel@example.com")
        user.tier = self.main_tier
        user.subscription_id = "sub_main_cancel_123"
        user.pending_tier = self.free_tier
        user.billing_period_end = timezone.make_aware(
            datetime.datetime(2026, 5, 15, 12, 0, 0)
        )
        user.save()
        self.client.force_login(user)

        response = self.client.get("/account/")

        self.assertEqual(response.context["account_plan_state"], {})
        self.assertNotContains(response, 'id="account-plan-state"')
        # The red pending-cancellation notice is still rendered.
        self.assertContains(response, 'id="pending-cancellation-notice"')

    def test_active_override_keeps_frame_and_dedicated_notice(self):
        user = User.objects.create_user(email="ov@example.com")
        user.tier = self.basic_tier
        user.subscription_id = "sub_basic_ov_123"
        user.billing_period_end = timezone.make_aware(
            datetime.datetime(2026, 4, 1, 12, 0, 0)
        )
        user.save()
        TierOverride.objects.create(
            user=user,
            original_tier=self.basic_tier,
            override_tier=self.premium_tier,
            expires_at=timezone.now() + datetime.timedelta(days=14),
        )
        self.client.force_login(user)

        response = self.client.get("/account/")

        # The dedicated tier-override-notice is always rendered when an
        # override is active.
        self.assertContains(response, 'id="tier-override-notice"')
        # The plan-state frame survives suppression because its note is
        # non-empty (``Base subscription. Temporary Premium ...``).
        state = response.context["account_plan_state"]
        self.assertTrue(state)
        self.assertEqual(state.get("badge"), "Current plan")
        self.assertIn("Temporary", state.get("note", ""))
        self.assertContains(response, 'id="account-plan-state"')

    def test_stale_subscription_keeps_frame(self):
        """A user with ``subscription_id`` but no paid tier sees the
        ``Your subscription needs review.`` frame."""
        user = User.objects.create_user(email="stale@example.com")
        user.subscription_id = "sub_stale_123"
        # tier left None / free implicitly
        user.tier = None
        user.save(update_fields=["subscription_id", "tier"])
        self.client.force_login(user)

        response = self.client.get("/account/")

        state = response.context["account_plan_state"]
        self.assertTrue(state)
        self.assertEqual(state.get("badge"), "Included")
        self.assertIn("review", state.get("note", "").lower())
        self.assertContains(response, 'id="account-plan-state"')


class AccountPageNoTemplateLeakTest(TestCase):
    """Smoke-test that no Django template variable leaks as raw text
    after the cleanup."""

    def test_no_raw_template_braces_on_free_user_account_page(self):
        user = User.objects.create_user(email="leak@example.com")
        self.client.force_login(user)

        response = self.client.get("/account/")

        content = response.content.decode()
        # ``{{`` only ever appears in unrendered templates; if it leaks
        # to the response body we have a broken include.
        self.assertNotIn("{{", content)
        # ``{%`` similarly should never leak as raw text.
        self.assertNotIn("{%", content)
