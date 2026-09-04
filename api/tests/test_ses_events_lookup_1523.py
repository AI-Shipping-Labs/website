"""SES EmailLog lookup contract around ses_message_id (issue #1523)."""

import json
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils.dateparse import parse_datetime

from api.tests.test_ses_events import (
    URL,
    VALIDATOR_PATH,
    _delivery_payload,
    _engagement_payload,
)
from api.tests.test_ses_events_correlation import (
    _bounce_inner,
    _complaint_inner,
    _envelope,
    _post,
)
from api.views import ses_events as ses_events_views
from api.views.ses_events import _find_email_log
from email_app.models import EmailLog, SesEvent

User = get_user_model()


def _delivery_payload_with_ses_id(message_id, email, ses_message_id):
    payload = _delivery_payload(message_id, email)
    inner = json.loads(payload["Message"])
    inner["mail"]["messageId"] = ses_message_id
    payload["Message"] = json.dumps(inner)
    return payload


class FindEmailLogHelperTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(email="ses-lookup@example.com")
        cls.blank_a = EmailLog.objects.create(
            user=cls.user,
            email_type="welcome",
            ses_message_id="",
        )
        cls.blank_b = EmailLog.objects.create(
            user=cls.user,
            email_type="payment_failed",
            ses_message_id="",
        )
        cls.real = EmailLog.objects.create(
            user=cls.user,
            email_type="campaign",
            ses_message_id="ses-lookup-1",
        )

    def test_blank_or_missing_id_returns_none_without_matching_empty_rows(self):
        self.assertIsNone(_find_email_log(None))
        self.assertIsNone(_find_email_log(""))
        self.assertIsNone(_find_email_log("   ".strip()))
        self.assertGreaterEqual(
            EmailLog.objects.filter(ses_message_id="").count(),
            2,
        )

    def test_exact_ses_message_id_equality(self):
        self.assertEqual(_find_email_log("ses-lookup-1").pk, self.real.pk)
        self.assertIsNone(_find_email_log("ses-lookup-1-extra"))
        self.assertIsNone(_find_email_log("SES-LOOKUP-1"))


class BlankInboundSesIdTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="blank-ses@example.com")
        self.user.unsubscribed = False
        self.user.save(update_fields=["unsubscribed"])
        self.blank_log = EmailLog.objects.create(
            user=self.user,
            email_type="welcome",
            ses_message_id="",
        )
        EmailLog.objects.create(
            user=self.user,
            email_type="cancellation",
            ses_message_id="",
        )
        self.real_log = EmailLog.objects.create(
            user=self.user,
            email_type="campaign",
            ses_message_id="ses-real-blank-test",
        )

    def test_missing_mail_message_id_does_not_attach_blank_logs(self):
        inner = _bounce_inner(
            ses_message_id="ses-real-blank-test",
            email=self.user.email,
        )
        inner["mail"].pop("messageId")
        response = _post(self.client, _envelope("m-bounce-missing-msgid", inner))
        self.assertEqual(response.json(), {"status": "ok"})

        event = SesEvent.objects.get(message_id="m-bounce-missing-msgid")
        self.assertIsNone(event.email_log_id)
        self.blank_log.refresh_from_db()
        self.real_log.refresh_from_db()
        self.assertIsNone(self.blank_log.bounced_at)
        self.assertIsNone(self.real_log.bounced_at)

    def test_whitespace_only_mail_message_id_does_not_attach_blank_logs(self):
        inner = _bounce_inner(
            ses_message_id="   ",
            email=self.user.email,
        )
        response = _post(self.client, _envelope("m-bounce-ws-msgid", inner))
        self.assertEqual(response.json(), {"status": "ok"})

        event = SesEvent.objects.get(message_id="m-bounce-ws-msgid")
        self.assertIsNone(event.email_log_id)
        self.blank_log.refresh_from_db()
        self.assertIsNone(self.blank_log.bounced_at)


class KnownSesIdCorrelationTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="known-ses@example.com")
        self.log = EmailLog.objects.create(
            user=self.user,
            email_type="campaign",
            ses_message_id="ses-camp-1",
        )

    def test_known_ses_id_correlates_bounce_to_email_log(self):
        response = _post(
            self.client,
            _envelope(
                "m-bounce-known-1523",
                _bounce_inner(
                    ses_message_id="ses-camp-1",
                    email=self.user.email,
                ),
            ),
        )
        self.assertEqual(response.json(), {"status": "ok"})
        event = SesEvent.objects.get(message_id="m-bounce-known-1523")
        self.assertEqual(event.email_log_id, self.log.pk)
        self.log.refresh_from_db()
        self.assertIsNotNone(self.log.bounced_at)

    def test_open_then_click_update_matching_log(self):
        open_at = "2026-05-06T10:00:00.000Z"
        click_at = "2026-05-06T12:00:00.000Z"
        with mock.patch(VALIDATOR_PATH, return_value=True):
            open_response = self.client.post(
                URL,
                data=json.dumps(
                    _engagement_payload(
                        "m-open-1523",
                        self.user.email,
                        "ses-camp-1",
                        "Open",
                        open_at,
                    ),
                ),
                content_type="application/json",
            )
            click_response = self.client.post(
                URL,
                data=json.dumps(
                    _engagement_payload(
                        "m-click-1523",
                        self.user.email,
                        "ses-camp-1",
                        "Click",
                        click_at,
                    ),
                ),
                content_type="application/json",
            )
        self.assertEqual(open_response.json(), {"status": "ok"})
        self.assertEqual(click_response.json(), {"status": "ok"})
        self.log.refresh_from_db()
        self.assertEqual(self.log.opened_at, parse_datetime(open_at))
        self.assertEqual(self.log.opens, 1)
        self.assertEqual(self.log.clicked_at, parse_datetime(click_at))
        self.assertEqual(self.log.clicks, 1)


class DeliveryDoesNotLookupEmailLogTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(email="delivery-ses@example.com")
        self.log = EmailLog.objects.create(
            user=self.user,
            email_type="campaign",
            ses_message_id="ses-delivery-1",
        )

    def test_delivery_does_not_correlate_by_ses_message_id(self):
        with mock.patch.object(
            ses_events_views,
            "_find_email_log",
            wraps=ses_events_views._find_email_log,
        ) as find_log:
            with mock.patch(VALIDATOR_PATH, return_value=True):
                response = self.client.post(
                    URL,
                    data=json.dumps(
                        _delivery_payload_with_ses_id(
                            "m-del-1523",
                            self.user.email,
                            "ses-delivery-1",
                        ),
                    ),
                    content_type="application/json",
                )
        self.assertEqual(response.json(), {"status": "ok"})
        find_log.assert_not_called()
        event = SesEvent.objects.get(message_id="m-del-1523")
        self.assertIsNone(event.email_log_id)
        self.assertEqual(event.action_taken, "logged only")
        self.log.refresh_from_db()
        self.assertIsNone(self.log.opened_at)
        self.assertEqual(self.log.opens, 0)
        self.assertIsNone(self.log.bounced_at)


class SesLookupPathTest(TestCase):
    def test_bounce_complaint_open_click_use_find_email_log(self):
        user = User.objects.create_user(email="path-ses@example.com")
        log = EmailLog.objects.create(
            user=user,
            email_type="campaign",
            ses_message_id="ses-path-1",
        )
        with mock.patch.object(
            ses_events_views,
            "_find_email_log",
            wraps=ses_events_views._find_email_log,
        ) as find_log:
            _post(
                self.client,
                _envelope(
                    "m-path-bounce",
                    _bounce_inner(
                        ses_message_id="ses-path-1",
                        email=user.email,
                    ),
                ),
            )
            self.assertGreaterEqual(find_log.call_count, 1)
            find_log.assert_called_with("ses-path-1")
            find_log.reset_mock()

            _post(
                self.client,
                _envelope(
                    "m-path-complaint",
                    _complaint_inner(
                        ses_message_id="ses-path-1",
                        email=user.email,
                    ),
                ),
            )
            find_log.assert_called_with("ses-path-1")
            find_log.reset_mock()

            with mock.patch(VALIDATOR_PATH, return_value=True):
                self.client.post(
                    URL,
                    data=json.dumps(
                        _engagement_payload(
                            "m-path-open",
                            user.email,
                            "ses-path-1",
                            "Open",
                            "2026-05-06T10:00:00.000Z",
                        ),
                    ),
                    content_type="application/json",
                )
            find_log.assert_called_with("ses-path-1")
            find_log.reset_mock()

            with mock.patch(VALIDATOR_PATH, return_value=True):
                self.client.post(
                    URL,
                    data=json.dumps(
                        _engagement_payload(
                            "m-path-click",
                            user.email,
                            "ses-path-1",
                            "Click",
                            "2026-05-06T12:00:00.000Z",
                        ),
                    ),
                    content_type="application/json",
                )
            find_log.assert_called_with("ses-path-1")
            log.refresh_from_db()
            self.assertEqual(log.opens, 1)
            self.assertEqual(log.clicks, 1)
