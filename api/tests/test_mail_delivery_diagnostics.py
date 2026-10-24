"""Package-mail diagnostics expose evidence without private mail material."""

from community_base.mail.models import EmailDelivery
from django.test import TestCase

from accounts.models import Token, User

URL = "/api/diagnostics/mail-deliveries"


class MailDeliveryDiagnosticsTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(email="operator@example.com", is_staff=True)
        cls.token = Token.objects.create(user=cls.staff, name="mail-evidence")
        cls.delivery = cls.make_delivery("reset-evidence", message_id="relay-message-42")
        cls.make_delivery("unrelated", purpose="email_verification_signup")

    @classmethod
    def make_delivery(cls, key, purpose="password_reset", message_id=""):
        return EmailDelivery.objects.create(
            idempotency_key=key,
            purpose=purpose,
            template_key=purpose,
            recipient_email="operator@example.com",
            state=EmailDelivery.State.PROVIDER_ACCEPTED,
            external_message_id=message_id,
            context_hash="0" * 64,
            context_data={"reset_token": "PRIVATE-RESET-TOKEN"},
            transport_options={"reply_to": ["PRIVATE-ADDRESS@example.com"]},
            reason_code="PRIVATE-ERROR",
        )

    def get_evidence(self, query, **extra):
        return self.client.get(URL, query, HTTP_AUTHORIZATION=f"Token {self.token.key}", **extra)

    def test_exact_key_returns_only_safe_delivery_evidence(self):
        response = self.get_evidence({"idempotency_key": "reset-evidence"})
        self.assertEqual(response.status_code, 200)
        rows = response.json()["deliveries"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0], {
            "id": str(self.delivery.id),
            "purpose": "password_reset",
            "template_key": "password_reset",
            "state": "provider_accepted",
            "external_message_id": "relay-message-42",
            "created_at": self.delivery.created_at.isoformat(),
            "updated_at": self.delivery.updated_at.isoformat(),
        })

    def test_exact_recipient_and_purpose_are_required_together(self):
        for query in ({}, {"recipient": "operator@example.com"}, {"purpose": "password_reset"}):
            with self.subTest(query=query):
                self.assertEqual(self.get_evidence(query).status_code, 422)
        response = self.get_evidence({"recipient": "OPERATOR@example.com", "purpose": "password_reset"})
        self.assertEqual([row["id"] for row in response.json()["deliveries"]], [str(self.delivery.id)])

    def test_diagnostics_bound_recent_results(self):
        for index in range(22):
            self.make_delivery(f"reset-{index}")
        response = self.get_evidence({"recipient": "operator@example.com", "purpose": "password_reset"})
        self.assertEqual(len(response.json()["deliveries"]), 20)
        self.assertNotIn(str(self.delivery.id), [row["id"] for row in response.json()["deliveries"]])

    def test_staff_token_and_read_only_boundary(self):
        self.assertEqual(self.client.get(URL).status_code, 401)
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(URL).status_code, 401)
        response = self.client.post(URL, HTTP_AUTHORIZATION=f"Token {self.token.key}")
        self.assertEqual(response.status_code, 405)
        self.staff.is_staff = False
        self.staff.save(update_fields=["is_staff"])
        self.assertEqual(self.get_evidence({"idempotency_key": "reset-evidence"}).status_code, 401)
