"""Tests for the kernel access policy wiring (plan issue A0.3).

TierAccessPolicy must reproduce the site's tier semantics through the
community-base kernel protocol:

- user_level: anonymous 0, tier level, staff premium, override grants max
- can_access: open is public, the registered wall follows issue #465
  (anonymous denied, verified free allowed, unverified free denied),
  paid levels follow tier level
- level_label falls back to the level number for unknown values
- the kernel resolves the configured policy, so
  ``community_base.kernel.access.can_access`` answers with site semantics
"""

from community_base.kernel.access import LEVEL_BASIC, LEVEL_MAIN, LEVEL_OPEN, LEVEL_PREMIUM
from django.contrib.auth.models import AnonymousUser
from django.test import TestCase, tag
from django.utils import timezone

from accounts.models import TierOverride, User
from content.access import LEVEL_REGISTERED
from content.access_policy import TierAccessPolicy
from tests.fixtures import TierSetupMixin, set_membership

POLICY = TierAccessPolicy()


def make_user(email, tier=None, *, verified=True, staff=False):
    user = User.objects.create_user(
        email=email,
        password="testpass",
        email_verified=verified,
        is_staff=staff,
    )
    set_membership(user, tier=tier)
    return user


@tag('core')
class TierAccessPolicyUserLevelTest(TierSetupMixin, TestCase):
    def test_anonymous_user_is_level_zero(self):
        self.assertEqual(POLICY.user_level(None), 0)
        self.assertEqual(POLICY.user_level(AnonymousUser()), 0)

    def test_tier_level_and_staff_maximum(self):
        free = make_user("free-policy@test.com", self.free_tier)
        staff = make_user("staff-policy@test.com", self.free_tier, staff=True)

        self.assertEqual(POLICY.user_level(free), 0)
        self.assertEqual(POLICY.user_level(staff), LEVEL_PREMIUM)

    def test_active_override_grants_the_higher_level(self):
        learner = make_user("override-policy@test.com", self.free_tier)
        TierOverride.objects.create(
            user=learner,
            override_tier=self.main_tier,
            is_active=True,
            expires_at=timezone.now() + timezone.timedelta(days=7),
        )

        self.assertEqual(POLICY.user_level(learner), LEVEL_MAIN)


@tag('core')
class TierAccessPolicyCanAccessTest(TierSetupMixin, TestCase):
    def test_open_content_is_public(self):
        self.assertTrue(POLICY.can_access(None, LEVEL_OPEN))
        self.assertTrue(POLICY.can_access(AnonymousUser(), LEVEL_OPEN))

    def test_registered_wall_follows_the_site_rule(self):
        anonymous = None
        verified_free = make_user("verified@test.com", self.free_tier)
        unverified_free = make_user(
            "unverified@test.com", self.free_tier, verified=False
        )
        basic = make_user("basic@test.com", self.basic_tier)

        self.assertFalse(POLICY.can_access(anonymous, LEVEL_REGISTERED))
        self.assertTrue(POLICY.can_access(verified_free, LEVEL_REGISTERED))
        self.assertFalse(POLICY.can_access(unverified_free, LEVEL_REGISTERED))
        self.assertTrue(POLICY.can_access(basic, LEVEL_REGISTERED))

    def test_paid_levels_follow_tier_level(self):
        free = make_user("paid-free@test.com", self.free_tier)
        main = make_user("paid-main@test.com", self.main_tier)

        self.assertFalse(POLICY.can_access(free, LEVEL_BASIC))
        self.assertTrue(POLICY.can_access(main, LEVEL_MAIN))


@tag('core')
class TierAccessPolicyLevelLabelTest(TestCase):
    def test_known_and_unknown_levels(self):
        self.assertEqual(POLICY.level_label(LEVEL_OPEN), "Open")
        self.assertEqual(POLICY.level_label(LEVEL_REGISTERED), "Registered")
        self.assertEqual(POLICY.level_label(99), "99")


@tag('core')
class KernelResolutionTest(TierSetupMixin, TestCase):
    """The kernel resolves the configured policy for site semantics."""

    def test_kernel_can_access_uses_the_tier_policy(self):
        from community_base.kernel.access import can_access as kernel_can_access

        main = make_user("kernel-main@test.com", self.main_tier)
        free = make_user("kernel-free@test.com", self.free_tier)

        self.assertTrue(kernel_can_access(main, LEVEL_MAIN))
        self.assertFalse(kernel_can_access(free, LEVEL_MAIN))
        self.assertEqual(
            kernel_can_access(free, LEVEL_REGISTERED),
            bool(free.email_verified),
        )
