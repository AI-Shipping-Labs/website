"""Tests for the content sync source API (issue #634)."""

import json
from io import StringIO
from unittest.mock import MagicMock, patch

from community_base.content_sync.models import (
    ContentSource as PackageContentSource,
)
from community_base.content_sync.models import SyncLog as PackageSyncLog
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from accounts.models import Token
from integrations.models import ContentSource

User = get_user_model()

DELETE_MESSAGE = (
    "Content sync source deletion is not available through the API. "
    "Go to Studio to delete this source manually."
)


class SyncSourcesApiTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="staff-sync-api@test.com",
            password="pw",
            is_staff=True,
        )
        cls.member = User.objects.create_user(
            email="member-sync-api@test.com",
            password="pw",
        )
        cls.staff_token = Token.objects.create(user=cls.staff, name="sync")
        cls.non_staff_token = Token(
            key="non-staff-sync-token",
            user=cls.member,
            name="legacy-member-token",
        )
        Token.objects.bulk_create([cls.non_staff_token])
        cls.source = ContentSource.objects.create(
            repo_name="AI-Shipping-Labs/content",
            webhook_secret="configured-secret",
            is_private=True,
            last_sync_status="success",
            last_synced_at=timezone.now(),
            last_synced_commit="abcdef1234567890",
        )

    def _auth(self, token=None):
        if token is None:
            token = self.staff_token
        return {"HTTP_AUTHORIZATION": f"Token {token.key}"}

    def test_list_sources_requires_valid_staff_token(self):
        cases = [
            ({}, {"error": "Authentication token required"}),
            (
                {"HTTP_AUTHORIZATION": self.staff_token.key},
                {"error": "Authentication token required"},
            ),
            (
                {"HTTP_AUTHORIZATION": "Token does-not-exist"},
                {"error": "Invalid token"},
            ),
            (
                self._auth(self.non_staff_token),
                {"error": "Invalid token"},
            ),
        ]

        for headers, expected_body in cases:
            with self.subTest(headers=headers):
                response = self.client.get("/api/sync/sources", **headers)
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.json(), expected_body)

    def test_list_sources_returns_metadata_without_webhook_secret(self):
        missing_secret_source = ContentSource.objects.create(
            repo_name="AI-Shipping-Labs/missing-secret",
            webhook_secret="   ",
        )
        response = self.client.get("/api/sync/sources", **self._auth())

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data["sources"]), 2)
        sources = {source["repo_name"]: source for source in data["sources"]}
        source = sources["AI-Shipping-Labs/content"]
        self.assertEqual(source["id"], str(self.source.pk))
        self.assertEqual(source["repo_name"], "AI-Shipping-Labs/content")
        self.assertEqual(source["short_name"], "content")
        self.assertTrue(source["is_private"])
        self.assertTrue(source["webhook_secret_configured"])
        self.assertEqual(source["webhook_security_status"], "configured")
        self.assertEqual(source["last_sync_status"], "success")
        self.assertEqual(source["last_synced_commit"], "abcdef1234567890")
        self.assertEqual(source["short_synced_commit"], "abcdef1")
        missing = sources["AI-Shipping-Labs/missing-secret"]
        self.assertEqual(missing["id"], str(missing_secret_source.pk))
        self.assertFalse(missing["webhook_secret_configured"])
        self.assertEqual(missing["webhook_security_status"], "missing_secret")
        for serialized in sources.values():
            self.assertNotIn("webhook_secret", serialized)
            self.assertNotIn("last_sync_log", serialized)

    def test_openapi_example_documents_webhook_security_metadata(self):
        from api.openapi import build_spec
        from api.urls import urlpatterns

        document = build_spec(urlpatterns)
        example = (
            document["paths"]["/api/sync/sources"]["get"]
            ["responses"]["200"]["content"]["application/json"]["example"]
            ["sources"][0]
        )

        self.assertTrue(example["webhook_secret_configured"])
        self.assertEqual(example["webhook_security_status"], "configured")
        self.assertNotIn("webhook_secret", example)
        self.assertNotIn("last_sync_log", example)

    # -- Webhook secret retrieval (issue #1766) ---------------------------

    def test_webhook_secret_endpoint_requires_valid_staff_token(self):
        cases = [
            ({}, {"error": "Authentication token required"}),
            (
                {"HTTP_AUTHORIZATION": self.staff_token.key},
                {"error": "Authentication token required"},
            ),
            (
                {"HTTP_AUTHORIZATION": "Token does-not-exist"},
                {"error": "Invalid token"},
            ),
            (
                self._auth(self.non_staff_token),
                {"error": "Invalid token"},
            ),
        ]

        url = f"/api/sync/sources/{self.source.pk}/webhook-secret"
        for headers, expected_body in cases:
            with self.subTest(headers=headers):
                response = self.client.get(url, **headers)
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.json(), expected_body)

    def test_webhook_secret_endpoint_returns_stored_secret_for_staff(self):
        response = self.client.get(
            f"/api/sync/sources/{self.source.pk}/webhook-secret",
            **self._auth(),
        )

        self.assertEqual(response.json(), {
            "id": str(self.source.pk),
            "repo_name": "AI-Shipping-Labs/content",
            "webhook_secret": "configured-secret",
        })

    def test_webhook_secret_endpoint_returns_empty_string_when_not_configured(self):
        blank = ContentSource.objects.create(
            repo_name="AI-Shipping-Labs/blank-secret",
            webhook_secret="   ",
        )
        response = self.client.get(
            f"/api/sync/sources/{blank.pk}/webhook-secret",
            **self._auth(),
        )

        self.assertEqual(response.json()["webhook_secret"], "")

    def test_webhook_secret_endpoint_missing_source_returns_404(self):
        import uuid

        response = self.client.get(
            f"/api/sync/sources/{uuid.uuid4()}/webhook-secret",
            **self._auth(),
        )

        self.assertEqual(response.status_code, 404)

    def test_openapi_documents_webhook_secret_endpoint(self):
        from api.openapi import build_spec
        from api.urls import urlpatterns

        document = build_spec(urlpatterns)
        operation = (
            document["paths"]
            ["/api/sync/sources/{source_id}/webhook-secret"]["get"]
        )

        self.assertEqual(operation["summary"], "Get the source's stored webhook secret")
        self.assertNotIn("example", operation["responses"]["200"])

    def test_delete_webhook_secret_returns_405_without_mutating(self):
        response = self.client.delete(
            f"/api/sync/sources/{self.source.pk}/webhook-secret",
            **self._auth(),
        )

        self.assertEqual(response.status_code, 405)
        self.source.refresh_from_db()
        self.assertEqual(self.source.webhook_secret, "configured-secret")

    def test_delete_sources_collection_returns_guidance_without_mutating(self):
        response = self.client.delete("/api/sync/sources", **self._auth())

        self.assertEqual(response.status_code, 405)
        self.assertEqual(
            response.json(),
            {
                "error": DELETE_MESSAGE,
                "code": "sync_source_delete_not_available",
            },
        )
        self.assertTrue(ContentSource.objects.filter(pk=self.source.pk).exists())

    def test_trigger_source_sync_queues_job_with_force(self):
        with patch(
            "integrations.services.content_sync_queue.package_queue_source_sync",
            return_value=(MagicMock(pk="intent-1234"), True),
        ) as mock_queue:
            response = self.client.post(
                f"/api/sync/sources/{self.source.pk}/trigger",
                data=json.dumps({"force": True}),
                content_type="application/json",
                **self._auth(),
            )

        self.assertEqual(response.status_code, 202)
        data = response.json()
        self.assertEqual(data["status"], "queued")
        self.assertEqual(data["task_id"], "intent-1234")
        self.assertFalse(data["ran_inline"])
        self.assertEqual(data["source"]["id"], str(self.source.pk))
        # The queued marker lands in the package SyncLog and source row the
        # operator surfaces read; the P6 mapping preserves the legacy pk.
        package_source = PackageContentSource.objects.get(pk=self.source.pk)
        self.assertEqual(package_source.last_sync_status, "queued")
        self.assertTrue(
            PackageSyncLog.objects.filter(
                source_id=self.source.pk, status="queued"
            ).exists()
        )
        self.assertEqual(mock_queue.call_count, 1)
        self.assertTrue(mock_queue.call_args.kwargs["force"])

    def test_trigger_missing_source_returns_404(self):
        response = self.client.post(
            "/api/sync/sources/00000000-0000-0000-0000-000000000000/trigger",
            data=json.dumps({}),
            content_type="application/json",
            **self._auth(),
        )

        self.assertEqual(response.status_code, 404)

    def test_delete_trigger_returns_guidance_without_mutating(self):
        response = self.client.delete(
            f"/api/sync/sources/{self.source.pk}/trigger",
            **self._auth(),
        )

        self.assertEqual(response.status_code, 405)
        self.assertEqual(
            response.json(),
            {
                "error": DELETE_MESSAGE,
                "code": "sync_source_delete_not_available",
            },
        )
        self.assertTrue(ContentSource.objects.filter(pk=self.source.pk).exists())


class WorkshopsContentAutoSyncTriggerTest(TestCase):
    """Regression for issue #916 — the website end of workshops auto-sync.

    The cross-repo fix lives in ``AI-Shipping-Labs/workshops-content``
    (its ``sync-production.yml`` now triggers ``on: push`` and calls this
    trigger API without ``force``). For that chain to reliably advance the
    site's ``workshops-content`` HEAD, this repo must guarantee:

    1. ``seed_content_sources`` registers the ``workshops-content`` source
       so ``scripts/sync_production.py``'s ``find_source`` resolves it.
    2. A non-forced trigger against that source resolves and queues a sync
       (NOT a 404, NOT silently dropped) — the exact call the workflow makes.
    3. The queued task carries ``force=False`` — the website's
       HEAD-advanced detection handles idempotency, so no ``force`` is sent
       by the auto-sync workflow.

    A push to an unrelated source id must NOT queue (404), proving the
    trigger is scoped to the resolved source.
    """

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="staff-ws-autosync@test.com",
            password="pw",
            is_staff=True,
        )
        cls.staff_token = Token.objects.create(user=cls.staff, name="ws-sync")
        # Register sources exactly as production does on first-time setup.
        call_command("seed_content_sources", stdout=StringIO())
        cls.workshops_source = PackageContentSource.objects.get(
            repo_name="AI-Shipping-Labs/workshops-content",
        )

    def _auth(self):
        return {"HTTP_AUTHORIZATION": f"Token {self.staff_token.key}"}

    def test_non_forced_trigger_resolves_and_queues_workshops_sync(self):
        with patch(
            "integrations.services.content_sync_queue.package_queue_source_sync",
            return_value=(MagicMock(pk="intent-ws"), True),
        ) as mock_queue:
            response = self.client.post(
                f"/api/sync/sources/{self.workshops_source.pk}/trigger",
                # Empty JSON body, no force — exactly what the workflow's
                # POST .../trigger does post-fix (no force param).
                data=json.dumps({}),
                content_type="application/json",
                **self._auth(),
            )

        self.assertEqual(response.status_code, 202)
        data = response.json()
        self.assertEqual(data["status"], "queued")
        self.assertEqual(data["task_id"], "intent-ws")
        self.assertEqual(
            data["source"]["repo_name"],
            "AI-Shipping-Labs/workshops-content",
        )
        # The queued task must NOT force — idempotency is the site's job.
        self.assertEqual(mock_queue.call_count, 1)
        self.assertFalse(mock_queue.call_args.kwargs["force"])
        # A queued SyncLog row exists for this source (the dashboard pill).
        self.assertTrue(
            PackageSyncLog.objects.filter(
                source_id=self.workshops_source.pk, status="queued"
            ).exists()
        )

    def test_trigger_against_unknown_source_does_not_queue(self):
        with patch(
            "integrations.services.content_sync_queue.package_queue_source_sync",
        ) as mock_queue:
            response = self.client.post(
                "/api/sync/sources/"
                "00000000-0000-0000-0000-000000000000/trigger",
                data=json.dumps({}),
                content_type="application/json",
                **self._auth(),
            )

        self.assertEqual(response.status_code, 404)
        mock_queue.assert_not_called()
        self.assertFalse(
            PackageSyncLog.objects.filter(
                source_id=self.workshops_source.pk
            ).exists()
        )
