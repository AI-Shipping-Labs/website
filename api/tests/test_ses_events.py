"""Tests for the SES bounce / complaint webhook (issue #453).

The view at ``POST /api/ses-events`` expects SNS-formatted JSON. We patch
``validate_sns_notification`` so the tests don't have to forge real signatures
and ``urllib.request.urlopen`` so we can assert the SubscribeURL is fetched
without making a real HTTP call.
"""

import json
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils.dateparse import parse_datetime

from email_app.models import EmailLog, SesEvent

User = get_user_model()


URL = "/api/ses-events"
VALIDATOR_PATH = "api.views.ses_events.validate_sns_notification"
URLOPEN_PATH = "api.views.ses_events.urllib.request.urlopen"


def _bounce_payload(message_id, email, bounce_type="Permanent"):
    """Build a fully-formed SNS Notification + SES Bounce envelope."""
    inner = {
        "notificationType": "Bounce",
        "bounce": {
            "bounceType": bounce_type,
            "bounceSubType": "General",
            "bouncedRecipients": [{"emailAddress": email}],
        },
        "mail": {
            "source": "noreply@aishippinglabs.com",
            "destination": [email],
        },
    }
    return {
        "Type": "Notification",
        "MessageId": message_id,
        "TopicArn": "arn:aws:sns:us-east-1:1:ses-bounces",
        "Subject": "Amazon SES Email Event Notification",
        "Message": json.dumps(inner),
        "Timestamp": "2026-05-06T00:00:00.000Z",
        "SignatureVersion": "1",
        "Signature": "stub",
        "SigningCertURL": "https://sns.us-east-1.amazonaws.com/cert.pem",
    }


def _complaint_payload(message_id, email):
    inner = {
        "notificationType": "Complaint",
        "complaint": {
            "complainedRecipients": [{"emailAddress": email}],
            "complaintFeedbackType": "abuse",
        },
        "mail": {
            "source": "noreply@aishippinglabs.com",
            "destination": [email],
        },
    }
    return {
        "Type": "Notification",
        "MessageId": message_id,
        "TopicArn": "arn:aws:sns:us-east-1:1:ses-bounces",
        "Message": json.dumps(inner),
        "Timestamp": "2026-05-06T00:00:00.000Z",
        "SignatureVersion": "1",
        "Signature": "stub",
        "SigningCertURL": "https://sns.us-east-1.amazonaws.com/cert.pem",
    }


def _delivery_payload(message_id, email):
    inner = {
        "notificationType": "Delivery",
        "delivery": {"recipients": [email]},
        "mail": {
            "source": "noreply@aishippinglabs.com",
            "destination": [email],
        },
    }
    return {
        "Type": "Notification",
        "MessageId": message_id,
        "TopicArn": "arn:aws:sns:us-east-1:1:ses-bounces",
        "Message": json.dumps(inner),
        "Timestamp": "2026-05-06T00:00:00.000Z",
        "SignatureVersion": "1",
        "Signature": "stub",
        "SigningCertURL": "https://sns.us-east-1.amazonaws.com/cert.pem",
    }


def _engagement_payload(message_id, email, ses_message_id, notification_type, timestamp):
    detail_key = notification_type.lower()
    inner = {
        "notificationType": notification_type,
        detail_key: {"timestamp": timestamp},
        "mail": {
            "timestamp": "2026-05-06T00:00:00.000Z",
            "source": "noreply@aishippinglabs.com",
            "messageId": ses_message_id,
            "destination": [email],
        },
    }
    if notification_type == "Click":
        inner[detail_key]["link"] = "https://example.test/article"
    return {
        "Type": "Notification",
        "MessageId": message_id,
        "TopicArn": "arn:aws:sns:us-east-1:1:ses-engagement",
        "Message": json.dumps(inner),
        "Timestamp": timestamp,
        "SignatureVersion": "1",
        "Signature": "stub",
        "SigningCertURL": "https://sns.us-east-1.amazonaws.com/cert.pem",
    }


def _subscription_confirmation(message_id, subscribe_url):
    return {
        "Type": "SubscriptionConfirmation",
        "MessageId": message_id,
        "Token": "stubtoken",
        "TopicArn": "arn:aws:sns:us-east-1:1:ses-bounces",
        "Message": "You have chosen to subscribe to the topic.",
        "SubscribeURL": subscribe_url,
        "Timestamp": "2026-05-06T00:00:00.000Z",
        "SignatureVersion": "1",
        "Signature": "stub",
        "SigningCertURL": "https://sns.us-east-1.amazonaws.com/cert.pem",
    }


class SesEventsSignatureTest(TestCase):
    """Verify the SNS signature is the auth gate."""

    def test_invalid_sns_signature_returns_403(self):
        users_before = User.objects.count()
        events_before = SesEvent.objects.count()
        with mock.patch(VALIDATOR_PATH, return_value=False):
            response = self.client.post(
                URL,
                data=json.dumps(_bounce_payload("m-bad", "x@example.com")),
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 403)
        # Rule 12: rejected requests have no side-effects.
        self.assertEqual(User.objects.count(), users_before)
        self.assertEqual(SesEvent.objects.count(), events_before)


class SesEventsSubscriptionConfirmationTest(TestCase):
    def test_subscription_confirmation_fetches_subscribe_url(self):
        subscribe_url = (
            "https://sns.us-east-1.amazonaws.com/?Action=ConfirmSubscription"
            "&TopicArn=arn:aws:sns:us-east-1:1:ses-bounces&Token=stubtoken"
        )
        payload = _subscription_confirmation("m-confirm-1", subscribe_url)

        urlopen_cm = mock.MagicMock()
        urlopen_cm.__enter__.return_value.read.return_value = b"<ok/>"
        urlopen_cm.__exit__.return_value = False

        with mock.patch(VALIDATOR_PATH, return_value=True), mock.patch(
            URLOPEN_PATH, return_value=urlopen_cm,
        ) as urlopen_mock:
            response = self.client.post(
                URL,
                data=json.dumps(payload),
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 200)
        urlopen_mock.assert_called_once()
        # First positional argument is the URL we POST'd as SubscribeURL.
        args, _kwargs = urlopen_mock.call_args
        self.assertEqual(args[0], subscribe_url)

        event = SesEvent.objects.get(message_id="m-confirm-1")
        self.assertEqual(
            event.event_type, SesEvent.EVENT_TYPE_SUBSCRIPTION_CONFIRMATION,
        )
        self.assertEqual(event.action_taken, "subscribe_url_fetched")


class SesEventsBounceTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user_email = "bounced@example.com"

    def setUp(self):
        # Each test gets a fresh user row -- bounce flips ``unsubscribed``
        # which would leak across tests if shared via setUpTestData.
        self.user = User.objects.create_user(
            email=self.user_email,
        )
        self.user.unsubscribed = False
        self.user.tags = []
        self.user.soft_bounce_count = 0
        self.user.save(
            update_fields=["unsubscribed", "tags", "soft_bounce_count"],
        )

    def _post(self, payload):
        return self.client.post(
            URL,
            data=json.dumps(payload),
            content_type="application/json",
        )

    def test_permanent_bounce_unsubscribes_user_and_tags(self):
        with mock.patch(VALIDATOR_PATH, return_value=True):
            response = self._post(_bounce_payload("m-perm-1", self.user_email))
        self.assertEqual(response.status_code, 200)

        self.user.refresh_from_db()
        self.assertTrue(self.user.unsubscribed)
        self.assertIn("bounced", self.user.tags)

        event = SesEvent.objects.get(message_id="m-perm-1")
        self.assertEqual(event.event_type, SesEvent.EVENT_TYPE_BOUNCE_PERMANENT)
        self.assertEqual(event.user, self.user)

    def test_permanent_bounce_unknown_email_logs_only(self):
        users_before = User.objects.count()
        payload = _bounce_payload("m-perm-2", "nobody@nowhere.example")
        with mock.patch(VALIDATOR_PATH, return_value=True):
            response = self._post(payload)
        self.assertEqual(response.status_code, 200)

        self.assertEqual(User.objects.count(), users_before)
        event = SesEvent.objects.get(message_id="m-perm-2")
        self.assertIsNone(event.user)
        self.assertEqual(event.event_type, SesEvent.EVENT_TYPE_BOUNCE_PERMANENT)

    def test_transient_bounce_increments_counter(self):
        with mock.patch(VALIDATOR_PATH, return_value=True):
            response = self._post(
                _bounce_payload("m-soft-1", self.user_email, bounce_type="Transient"),
            )
        self.assertEqual(response.status_code, 200)

        self.user.refresh_from_db()
        self.assertEqual(self.user.soft_bounce_count, 1)
        self.assertFalse(self.user.unsubscribed)
        self.assertNotIn("bounced", self.user.tags)

    def test_three_transient_bounces_unsubscribe(self):
        with mock.patch(VALIDATOR_PATH, return_value=True):
            self._post(
                _bounce_payload("m-soft-a", self.user_email, bounce_type="Transient"),
            )
            self._post(
                _bounce_payload("m-soft-b", self.user_email, bounce_type="Transient"),
            )
            self.user.refresh_from_db()
            # After two soft bounces: counter is 2, still subscribed.
            self.assertEqual(self.user.soft_bounce_count, 2)
            self.assertFalse(self.user.unsubscribed)

            response = self._post(
                _bounce_payload("m-soft-c", self.user_email, bounce_type="Transient"),
            )

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        # Threshold reached -> flipped + counter reset.
        self.assertEqual(self.user.soft_bounce_count, 0)
        self.assertTrue(self.user.unsubscribed)
        self.assertIn("bounced", self.user.tags)


class SesEventsComplaintTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="angry@example.com")
        self.user.unsubscribed = False
        self.user.tags = []
        self.user.save(update_fields=["unsubscribed", "tags"])

    def test_complaint_unsubscribes_and_tags(self):
        with mock.patch(VALIDATOR_PATH, return_value=True):
            response = self.client.post(
                URL,
                data=json.dumps(_complaint_payload("m-cmp-1", self.user.email)),
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 200)

        self.user.refresh_from_db()
        self.assertTrue(self.user.unsubscribed)
        self.assertIn("complained", self.user.tags)

        event = SesEvent.objects.get(message_id="m-cmp-1")
        self.assertEqual(event.event_type, SesEvent.EVENT_TYPE_COMPLAINT)
        self.assertEqual(event.user, self.user)


class SesEventsDeliveryTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="happy@example.com")
        self.user.unsubscribed = False
        self.user.tags = []
        self.user.soft_bounce_count = 0
        self.user.save(
            update_fields=["unsubscribed", "tags", "soft_bounce_count"],
        )

    def test_delivery_notification_no_op(self):
        with mock.patch(VALIDATOR_PATH, return_value=True):
            response = self.client.post(
                URL,
                data=json.dumps(_delivery_payload("m-del-1", self.user.email)),
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 200)

        self.user.refresh_from_db()
        self.assertFalse(self.user.unsubscribed)
        self.assertEqual(self.user.soft_bounce_count, 0)
        self.assertEqual(self.user.tags, [])

        # Audit row IS created so operators can see deliveries went through.
        self.assertEqual(
            SesEvent.objects.filter(message_id="m-del-1").count(), 1,
        )


class SesEventsEngagementTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="engaged@example.com")
        self.log = EmailLog.objects.create(
            user=self.user,
            email_type="campaign",
            ses_message_id="ses-eng-1",
        )

    def _post(self, payload):
        with mock.patch(VALIDATOR_PATH, return_value=True):
            return self.client.post(
                URL,
                data=json.dumps(payload),
                content_type="application/json",
            )

    def test_open_event_sets_opened_at_and_increments_opens(self):
        timestamp = "2026-05-06T10:00:00.000Z"
        response = self._post(
            _engagement_payload(
                "m-open-1", self.user.email, "ses-eng-1", "Open", timestamp,
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.log.refresh_from_db()
        self.assertEqual(self.log.opened_at, parse_datetime(timestamp))
        self.assertEqual(self.log.opens, 1)

    def test_repeat_open_increments_count_but_keeps_first_timestamp(self):
        first = "2026-05-06T10:00:00.000Z"
        second = "2026-05-06T11:00:00.000Z"

        self._post(_engagement_payload(
            "m-open-a", self.user.email, "ses-eng-1", "Open", first,
        ))
        self._post(_engagement_payload(
            "m-open-b", self.user.email, "ses-eng-1", "Open", second,
        ))

        self.log.refresh_from_db()
        self.assertEqual(self.log.opened_at, parse_datetime(first))
        self.assertEqual(self.log.opens, 2)

    def test_click_event_sets_clicked_at_and_clicks(self):
        timestamp = "2026-05-06T12:00:00.000Z"
        response = self._post(
            _engagement_payload(
                "m-click-1", self.user.email, "ses-eng-1", "Click", timestamp,
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.log.refresh_from_db()
        self.assertEqual(self.log.clicked_at, parse_datetime(timestamp))
        self.assertEqual(self.log.clicks, 1)

    def test_click_event_sets_opened_at_when_open_was_missed(self):
        timestamp = "2026-05-06T12:00:00.000Z"
        self._post(
            _engagement_payload(
                "m-click-2", self.user.email, "ses-eng-1", "Click", timestamp,
            ),
        )

        self.log.refresh_from_db()
        self.assertEqual(self.log.opened_at, parse_datetime(timestamp))
        self.assertEqual(self.log.clicked_at, parse_datetime(timestamp))
        self.assertEqual(self.log.opens, 0)
        self.assertEqual(self.log.clicks, 1)

    def test_unknown_ses_message_id_logged_no_error(self):
        response = self._post(
            _engagement_payload(
                "m-open-ghost", self.user.email, "ghost", "Open",
                "2026-05-06T10:00:00.000Z",
            ),
        )

        self.assertEqual(response.status_code, 200)
        event = SesEvent.objects.get(message_id="m-open-ghost")
        self.assertEqual(event.event_type, SesEvent.EVENT_TYPE_OPEN)
        self.assertIsNone(event.user)

    def test_duplicate_message_id_idempotent_for_open(self):
        payload = _engagement_payload(
            "m-open-dup", self.user.email, "ses-eng-1", "Open",
            "2026-05-06T10:00:00.000Z",
        )

        r1 = self._post(payload)
        r2 = self._post(payload)

        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r2.status_code, 200)
        self.log.refresh_from_db()
        self.assertEqual(self.log.opens, 1)
        self.assertEqual(
            SesEvent.objects.filter(message_id="m-open-dup").count(), 1,
        )

    def test_duplicate_message_id_idempotent_for_click(self):
        payload = _engagement_payload(
            "m-click-dup", self.user.email, "ses-eng-1", "Click",
            "2026-05-06T10:00:00.000Z",
        )

        r1 = self._post(payload)
        r2 = self._post(payload)

        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r2.status_code, 200)
        self.log.refresh_from_db()
        self.assertEqual(self.log.clicks, 1)
        self.assertEqual(
            SesEvent.objects.filter(message_id="m-click-dup").count(), 1,
        )


class SesEventsIdempotencyTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="dup@example.com")
        self.user.unsubscribed = False
        self.user.tags = []
        self.user.save(update_fields=["unsubscribed", "tags"])

    def test_duplicate_message_id_ignored(self):
        payload = _bounce_payload("m-dup-1", self.user.email)
        body = json.dumps(payload)
        with mock.patch(VALIDATOR_PATH, return_value=True):
            r1 = self.client.post(URL, data=body, content_type="application/json")
            r2 = self.client.post(URL, data=body, content_type="application/json")

        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r2.status_code, 200)

        self.user.refresh_from_db()
        self.assertTrue(self.user.unsubscribed)
        # Tag appended exactly once even after the retry.
        self.assertEqual(self.user.tags.count("bounced"), 1)

        # And only one audit row landed.
        self.assertEqual(
            SesEvent.objects.filter(message_id="m-dup-1").count(), 1,
        )


class SesEventsCsrfExemptionTest(TestCase):
    """The endpoint must accept POSTs without a CSRF token (SNS has none)."""

    def test_csrf_exempt(self):
        user = User.objects.create_user(email="csrf-target@example.com")
        # Use enforce_csrf_checks=True to make the test client behave like
        # a real cross-origin POST. Without csrf_exempt this would 403.
        client = self.client_class(enforce_csrf_checks=True)
        with mock.patch(VALIDATOR_PATH, return_value=True):
            response = client.post(
                URL,
                data=json.dumps(_bounce_payload("m-csrf-1", user.email)),
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 200)


class SesEventsAuditTest(TestCase):
    def test_event_audit_row_preserves_raw_payload(self):
        payload = _bounce_payload("m-audit-1", "audit@example.com")
        with mock.patch(VALIDATOR_PATH, return_value=True):
            response = self.client.post(
                URL,
                data=json.dumps(payload),
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 200)

        events = SesEvent.objects.filter(message_id="m-audit-1")
        self.assertEqual(events.count(), 1)
        event = events.get()
        # Raw payload preserved verbatim (the JSONField stores parsed form).
        self.assertEqual(event.raw_payload, payload)
        self.assertEqual(event.recipient_email, "audit@example.com")
