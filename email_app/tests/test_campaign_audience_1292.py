from datetime import datetime, timedelta
from datetime import timezone as dt_timezone

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from accounts.models import TierOverride
from email_app.models import EmailCampaign
from email_app.services.campaign_audience import campaign_recipient_count
from events.models import Event, EventRegistration
from tests.fixtures import TierSetupMixin

User = get_user_model()


class CampaignAudienceParityTest(TierSetupMixin, TestCase):
    def test_preview_count_matches_send_queryset_across_filters(self):
        User.objects.create_user(
            email="match-1292@example.com", password="pw", email_verified=True,
            slack_member=True, tags=["wanted"]
        )
        User.objects.create_user(
            email="excluded-1292@example.com", password="pw", email_verified=True,
            slack_member=True, tags=["wanted", "blocked"]
        )
        User.objects.create_user(
            email="wrong-slack-1292@example.com", password="pw", email_verified=True,
            slack_member=False, tags=["wanted"]
        )
        User.objects.create_user(
            email="unverified-1292@example.com", password="pw", email_verified=False,
            slack_member=True, tags=["wanted"]
        )
        campaign = EmailCampaign.objects.create(
            subject="Parity", body="Hello", target_min_level=0,
            target_tags_any=["wanted"], target_tags_none=["blocked"],
            slack_filter="yes", audience_verification="verified_only",
        )
        audience = {
            "target_min_level": campaign.target_min_level,
            "target_tags_any": campaign.target_tags_any,
            "target_tags_none": campaign.target_tags_none,
            "slack_filter": campaign.slack_filter,
            "audience_verification": campaign.audience_verification,
            "target_event_id": campaign.target_event_id,
        }
        self.assertEqual(campaign_recipient_count(**audience), 1)
        self.assertEqual(campaign.get_recipient_count(), 1)
        self.assertEqual(
            list(campaign.get_eligible_recipients().values_list("email", flat=True)),
            ["match-1292@example.com"],
        )

    def test_every_audience_dimension_matches_send_queryset(self):
        event = Event.objects.create(
            title="Audience event", slug="audience-event-1292",
            start_datetime=datetime(2026, 7, 18, tzinfo=dt_timezone.utc),
        )
        eligible = User.objects.create_user(
            email="eligible-all-1292@example.com", tier=self.main_tier,
            email_verified=True, slack_member=True, tags=["include"],
        )
        overridden = User.objects.create_user(
            email="override-all-1292@example.com", tier=self.free_tier,
            email_verified=True, slack_member=True, tags=["include"],
        )
        TierOverride.objects.create(
            user=overridden, original_tier=self.free_tier,
            override_tier=self.main_tier,
            expires_at=timezone.now() + timedelta(days=1), is_active=True,
        )
        excluded = User.objects.create_user(
            email="excluded-all-1292@example.com", tier=self.main_tier,
            email_verified=True, slack_member=True, tags=["include", "exclude"],
        )
        unverified = User.objects.create_user(
            email="unverified-all-1292@example.com", tier=self.main_tier,
            email_verified=False, slack_member=True, tags=["include"],
        )
        User.objects.create_user(
            email="unsubscribed-all-1292@example.com", tier=self.main_tier,
            email_verified=True, unsubscribed=True, slack_member=True,
            tags=["include"],
        )
        User.objects.create_user(
            email="wrong-slack-all-1292@example.com", tier=self.main_tier,
            email_verified=True, slack_member=False, tags=["include"],
        )
        for user in (eligible, overridden, excluded, unverified):
            EventRegistration.objects.create(event=event, user=user)

        cases = (
            ({}, 4),
            ({"target_min_level": 20}, 4),
            ({"target_min_level": 20, "target_tags_any": ["include"]}, 4),
            ({"target_min_level": 20, "target_tags_none": ["exclude"]}, 3),
            ({"target_min_level": 20, "slack_filter": "yes"}, 3),
            ({"target_min_level": 20, "audience_verification": "everyone"}, 5),
            ({"target_min_level": 20, "target_event": event}, 3),
        )
        for fields, expected in cases:
            with self.subTest(fields=fields):
                campaign = EmailCampaign.objects.create(
                    subject="Parity dimensions", body="Body", **fields
                )
                audience = {
                    "target_min_level": campaign.target_min_level,
                    "target_tags_any": campaign.target_tags_any,
                    "target_tags_none": campaign.target_tags_none,
                    "slack_filter": campaign.slack_filter,
                    "audience_verification": campaign.audience_verification,
                    "target_event_id": campaign.target_event_id,
                }
                ids = list(campaign.get_eligible_recipients().values_list("pk", flat=True))
                self.assertEqual(len(ids), len(set(ids)))
                self.assertEqual(len(ids), expected)
                self.assertEqual(campaign_recipient_count(**audience), expected)
