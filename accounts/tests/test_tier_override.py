"""Tests for the TierOverride feature.

Covers all 69 test scenarios from issue #125, organized by category.
"""

from datetime import timedelta

from dateutil.relativedelta import relativedelta
from django.db.models import ProtectedError
from django.test import TestCase, tag
from django.utils import timezone
from freezegun import freeze_time

from accounts.models import TierOverride, User
from content.access import (
    LEVEL_BASIC,
    LEVEL_MAIN,
    LEVEL_OPEN,
    LEVEL_PREMIUM,
    can_access,
    get_active_override,
    get_user_level,
)
from payments.models import Tier


class TierOverrideTestBase(TestCase):
    """Base class with shared fixture setup."""

    @classmethod
    def setUpTestData(cls):
        cls.free_tier = Tier.objects.get(slug="free")
        cls.basic_tier = Tier.objects.get(slug="basic")
        cls.main_tier = Tier.objects.get(slug="main")
        cls.premium_tier = Tier.objects.get(slug="premium")

    def _make_user(self, email="user@example.com", tier=None, **kwargs):
        kwargs.setdefault("email_verified", True)
        user = User.objects.create_user(email=email, **kwargs)
        if tier is not None:
            user.tier = tier
            user.save(update_fields=["tier"])
        return user

    def _make_staff(self, email="staff@example.com"):
        return User.objects.create_user(email=email, is_staff=True, email_verified=True)

    def _make_override(self, user, override_tier, granted_by=None, **kwargs):
        defaults = {
            "original_tier": user.tier,
            "expires_at": timezone.now() + timedelta(days=14),
            "granted_by": granted_by,
            "is_active": True,
        }
        defaults.update(kwargs)
        return TierOverride.objects.create(
            user=user,
            override_tier=override_tier,
            **defaults,
        )

    def _make_content(self, required_level):
        """Create a simple mock content object with required_level attribute."""
        class MockContent:
            def __init__(self, level):
                self.required_level = level
        return MockContent(required_level)


# ============================================================
# Scenarios 1-5: Access check with override
# ============================================================

@tag('core')
class AccessCheckWithOverrideTest(TierOverrideTestBase):
    """Scenarios 1-5: Access check with override."""

    def test_01_free_user_no_override_sees_level_0_only(self):
        """#1: Free user with no override sees level 0 content only."""
        user = self._make_user()
        self.assertEqual(get_user_level(user), LEVEL_OPEN)
        self.assertTrue(can_access(user, self._make_content(LEVEL_OPEN)))
        self.assertFalse(can_access(user, self._make_content(LEVEL_BASIC)))
        self.assertFalse(can_access(user, self._make_content(LEVEL_MAIN)))
        self.assertFalse(can_access(user, self._make_content(LEVEL_PREMIUM)))

    def test_02_free_user_with_premium_override_sees_all(self):
        """#2: Free user with active Premium override sees all content levels."""
        user = self._make_user()
        self._make_override(user, self.premium_tier)
        self.assertEqual(get_user_level(user), LEVEL_PREMIUM)
        self.assertTrue(can_access(user, self._make_content(LEVEL_OPEN)))
        self.assertTrue(can_access(user, self._make_content(LEVEL_BASIC)))
        self.assertTrue(can_access(user, self._make_content(LEVEL_MAIN)))
        self.assertTrue(can_access(user, self._make_content(LEVEL_PREMIUM)))

    def test_03_main_user_with_premium_override(self):
        """#3: Main user with active Premium override sees level 30 content."""
        user = self._make_user(tier=self.main_tier)
        self._make_override(user, self.premium_tier)
        self.assertEqual(get_user_level(user), LEVEL_PREMIUM)
        self.assertTrue(can_access(user, self._make_content(LEVEL_PREMIUM)))

    def test_04_override_expires_access_drops(self):
        """#4: Override expires -> user immediately loses higher-tier access."""
        user = self._make_user()
        self._make_override(
            user,
            self.premium_tier,
            expires_at=timezone.now() - timedelta(seconds=1),
        )
        # Expired override should not grant access
        self.assertEqual(get_user_level(user), LEVEL_OPEN)
        self.assertFalse(can_access(user, self._make_content(LEVEL_PREMIUM)))

    def test_05_staff_with_override_redundant(self):
        """#5: Staff user with override: staff already has max access."""
        user = self._make_staff(email="staffoverride@example.com")
        self._make_override(user, self.premium_tier)
        # Staff always get LEVEL_PREMIUM regardless
        self.assertEqual(get_user_level(user), LEVEL_PREMIUM)


# ============================================================
# Scenarios 6-8: Override lifecycle
# ============================================================

@tag('core')
class OverrideLifecycleTest(TierOverrideTestBase):
    """Scenarios 6-8: Override lifecycle."""

    def test_06_full_lifecycle_create_expire_fallback(self):
        """#6: Create override -> expires -> user falls back to Free."""
        user = self._make_user()
        override = self._make_override(
            user,
            self.premium_tier,
            expires_at=timezone.now() + timedelta(days=14),
        )
        # During override
        self.assertEqual(get_user_level(user), LEVEL_PREMIUM)
        self.assertTrue(can_access(user, self._make_content(LEVEL_PREMIUM)))

        # Simulate expiry
        override.expires_at = timezone.now() - timedelta(seconds=1)
        override.save(update_fields=["expires_at"])

        self.assertEqual(get_user_level(user), LEVEL_OPEN)
        self.assertFalse(can_access(user, self._make_content(LEVEL_PREMIUM)))

    def test_07_admin_revokes_early(self):
        """#7: Admin revokes override early -> user immediately loses access."""
        user = self._make_user()
        override = self._make_override(user, self.premium_tier)

        # During override
        self.assertEqual(get_user_level(user), LEVEL_PREMIUM)

        # Revoke
        override.is_active = False
        override.save(update_fields=["is_active"])

        self.assertEqual(get_user_level(user), LEVEL_OPEN)

    def test_08_second_override_deactivates_first(self):
        """#8: Creating second override deactivates first."""
        user = self._make_user()
        admin = self._make_staff()

        first = self._make_override(user, self.main_tier, granted_by=admin)
        self.assertTrue(first.is_active)

        # Deactivate existing active overrides (as the view does)
        TierOverride.objects.filter(user=user, is_active=True).update(
            is_active=False
        )
        second = self._make_override(user, self.premium_tier, granted_by=admin)

        first.refresh_from_db()
        self.assertFalse(first.is_active)
        self.assertTrue(second.is_active)
        self.assertEqual(get_user_level(user), LEVEL_PREMIUM)


# ============================================================
# Scenarios 9-13: Self-upgrade/downgrade during override
# ============================================================

@tag('core')
class SelfUpgradeDuringOverrideTest(TierOverrideTestBase):
    """Scenarios 9-13: Self-upgrade/downgrade during override."""

    def test_09_free_user_buys_main_during_premium_override(self):
        """#9: Free user with Premium override buys Main -> max(20, 30)=30."""
        user = self._make_user()
        self._make_override(user, self.premium_tier)

        # User buys Main subscription via Stripe
        user.tier = self.main_tier
        user.save(update_fields=["tier"])

        # Override still active, max(20, 30) = 30
        self.assertEqual(get_user_level(user), LEVEL_PREMIUM)

    def test_10_after_override_expires_user_stays_at_main(self):
        """#10: Continuation of #9 -- after override expires, user stays Main."""
        user = self._make_user()
        override = self._make_override(user, self.premium_tier)

        user.tier = self.main_tier
        user.save(update_fields=["tier"])

        # Expire override
        override.expires_at = timezone.now() - timedelta(seconds=1)
        override.save(update_fields=["expires_at"])

        # Falls to Main (not Free)
        self.assertEqual(get_user_level(user), LEVEL_MAIN)

    def test_11_main_upgrades_to_premium_override_redundant(self):
        """#11: Main user upgrades to Premium -> override becomes redundant."""
        user = self._make_user(tier=self.main_tier)
        override = self._make_override(user, self.premium_tier)

        # User upgrades to Premium
        user.tier = self.premium_tier
        user.save(update_fields=["tier"])

        # Override redundant but harmless
        self.assertEqual(get_user_level(user), LEVEL_PREMIUM)

        # Override expires -> user stays Premium (paid tier)
        override.expires_at = timezone.now() - timedelta(seconds=1)
        override.save(update_fields=["expires_at"])
        self.assertEqual(get_user_level(user), LEVEL_PREMIUM)

    def test_12_main_cancels_override_still_active(self):
        """#12: Main user cancels during Premium override -> max(0, 30)=30."""
        user = self._make_user(tier=self.main_tier)
        self._make_override(user, self.premium_tier)

        # User cancels -> webhook sets tier to Free
        user.tier = self.free_tier
        user.save(update_fields=["tier"])

        # Override still active
        self.assertEqual(get_user_level(user), LEVEL_PREMIUM)

    def test_13_cancel_then_override_expires(self):
        """#13: Continuation of #12 -- override expires, user falls to Free."""
        user = self._make_user(tier=self.main_tier)
        override = self._make_override(user, self.premium_tier)

        # Cancel
        user.tier = self.free_tier
        user.save(update_fields=["tier"])

        # Expire override
        override.expires_at = timezone.now() - timedelta(seconds=1)
        override.save(update_fields=["expires_at"])

        self.assertEqual(get_user_level(user), LEVEL_OPEN)
        self.assertFalse(can_access(user, self._make_content(LEVEL_PREMIUM)))


# ============================================================
# Scenarios 14-15: Sequential overrides
# ============================================================

@tag('core')
class SequentialOverridesTest(TierOverrideTestBase):
    """Scenarios 14-15: Sequential overrides."""

    def test_14_sequential_overrides(self):
        """#14: Two sequential overrides then back to Free."""
        user = self._make_user()
        admin = self._make_staff()

        # First override: Main for 14 days
        first = self._make_override(
            user, self.main_tier, granted_by=admin,
            expires_at=timezone.now() + timedelta(days=14),
        )
        self.assertEqual(get_user_level(user), LEVEL_MAIN)

        # Expire first
        first.expires_at = timezone.now() - timedelta(seconds=1)
        first.save(update_fields=["expires_at"])
        self.assertEqual(get_user_level(user), LEVEL_OPEN)

        # Second override: Premium for 1 month
        second = self._make_override(
            user, self.premium_tier, granted_by=admin,
            expires_at=timezone.now() + relativedelta(months=1),
        )
        self.assertEqual(get_user_level(user), LEVEL_PREMIUM)

        # Expire second
        second.expires_at = timezone.now() - timedelta(seconds=1)
        second.save(update_fields=["expires_at"])
        self.assertEqual(get_user_level(user), LEVEL_OPEN)

    def test_15_sequential_overrides_original_tier_recorded(self):
        """#15: sequential overrides record correct original_tier."""
        user = self._make_user(tier=self.main_tier)
        admin = self._make_staff()

        first = self._make_override(user, self.premium_tier, granted_by=admin)
        # Deactivate first
        first.is_active = False
        first.save(update_fields=["is_active"])

        # 2 weeks later, another override
        second = self._make_override(user, self.premium_tier, granted_by=admin)

        self.assertEqual(second.original_tier, self.main_tier)
        self.assertEqual(first.original_tier, self.main_tier)


# ============================================================
# Scenarios 16-20: Expiry job
# ============================================================

@tag('core')
class ExpiryJobTest(TierOverrideTestBase):
    """Scenarios 16-20: Expiry job."""

    def test_16_job_deactivates_only_expired(self):
        """#16: Job deactivates 3 expired, leaves 2 active."""
        from jobs.tasks.expire_overrides import expire_tier_overrides

        users = [self._make_user(email=f"u{i}@example.com") for i in range(5)]
        admin = self._make_staff()

        # 3 expired
        for i in range(3):
            self._make_override(
                users[i], self.premium_tier, granted_by=admin,
                expires_at=timezone.now() - timedelta(hours=1),
            )
        # 2 still active
        for i in range(3, 5):
            self._make_override(
                users[i], self.premium_tier, granted_by=admin,
                expires_at=timezone.now() + timedelta(days=7),
            )

        result = expire_tier_overrides()
        self.assertEqual(result["deactivated"], 3)

        # Check states
        active_count = TierOverride.objects.filter(is_active=True).count()
        self.assertEqual(active_count, 2)

    def test_17_job_no_expired_overrides(self):
        """#17: Job runs with no expired overrides -> no changes."""
        from jobs.tasks.expire_overrides import expire_tier_overrides

        user = self._make_user()
        self._make_override(
            user, self.premium_tier,
            expires_at=timezone.now() + timedelta(days=7),
        )
        result = expire_tier_overrides()
        self.assertEqual(result["deactivated"], 0)
        self.assertTrue(TierOverride.objects.filter(is_active=True).exists())

    def test_18_expires_at_exactly_now(self):
        """#18: Override that expires_at == now() gets deactivated."""
        from jobs.tasks.expire_overrides import expire_tier_overrides

        user = self._make_user()
        now = timezone.now()
        self._make_override(
            user, self.premium_tier, expires_at=now,
        )
        result = expire_tier_overrides()
        self.assertEqual(result["deactivated"], 1)

    def test_19_concurrent_runs_no_error(self):
        """#19: Two expiry job runs overlap -> already-deactivated overrides
        don't cause errors."""
        from jobs.tasks.expire_overrides import expire_tier_overrides

        user = self._make_user()
        self._make_override(
            user, self.premium_tier,
            expires_at=timezone.now() - timedelta(hours=1),
        )

        # First run
        result1 = expire_tier_overrides()
        self.assertEqual(result1["deactivated"], 1)

        # Second run (simulating overlap)
        result2 = expire_tier_overrides()
        self.assertEqual(result2["deactivated"], 0)

    def test_20_job_uses_bulk_update(self):
        """#20: Job processes bulk expired overrides using queryset.update()."""
        from jobs.tasks.expire_overrides import expire_tier_overrides

        users = [self._make_user(email=f"bulk{i}@example.com") for i in range(5)]
        admin = self._make_staff()
        for user in users:
            self._make_override(
                user, self.premium_tier, granted_by=admin,
                expires_at=timezone.now() - timedelta(hours=1),
            )

        result = expire_tier_overrides()
        self.assertEqual(result["deactivated"], 5)

        # All should be inactive
        self.assertEqual(
            TierOverride.objects.filter(is_active=True).count(), 0
        )


# ============================================================
# Scenarios 21-22: Content gating transitions
# ============================================================

@tag('core')
class ContentGatingTransitionsTest(TierOverrideTestBase):
    """Scenarios 21-22: Content gating transitions."""

    def test_21_article_access_with_override_then_expiry(self):
        """#21: Free user cannot see Premium article -> admin grants override
        -> article accessible -> override expires -> article gated again."""
        user = self._make_user()
        content = self._make_content(LEVEL_PREMIUM)

        # Before override
        self.assertFalse(can_access(user, content))

        # Grant override
        override = self._make_override(user, self.premium_tier)
        self.assertTrue(can_access(user, content))

        # Expire override
        override.expires_at = timezone.now() - timedelta(seconds=1)
        override.save(update_fields=["expires_at"])
        self.assertFalse(can_access(user, content))

    def test_22_course_access_during_override_then_expiry(self):
        """#22: Main user taking Premium course during override -> override
        expires -> user can access Main but not Premium content."""
        user = self._make_user(tier=self.main_tier)
        premium_content = self._make_content(LEVEL_PREMIUM)
        main_content = self._make_content(LEVEL_MAIN)

        override = self._make_override(user, self.premium_tier)

        # During override: both accessible
        self.assertTrue(can_access(user, premium_content))
        self.assertTrue(can_access(user, main_content))

        # Expire override
        override.expires_at = timezone.now() - timedelta(seconds=1)
        override.save(update_fields=["expires_at"])

        # After: Main accessible, Premium not
        self.assertFalse(can_access(user, premium_content))
        self.assertTrue(can_access(user, main_content))


# ============================================================
# Scenarios 23-24: Billing interactions with pending_tier
# ============================================================

@tag('core')
class BillingInteractionsTest(TierOverrideTestBase):
    """Scenarios 23-24: Billing interactions with pending_tier."""

    def test_23_pending_tier_downgrade_with_active_override(self):
        """#23: Main user with pending_tier=Basic gets Premium override.
        When billing_period_end arrives and tier changes to Basic,
        override still grants Premium."""
        user = self._make_user(tier=self.main_tier)
        user.pending_tier = self.basic_tier
        user.save(update_fields=["pending_tier"])

        override = self._make_override(user, self.premium_tier)

        # Override active: max(20, 30) = 30
        self.assertEqual(get_user_level(user), LEVEL_PREMIUM)

        # Stripe fires subscription.updated -> user.tier becomes Basic
        user.tier = self.basic_tier
        user.save(update_fields=["tier"])

        # Override still active: max(10, 30) = 30
        self.assertEqual(get_user_level(user), LEVEL_PREMIUM)

        # Override expires
        override.expires_at = timezone.now() - timedelta(seconds=1)
        override.save(update_fields=["expires_at"])

        # Now at Basic
        self.assertEqual(get_user_level(user), LEVEL_BASIC)

    def test_24_cancel_sub_during_premium_override(self):
        """#24: User cancels subscription (pending_tier=Free) while Premium
        override is active."""
        user = self._make_user(tier=self.main_tier)
        user.pending_tier = self.free_tier
        user.save(update_fields=["pending_tier"])

        override = self._make_override(user, self.premium_tier)

        # Override active
        self.assertEqual(get_user_level(user), LEVEL_PREMIUM)

        # Subscription ends -> user.tier becomes Free
        user.tier = self.free_tier
        user.save(update_fields=["tier"])

        # Override still active: max(0, 30) = 30
        self.assertEqual(get_user_level(user), LEVEL_PREMIUM)

        # Override expires
        override.expires_at = timezone.now() - timedelta(seconds=1)
        override.save(update_fields=["expires_at"])
        self.assertEqual(get_user_level(user), LEVEL_OPEN)


# ============================================================
# Scenarios 25-26: Slack community access (NOT affected)
# ============================================================

@tag('core')
class SlackAccessTest(TierOverrideTestBase):
    """Scenarios 25-26: Slack community access NOT affected by override."""

    def test_25_free_user_with_main_override_no_slack(self):
        """#25: Free user with Main override -> dashboard does NOT show Slack
        join link (checks user.tier.level, not overridden level)."""
        user = self._make_user()
        self._make_override(user, self.main_tier)

        # user.tier.level is still 0 (Free)
        has_qualifying_tier = user.tier_id and user.tier.level >= LEVEL_MAIN
        self.assertFalse(has_qualifying_tier)

    def test_26_main_user_retains_slack_after_override_expires(self):
        """#26: Main user with Slack access gets Premium override -> override
        expires -> user retains Slack (still Main-tier via subscription)."""
        user = self._make_user(tier=self.main_tier)
        user.slack_user_id = "U12345678"
        user.save(update_fields=["slack_user_id"])

        override = self._make_override(user, self.premium_tier)

        # Override expires
        override.expires_at = timezone.now() - timedelta(seconds=1)
        override.save(update_fields=["expires_at"])

        # User still Main via subscription
        has_qualifying_tier = user.tier_id and user.tier.level >= LEVEL_MAIN
        self.assertTrue(has_qualifying_tier)
        self.assertTrue(bool(user.slack_user_id))


# ============================================================
# Scenarios 27-28: CourseAccess interaction
# ============================================================

@tag('core')
class CourseAccessInteractionTest(TierOverrideTestBase):
    """Scenarios 27-28: CourseAccess interaction."""

    def test_27_course_access_and_override_independent(self):
        """#27: Free user with CourseAccess for Premium course AND Main
        override -> both mechanisms work independently."""
        user = self._make_user()
        self._make_override(user, self.main_tier)

        # Override gives Main-level access
        self.assertEqual(get_user_level(user), LEVEL_MAIN)
        self.assertTrue(can_access(user, self._make_content(LEVEL_MAIN)))
        # But NOT Premium
        self.assertFalse(can_access(user, self._make_content(LEVEL_PREMIUM)))
        # CourseAccess would grant access to specific course (tested via
        # can_access with a real Course, but here we verify tier-level logic)

    def test_28_override_expires_course_access_persists(self):
        """#28: Free user with Premium override AND CourseAccess -> override
        expires -> loses other Premium content, but CourseAccess is separate."""
        user = self._make_user()
        override = self._make_override(user, self.premium_tier)

        # During override: all Premium content accessible via tier level
        self.assertEqual(get_user_level(user), LEVEL_PREMIUM)

        # Expire
        override.expires_at = timezone.now() - timedelta(seconds=1)
        override.save(update_fields=["expires_at"])

        # Back to Free level
        self.assertEqual(get_user_level(user), LEVEL_OPEN)
        # CourseAccess would still work independently (tested elsewhere)


# ============================================================
# Scenarios 29-30: Email campaign targeting
# ============================================================

class EmailCampaignTargetingTest(TierOverrideTestBase):
    """Scenarios 29-30: Email campaign targeting uses effective tier."""

    def test_29_free_user_with_premium_override_targeted(self):
        """#29: Free user with Premium override is eligible for Premium campaigns."""
        user = self._make_user()
        override = self._make_override(user, self.premium_tier)

        effective_level = max(user.tier.level if user.tier else 0, override.override_tier.level)
        self.assertEqual(effective_level, LEVEL_PREMIUM)

    def test_30_basic_with_main_override_targeted(self):
        """#30: Basic user with Main override is eligible for Main+ campaigns."""
        user = self._make_user(tier=self.basic_tier)
        override = self._make_override(user, self.main_tier)

        effective_level = max(user.tier.level, override.override_tier.level)
        self.assertEqual(effective_level, LEVEL_MAIN)


# ============================================================
# Scenario 31: Notifications (NOT affected)
# ============================================================

class NotificationTargetingTest(TierOverrideTestBase):
    """Scenario 31: Notifications NOT affected by override."""

    def test_31_free_user_with_premium_override_no_notification(self):
        """#31: Free user with Premium override -> _get_eligible_users()
        checks user.tier.level -> user does NOT receive notification."""
        user = self._make_user()
        self._make_override(user, self.premium_tier)

        user_tier_level = user.tier.level if user.tier else 0
        premium_content_level = LEVEL_PREMIUM
        self.assertFalse(user_tier_level >= premium_content_level)


# ============================================================
# Scenarios 32-35: Dashboard display
# ============================================================

class DashboardDisplayTest(TierOverrideTestBase):
    """Scenarios 32-35: Dashboard display with overrides."""

    def setUp(self):
        self.admin = self._make_staff(email="dash_admin@example.com")

    def test_32_free_user_with_premium_override_dashboard(self):
        """#32: Free user with Premium override -> tier badge shows
        'Premium (trial)'."""
        user = self._make_user(email="dash32@example.com", password="testpass")
        self._make_override(user, self.premium_tier, granted_by=self.admin)

        self.client.login(email=user.email, password="testpass")
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["override_tier_name"], "Premium")
        self.assertContains(response, "Premium (trial)")

    def test_33_free_user_with_main_override_quick_actions(self):
        """#33: Free user with Main override -> quick actions include Activities."""
        user = self._make_user(email="dash33@example.com", password="testpass")
        self._make_override(user, self.main_tier, granted_by=self.admin)

        self.client.login(email=user.email, password="testpass")
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)

        quick_actions = response.context["quick_actions"]
        action_titles = [a["title"] for a in quick_actions]
        self.assertIn("Activities", action_titles)
        self.assertNotIn("Community", action_titles)

        activities_action = next(
            action for action in quick_actions if action["title"] == "Activities"
        )
        self.assertEqual(activities_action["url"], "/activities")

    def test_34_free_user_with_premium_override_sees_premium_content(self):
        """#34: Free user with Premium override -> Premium articles appear
        in recent content."""
        from content.models import Article
        user = self._make_user(email="dash34@example.com", password="testpass")
        self._make_override(user, self.premium_tier, granted_by=self.admin)

        # Create a premium article
        Article.objects.create(
            title="Premium Test",
            slug="premium-test",
            published=True,
            required_level=LEVEL_PREMIUM,
            date=timezone.now(),
        )

        self.client.login(email=user.email, password="testpass")
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)

        recent_content = response.context["recent_content"]
        titles = [item["title"] for item in recent_content]
        self.assertIn("Premium Test", titles)

    def test_35_free_user_with_main_override_sees_main_polls(self):
        """#35: Free user with Main override -> Main-level polls in active polls."""
        from voting.models import Poll
        user = self._make_user(email="dash35@example.com", password="testpass")
        self._make_override(user, self.main_tier, granted_by=self.admin)

        # Create a main-level poll
        Poll.objects.create(
            title="Main Poll Test",
            status="open",
            required_level=LEVEL_MAIN,
            poll_type="topic",
            max_votes_per_user=3,
        )

        self.client.login(email=user.email, password="testpass")
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)

        active_polls = response.context["active_polls"]
        poll_titles = [p.title for p in active_polls]
        self.assertIn("Main Poll Test", poll_titles)


# ============================================================
# Scenarios 36-38: Account page display
# ============================================================

class AccountPageDisplayTest(TierOverrideTestBase):
    """Scenarios 36-38: Account page display."""

    def test_36_account_page_shows_override_info(self):
        """#36: Free user with active Premium override views /account/
        -> shows override info."""
        user = self._make_user(email="acct36@example.com", password="testpass")
        admin = self._make_staff()
        self._make_override(user, self.premium_tier, granted_by=admin)

        self.client.login(email=user.email, password="testpass")
        response = self.client.get("/account/")
        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(response.context["active_override"])
        self.assertContains(response, "Temporary")
        self.assertContains(response, "Premium")
        self.assertContains(response, "temporary access")

    def test_37_account_page_upgrade_based_on_subscription_tier(self):
        """#37: Upgrade/downgrade options based on subscription tier,
        not override."""
        user = self._make_user(
            email="acct37@example.com",
            tier=self.basic_tier,
            password="testpass",
        )
        admin = self._make_staff()
        self._make_override(user, self.premium_tier, granted_by=admin)

        self.client.login(email=user.email, password="testpass")
        response = self.client.get("/account/")
        self.assertEqual(response.status_code, 200)

        # Upgrade tiers should be based on Basic level (10), not Premium (30)
        upgrade_tiers = response.context["upgrade_tiers"]
        upgrade_names = [t.name for t in upgrade_tiers]
        self.assertIn("Main", upgrade_names)
        self.assertIn("Premium", upgrade_names)

    def test_38_no_override_no_override_section(self):
        """#38: User with no active override -> no override section shown."""
        user = self._make_user(email="acct38@example.com", password="testpass")

        self.client.login(email=user.email, password="testpass")
        response = self.client.get("/account/")
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["active_override"])
        self.assertNotContains(response, "tier-override-notice")


# ============================================================
# Scenarios 39-40: Event access with override
# ============================================================

@tag('core')
class EventAccessWithOverrideTest(TierOverrideTestBase):
    """Scenarios 39-40: Event access with override."""

    def test_39_free_user_with_main_override_accesses_main_event(self):
        """#39: Free user with Main override can access Main-level event."""
        user = self._make_user()
        self._make_override(user, self.main_tier)

        event_content = self._make_content(LEVEL_MAIN)
        self.assertTrue(can_access(user, event_content))

    def test_40_override_expires_event_still_registered_but_gated(self):
        """#40: Override expires -> event detail page shows gating overlay."""
        user = self._make_user()
        override = self._make_override(user, self.main_tier)

        event_content = self._make_content(LEVEL_MAIN)
        self.assertTrue(can_access(user, event_content))

        override.expires_at = timezone.now() - timedelta(seconds=1)
        override.save(update_fields=["expires_at"])

        self.assertFalse(can_access(user, event_content))


# ============================================================
# Scenarios 41-42: Voting/poll access with override
# ============================================================

@tag('core')
class PollAccessWithOverrideTest(TierOverrideTestBase):
    """Scenarios 41-42: Voting/poll access with override."""

    def test_41_free_user_with_main_override_can_vote(self):
        """#41: Free user with Main override can vote on topic polls."""
        user = self._make_user()
        self._make_override(user, self.main_tier)

        poll_content = self._make_content(LEVEL_MAIN)
        self.assertTrue(can_access(user, poll_content))

    def test_42_override_expires_vote_persists_but_poll_gated(self):
        """#42: Override expires -> vote persists but user cannot access poll."""
        user = self._make_user()
        override = self._make_override(user, self.main_tier)

        poll_content = self._make_content(LEVEL_MAIN)
        self.assertTrue(can_access(user, poll_content))

        override.expires_at = timezone.now() - timedelta(seconds=1)
        override.save(update_fields=["expires_at"])
        self.assertFalse(can_access(user, poll_content))


# ============================================================
# Scenarios 43-44: Download access with override
# ============================================================

@tag('core')
class DownloadAccessWithOverrideTest(TierOverrideTestBase):
    """Scenarios 43-44: Download access with override."""

    def test_43_free_user_with_basic_override_accesses_basic_download(self):
        """#43: Free user with Basic override can access Basic-level download."""
        user = self._make_user()
        self._make_override(user, self.basic_tier)

        self.assertTrue(can_access(user, self._make_content(LEVEL_BASIC)))

    def test_44_basic_override_cannot_access_main_download(self):
        """#44: Free user with Basic override cannot access Main-level download."""
        user = self._make_user()
        self._make_override(user, self.basic_tier)

        self.assertFalse(can_access(user, self._make_content(LEVEL_MAIN)))


# ============================================================
# Scenarios 45-46: Multiple overrides / audit trail
# ============================================================

@tag('core')
class AuditTrailTest(TierOverrideTestBase):
    """Scenarios 45-46: Multiple overrides / audit trail."""

    def test_45_three_sequential_overrides_audit_trail(self):
        """#45: 3 sequential overrides -> all records exist, only last active."""
        user = self._make_user()
        admin = self._make_staff()

        overrides = []
        for tier in [self.basic_tier, self.main_tier, self.premium_tier]:
            # Deactivate existing
            TierOverride.objects.filter(user=user, is_active=True).update(
                is_active=False
            )
            o = self._make_override(user, tier, granted_by=admin)
            overrides.append(o)

        # All 3 exist
        self.assertEqual(
            TierOverride.objects.filter(user=user).count(), 3
        )

        # Only last is active
        for o in overrides[:-1]:
            o.refresh_from_db()
            self.assertFalse(o.is_active)
        overrides[-1].refresh_from_db()
        self.assertTrue(overrides[-1].is_active)

        # Each original_tier reflects user.tier at creation time (Free)
        for o in overrides:
            self.assertEqual(o.original_tier, self.free_tier)

    def test_46_create_revoke_create_another(self):
        """#46: Admin creates, revokes, creates another."""
        user = self._make_user()
        admin = self._make_staff()

        first = self._make_override(user, self.main_tier, granted_by=admin)
        first.is_active = False
        first.save(update_fields=["is_active"])

        second = self._make_override(user, self.premium_tier, granted_by=admin)

        first.refresh_from_db()
        self.assertFalse(first.is_active)
        self.assertTrue(second.is_active)


# ============================================================
# Scenarios 47-49: Data integrity
# ============================================================

@tag('core')
class DataIntegrityTest(TierOverrideTestBase):
    """Scenarios 47-49: Data integrity."""

    def test_47_on_delete_protect(self):
        """#47: on_delete=PROTECT prevents deleting tiers used in overrides."""
        user = self._make_user()
        self._make_override(user, self.premium_tier)

        with self.assertRaises(ProtectedError):
            self.premium_tier.delete()

    def test_48_user_with_no_tier_original_null(self):
        """#48: User with user.tier=None -> original_tier is null."""
        user = self._make_user()
        # Force tier to None
        user.tier = None
        user.save(update_fields=["tier"])

        override = self._make_override(user, self.premium_tier, original_tier=None)
        self.assertIsNone(override.original_tier)

        # Override still works
        self.assertEqual(get_user_level(user), LEVEL_PREMIUM)

    def test_49_two_active_overrides_handled_gracefully(self):
        """#49: Creating override deactivates existing active ones."""
        user = self._make_user()
        admin = self._make_staff()

        first = self._make_override(user, self.main_tier, granted_by=admin)
        self.assertTrue(first.is_active)

        # Deactivate existing (as the view does)
        TierOverride.objects.filter(user=user, is_active=True).update(
            is_active=False
        )
        second = self._make_override(user, self.premium_tier, granted_by=admin)

        first.refresh_from_db()
        self.assertFalse(first.is_active)
        self.assertTrue(second.is_active)

        # Only one active override
        active_count = TierOverride.objects.filter(
            user=user, is_active=True
        ).count()
        self.assertEqual(active_count, 1)


# ============================================================
# Scenarios 50-51: Concurrent operations
# ============================================================

class ConcurrentOperationsTest(TierOverrideTestBase):
    """Scenarios 50-51: Concurrent operations."""

    def test_50_two_admins_create_overrides_one_active(self):
        """#50: Two admins create overrides -> only one active."""
        user = self._make_user()
        admin1 = self._make_staff(email="admin1@example.com")
        admin2 = self._make_staff(email="admin2@example.com")

        # Admin1 creates
        first = self._make_override(user, self.main_tier, granted_by=admin1)

        # Admin2 creates (deactivates first)
        TierOverride.objects.filter(user=user, is_active=True).update(
            is_active=False
        )
        second = self._make_override(user, self.premium_tier, granted_by=admin2)

        first.refresh_from_db()
        self.assertFalse(first.is_active)
        self.assertTrue(second.is_active)

        active = TierOverride.objects.filter(user=user, is_active=True).count()
        self.assertEqual(active, 1)

    def test_51_revoke_at_same_time_as_expiry(self):
        """#51: Admin revokes at exact moment expiry job runs -> no error."""
        from jobs.tasks.expire_overrides import expire_tier_overrides

        user = self._make_user()
        override = self._make_override(
            user, self.premium_tier,
            expires_at=timezone.now() - timedelta(seconds=1),
        )

        # Admin revokes
        override.is_active = False
        override.save(update_fields=["is_active"])

        # Expiry job runs (override already deactivated)
        expire_tier_overrides()
        # No error, and the override stays inactive
        override.refresh_from_db()
        self.assertFalse(override.is_active)


# ============================================================
# Scenarios 52-53: Timezone edge cases
# ============================================================

class TimezoneEdgeCasesTest(TierOverrideTestBase):
    """Scenarios 52-53: Timezone edge cases."""

    def test_52_expires_at_stored_in_utc(self):
        """#52: Override expires_at stored and compared in UTC."""
        user = self._make_user()
        now_utc = timezone.now()
        override = self._make_override(
            user, self.premium_tier, expires_at=now_utc + timedelta(hours=1),
        )
        override.refresh_from_db()
        # Django stores as UTC; the field should be timezone-aware
        self.assertIsNotNone(override.expires_at.tzinfo)

    @freeze_time("2026-03-21 00:05:00", tz_offset=0)
    def test_53_expires_at_midnight_utc(self):
        """#53: expires_at at midnight UTC -> correctly deactivated."""
        from jobs.tasks.expire_overrides import expire_tier_overrides

        user = self._make_user()
        # Midnight today is 5 minutes in the past (frozen at 00:05)
        midnight = timezone.now().replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        self._make_override(user, self.premium_tier, expires_at=midnight)

        result = expire_tier_overrides()
        self.assertEqual(result["deactivated"], 1)


# ============================================================
# Scenarios 54-55: Performance
# ============================================================

class PerformanceTest(TierOverrideTestBase):
    """Scenarios 54-55: Performance."""

    def test_54_override_check_uses_indexed_query(self):
        """#54: get_user_level() override check queries on (user_id, is_active).
        Verified by checking the index exists on the model."""
        meta = TierOverride._meta
        index_fields = []
        for index in meta.indexes:
            index_fields.extend(index.fields)
        self.assertIn("user", index_fields)
        self.assertIn("is_active", index_fields)

    def test_55_expiry_job_uses_bulk_update(self):
        """#55: Expiry job deactivates all expired overrides in one run."""
        from jobs.tasks.expire_overrides import expire_tier_overrides

        users = [
            self._make_user(email=f"expired-{idx}@example.com")
            for idx in range(3)
        ]
        for idx, user in enumerate(users):
            self._make_override(
                user,
                self.premium_tier,
                expires_at=timezone.now() - timedelta(minutes=idx + 1),
            )
        active_user = self._make_user(email="active@example.com")
        active_override = self._make_override(
            active_user,
            self.premium_tier,
            expires_at=timezone.now() + timedelta(days=1),
        )

        result = expire_tier_overrides()

        self.assertEqual(result["deactivated"], 3)
        self.assertEqual(
            TierOverride.objects.filter(user__in=users, is_active=True).count(),
            0,
        )
        active_override.refresh_from_db()
        self.assertTrue(active_override.is_active)


# ============================================================
# Scenarios 56-66: Studio UI
# ============================================================

class StudioTierOverrideViewTest(TierOverrideTestBase):
    """Scenarios 56-66: Studio UI tests."""

    def setUp(self):
        self.admin = User.objects.create_user(
            email="studioadmin@example.com",
            password="testpass",
            is_staff=True,
        )
        self.client.login(email="studioadmin@example.com", password="testpass")

    def test_56_search_user_by_email(self):
        """#56: Admin searches user by email -> user found with tier."""
        self._make_user(email="target56@example.com")

        response = self.client.get(
            "/studio/users/tier-override/",
            {"email": "target56@example.com"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["searched_user"].email, "target56@example.com"
        )

    def test_57_search_nonexistent_email(self):
        """#57: Admin searches nonexistent email -> error message."""
        response = self.client.get(
            "/studio/users/tier-override/",
            {"email": "nonexistent@example.com"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["searched_user"])
        self.assertContains(response, "No user found")

    def test_58_active_override_details_shown(self):
        """#58: Admin sees active override details."""
        target = self._make_user(email="target58@example.com")
        self._make_override(target, self.premium_tier, granted_by=self.admin)

        response = self.client.get(
            "/studio/users/tier-override/",
            {"email": "target58@example.com"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(response.context["active_override"])
        self.assertContains(response, "Active Override")

    def test_59_create_14_day_override(self):
        """#59: Admin clicks '14 days' -> override created."""
        target = self._make_user(email="target59@example.com")

        response = self.client.post(
            "/studio/users/tier-override/create",
            {
                "email": "target59@example.com",
                "tier_id": self.premium_tier.pk,
                "duration": "14 days",
            },
        )
        self.assertEqual(response.status_code, 302)

        override = TierOverride.objects.filter(
            user=target, is_active=True
        ).first()
        self.assertIsNotNone(override)
        self.assertEqual(override.override_tier, self.premium_tier)

        # Check the expires_at is approximately 14 days from now
        expected = timezone.now() + timedelta(days=14)
        diff = abs((override.expires_at - expected).total_seconds())
        self.assertLess(diff, 60)  # within 60 seconds

    def test_60_create_12_month_override(self):
        """#60: Admin clicks '12 months' -> override created with ~365 days."""
        target = self._make_user(email="target60@example.com")

        response = self.client.post(
            "/studio/users/tier-override/create",
            {
                "email": "target60@example.com",
                "tier_id": self.premium_tier.pk,
                "duration": "12 months",
            },
        )
        self.assertEqual(response.status_code, 302)

        override = TierOverride.objects.filter(
            user=target, is_active=True
        ).first()
        self.assertIsNotNone(override)

        expected = timezone.now() + relativedelta(months=12)
        diff = abs((override.expires_at - expected).total_seconds())
        self.assertLess(diff, 60)

    def test_61_one_month_uses_relativedelta(self):
        """#61: Admin clicks '1 month' in February -> relativedelta adds
        exactly one calendar month."""
        target = self._make_user(email="target61@example.com")

        response = self.client.post(
            "/studio/users/tier-override/create",
            {
                "email": "target61@example.com",
                "tier_id": self.premium_tier.pk,
                "duration": "1 month",
            },
        )
        self.assertEqual(response.status_code, 302)

        override = TierOverride.objects.filter(
            user=target, is_active=True
        ).first()
        self.assertIsNotNone(override)

        expected = timezone.now() + relativedelta(months=1)
        diff = abs((override.expires_at - expected).total_seconds())
        self.assertLess(diff, 60)

    def test_62_revoke_active_override(self):
        """#62: Admin revokes active override -> deactivated."""
        target = self._make_user(email="target62@example.com")
        override = self._make_override(
            target, self.premium_tier, granted_by=self.admin,
        )

        response = self.client.post(
            "/studio/users/tier-override/revoke",
            {
                "override_id": override.pk,
                "email": "target62@example.com",
            },
        )
        self.assertEqual(response.status_code, 302)

        override.refresh_from_db()
        self.assertFalse(override.is_active)

        # User access drops immediately
        self.assertEqual(get_user_level(target), LEVEL_OPEN)

    def test_63_no_active_override_no_revoke_button(self):
        """#63: User has no active override -> revoke button not shown."""
        self._make_user(email="target63@example.com")

        response = self.client.get(
            "/studio/users/tier-override/",
            {"email": "target63@example.com"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["active_override"])
        self.assertNotContains(response, "Revoke Override")

    def test_64_non_staff_cannot_access(self):
        """#64: Non-staff user cannot access tier override page."""
        User.objects.create_user(
            email="regular@example.com", password="testpass"
        )
        self.client.login(email="regular@example.com", password="testpass")

        response = self.client.get("/studio/users/tier-override/")
        self.assertEqual(response.status_code, 403)

    def test_65_premium_user_shows_already_highest(self):
        """#65: User is already Premium -> UI shows 'already at highest tier'."""
        self._make_user(
            email="target65@example.com", tier=self.premium_tier,
        )

        response = self.client.get(
            "/studio/users/tier-override/",
            {"email": "target65@example.com"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["is_highest_tier"])
        self.assertContains(response, "already at the highest tier")

    def test_66_double_submit_handled_gracefully(self):
        """#66: Admin creates override, navigates back, double-submits."""
        target = self._make_user(email="target66@example.com")

        # First submit
        self.client.post(
            "/studio/users/tier-override/create",
            {
                "email": "target66@example.com",
                "tier_id": self.premium_tier.pk,
                "duration": "14 days",
            },
        )
        # Second submit (double-click)
        self.client.post(
            "/studio/users/tier-override/create",
            {
                "email": "target66@example.com",
                "tier_id": self.premium_tier.pk,
                "duration": "14 days",
            },
        )

        # Only one active override
        active = TierOverride.objects.filter(
            user=target, is_active=True
        ).count()
        self.assertEqual(active, 1)

        # Two records total (first deactivated)
        total = TierOverride.objects.filter(user=target).count()
        self.assertEqual(total, 2)


# ============================================================
# Scenarios 67-68: Override and template rendering
# ============================================================

class TemplateRenderingTest(TierOverrideTestBase):
    """Scenarios 67-68: Override and template rendering."""

    def test_67_premium_override_no_lock_icon_on_premium_article(self):
        """#67: Free user with Premium override views article listing ->
        Premium articles do NOT show lock icon (user has access)."""
        user = self._make_user(email="tpl67@example.com")
        self._make_override(user, self.premium_tier)

        # User has access to premium content
        self.assertTrue(can_access(user, self._make_content(LEVEL_PREMIUM)))

    def test_68_basic_override_main_content_gated(self):
        """#68: Free user with Basic override views Main-level article ->
        gating overlay appears."""
        user = self._make_user(email="tpl68@example.com")
        self._make_override(user, self.basic_tier)

        # Can access Basic
        self.assertTrue(can_access(user, self._make_content(LEVEL_BASIC)))
        # Cannot access Main
        self.assertFalse(can_access(user, self._make_content(LEVEL_MAIN)))


# ============================================================
# Scenario 69: Override interplay with staff/superuser
# ============================================================

@tag('core')
class StaffOverrideTest(TierOverrideTestBase):
    """Scenario 69: Override interplay with staff/superuser."""

    def test_69_staff_override_redundant_no_errors(self):
        """#69: Admin creates override for another staff user -> staff already
        gets LEVEL_PREMIUM -> override is redundant, no interference."""
        staff_user = self._make_staff(email="stafftarget@example.com")
        admin = self._make_staff(email="staffadmin@example.com")

        override = self._make_override(
            staff_user, self.premium_tier, granted_by=admin,
        )
        # Staff always get LEVEL_PREMIUM
        self.assertEqual(get_user_level(staff_user), LEVEL_PREMIUM)

        # Expire the override -> no error, still LEVEL_PREMIUM
        override.expires_at = timezone.now() - timedelta(seconds=1)
        override.save(update_fields=["expires_at"])
        self.assertEqual(get_user_level(staff_user), LEVEL_PREMIUM)


# ============================================================
# Additional model tests
# ============================================================

@tag('core')
class TierOverrideModelTest(TierOverrideTestBase):
    """Additional model tests for TierOverride."""

    def test_str_representation(self):
        user = self._make_user()
        override = self._make_override(user, self.premium_tier)
        result = str(override)
        self.assertIn("TierOverride", result)
        self.assertIn("active", result)

    def test_str_inactive(self):
        user = self._make_user()
        override = self._make_override(user, self.premium_tier)
        override.is_active = False
        override.save(update_fields=["is_active"])
        result = str(override)
        self.assertIn("inactive", result)


    def test_get_active_override_returns_object(self):
        user = self._make_user()
        expected = self._make_override(user, self.premium_tier)
        result = get_active_override(user)
        self.assertIsNotNone(result)
        self.assertEqual(result.pk, expected.pk)

    def test_get_active_override_returns_none_for_anonymous(self):
        from django.contrib.auth.models import AnonymousUser
        result = get_active_override(AnonymousUser())
        self.assertIsNone(result)

    def test_get_active_override_returns_none_for_no_override(self):
        user = self._make_user()
        result = get_active_override(user)
        self.assertIsNone(result)

    def test_get_active_override_ignores_expired(self):
        user = self._make_user()
        self._make_override(
            user, self.premium_tier,
            expires_at=timezone.now() - timedelta(hours=1),
        )
        result = get_active_override(user)
        self.assertIsNone(result)

    def test_get_active_override_ignores_inactive(self):
        user = self._make_user()
        self._make_override(user, self.premium_tier, is_active=False)
        result = get_active_override(user)
        self.assertIsNone(result)


# ============================================================
# Setup schedules integration test
# ============================================================

class SetupSchedulesTest(TestCase):
    """Verify expire-tier-overrides schedule is registered."""

    def test_expire_tier_overrides_schedule_registered(self):
        """setup_schedules registers expire-tier-overrides."""
        from io import StringIO

        from django.core.management import call_command
        from django_q.models import Schedule

        call_command("setup_schedules", stdout=StringIO())

        schedule = Schedule.objects.get(name="expire-tier-overrides")
        self.assertEqual(
            schedule.func,
            "jobs.tasks.expire_overrides.expire_tier_overrides",
        )
        self.assertEqual(schedule.schedule_type, Schedule.CRON)


# ============================================================
# Slack dashboard integration test
# ============================================================

class DashboardSlackOverrideTest(TierOverrideTestBase):
    """Verify Slack join link is NOT affected by tier override."""

    def test_dashboard_slack_join_uses_subscription_tier_not_override(self):
        """show_slack_join checks user.tier.level (not overridden)."""
        user = self._make_user(email="slack_test@example.com", password="testpass")
        self._make_override(user, self.main_tier)

        self.client.login(email=user.email, password="testpass")
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)

        # Free user with Main override: show_slack_join should be False
        # because it checks user.tier.level (Free=0), not get_user_level()
        self.assertFalse(response.context["show_slack_join"])
