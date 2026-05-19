"""Tests for ``EmailCampaign.audience_verification`` (issue #692).

The new selector lets operators relax the historical ``email_verified=True``
filter on ``get_eligible_recipients`` for legitimate broadcast cases (major
announcements, billing changes, terms updates). ``unsubscribed=False`` is
always enforced -- never relaxed.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase, tag

from email_app.models import EmailCampaign
from tests.fixtures import TierSetupMixin

User = get_user_model()


@tag("core")
class AudienceVerificationFieldTest(TierSetupMixin, TestCase):
    """Field constants and default value."""

    def test_choices_constants_defined_on_model(self):
        self.assertEqual(
            EmailCampaign.AUDIENCE_VERIFICATION_VERIFIED_ONLY,
            "verified_only",
        )
        self.assertEqual(
            EmailCampaign.AUDIENCE_VERIFICATION_EVERYONE,
            "everyone",
        )
        self.assertEqual(
            EmailCampaign.AUDIENCE_VERIFICATION_CHOICES,
            [
                ("verified_only", "Verified only"),
                ("everyone", "Everyone (including unverified)"),
            ],
        )

    def test_default_value_is_verified_only(self):
        """New campaigns default to ``verified_only``."""
        campaign = EmailCampaign.objects.create(
            subject="x", body="y", target_min_level=0,
        )
        self.assertEqual(
            campaign.audience_verification,
            EmailCampaign.AUDIENCE_VERIFICATION_VERIFIED_ONLY,
        )


@tag("core")
class GetEligibleRecipientsAudienceVerificationTest(
    TierSetupMixin, TestCase,
):
    """``get_eligible_recipients`` honours the new selector.

    Fixture: 2 verified + 2 unverified users at the same tier, with one
    unsubscribed user in each group to verify ``unsubscribed=False``
    stays enforced in both modes.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.verified_subscribed = User.objects.create_user(
            email="v-sub@test.com", tier=cls.free_tier,
            email_verified=True, unsubscribed=False,
        )
        cls.verified_unsubscribed = User.objects.create_user(
            email="v-unsub@test.com", tier=cls.free_tier,
            email_verified=True, unsubscribed=True,
        )
        cls.unverified_subscribed = User.objects.create_user(
            email="u-sub@test.com", tier=cls.free_tier,
            email_verified=False, unsubscribed=False,
        )
        cls.unverified_unsubscribed = User.objects.create_user(
            email="u-unsub@test.com", tier=cls.free_tier,
            email_verified=False, unsubscribed=True,
        )

    def test_verified_only_returns_only_verified_subscribed(self):
        """Default mode keeps the historical filter."""
        campaign = EmailCampaign.objects.create(
            subject="x", body="y", target_min_level=0,
            audience_verification=(
                EmailCampaign.AUDIENCE_VERIFICATION_VERIFIED_ONLY
            ),
        )
        recipients = list(campaign.get_eligible_recipients())
        emails = {u.email for u in recipients}
        self.assertEqual(emails, {"v-sub@test.com"})

    def test_everyone_includes_unverified_but_excludes_unsubscribed(self):
        """``everyone`` mode drops the verified filter but keeps unsubscribed."""
        campaign = EmailCampaign.objects.create(
            subject="x", body="y", target_min_level=0,
            audience_verification=(
                EmailCampaign.AUDIENCE_VERIFICATION_EVERYONE
            ),
        )
        recipients = list(campaign.get_eligible_recipients())
        emails = {u.email for u in recipients}
        self.assertEqual(
            emails,
            {"v-sub@test.com", "u-sub@test.com"},
        )

    def test_unsubscribed_is_never_in_results_regardless_of_mode(self):
        """Regression guard: unsubscribed=False is the one rule never relaxed."""
        for mode in (
            EmailCampaign.AUDIENCE_VERIFICATION_VERIFIED_ONLY,
            EmailCampaign.AUDIENCE_VERIFICATION_EVERYONE,
        ):
            with self.subTest(mode=mode):
                campaign = EmailCampaign.objects.create(
                    subject=f"x {mode}", body="y", target_min_level=0,
                    audience_verification=mode,
                )
                recipients = list(campaign.get_eligible_recipients())
                emails = {u.email for u in recipients}
                self.assertNotIn("v-unsub@test.com", emails)
                self.assertNotIn("u-unsub@test.com", emails)

    def test_everyone_recipient_count_strictly_greater_than_verified_only(
        self,
    ):
        """Switching to ``everyone`` widens the audience."""
        verified = EmailCampaign.objects.create(
            subject="a", body="y", target_min_level=0,
            audience_verification=(
                EmailCampaign.AUDIENCE_VERIFICATION_VERIFIED_ONLY
            ),
        )
        everyone = EmailCampaign.objects.create(
            subject="b", body="y", target_min_level=0,
            audience_verification=(
                EmailCampaign.AUDIENCE_VERIFICATION_EVERYONE
            ),
        )
        self.assertGreater(
            everyone.get_recipient_count(),
            verified.get_recipient_count(),
        )
