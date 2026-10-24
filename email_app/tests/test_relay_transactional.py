from unittest.mock import Mock, patch

import requests
from django.test import TestCase

from accounts.models import User
from email_app.models import RelayTransactionalDelivery
from email_app.relay_transactional import (
    RelayTransactionalError,
    calendar_alternative,
    send_relay_transactional,
)
from notifications.models import Notification


def relay_config(key, default=""):
    values = {
        "RELAY_BASE_URL": "https://relay.example.test",
        "RELAY_API_KEY": "test-relay-key",
    }
    return values.get(key, default)


def relay_response(
    *,
    status="queued",
    message_id="relay-message-1",
    template_key="event_registration",
    idempotency_key="registration:1",
    replay=False,
):
    response = Mock(status_code=202)
    response.json.return_value = {
        "message": {
            "id": message_id,
            "status": status,
            "template_key": template_key,
            "idempotency_key": idempotency_key,
        },
        "idempotent_replay": replay,
    }
    return response


@patch("email_app.relay_transactional.is_enabled", return_value=False)
@patch("email_app.relay_transactional.get_config", side_effect=relay_config)
class RelayTransactionalRequestTest(TestCase):
    def test_calendar_request_preserves_native_alternative(self, _config, _enabled):
        response = relay_response()
        ics = "BEGIN:VCALENDAR\r\nMETHOD:REQUEST\r\nEND:VCALENDAR\r\n"

        with patch("email_app.relay_transactional.requests.post", return_value=response) as post:
            result = send_relay_transactional(
                "member@example.com",
                "event_registration",
                {"user_name": "Ada", "event_title": "Relay event"},
                idempotency_key="registration:1",
                safe_correlation="event=1 user=2",
                calendar=calendar_alternative(ics, method="REQUEST"),
            )

        self.assertEqual(result.message_id, "relay-message-1")
        request = post.call_args
        self.assertEqual(
            request.args[0],
            "https://relay.example.test/api/transactional/send",
        )
        self.assertEqual(
            request.kwargs["headers"]["Authorization"],
            "Bearer test-relay-key",
        )
        payload = request.kwargs["json"]
        self.assertEqual(payload["email"], "member@example.com")
        self.assertEqual(payload["template_key"], "event_registration")
        self.assertEqual(payload["idempotency_key"], "registration:1")
        self.assertEqual(
            payload["calendar_alternative"],
            {"content": ics, "method": "REQUEST", "name": "event.ics"},
        )
        delivery = RelayTransactionalDelivery.objects.get()
        self.assertEqual(delivery.status, RelayTransactionalDelivery.STATUS_ACCEPTED)
        self.assertEqual(delivery.message_id, "relay-message-1")

    def test_accepted_delivery_is_not_submitted_twice(self, _config, _enabled):
        response = relay_response()
        with patch("email_app.relay_transactional.requests.post", return_value=response) as post:
            first = send_relay_transactional(
                "member@example.com",
                "event_registration",
                {},
                idempotency_key="registration:1",
                safe_correlation="event=1 user=2",
            )
            second = send_relay_transactional(
                "member@example.com",
                "event_registration",
                {},
                idempotency_key="registration:1",
                safe_correlation="event=1 user=2",
            )

        self.assertEqual(post.call_count, 1)
        self.assertEqual(first.message_id, second.message_id)
        self.assertTrue(second.idempotent_replay)
        self.assertEqual(RelayTransactionalDelivery.objects.count(), 1)

    def test_timeout_is_durable_safe_alerted_and_retryable(self, _config, _enabled):
        staff = User.objects.create_user(
            email="operator@example.com",
            is_staff=True,
        )
        recipient = "former-member@example.com"
        response = relay_response(
            template_key="account_deletion_completed",
            idempotency_key="account-deletion-completed:9",
            replay=True,
        )
        with patch(
            "email_app.relay_transactional.requests.post",
            side_effect=[requests.Timeout(), response],
        ) as post:
            with self.assertLogs("email_app.relay_transactional", level="ERROR") as logs:
                with self.assertRaises(RelayTransactionalError) as caught:
                    send_relay_transactional(
                        recipient,
                        "account_deletion_completed",
                        {"support_id": 9},
                        idempotency_key="account-deletion-completed:9",
                        safe_correlation="privacy_request=9",
                    )
            result = send_relay_transactional(
                recipient,
                "account_deletion_completed",
                {"support_id": 9},
                idempotency_key="account-deletion-completed:9",
                safe_correlation="privacy_request=9",
            )

        self.assertTrue(caught.exception.outcome_unknown)
        self.assertEqual(post.call_count, 2)
        request_keys = [call.kwargs["json"]["idempotency_key"] for call in post.call_args_list]
        self.assertEqual(
            request_keys,
            ["account-deletion-completed:9", "account-deletion-completed:9"],
        )
        self.assertEqual(result.message_id, "relay-message-1")
        self.assertTrue(result.idempotent_replay)
        delivery = RelayTransactionalDelivery.objects.get()
        self.assertEqual(delivery.attempt_count, 2)
        self.assertEqual(delivery.status, RelayTransactionalDelivery.STATUS_ACCEPTED)
        alert = Notification.objects.get(user=staff)
        diagnostics = f"{delivery.safe_correlation} {alert.title} {alert.body} {' '.join(logs.output)}"
        self.assertNotIn(recipient, diagnostics)
        self.assertNotIn("test-relay-key", diagnostics)

    def test_mismatched_response_does_not_burn_key_as_accepted(self, _config, _enabled):
        response = relay_response(template_key="wrong_template")

        with patch("email_app.relay_transactional.requests.post", return_value=response):
            with self.assertRaisesMessage(
                RelayTransactionalError,
                "relay_rejected",
            ):
                send_relay_transactional(
                    "member@example.com",
                    "event_registration",
                    {},
                    idempotency_key="registration:1",
                    safe_correlation="event=1 user=2",
                )

        delivery = RelayTransactionalDelivery.objects.get()
        self.assertEqual(delivery.status, RelayTransactionalDelivery.STATUS_FAILED)
        self.assertEqual(delivery.error_code, "relay_rejected")
        self.assertFalse(delivery.message_id)

    def test_null_message_id_is_rejected(self, _config, _enabled):
        response = relay_response(message_id=None)

        with patch("email_app.relay_transactional.requests.post", return_value=response):
            with self.assertRaisesMessage(
                RelayTransactionalError,
                "relay_rejected",
            ):
                send_relay_transactional(
                    "member@example.com",
                    "event_registration",
                    {},
                    idempotency_key="registration:1",
                    safe_correlation="event=1 user=2",
                )

        delivery = RelayTransactionalDelivery.objects.get()
        self.assertEqual(delivery.status, RelayTransactionalDelivery.STATUS_FAILED)
        self.assertFalse(delivery.message_id)

    def test_failed_relay_status_is_durable_and_alerted(self, _config, _enabled):
        staff = User.objects.create_user(
            email="operator@example.com",
            is_staff=True,
        )
        response = relay_response(status="failed")

        with patch("email_app.relay_transactional.requests.post", return_value=response):
            with self.assertLogs("email_app.relay_transactional", level="ERROR"):
                with self.assertRaisesMessage(
                    RelayTransactionalError,
                    "relay_rejected",
                ):
                    send_relay_transactional(
                        "member@example.com",
                        "event_registration",
                        {},
                        idempotency_key="registration:1",
                        safe_correlation="event=1 user=2",
                    )

        delivery = RelayTransactionalDelivery.objects.get()
        self.assertEqual(delivery.status, RelayTransactionalDelivery.STATUS_FAILED)
        self.assertEqual(delivery.error_code, "relay_rejected")
        alert = Notification.objects.get(user=staff)
        self.assertIn("event_registration", alert.title)
        self.assertIn("relay_rejected", alert.body)

    def test_accepted_state_is_not_downgraded_by_late_failure(self, _config, _enabled):
        from email_app.relay_transactional import _fail_delivery

        response = relay_response()
        with patch("email_app.relay_transactional.requests.post", return_value=response):
            send_relay_transactional(
                "member@example.com",
                "event_registration",
                {},
                idempotency_key="registration:1",
                safe_correlation="event=1 user=2",
            )
        with patch("email_app.relay_transactional._alert_operators") as alert:
            _fail_delivery(
                "event_registration",
                "registration:1",
                "event=1 user=2",
                "relay_ack_unknown",
                True,
            )

        delivery = RelayTransactionalDelivery.objects.get()
        self.assertEqual(delivery.status, RelayTransactionalDelivery.STATUS_ACCEPTED)
        self.assertEqual(delivery.message_id, "relay-message-1")
        self.assertFalse(delivery.error_code)
        alert.assert_not_called()
