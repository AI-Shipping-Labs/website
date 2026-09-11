"""Tests that campaign audience queries exclude unverified users (issue #452).

The base recipient queryset on ``EmailCampaign`` already filters
``email_verified=True`` (#357), but issue #452 makes that constraint
load-bearing for the new lifecycle: an unverified row that is about to
be purged must not receive marketing in the meantime.

Verification / password-reset / receipt emails go through the
transactional path (``EmailService.send`` directly with a User
instance) and are not affected by this filter.
"""

import datetime

from django.test import TestCase, tag
from django.utils import timezone

from email_app.models import EmailCampaign
from tests.fixtures import TierSetupMixin, create_user_with_membership


@tag("core")
class CampaignExcludesUnverifiedTest(TierSetupMixin, TestCase):
    def test_campaign_audience_excludes_unverified_users(self):
        """Only the verified user makes it into the campaign audience."""
        verified = create_user_with_membership(
            email="verified@example.com",
            tier=self.free_tier,
            email_verified=True,
            unsubscribed=False,
        )
        # Soon-to-expire unverified user.
        create_user_with_membership(
            email="expiring@example.com",
            tier=self.free_tier,
            email_verified=False,
            unsubscribed=False,
            verification_expires_at=timezone.now()
            + datetime.timedelta(hours=12),
        )
        # Already-expired unverified user (would be purged on the next
        # daily run, but might still be in the DB at campaign send time).
        create_user_with_membership(
            email="expired@example.com",
            tier=self.free_tier,
            email_verified=False,
            unsubscribed=False,
            verification_expires_at=timezone.now()
            - datetime.timedelta(hours=12),
        )

        campaign = EmailCampaign.objects.create(
            subject="Hi",
            body="Hello",
            target_min_level=0,
        )

        recipients = list(campaign.get_eligible_recipients())
        self.assertEqual(len(recipients), 1)
        self.assertEqual(recipients[0].pk, verified.pk)
