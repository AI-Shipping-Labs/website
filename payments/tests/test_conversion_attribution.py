"""Integration tests for ConversionAttribution snapshots on Stripe conversion.

These cover the scenarios listed in issue #195:
- Full attribution snapshot on first paid checkout
- Yearly conversion records correct MRR
- Conversion with no prior UserAttribution still records a row
- Snapshot is frozen (later UserAttribution mutations don't mutate the row)
- Stripe webhook retry doesn't create duplicates
- Attribution failure doesn't break tier upgrade
- customer.subscription.updated doesn't create new snapshots
- Re-subscribe creates a second snapshot
- One-off course purchase records attribution without tier/MRR
- Campaign FK resolves when matching UtmCampaign exists
- Read-only admin protects snapshot integrity
"""

import hashlib
import hmac
import json
import time
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import Permission
from django.test import Client, TestCase, override_settings

from accounts.models import User
from analytics.models import UserAttribution
from content.models import Course
from integrations.models import UtmCampaign
from payments.models import ConversionAttribution, Tier
from payments.services import (
    handle_checkout_completed,
    handle_subscription_updated,
)

WEBHOOK_URL = "/api/webhooks/payments"
TEST_WEBHOOK_SECRET = "whsec_test_secret_key_for_testing"


def _build_stripe_signature(payload_bytes, secret=TEST_WEBHOOK_SECRET):
    """Build a valid Stripe webhook signature for testing."""
    timestamp = str(int(time.time()))
    signed_payload = f"{timestamp}.{payload_bytes.decode('utf-8')}"
    signature = hmac.new(
        secret.encode("utf-8"),
        signed_payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"t={timestamp},v1={signature}"


def _make_event_payload(event_id, event_type, data_object):
    """Build a Stripe event JSON payload."""
    return {
        "id": event_id,
        "type": event_type,
        "data": {
            "object": data_object,
        },
    }


def _build_attribution(user, **overrides):
    """Set UTM attribution fields on the UserAttribution row for ``user``.

    The post_save signal in ``analytics.signals`` already creates a blank
    UserAttribution row when a User is created, so this helper UPDATES
    that existing row rather than inserting. Returns the refreshed row.

    Each test calls this rather than building shared fixtures because
    every scenario needs different attribution values.
    """
    defaults = {
        "first_touch_utm_source": "newsletter",
        "first_touch_utm_medium": "email",
        "first_touch_utm_campaign": "launch_apr2026",
        "first_touch_utm_content": "ai_hero_list",
        "first_touch_utm_term": "",
        "last_touch_utm_source": "twitter",
        "last_touch_utm_medium": "social",
        "last_touch_utm_campaign": "may_blast",
        "last_touch_utm_content": "tweet_1",
        "last_touch_utm_term": "",
        "signup_path": "email_password",
    }
    defaults.update(overrides)
    attribution, _ = UserAttribution.objects.update_or_create(
        user=user, defaults=defaults,
    )
    return attribution


def _configure_tier_prices(slug, monthly_price_id, yearly_price_id):
    """Set Stripe price IDs on a seeded Tier so price_id lookup works."""
    tier = Tier.objects.get(slug=slug)
    tier.stripe_price_id_monthly = monthly_price_id
    tier.stripe_price_id_yearly = yearly_price_id
    tier.save(update_fields=[
        "stripe_price_id_monthly", "stripe_price_id_yearly",
    ])
    return tier


class FullAttributionSnapshotTest(TestCase):
    """First paid checkout snapshots full first-touch and last-touch UTMs."""

    def test_monthly_basic_checkout_copies_both_snapshots(self):
        tier = _configure_tier_prices(
            "basic", "price_basic_monthly", "price_basic_yearly",
        )
        user = User.objects.create_user(email="convert@test.com")
        _build_attribution(user)

        session_data = {
            "id": "cs_full_attrib_1",
            "customer": "cus_full_attrib_1",
            "customer_details": {"email": "convert@test.com"},
            "subscription": "sub_full_attrib_1",
            "client_reference_id": str(user.pk),
            "metadata": {"tier_slug": "basic", "user_id": str(user.pk)},
        }

        with patch(
            "payments.services._get_subscription_price_id",
            return_value="price_basic_monthly",
        ):
            handle_checkout_completed(session_data)

        rows = ConversionAttribution.objects.filter(user=user)
        self.assertEqual(rows.count(), 1)
        row = rows.first()
        # First-touch copied verbatim
        self.assertEqual(row.first_touch_utm_source, "newsletter")
        self.assertEqual(row.first_touch_utm_medium, "email")
        self.assertEqual(row.first_touch_utm_campaign, "launch_apr2026")
        self.assertEqual(row.first_touch_utm_content, "ai_hero_list")
        self.assertEqual(row.first_touch_utm_term, "")
        # Last-touch copied verbatim
        self.assertEqual(row.last_touch_utm_source, "twitter")
        self.assertEqual(row.last_touch_utm_medium, "social")
        self.assertEqual(row.last_touch_utm_campaign, "may_blast")
        self.assertEqual(row.last_touch_utm_content, "tweet_1")
        self.assertEqual(row.last_touch_utm_term, "")
        # Tier / billing / pricing
        self.assertEqual(row.tier, tier)
        self.assertEqual(row.billing_period, "monthly")
        self.assertEqual(row.amount_eur, 20)
        self.assertEqual(row.mrr_eur, 20)
        # Session metadata
        self.assertEqual(row.stripe_session_id, "cs_full_attrib_1")
        self.assertEqual(row.stripe_subscription_id, "sub_full_attrib_1")


class YearlyConversionMrrTest(TestCase):
    """Yearly conversion stores amount and divides by 12 for MRR."""

    def test_yearly_main_checkout_sets_mrr_to_amount_div_12(self):
        tier = _configure_tier_prices(
            "main", "price_main_monthly", "price_main_yearly",
        )
        user = User.objects.create_user(email="yearly@test.com")
        _build_attribution(user)

        session_data = {
            "id": "cs_yearly_1",
            "customer": "cus_yearly_1",
            "customer_details": {"email": "yearly@test.com"},
            "subscription": "sub_yearly_1",
            "client_reference_id": str(user.pk),
            "metadata": {"tier_slug": "main", "user_id": str(user.pk)},
        }

        with patch(
            "payments.services._get_subscription_price_id",
            return_value="price_main_yearly",
        ):
            handle_checkout_completed(session_data)

        row = ConversionAttribution.objects.get(user=user)
        self.assertEqual(row.tier, tier)
        self.assertEqual(row.billing_period, "yearly")
        self.assertEqual(row.amount_eur, 500)
        self.assertEqual(row.mrr_eur, 500 // 12)
        self.assertEqual(row.mrr_eur, 41)


class NoPriorAttributionTest(TestCase):
    """Conversion for a user with no UserAttribution still creates a row."""

    def test_missing_attribution_writes_blank_utm_row(self):
        tier = _configure_tier_prices(
            "basic", "price_basic_monthly", "price_basic_yearly",
        )
        user = User.objects.create_user(email="noattrib@test.com")
        # Simulate "user pre-dates #194 / never had UTMs" by deleting the
        # signal-created blank row. The conversion handler must still
        # write a ConversionAttribution with all UTM fields blank rather
        # than dropping the row.
        UserAttribution.objects.filter(user=user).delete()

        session_data = {
            "id": "cs_noattrib_1",
            "customer": "cus_noattrib_1",
            "customer_details": {"email": "noattrib@test.com"},
            "subscription": "sub_noattrib_1",
            "client_reference_id": str(user.pk),
            "metadata": {"tier_slug": "basic", "user_id": str(user.pk)},
        }

        with patch(
            "payments.services._get_subscription_price_id",
            return_value="price_basic_monthly",
        ):
            handle_checkout_completed(session_data)

        row = ConversionAttribution.objects.get(user=user)
        # All twelve UTM strings are blank
        for field in [
            "first_touch_utm_source",
            "first_touch_utm_medium",
            "first_touch_utm_campaign",
            "first_touch_utm_content",
            "first_touch_utm_term",
            "last_touch_utm_source",
            "last_touch_utm_medium",
            "last_touch_utm_campaign",
            "last_touch_utm_content",
            "last_touch_utm_term",
        ]:
            self.assertEqual(getattr(row, field), "", f"{field} should be blank")
        self.assertIsNone(row.first_touch_campaign)
        self.assertIsNone(row.last_touch_campaign)
        # Tier / amount still populated from the resolved tier
        self.assertEqual(row.tier, tier)
        self.assertEqual(row.amount_eur, 20)
        self.assertEqual(row.mrr_eur, 20)


class FrozenSnapshotTest(TestCase):
    """Later UserAttribution changes don't mutate an existing snapshot."""

    def test_attribution_update_after_conversion_does_not_change_row(self):
        _configure_tier_prices(
            "basic", "price_basic_monthly", "price_basic_yearly",
        )
        user = User.objects.create_user(email="frozen@test.com")
        attribution = _build_attribution(
            user, first_touch_utm_campaign="launch_apr2026",
        )

        session_data = {
            "id": "cs_frozen_1",
            "customer": "cus_frozen_1",
            "customer_details": {"email": "frozen@test.com"},
            "subscription": "sub_frozen_1",
            "client_reference_id": str(user.pk),
            "metadata": {"tier_slug": "basic", "user_id": str(user.pk)},
        }

        with patch(
            "payments.services._get_subscription_price_id",
            return_value="price_basic_monthly",
        ):
            handle_checkout_completed(session_data)

        row = ConversionAttribution.objects.get(user=user)
        original_created_at = row.created_at
        self.assertEqual(row.first_touch_utm_campaign, "launch_apr2026")

        # Simulate a new visit that overwrites the UserAttribution row.
        attribution.first_touch_utm_campaign = "summer_promo"
        attribution.save(update_fields=["first_touch_utm_campaign"])

        row.refresh_from_db()
        self.assertEqual(row.first_touch_utm_campaign, "launch_apr2026")
        self.assertEqual(row.created_at, original_created_at)


class WebhookRetryIdempotencyTest(TestCase):
    """Delivering the same checkout.session.completed twice creates 1 row."""

    @override_settings(STRIPE_WEBHOOK_SECRET=TEST_WEBHOOK_SECRET)
    def test_duplicate_delivery_only_creates_one_attribution_row(self):
        _configure_tier_prices(
            "basic", "price_basic_monthly", "price_basic_yearly",
        )
        user = User.objects.create_user(email="dupe_attrib@test.com")
        _build_attribution(user)

        event = _make_event_payload(
            "evt_attrib_dupe_1",
            "checkout.session.completed",
            {
                "id": "cs_test_dup",
                "customer": "cus_dup",
                "customer_details": {"email": "dupe_attrib@test.com"},
                "subscription": "sub_dup",
                "client_reference_id": str(user.pk),
                "metadata": {"tier_slug": "basic", "user_id": str(user.pk)},
            },
        )
        payload = json.dumps(event).encode()
        sig = _build_stripe_signature(payload)

        with patch(
            "payments.services._get_subscription_price_id",
            return_value="price_basic_monthly",
        ):
            response1 = self.client.post(
                WEBHOOK_URL, data=payload,
                content_type="application/json",
                HTTP_STRIPE_SIGNATURE=sig,
            )
            response2 = self.client.post(
                WEBHOOK_URL, data=payload,
                content_type="application/json",
                HTTP_STRIPE_SIGNATURE=sig,
            )

        self.assertEqual(response1.status_code, 200)
        self.assertEqual(response2.status_code, 200)
        count = ConversionAttribution.objects.filter(
            stripe_session_id="cs_test_dup",
        ).count()
        self.assertEqual(count, 1)

    def test_second_call_with_same_session_id_is_a_noop(self):
        """Direct call of the service twice also stays at one row.

        Belt-and-braces idempotency on stripe_session_id protects against
        paths that bypass the WebhookEvent guard (e.g. future retry of a
        same-session event that somehow gets a new event ID).
        """
        _configure_tier_prices(
            "basic", "price_basic_monthly", "price_basic_yearly",
        )
        user = User.objects.create_user(email="direct_dup@test.com")
        _build_attribution(user)

        session_data = {
            "id": "cs_direct_dup",
            "customer": "cus_direct_dup",
            "customer_details": {"email": "direct_dup@test.com"},
            "subscription": "sub_direct_dup",
            "client_reference_id": str(user.pk),
            "metadata": {"tier_slug": "basic", "user_id": str(user.pk)},
        }

        with patch(
            "payments.services._get_subscription_price_id",
            return_value="price_basic_monthly",
        ):
            handle_checkout_completed(session_data)
            handle_checkout_completed(session_data)

        self.assertEqual(
            ConversionAttribution.objects.filter(user=user).count(), 1,
        )


class AttributionFailureDoesNotBlockTierTest(TestCase):
    """A raising attribution helper must not break tier/customer/sub updates."""

    def test_helper_exception_is_swallowed_and_tier_still_updates(self):
        tier = _configure_tier_prices(
            "basic", "price_basic_monthly", "price_basic_yearly",
        )
        user = User.objects.create_user(email="boom@test.com")
        _build_attribution(user)

        session_data = {
            "id": "cs_boom_1",
            "customer": "cus_boom_1",
            "customer_details": {"email": "boom@test.com"},
            "subscription": "sub_boom_1",
            "client_reference_id": str(user.pk),
            "metadata": {"tier_slug": "basic", "user_id": str(user.pk)},
        }

        with patch(
            "payments.services._record_conversion_attribution",
            side_effect=RuntimeError("boom"),
        ), patch(
            "payments.services.logger",
        ) as mock_logger, patch(
            "payments.services._get_subscription_price_id",
            return_value="price_basic_monthly",
        ):
            handle_checkout_completed(session_data)

        # Tier update still succeeded
        user.refresh_from_db()
        self.assertEqual(user.tier, tier)
        self.assertEqual(user.stripe_customer_id, "cus_boom_1")
        self.assertEqual(user.subscription_id, "sub_boom_1")
        # No attribution row was created
        self.assertFalse(
            ConversionAttribution.objects.filter(user=user).exists(),
        )
        # Failure was logged with logger.exception
        mock_logger.exception.assert_called()


class SubscriptionUpdateDoesNotCreateSnapshotTest(TestCase):
    """customer.subscription.updated / .deleted do not write attribution rows."""

    def test_subscription_updated_does_not_create_conversion_row(self):
        basic = _configure_tier_prices(
            "basic", "price_basic_monthly", "price_basic_yearly",
        )
        main = _configure_tier_prices(
            "main", "price_main_monthly", "price_main_yearly",
        )
        user = User.objects.create_user(email="upgrade@test.com")
        _build_attribution(user)
        user.tier = basic
        user.subscription_id = "sub_upgrade"
        user.stripe_customer_id = "cus_upgrade"
        user.save(update_fields=["tier", "subscription_id", "stripe_customer_id"])

        # Seed an initial ConversionAttribution row
        initial = ConversionAttribution.objects.create(
            user=user,
            stripe_session_id="cs_initial_upgrade",
            stripe_subscription_id="sub_upgrade",
            tier=basic,
            billing_period="monthly",
            amount_eur=20,
            mrr_eur=20,
        )

        # Now upgrade the plan via customer.subscription.updated
        subscription_data = {
            "id": "sub_upgrade",
            "customer": "cus_upgrade",
            "status": "active",
            "cancel_at_period_end": False,
            "current_period_end": 1700000000,
            "items": {
                "data": [{"price": {"id": "price_main_monthly"}}],
            },
        }
        handle_subscription_updated(subscription_data)

        # No new ConversionAttribution row was created
        self.assertEqual(
            ConversionAttribution.objects.filter(user=user).count(), 1,
        )
        # Existing row unchanged
        initial.refresh_from_db()
        self.assertEqual(initial.tier, basic)
        self.assertEqual(initial.billing_period, "monthly")
        # User's tier still updated as expected
        user.refresh_from_db()
        self.assertEqual(user.tier, main)


class ResubscribeCreatesSecondSnapshotTest(TestCase):
    """Re-subscribe after cancellation creates a new attribution row."""

    def test_second_checkout_creates_second_row(self):
        _configure_tier_prices(
            "basic", "price_basic_monthly", "price_basic_yearly",
        )
        user = User.objects.create_user(email="resub@test.com")
        attribution = _build_attribution(
            user,
            first_touch_utm_campaign="launch_apr2026",
            last_touch_utm_campaign="launch_apr2026",
        )

        first_session = {
            "id": "cs_resub_1",
            "customer": "cus_resub",
            "customer_details": {"email": "resub@test.com"},
            "subscription": "sub_resub_1",
            "client_reference_id": str(user.pk),
            "metadata": {"tier_slug": "basic", "user_id": str(user.pk)},
        }
        with patch(
            "payments.services._get_subscription_price_id",
            return_value="price_basic_monthly",
        ):
            handle_checkout_completed(first_session)

        # User's attribution shifts (new traffic between sub #1 and sub #2)
        attribution.last_touch_utm_campaign = "summer_promo"
        attribution.save(update_fields=["last_touch_utm_campaign"])

        # Second checkout (new session ID)
        second_session = {
            "id": "cs_resub_2",
            "customer": "cus_resub",
            "customer_details": {"email": "resub@test.com"},
            "subscription": "sub_resub_2",
            "client_reference_id": str(user.pk),
            "metadata": {"tier_slug": "basic", "user_id": str(user.pk)},
        }
        with patch(
            "payments.services._get_subscription_price_id",
            return_value="price_basic_monthly",
        ):
            handle_checkout_completed(second_session)

        rows = list(
            ConversionAttribution.objects
            .filter(user=user).order_by("-created_at"),
        )
        self.assertEqual(len(rows), 2)
        # Second row reflects the new last-touch campaign
        self.assertEqual(rows[0].last_touch_utm_campaign, "summer_promo")
        self.assertEqual(rows[0].stripe_session_id, "cs_resub_2")
        # First row still carries the original last-touch campaign
        self.assertEqual(rows[1].last_touch_utm_campaign, "launch_apr2026")
        self.assertEqual(rows[1].stripe_session_id, "cs_resub_1")


class CoursePurchaseAttributionTest(TestCase):
    """One-off course purchase records attribution with null tier / null MRR."""

    def test_course_purchase_writes_attribution_without_tier(self):
        user = User.objects.create_user(email="course@test.com")
        _build_attribution(user)
        course = Course.objects.create(
            title="Test Course",
            slug="test-course",
            status="published",
            individual_price_eur=Decimal("99.00"),
        )

        session_data = {
            "id": "cs_course_1",
            "customer": "cus_course_1",
            "customer_details": {"email": "course@test.com"},
            "subscription": "",
            "client_reference_id": str(user.pk),
            "metadata": {"user_id": str(user.pk), "course_id": str(course.pk)},
        }

        handle_checkout_completed(session_data)

        row = ConversionAttribution.objects.get(user=user)
        self.assertIsNone(row.tier)
        self.assertEqual(row.billing_period, "")
        self.assertIsNone(row.mrr_eur)
        self.assertEqual(row.amount_eur, 99)
        # UTMs still copied from UserAttribution
        self.assertEqual(row.first_touch_utm_campaign, "launch_apr2026")
        self.assertEqual(row.last_touch_utm_campaign, "may_blast")


class CampaignForeignKeySnapshotTest(TestCase):
    """first_touch_campaign FK is copied verbatim from UserAttribution."""

    def test_campaign_fk_is_copied_to_snapshot(self):
        _configure_tier_prices(
            "basic", "price_basic_monthly", "price_basic_yearly",
        )
        campaign = UtmCampaign.objects.create(
            name="Launch Apr 2026",
            slug="launch_apr2026",
            default_utm_source="newsletter",
            default_utm_medium="email",
        )
        user = User.objects.create_user(email="fkcamp@test.com")
        _build_attribution(
            user,
            first_touch_utm_campaign="launch_apr2026",
            first_touch_campaign=campaign,
        )

        session_data = {
            "id": "cs_fkcamp_1",
            "customer": "cus_fkcamp_1",
            "customer_details": {"email": "fkcamp@test.com"},
            "subscription": "sub_fkcamp_1",
            "client_reference_id": str(user.pk),
            "metadata": {"tier_slug": "basic", "user_id": str(user.pk)},
        }

        with patch(
            "payments.services._get_subscription_price_id",
            return_value="price_basic_monthly",
        ):
            handle_checkout_completed(session_data)

        row = ConversionAttribution.objects.get(user=user)
        self.assertEqual(row.first_touch_campaign, campaign)
        # String snapshot also present alongside the FK
        self.assertEqual(row.first_touch_utm_campaign, "launch_apr2026")


class ReadOnlyAdminTest(TestCase):
    """Admin changelist works; add/change pages have no save/delete buttons."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="staff@test.com",
            password="pw",
            is_staff=True,
        )
        cls.staff.user_permissions.add(
            *Permission.objects.filter(
                content_type__app_label="payments",
                content_type__model="conversionattribution",
            )
        )

    def _login(self):
        client = Client()
        client.force_login(self.staff)
        return client

    def test_changelist_renders(self):
        row = ConversionAttribution.objects.create(
            user=self.staff,
            stripe_session_id="cs_admin_list",
        )
        client = self._login()
        response = client.get("/admin/payments/conversionattribution/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, row.stripe_session_id)

    def test_change_page_has_no_save_or_delete_buttons(self):
        row = ConversionAttribution.objects.create(
            user=self.staff,
            stripe_session_id="cs_admin_change",
        )
        client = self._login()
        response = client.get(
            f"/admin/payments/conversionattribution/{row.pk}/change/",
        )
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        # has_change_permission=False removes the submit-row entirely
        self.assertNotIn('name="_save"', html)
        self.assertNotIn('name="_continue"', html)
        self.assertNotIn('class="deletelink"', html)

    def test_add_page_returns_403(self):
        client = self._login()
        response = client.get("/admin/payments/conversionattribution/add/")
        self.assertEqual(response.status_code, 403)

    def test_delete_page_returns_403(self):
        row = ConversionAttribution.objects.create(
            user=self.staff,
            stripe_session_id="cs_admin_delete",
        )
        client = self._login()
        response = client.get(
            f"/admin/payments/conversionattribution/{row.pk}/delete/",
        )
        self.assertEqual(response.status_code, 403)


class ConversionAttributionModelTest(TestCase):
    """Model-level checks for constraints and ordering."""

    def test_default_ordering_is_newest_first(self):
        user = User.objects.create_user(email="order@test.com")
        older = ConversionAttribution.objects.create(
            user=user, stripe_session_id="cs_order_1",
        )
        newer = ConversionAttribution.objects.create(
            user=user, stripe_session_id="cs_order_2",
        )
        rows = list(ConversionAttribution.objects.filter(user=user))
        self.assertEqual(rows[0], newer)
        self.assertEqual(rows[1], older)
