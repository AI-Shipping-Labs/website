import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from accounts.models import EmailAlias, Token
from api.openapi.builder import build_spec
from api.urls import urlpatterns
from email_app.models import EmailCampaign, EmailLog, SesEvent

User = get_user_model()


class AggregateEmailLogApiTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(email="api-staff@example.com", is_staff=True)
        cls.member = User.objects.create_user(email="api-member@example.com")
        cls.other = User.objects.create_user(email="api-other@example.com")
        EmailAlias.objects.create(user=cls.member, email="api-old@example.com")
        cls.staff_token = Token.objects.create(user=cls.staff, name="email-log")
        cls.member_token = Token(
            key="member-email-log-token", user=cls.member, name="member",
        )
        Token.objects.bulk_create([cls.member_token])
        cls.campaign = EmailCampaign.objects.create(subject="API campaign", body="Body")
        cls.sent = EmailLog.objects.create(
            user=cls.member, recipient_email=cls.member.email,
            email_type="welcome", subject="Welcome", ses_message_id="api-sent",
            dedupe_key="private-dedupe-key",
        )
        cls.delivered = EmailLog.objects.create(
            recipient_email="api-old@example.com", email_type="campaign",
            subject="API campaign", campaign=cls.campaign,
            ses_message_id="api-delivered",
        )
        cls.clicked = EmailLog.objects.create(
            user=cls.member, recipient_email="historic@elsewhere.test",
            email_type="notice", subject="Notice", clicks=2,
            ses_message_id="api-clicked", bounce_diagnostic="private diagnostic",
        )
        cls.external = EmailLog.objects.create(
            recipient_email="outside@example.net", email_type="external",
            subject="", ses_message_id="api-external",
        )
        SesEvent.objects.create(
            event_type=SesEvent.EVENT_TYPE_DELIVERY,
            message_id="api-delivery-event",
            raw_payload={"private": True},
            recipient_email=cls.delivered.recipient_email,
            email_log=cls.delivered,
        )

    def auth(self, token=None):
        token = token or self.staff_token
        return {"HTTP_AUTHORIZATION": f"Token {token.key}"}

    def get(self, query="", token=None):
        return self.client.get("/api/email-log" + query, **self.auth(token))

    def test_staff_token_boundary(self):
        self.assertEqual(self.client.get("/api/email-log").status_code, 401)
        self.assertEqual(
            self.client.get(
                "/api/email-log", HTTP_AUTHORIZATION="Token invalid",
            ).status_code,
            401,
        )
        self.assertEqual(self.get(token=self.member_token).status_code, 401)
        self.assertEqual(self.get().status_code, 200)

    def test_privacy_limited_schema_and_total_before_pagination(self):
        payload = self.get("?limit=1&offset=1").json()
        self.assertEqual(payload["count"], 4)
        self.assertEqual(payload["limit"], 1)
        self.assertEqual(payload["offset"], 1)
        self.assertEqual(len(payload["email_logs"]), 1)
        allowed = {
            "id", "recipient_email", "user_id", "user_email", "email_type",
            "subject", "campaign_id", "campaign_subject", "sent_at",
            "ses_message_id", "opened_at", "opens", "clicked_at", "clicks",
            "bounced_at", "bounce_type", "bounce_subtype", "complained_at",
            "disposition",
        }
        self.assertEqual(set(payload["email_logs"][0]), allowed)
        serialized = str(payload)
        for prohibited in (
            "raw_payload", "body_html", "body_text", "bounce_diagnostic",
            "dedupe_key", "unsubscribe", "verification", "action_token", "bcc", "cc",
        ):
            self.assertNotIn(prohibited, serialized)

    def test_search_kind_status_and_canonical_exact_expansion(self):
        partial = self.get("?q=OUTSIDE@EXAMPLE").json()
        self.assertEqual([row["id"] for row in partial["email_logs"]], [self.external.pk])
        exact_alias = self.get("?q=API-OLD@example.com").json()
        self.assertCountEqual(
            [row["id"] for row in exact_alias["email_logs"]],
            [self.sent.pk, self.delivered.pk, self.clicked.pk],
        )
        kind = self.get("?kind=campaign").json()
        self.assertEqual([row["id"] for row in kind["email_logs"]], [self.delivered.pk])
        delivered = self.get("?status=delivered").json()
        self.assertEqual(delivered["email_logs"][0]["id"], self.delivered.pk)
        self.assertEqual(delivered["email_logs"][0]["disposition"], "delivered")
        clicked = self.get("?status=clicked").json()
        self.assertEqual(clicked["email_logs"][0]["id"], self.clicked.pk)

    def test_dates_are_inclusive_and_unknown_filters_are_empty_200(self):
        day = datetime.date(2026, 7, 15)
        at_end = timezone.make_aware(
            datetime.datetime.combine(day, datetime.time(23, 59, 59)), datetime.UTC,
        )
        EmailLog.objects.filter(pk=self.sent.pk).update(sent_at=at_end)
        payload = self.get("?since=2026-07-15&until=2026-07-15").json()
        self.assertEqual([row["id"] for row in payload["email_logs"]], [self.sent.pk])
        self.assertEqual(self.get("?kind=unknown-kind").status_code, 200)
        self.assertEqual(self.get("?kind=unknown-kind").json()["count"], 0)
        self.assertEqual(self.get("?q=nobody%40example.invalid").json()["count"], 0)

    def test_validation_and_limit_clamping(self):
        for query in (
            "?status=invalid", "?since=bad", "?until=bad",
            "?since=2026-07-16&until=2026-07-15", "?limit=-1",
            "?limit=nope", "?offset=-1", "?offset=nope",
        ):
            with self.subTest(query=query):
                response = self.get(query)
                self.assertEqual(response.status_code, 422)
                self.assertEqual(response.json()["code"], "validation_error")
        self.assertEqual(self.get("?limit=9999").json()["limit"], 200)

    def test_per_user_api_is_alias_aware_and_response_compatible(self):
        primary = self.client.get(
            "/api/users/api-member@example.com/email-log?kind=campaign",
            **self.auth(),
        )
        alias = self.client.get(
            "/api/users/API-OLD@example.com/email-log",
            **self.auth(),
        )
        self.assertEqual(primary.status_code, 200)
        self.assertEqual([row["id"] for row in primary.json()["email_logs"]], [self.delivered.pk])
        self.assertCountEqual(
            [row["id"] for row in alias.json()["email_logs"]],
            [self.sent.pk, self.delivered.pk, self.clicked.pk],
        )
        self.assertIn("count", alias.json())
        self.assertIn("limit", alias.json())

    def test_openapi_documents_both_endpoints_and_validation(self):
        spec = build_spec(urlpatterns)
        aggregate = spec["paths"]["/api/email-log"]["get"]
        per_user = spec["paths"]["/api/users/{email}/email-log"]["get"]
        parameters = {item["name"]: item for item in aggregate["parameters"]}
        self.assertEqual(parameters["status"]["schema"]["enum"], [
            "sent", "delivered", "opened", "clicked", "bounced", "complained",
        ])
        self.assertEqual(parameters["since"]["schema"]["format"], "date")
        self.assertIn("401", aggregate["responses"])
        self.assertIn("422", aggregate["responses"])
        row_schema = aggregate["responses"]["200"]["content"]["application/json"]["schema"]
        self.assertNotIn("raw_payload", str(row_schema))
        self.assertIn("status", {item["name"] for item in per_user["parameters"]})
