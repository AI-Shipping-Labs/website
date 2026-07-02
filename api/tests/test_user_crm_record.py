"""Tests for the operator CRM-record repair endpoint (#1115)."""

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.models import EmailAlias, Token
from api.openapi import build_spec
from api.urls import urlpatterns
from community.models import CommunityAuditLog
from crm.models import CRMRecord
from questionnaires.models import Questionnaire, Response

User = get_user_model()


class UserCrmRecordApiTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="staff@test.com", password="pw", is_staff=True,
        )
        cls.token = Token.objects.create(user=cls.staff, name="crm-bot")
        cls.questionnaire = Questionnaire.objects.create(
            slug="crm-repair-onboarding",
            title="CRM repair onboarding",
            purpose="onboarding",
        )

    def _auth(self):
        return {"HTTP_AUTHORIZATION": f"Token {self.token.key}"}

    def _post(self, email):
        return self.client.post(
            f"/api/users/{email}/crm-record",
            content_type="application/json",
            **self._auth(),
        )

    def _submitted(self, user):
        return Response.objects.create(
            questionnaire=self.questionnaire,
            respondent=user,
            status="submitted",
        )

    def test_creates_crm_record_for_submitted_onboarding(self):
        member = User.objects.create_user(email="member@test.com")
        self._submitted(member)

        response = self._post("member@test.com")

        self.assertEqual(response.status_code, 201)
        body = response.json()
        record = CRMRecord.objects.get(user=member)
        self.assertTrue(body["created"])
        self.assertEqual(body["email"], "member@test.com")
        self.assertEqual(body["crm_record"]["id"], record.pk)
        self.assertEqual(
            body["crm_record"]["onboarding_url"],
            f"/studio/crm/{record.pk}/#onboarding",
        )
        self.assertEqual(record.created_by, self.staff)
        audit = CommunityAuditLog.objects.get(action="api_crm_record")
        self.assertEqual(audit.user, member)
        self.assertIn("crm-bot", audit.details)

    def test_reuses_existing_crm_record(self):
        member = User.objects.create_user(email="member@test.com")
        self._submitted(member)
        record = CRMRecord.objects.create(user=member, status="archived")

        response = self._post("member@test.com")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["created"])
        self.assertEqual(response.json()["crm_record"]["id"], record.pk)
        self.assertEqual(CRMRecord.objects.filter(user=member).count(), 1)

    def test_alias_email_resolves_to_canonical_user(self):
        member = User.objects.create_user(email="canonical@test.com")
        EmailAlias.objects.create(user=member, email="billing@test.com")
        self._submitted(member)

        response = self._post("billing@test.com")

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["email"], "canonical@test.com")
        self.assertTrue(CRMRecord.objects.filter(user=member).exists())

    def test_requires_submitted_onboarding(self):
        member = User.objects.create_user(email="draft@test.com")
        Response.objects.create(
            questionnaire=self.questionnaire,
            respondent=member,
            status="draft",
        )

        response = self._post("draft@test.com")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "onboarding_not_submitted")
        self.assertFalse(CRMRecord.objects.filter(user=member).exists())

    def test_unknown_user_returns_404(self):
        response = self._post("ghost@test.com")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], "user_not_found")


class UserCrmRecordOpenApiTest(TestCase):
    def test_crm_record_path_present(self):
        spec = build_spec(urlpatterns)
        self.assertIn("/api/users/{email}/crm-record", spec["paths"])
        operation = spec["paths"]["/api/users/{email}/crm-record"]["post"]
        self.assertIn("submitted onboarding", operation["description"])
