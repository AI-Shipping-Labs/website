"""Tests for SES bounce / complaint correlation back to EmailLog (issue #495).

The webhook at ``POST /api/ses-events`` correlates incoming SES bounce and
complaint events to the originating ``EmailLog`` by inner ``mail.messageId``,
persists bounce diagnostics on both the audit row and the email log, and
accepts both legacy ``notificationType`` and configuration-set
``eventType`` payload shapes. These tests pin those guarantees.
"""

import json
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from email_app.models import EmailCampaign, EmailLog, SesEvent

User = get_user_model()


URL = "/api/ses-events"
VALIDATOR_PATH = "api.views.ses_events.validate_sns_notification"


def _bounce_inner(
    *,
    ses_message_id,
    email,
    bounce_type="Permanent",
    bounce_subtype="General",
    diagnostic_code="smtp; 550 5.1.1 user unknown",
    status="5.1.1",
):
    return {
        "notificationType": "Bounce",
        "bounce": {
            "bounceType": bounce_type,
            "bounceSubType": bounce_subtype,
            "timestamp": "2026-05-07T10:00:00.000Z",
            "bouncedRecipients": [{
                "emailAddress": email,
                "diagnosticCode": diagnostic_code,
                "status": status,
                "action": "failed",
            }],
        },
        "mail": {
            "timestamp": "2026-05-07T09:59:59.000Z",
            "source": "noreply@aishippinglabs.com",
            "messageId": ses_message_id,
            "destination": [email],
        },
    }


def _complaint_inner(*, ses_message_id, email, feedback_type="abuse"):
    return {
        "notificationType": "Complaint",
        "complaint": {
            "complainedRecipients": [{"emailAddress": email}],
            "complaintFeedbackType": feedback_type,
            "timestamp": "2026-05-07T10:00:00.000Z",
        },
        "mail": {
            "timestamp": "2026-05-07T09:59:59.000Z",
            "source": "noreply@aishippinglabs.com",
            "messageId": ses_message_id,
            "destination": [email],
        },
    }


def _envelope(message_id, inner):
    return {
        "Type": "Notification",
        "MessageId": message_id,
        "TopicArn": "arn:aws:sns:us-east-1:1:ses-bounces",
        "Message": json.dumps(inner),
        "Timestamp": "2026-05-07T10:00:00.000Z",
        "SignatureVersion": "1",
        "Signature": "stub",
        "SigningCertURL": "https://sns.us-east-1.amazonaws.com/cert.pem",
    }


def _post(client, payload):
    with mock.patch(VALIDATOR_PATH, return_value=True):
        return client.post(
            URL,
            data=json.dumps(payload),
            content_type="application/json",
        )


class BounceCorrelationToEmailLogTest(TestCase):
    """Bounce events should correlate to the EmailLog and stamp diagnostics."""

    def setUp(self):
        self.user = User.objects.create_user(email="bouncer@example.com")
        self.user.unsubscribed = False
        self.user.tags = []
        self.user.save(update_fields=["unsubscribed", "tags"])

    def test_campaign_bounce_links_to_email_log_user_and_campaign(self):
        campaign = EmailCampaign.objects.create(
            subject="Weekly digest", body="hello", status="sending",
        )
        log = EmailLog.objects.create(
            campaign=campaign,
            user=self.user,
            email_type="campaign",
            ses_message_id="ses-camp-1",
        )

        response = _post(
            self.client,
            _envelope(
                "m-bounce-camp-1",
                _bounce_inner(ses_message_id="ses-camp-1", email=self.user.email),
            ),
        )
        self.assertEqual(response.status_code, 200)

        event = SesEvent.objects.get(message_id="m-bounce-camp-1")
        # Direct correlation to the originating EmailLog row.
        self.assertEqual(event.email_log_id, log.pk)
        # And, transitively, to the campaign and recipient user.
        self.assertEqual(event.email_log.campaign_id, campaign.pk)
        self.assertEqual(event.user, self.user)
        self.assertEqual(event.bounce_type, "Permanent")
        self.assertEqual(event.bounce_subtype, "General")
        self.assertIn("550", event.diagnostic_code)

        log.refresh_from_db()
        self.assertIsNotNone(log.bounced_at)
        self.assertEqual(log.bounce_type, "Permanent")
        self.assertEqual(log.bounce_subtype, "General")
        self.assertIn("550", log.bounce_diagnostic)

    def test_verification_bounce_traceable_via_email_type(self):
        log = EmailLog.objects.create(
            user=self.user,
            email_type="email_verification",
            ses_message_id="ses-verify-1",
        )

        _post(
            self.client,
            _envelope(
                "m-bounce-verify-1",
                _bounce_inner(
                    ses_message_id="ses-verify-1",
                    email=self.user.email,
                    bounce_subtype="NoEmail",
                    diagnostic_code="smtp; 550 5.1.1 mailbox unavailable",
                ),
            ),
        )

        event = SesEvent.objects.get(message_id="m-bounce-verify-1")
        self.assertEqual(event.email_log_id, log.pk)
        # Staff can answer "what kind of email bounced?" via email_log.email_type.
        self.assertEqual(event.email_log.email_type, "email_verification")
        self.assertEqual(event.bounce_subtype, "NoEmail")

        log.refresh_from_db()
        self.assertEqual(log.bounce_subtype, "NoEmail")
        self.assertIn("mailbox unavailable", log.bounce_diagnostic)

    def test_verification_reminder_bounce_correlates(self):
        log = EmailLog.objects.create(
            user=self.user,
            email_type="email_verification_reminder",
            ses_message_id="ses-remind-1",
        )

        _post(
            self.client,
            _envelope(
                "m-bounce-remind-1",
                _bounce_inner(
                    ses_message_id="ses-remind-1", email=self.user.email,
                ),
            ),
        )

        event = SesEvent.objects.get(message_id="m-bounce-remind-1")
        self.assertEqual(event.email_log_id, log.pk)
        self.assertEqual(
            event.email_log.email_type, "email_verification_reminder",
        )

    def test_lead_magnet_delivery_bounce_correlates(self):
        log = EmailLog.objects.create(
            user=self.user,
            email_type="lead_magnet_delivery",
            ses_message_id="ses-lead-1",
        )

        _post(
            self.client,
            _envelope(
                "m-bounce-lead-1",
                _bounce_inner(
                    ses_message_id="ses-lead-1", email=self.user.email,
                ),
            ),
        )

        event = SesEvent.objects.get(message_id="m-bounce-lead-1")
        self.assertEqual(event.email_log_id, log.pk)
        self.assertEqual(event.email_log.email_type, "lead_magnet_delivery")

    def test_permanent_bounce_still_unsubscribes_when_correlation_succeeds(self):
        EmailLog.objects.create(
            user=self.user,
            email_type="email_verification",
            ses_message_id="ses-side-1",
        )

        _post(
            self.client,
            _envelope(
                "m-bounce-side-1",
                _bounce_inner(
                    ses_message_id="ses-side-1", email=self.user.email,
                ),
            ),
        )

        self.user.refresh_from_db()
        self.assertTrue(self.user.unsubscribed)
        self.assertIn("bounced", self.user.tags)

    def test_unmatched_ses_message_id_audited_without_link(self):
        """Bounce arrives but no EmailLog matches: log it, don't link, don't crash."""
        response = _post(
            self.client,
            _envelope(
                "m-bounce-orphan-1",
                _bounce_inner(
                    ses_message_id="never-sent-this", email=self.user.email,
                ),
            ),
        )
        self.assertEqual(response.status_code, 200)

        event = SesEvent.objects.get(message_id="m-bounce-orphan-1")
        self.assertIsNone(event.email_log_id)
        # The diagnostic and bounce type are still captured for triage.
        self.assertEqual(event.bounce_type, "Permanent")
        self.assertIn("550", event.diagnostic_code)


class ComplaintCorrelationToEmailLogTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="complainer@example.com")
        self.user.unsubscribed = False
        self.user.tags = []
        self.user.save(update_fields=["unsubscribed", "tags"])

    def test_complaint_links_to_email_log(self):
        log = EmailLog.objects.create(
            user=self.user,
            email_type="campaign",
            ses_message_id="ses-cmp-1",
        )

        _post(
            self.client,
            _envelope(
                "m-cmp-1",
                _complaint_inner(
                    ses_message_id="ses-cmp-1", email=self.user.email,
                ),
            ),
        )

        event = SesEvent.objects.get(message_id="m-cmp-1")
        self.assertEqual(event.email_log_id, log.pk)
        self.assertEqual(event.diagnostic_code, "abuse")

        log.refresh_from_db()
        self.assertIsNotNone(log.complained_at)
        # User-side effects unchanged.
        self.user.refresh_from_db()
        self.assertTrue(self.user.unsubscribed)
        self.assertIn("complained", self.user.tags)

    def test_unmatched_complaint_audited_without_link(self):
        response = _post(
            self.client,
            _envelope(
                "m-cmp-orphan-1",
                _complaint_inner(
                    ses_message_id="never-sent", email=self.user.email,
                ),
            ),
        )
        self.assertEqual(response.status_code, 200)

        event = SesEvent.objects.get(message_id="m-cmp-orphan-1")
        self.assertIsNone(event.email_log_id)


class IdempotencyTest(TestCase):
    """Replayed SNS deliveries must not double-stamp the EmailLog."""

    def setUp(self):
        self.user = User.objects.create_user(email="dup-corr@example.com")
        self.user.unsubscribed = False
        self.user.tags = []
        self.user.save(update_fields=["unsubscribed", "tags"])

    def test_duplicate_bounce_does_not_double_stamp_email_log(self):
        log = EmailLog.objects.create(
            user=self.user,
            email_type="campaign",
            ses_message_id="ses-idem-1",
        )
        envelope = _envelope(
            "m-bounce-idem-1",
            _bounce_inner(
                ses_message_id="ses-idem-1", email=self.user.email,
            ),
        )

        _post(self.client, envelope)
        log.refresh_from_db()
        first_stamp = log.bounced_at
        self.assertIsNotNone(first_stamp)

        # Replay the same MessageId (SNS retry).
        _post(self.client, envelope)

        # Audit row count unchanged.
        self.assertEqual(
            SesEvent.objects.filter(message_id="m-bounce-idem-1").count(), 1,
        )
        # bounced_at frozen at the first observation.
        log.refresh_from_db()
        self.assertEqual(log.bounced_at, first_stamp)
        # Tag applied exactly once.
        self.user.refresh_from_db()
        self.assertEqual(self.user.tags.count("bounced"), 1)

    def test_duplicate_complaint_does_not_double_stamp_email_log(self):
        log = EmailLog.objects.create(
            user=self.user,
            email_type="campaign",
            ses_message_id="ses-cmp-idem-1",
        )
        envelope = _envelope(
            "m-cmp-idem-1",
            _complaint_inner(
                ses_message_id="ses-cmp-idem-1", email=self.user.email,
            ),
        )

        _post(self.client, envelope)
        log.refresh_from_db()
        first_stamp = log.complained_at
        self.assertIsNotNone(first_stamp)

        _post(self.client, envelope)

        self.assertEqual(
            SesEvent.objects.filter(message_id="m-cmp-idem-1").count(), 1,
        )
        log.refresh_from_db()
        self.assertEqual(log.complained_at, first_stamp)
        self.user.refresh_from_db()
        self.assertEqual(self.user.tags.count("complained"), 1)


class EventTypePayloadShapeTest(TestCase):
    """Configuration-set event-publishing uses ``eventType`` instead of
    ``notificationType``. Both shapes must dispatch to the same handlers.
    """

    def setUp(self):
        self.user = User.objects.create_user(email="evtype@example.com")
        self.user.unsubscribed = False
        self.user.tags = []
        self.user.save(update_fields=["unsubscribed", "tags"])

    def test_event_type_bounce_payload_dispatches_and_correlates(self):
        log = EmailLog.objects.create(
            user=self.user,
            email_type="campaign",
            ses_message_id="ses-evtype-1",
        )
        inner = _bounce_inner(
            ses_message_id="ses-evtype-1", email=self.user.email,
        )
        # Strip the legacy field and add the event-publishing one.
        inner.pop("notificationType")
        inner["eventType"] = "Bounce"

        response = _post(
            self.client, _envelope("m-evtype-bounce-1", inner),
        )
        self.assertEqual(response.status_code, 200)

        event = SesEvent.objects.get(message_id="m-evtype-bounce-1")
        self.assertEqual(event.event_type, SesEvent.EVENT_TYPE_BOUNCE_PERMANENT)
        self.assertEqual(event.email_log_id, log.pk)

        self.user.refresh_from_db()
        self.assertTrue(self.user.unsubscribed)

    def test_event_type_complaint_payload_dispatches_and_correlates(self):
        log = EmailLog.objects.create(
            user=self.user,
            email_type="campaign",
            ses_message_id="ses-evtype-cmp",
        )
        inner = _complaint_inner(
            ses_message_id="ses-evtype-cmp", email=self.user.email,
        )
        inner.pop("notificationType")
        inner["eventType"] = "Complaint"

        response = _post(
            self.client, _envelope("m-evtype-cmp-1", inner),
        )
        self.assertEqual(response.status_code, 200)

        event = SesEvent.objects.get(message_id="m-evtype-cmp-1")
        self.assertEqual(event.event_type, SesEvent.EVENT_TYPE_COMPLAINT)
        self.assertEqual(event.email_log_id, log.pk)


class NoEmailDiagnosticTest(TestCase):
    """`Permanent` / `NoEmail` (non-existent mailbox) is the canonical
    bounce sub-type for "user does not exist", per the spec.
    """

    def test_no_email_subtype_is_persisted_with_diagnostic(self):
        user = User.objects.create_user(email="ghost@example.com")
        EmailLog.objects.create(
            user=user,
            email_type="email_verification",
            ses_message_id="ses-noemail-1",
        )

        _post(
            self.client,
            _envelope(
                "m-noemail-1",
                _bounce_inner(
                    ses_message_id="ses-noemail-1",
                    email=user.email,
                    bounce_subtype="NoEmail",
                    diagnostic_code=(
                        "smtp; 550 5.1.1 The email account that you "
                        "tried to reach does not exist."
                    ),
                ),
            ),
        )

        event = SesEvent.objects.get(message_id="m-noemail-1")
        self.assertEqual(event.bounce_type, "Permanent")
        self.assertEqual(event.bounce_subtype, "NoEmail")
        self.assertIn("does not exist", event.diagnostic_code)


class AdminVisibilityTest(TestCase):
    """The Django admin changelist must let staff search and filter events
    by recipient email, SES message id, user email, email_type/campaign,
    and bounce classification.
    """

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="admin@example.com", password="adminpass",
        )
        cls.staff.is_staff = True
        cls.staff.is_superuser = True
        cls.staff.save(update_fields=["is_staff", "is_superuser"])

    def setUp(self):
        self.client.force_login(self.staff)

    def test_changelist_includes_bounce_classification(self):
        user = User.objects.create_user(email="adminview@example.com")
        campaign = EmailCampaign.objects.create(
            subject="Spring digest", body="hi", status="sent",
        )
        log = EmailLog.objects.create(
            campaign=campaign,
            user=user,
            email_type="campaign",
            ses_message_id="ses-adm-1",
        )
        SesEvent.objects.create(
            message_id="m-adm-1",
            event_type=SesEvent.EVENT_TYPE_BOUNCE_PERMANENT,
            raw_payload={"stub": True},
            recipient_email=user.email,
            user=user,
            email_log=log,
            bounce_type="Permanent",
            bounce_subtype="NoEmail",
            diagnostic_code="smtp; 550 5.1.1 user unknown",
            action_taken="unsubscribed and tagged bounced",
        )

        url = reverse("admin:email_app_sesevent_changelist")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        # List columns expose bounce classification + correlated email_type.
        self.assertContains(response, "Permanent")
        self.assertContains(response, "NoEmail")
        self.assertContains(response, "campaign")
        # Recipient is shown.
        self.assertContains(response, "adminview@example.com")

    def test_changelist_search_by_recipient_email(self):
        user = User.objects.create_user(email="findme@example.com")
        SesEvent.objects.create(
            message_id="m-search-1",
            event_type=SesEvent.EVENT_TYPE_BOUNCE_PERMANENT,
            raw_payload={"stub": True},
            recipient_email=user.email,
            user=user,
            bounce_type="Permanent",
            bounce_subtype="General",
            diagnostic_code="",
            action_taken="",
        )
        SesEvent.objects.create(
            message_id="m-search-2",
            event_type=SesEvent.EVENT_TYPE_BOUNCE_PERMANENT,
            raw_payload={"stub": True},
            recipient_email="other@example.com",
            user=None,
            bounce_type="Permanent",
            bounce_subtype="General",
            diagnostic_code="",
            action_taken="",
        )

        url = reverse("admin:email_app_sesevent_changelist")
        response = self.client.get(url, {"q": "findme@example.com"})
        self.assertEqual(response.status_code, 200)
        # Search returns the matching event by recipient_email and not the other.
        # Inspect cl.queryset directly so we don't depend on which columns
        # the changelist HTML renders.
        cl = response.context["cl"]
        ids = list(cl.queryset.values_list("message_id", flat=True))
        self.assertIn("m-search-1", ids)
        self.assertNotIn("m-search-2", ids)

    def test_changelist_search_by_email_type(self):
        user = User.objects.create_user(email="byemailtype@example.com")
        log = EmailLog.objects.create(
            user=user,
            email_type="email_verification_reminder",
            ses_message_id="ses-byet-1",
        )
        SesEvent.objects.create(
            message_id="m-byet-1",
            event_type=SesEvent.EVENT_TYPE_BOUNCE_PERMANENT,
            raw_payload={"stub": True},
            recipient_email=user.email,
            user=user,
            email_log=log,
            bounce_type="Permanent",
            bounce_subtype="General",
            diagnostic_code="",
            action_taken="",
        )
        SesEvent.objects.create(
            message_id="m-byet-2",
            event_type=SesEvent.EVENT_TYPE_BOUNCE_PERMANENT,
            raw_payload={"stub": True},
            recipient_email="zzz@example.com",
            user=None,
            email_log=None,
            bounce_type="Permanent",
            bounce_subtype="General",
            diagnostic_code="",
            action_taken="",
        )

        url = reverse("admin:email_app_sesevent_changelist")
        response = self.client.get(url, {"q": "email_verification_reminder"})
        self.assertEqual(response.status_code, 200)
        cl = response.context["cl"]
        ids = list(cl.queryset.values_list("message_id", flat=True))
        self.assertIn("m-byet-1", ids)
        self.assertNotIn("m-byet-2", ids)
