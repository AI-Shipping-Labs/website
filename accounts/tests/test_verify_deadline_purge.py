"""Verify-or-delete purge: only notified, still-unverified, unengaged accounts go."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.utils import timezone

from accounts.models import DeletedUserArchive
from accounts.services.verify_deadline_purge import (
    VerifyDeadlineError,
    purge_unverified_after_deadline,
    unverified_audit,
)
from content.models import Course, Enrollment
from email_app.models import CampaignDelivery, EmailCampaign, EmailLog
from events.models import Event, EventRegistration
from tests.fixtures import TierSetupMixin, create_user_with_membership

User = get_user_model()


class PurgeBase(TierSetupMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.campaign = EmailCampaign.objects.create(
            subject="Confirm",
            body="Body",
            audience_verification="unverified_only",
            verify_deadline_days=7,
            status="sent",
        )

    def recipient(self, name, *, sent_days_ago=8, log=True, **fields):
        values = {
            "email": f"{name}@example.com",
            "tier": self.free_tier,
            "email_verified": False,
        }
        values.update(fields)
        user = create_user_with_membership(**values)
        sent_at = timezone.now() - timedelta(days=sent_days_ago)
        email_log = (
            EmailLog.objects.create(
                campaign=self.campaign,
                user=user,
                recipient_email=user.email,
                email_type="campaign_verify_deadline",
            )
            if log else None
        )
        CampaignDelivery.objects.create(
            campaign=self.campaign,
            user=user,
            recipient_user_pk=user.pk,
            recipient_email=user.email,
            state=CampaignDelivery.State.SENT,
            email_log=email_log,
            completed_at=sent_at,
        )
        return user

    def run_purge(self, **kwargs):
        kwargs.setdefault("dry_run", False)
        return purge_unverified_after_deadline(self.campaign.pk, **kwargs)

    def exists(self, user):
        return User.objects.filter(pk=user.pk).exists()


@tag("core")
class PurgeDecisionTest(PurgeBase):
    def test_unverified_recipient_past_deadline_is_archived_then_deleted(self):
        target = self.recipient("gone")
        event = Event.objects.create(
            title="Workshop", slug="workshop", start_datetime=timezone.now(),
        )
        EventRegistration.objects.create(event=event, user=target)

        result = self.run_purge()

        self.assertEqual(result["deleted"], 1)
        self.assertFalse(self.exists(target))
        archive = DeletedUserArchive.objects.get(original_user_id=target.pk)
        self.assertEqual(archive.email, "gone@example.com")
        self.assertEqual(archive.campaign_id, self.campaign.pk)
        self.assertEqual(archive.snapshot["event_registration_event_ids"], [event.pk])
        self.assertNotIn("password", archive.snapshot["fields"])

    def test_user_who_verified_before_the_run_is_untouched(self):
        verified = self.recipient("verified-late")
        User.objects.filter(pk=verified.pk).update(email_verified=True)

        result = self.run_purge()

        self.assertEqual(result["deleted"], 0)
        self.assertEqual(result["reasons"], {"verified": 1})
        self.assertTrue(self.exists(verified))
        self.assertFalse(DeletedUserArchive.objects.exists())

    def test_nothing_is_deleted_before_seven_days_have_passed(self):
        recent = self.recipient("recent", sent_days_ago=6)

        result = self.run_purge()

        self.assertEqual(result["due_deliveries"], 0)
        self.assertTrue(self.exists(recent))

    def test_protected_accounts_survive_and_are_counted_by_reason(self):
        protected = {
            "logged_in": self.recipient("logged", last_login=timezone.now()),
            "staff": self.recipient("staff", is_staff=True),
            "unsubscribed": self.recipient("unsub", unsubscribed=True),
            "inactive": self.recipient("inactive", is_active=False),
            "permanent_bounce": self.recipient(
                "bounced", bounce_state=User.BounceState.PERMANENT,
            ),
            "bounced_or_complained": self.recipient("nolog", log=False),
            "paid_tier": self.recipient("paid", tier=self.basic_tier),
        }
        stripe_user = self.recipient("stripe")
        stripe_user.membership.stripe_customer_id = "cus_123"
        stripe_user.membership.save()
        protected["stripe"] = stripe_user

        result = self.run_purge()

        self.assertEqual(result["deleted"], 0)
        self.assertEqual(
            result["reasons"], {reason: 1 for reason in protected},
        )
        for user in protected.values():
            self.assertTrue(self.exists(user))

    def test_account_with_real_activity_is_blocked_by_its_relation(self):
        enrolled = self.recipient("enrolled")
        course = Course.objects.create(title="C", slug="c", status="published")
        Enrollment.objects.create(user=enrolled, course=course)

        result = self.run_purge()

        self.assertEqual(result["deleted"], 0)
        self.assertTrue(self.exists(enrolled))
        self.assertTrue(
            any(key.startswith("blocked:") for key in result["reasons"]),
        )

    def test_dry_run_reports_deletable_count_and_writes_nothing(self):
        self.recipient("dry-a")
        self.recipient("dry-b")

        result = self.run_purge(dry_run=True)

        self.assertEqual(result["reasons"], {"deletable": 2})
        self.assertEqual(result["deleted"], 0)
        self.assertEqual(User.objects.filter(email__startswith="dry-").count(), 2)
        self.assertFalse(DeletedUserArchive.objects.exists())

    def test_limit_and_cursor_page_through_all_recipients(self):
        for index in range(5):
            self.recipient(f"page-{index}")

        first = self.run_purge(limit=3)
        second = self.run_purge(limit=3, after=first["last_delivery_id"])

        self.assertEqual((first["deleted"], first["complete"]), (3, False))
        self.assertEqual((second["deleted"], second["complete"]), (2, True))

    def test_plain_campaign_cannot_be_purged(self):
        plain = EmailCampaign.objects.create(
            subject="x", body="y", audience_verification="unverified_only",
        )

        with self.assertRaises(VerifyDeadlineError):
            purge_unverified_after_deadline(plain.pk)


@tag("core")
class UnverifiedAuditTest(PurgeBase):
    def test_counts_by_signup_age_and_segment(self):
        old = self.recipient("old")
        User.objects.filter(pk=old.pk).update(
            date_joined=timezone.now() - timedelta(days=400),
        )
        self.recipient("new", unsubscribed=True)
        create_user_with_membership(
            email="ok@example.com", tier=self.free_tier, email_verified=True,
        )

        audit = unverified_audit()

        self.assertEqual(audit["unverified"]["total"], 2)
        self.assertEqual(audit["unverified"]["emailable"], 1)
        self.assertEqual(audit["unverified"]["unsubscribed"], 1)
        self.assertEqual(audit["by_signup_age"]["1_to_2_years"]["total"], 1)
        self.assertEqual(audit["by_signup_age"]["under_30_days"]["total"], 1)
        self.assertEqual(audit["verified_total"], 1)
