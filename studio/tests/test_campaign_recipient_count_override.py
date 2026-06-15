"""Override-aware campaign recipient-count preview (issue #966).

``_recipient_count_for_level`` must count override holders so the Create-form
audience preview equals what ``EmailCampaign.get_eligible_recipients`` actually
ships for the same ``target_min_level`` (no tag/slack filters, default
verification). Expired / inactive / below-threshold overrides are excluded,
and ``.distinct()`` prevents double-counting.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.utils import timezone

from accounts.models import TierOverride
from content.access import LEVEL_MAIN
from email_app.models import EmailCampaign
from studio.views.campaigns import _recipient_count_for_level
from tests.fixtures import TierSetupMixin

User = get_user_model()


@tag('core')
class RecipientCountOverrideTest(TierSetupMixin, TestCase):
    """The preview count includes active override holders."""

    def _user(self, email, tier, *, verified=True, unsub=False):
        return User.objects.create_user(
            email=email, password="pw", tier=tier,
            email_verified=verified, unsubscribed=unsub,
        )

    def _override(self, user, tier, *, is_active=True, expires_in_days=7):
        TierOverride.objects.create(
            user=user,
            original_tier=user.tier,
            override_tier=tier,
            expires_at=timezone.now() + timedelta(days=expires_in_days),
            is_active=is_active,
        )

    def setUp(self):
        # (a) Main-base verified subscribed -> counted
        self.main_base = self._user("a-main@t.com", self.main_tier)
        # (b) Free-base + active Main override verified subscribed -> counted
        self.override_user = self._user("b-override@t.com", self.free_tier)
        self._override(self.override_user, self.main_tier)
        # (c) Free-base + EXPIRED Main override -> not counted
        self.expired_user = self._user("c-expired@t.com", self.free_tier)
        self._override(self.expired_user, self.main_tier, expires_in_days=-1)
        # (d) Free-base no override -> not counted
        self.free_user = self._user("d-free@t.com", self.free_tier)
        # (e) Basic-base + active Basic override -> not counted at Main
        self.basic_user = self._user("e-basic@t.com", self.basic_tier)
        self._override(self.basic_user, self.basic_tier)

    def test_count_includes_active_override_excludes_others(self):
        # Only {a, b} reach LEVEL_MAIN effectively.
        self.assertEqual(_recipient_count_for_level(LEVEL_MAIN), 2)

    def test_preview_equals_send_path_count(self):
        campaign = EmailCampaign.objects.create(
            subject="s", body="b", target_min_level=LEVEL_MAIN,
        )
        send_path_count = campaign.get_eligible_recipients().count()
        self.assertEqual(
            _recipient_count_for_level(LEVEL_MAIN), send_path_count,
        )
        # And the actual recipients are exactly a and b.
        recipient_emails = set(
            campaign.get_eligible_recipients().values_list("email", flat=True),
        )
        self.assertEqual(
            recipient_emails, {"a-main@t.com", "b-override@t.com"},
        )

    def test_expired_override_not_counted(self):
        # Removing the active fixtures leaves only the expired override user
        # at Free base -> the preview must be 0 at Main.
        self.main_base.delete()
        self.override_user.delete()
        self.assertEqual(_recipient_count_for_level(LEVEL_MAIN), 0)

    def test_distinct_prevents_double_count(self):
        # A user with BOTH a Main base tier AND an active Main override must
        # be counted once, not twice.
        dup = self._user("dup@t.com", self.main_tier)
        self._override(dup, self.main_tier)
        # Now eligible at Main: a, b, dup -> 3, not 4.
        self.assertEqual(_recipient_count_for_level(LEVEL_MAIN), 3)

    def test_unsubscribed_and_unverified_excluded(self):
        # An active Main override does not rescue an unsubscribed/unverified
        # user — those filters are still enforced.
        unsub = self._user("unsub@t.com", self.free_tier, unsub=True)
        self._override(unsub, self.main_tier)
        unverified = self._user("unver@t.com", self.free_tier, verified=False)
        self._override(unverified, self.main_tier)
        # Still only a and b.
        self.assertEqual(_recipient_count_for_level(LEVEL_MAIN), 2)
