"""Operator Maven occurrence ledger API coverage for issue #1568."""

import json
from contextlib import ExitStack
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.utils import timezone

from accounts.models import EmailAlias, Token
from api.openapi import build_spec
from api.urls import urlpatterns
from community.models import CommunityAuditLog
from integrations.models import IntegrationSetting, MavenEnrollmentEvent
from integrations.services.maven import (
    MAX_STEP_ATTEMPTS,
    RUNNING_STEP_LEASE,
    SLACK_NOT_IN_WORKSPACE_NOTE,
    STEP_NAMES,
)
from payments.models import Tier

User = get_user_model()
COLLECTION_URL = "/api/integrations/maven/occurrences"
PRIVATE_MARKERS = (
    "payload-private-1568",
    "dedupe-private-1568",
    "identity-private-1568",
    "Private Given 1568",
    "Private Family 1568",
    "legacy-private-1568@example.com",
    "provider-private-1568",
    "configured-private-1568",
)


class MavenOccurrenceApiTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="maven-api-staff@example.com", password="pw", is_staff=True
        )
        cls.member = User.objects.create_user(
            email="maven-api-member@example.com", password="pw"
        )
        cls.staff_token = Token.objects.create(user=cls.staff, name="maven-support")
        cls.staff_key = cls.staff_token.key
        cls.non_staff_token = Token(
            key="legacy-member-maven-token",
            user=cls.member,
            name="legacy-member-token",
        )
        Token.objects.bulk_create([cls.non_staff_token])
        cls.non_staff_key = cls.non_staff_token.key
        cls.inactive_staff = User.objects.create_user(
            email="inactive-maven-api@example.com",
            password="pw",
            is_staff=True,
        )
        cls.inactive_token = Token.objects.create(
            user=cls.inactive_staff, name="inactive-token"
        )
        cls.inactive_key = cls.inactive_token.key
        User.objects.filter(pk=cls.inactive_staff.pk).update(is_active=False)
        Tier.objects.get_or_create(
            slug="main", defaults={"name": "Main", "level": 20}
        )

    def auth(self, key=None):
        return {"HTTP_AUTHORIZATION": f"Token {key or self.staff_key}"}

    def detail_url(self, occurrence):
        return f"{COLLECTION_URL}/{occurrence.pk}"

    def retry_url(self, occurrence, step):
        return f"{self.detail_url(occurrence)}/steps/{step}/retry"

    def event(self, key, *, user=None, **fields):
        defaults = {
            "dedupe_key": key,
            "identity_hash": key,
            "user": user,
            "email": user.email if user else f"{key}@example.com",
            "course": "Reliable AI Systems",
            "cohort": "Autumn 2026",
            "course_key": "reliable-ai",
            "cohort_key": "autumn-2026",
            "event_type": "user_cohort.enrolled",
            "lifecycle": MavenEnrollmentEvent.LIFECYCLE_ACTIVE,
            "outcome": MavenEnrollmentEvent.OUTCOME_ONBOARDED,
        }
        defaults.update(fields)
        return MavenEnrollmentEvent.objects.create(**defaults)

    def assert_private_markers_absent(self, response):
        body = response.content.decode()
        for marker in PRIVATE_MARKERS:
            self.assertNotIn(marker, body)


class MavenOccurrenceAuthTest(MavenOccurrenceApiTestBase):
    def test_full_get_and_post_auth_matrix_is_structured_and_indistinguishable(self):
        occurrence = self.event("auth-matrix", user=self.member)
        targets = (
            ("get", self.detail_url(occurrence)),
            ("post", self.retry_url(occurrence, "welcome")),
        )
        bad_credentials = (
            ({}, "authentication_required"),
            ({"HTTP_AUTHORIZATION": self.staff_key}, "authentication_required"),
            ({"HTTP_AUTHORIZATION": "Bearer wrong"}, "authentication_required"),
            ({"HTTP_AUTHORIZATION": "Token unknown"}, "invalid_token"),
            (self.auth(self.inactive_key), "invalid_token"),
            (self.auth(self.non_staff_key), "invalid_token"),
        )

        for method, url in targets:
            for headers, code in bad_credentials:
                with self.subTest(method=method, code=code, headers=headers):
                    response = getattr(self.client, method)(url, **headers)
                    self.assertEqual(response.status_code, 401)
                    self.assertEqual(response.json()["code"], code)

        invalid_bodies = []
        for key in ("unknown", self.inactive_key, self.non_staff_key):
            response = self.client.get(
                self.detail_url(occurrence), **self.auth(key)
            )
            invalid_bodies.append(response.json())
        self.assertEqual(invalid_bodies[0], invalid_bodies[1])
        self.assertEqual(invalid_bodies[0], invalid_bodies[2])
        occurrence.refresh_from_db()
        self.assertEqual(occurrence.welcome_attempts, 0)
        self.assertFalse(CommunityAuditLog.objects.exists())

    def test_browser_session_is_not_api_authentication(self):
        occurrence = self.event("session-only", user=self.member)
        self.client.force_login(self.staff)
        response = self.client.get(self.detail_url(occurrence))
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["code"], "authentication_required")

    def test_authentication_runs_before_method_validation_and_lookup(self):
        response = self.client.put(f"{COLLECTION_URL}/999999")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["code"], "authentication_required")

    def test_valid_staff_token_gets_structured_405_without_mutation(self):
        occurrence = self.event(
            "method-order",
            user=self.member,
            welcome_status=MavenEnrollmentEvent.STEP_FAILED,
            welcome_attempts=MAX_STEP_ATTEMPTS,
        )
        responses = (
            self.client.post(COLLECTION_URL, **self.auth()),
            self.client.post(self.detail_url(occurrence), **self.auth()),
            self.client.get(self.retry_url(occurrence, "welcome"), **self.auth()),
        )
        for response in responses:
            self.assertEqual(response.status_code, 405)
            self.assertEqual(
                response.json(),
                {"error": "Method not allowed", "code": "method_not_allowed"},
            )
        occurrence.refresh_from_db()
        self.assertEqual(occurrence.welcome_attempts, MAX_STEP_ATTEMPTS)
        self.assertFalse(CommunityAuditLog.objects.exists())

    def test_token_post_is_csrf_exempt_and_ignores_request_body(self):
        occurrence = self.event(
            "csrf-body",
            user=self.member,
            welcome_status=MavenEnrollmentEvent.STEP_PENDING,
        )
        csrf_client = Client(enforce_csrf_checks=True)
        with patch("integrations.services.maven._send_welcome") as send:
            response = csrf_client.post(
                self.retry_url(occurrence, "welcome"),
                data=json.dumps({"private": "payload-private-1568"}),
                content_type="application/json",
                **self.auth(),
            )
        self.assertEqual(response.json()["retry"]["outcome"], "succeeded")
        self.assertEqual(send.call_count, 1)
        self.assert_private_markers_absent(response)


class MavenOccurrenceListTest(MavenOccurrenceApiTestBase):
    def test_deterministic_pagination_default_clamp_and_old_rows_reachable(self):
        MavenEnrollmentEvent.objects.bulk_create(
            [
                MavenEnrollmentEvent(
                    dedupe_key=f"page-{index}",
                    identity_hash=f"page-{index}",
                    email=f"page-{index}@example.com",
                )
                for index in range(205)
            ]
        )
        tied = list(MavenEnrollmentEvent.objects.order_by("id")[:2])
        shared_created = timezone.now() - timedelta(days=2)
        MavenEnrollmentEvent.objects.filter(pk__in=[row.pk for row in tied]).update(
            created_at=shared_created
        )

        default = self.client.get(COLLECTION_URL, **self.auth()).json()
        self.assertEqual(default["count"], 50)
        self.assertEqual(default["total_count"], 205)
        self.assertEqual(default["limit"], 50)
        self.assertEqual(default["offset"], 0)

        clamped = self.client.get(
            COLLECTION_URL, {"limit": "999"}, **self.auth()
        ).json()
        self.assertEqual(clamped["count"], 200)
        self.assertEqual(clamped["limit"], 200)
        tail = self.client.get(
            COLLECTION_URL, {"limit": "999", "offset": "200"}, **self.auth()
        ).json()
        self.assertEqual(tail["count"], 5)
        self.assertEqual(tail["total_count"], 205)
        tied_ids = [
            row["id"]
            for row in tail["occurrences"]
            if row["id"] in {tied[0].pk, tied[1].pk}
        ]
        self.assertEqual(tied_ids, sorted(tied_ids, reverse=True))

    def test_email_filter_matches_primary_alias_and_unredacted_occurrence_only(self):
        canonical = User.objects.create_user(email="canonical-1568@example.com")
        EmailAlias.objects.create(user=canonical, email="former-1568@example.com")
        linked_live = self.event("linked-live", user=canonical)
        linked_redacted = self.event(
            "linked-redacted",
            user=canonical,
            email="",
            payload_redacted_at=timezone.now(),
        )
        unlinked_live = self.event(
            "unlinked-live", email="Unlinked-1568@Example.com"
        )
        self.event(
            "unlinked-redacted",
            email="unlinked-redacted-1568@example.com",
            payload_redacted_at=timezone.now(),
        )

        for address in ("CANONICAL-1568@example.com", "FORMER-1568@example.com"):
            body = self.client.get(
                COLLECTION_URL, {"email": address}, **self.auth()
            ).json()
            self.assertEqual(
                {row["id"] for row in body["occurrences"]},
                {linked_live.pk, linked_redacted.pk},
            )
        unlinked = self.client.get(
            COLLECTION_URL,
            {"email": "unlinked-1568@example.com"},
            **self.auth(),
        ).json()
        self.assertEqual([row["id"] for row in unlinked["occurrences"]], [unlinked_live.pk])
        missing = self.client.get(
            COLLECTION_URL,
            {"email": "unlinked-redacted-1568@example.com"},
            **self.auth(),
        ).json()
        self.assertEqual(missing["occurrences"], [])
        self.assertEqual((missing["count"], missing["total_count"]), (0, 0))

    def test_label_key_lifecycle_status_and_failed_step_filters_use_and_semantics(self):
        now = timezone.now()
        target = self.event(
            "filter-target",
            course="Advanced Reliable Agents",
            course_key="course-provider-42",
            cohort="September Builders",
            cohort_key="cohort-provider-7",
            lifecycle=MavenEnrollmentEvent.LIFECYCLE_REMOVED,
            welcome_status=MavenEnrollmentEvent.STEP_FAILED,
            welcome_attempts=MAX_STEP_ATTEMPTS,
            welcome_attempted_at=now - RUNNING_STEP_LEASE,
            welcome_completed_at=now - RUNNING_STEP_LEASE,
        )
        self.event(
            "filter-decoy",
            course="Advanced Reliable Agents",
            cohort="September Builders",
            lifecycle=MavenEnrollmentEvent.LIFECYCLE_ACTIVE,
            slack_status=MavenEnrollmentEvent.STEP_FAILED,
            slack_attempts=1,
            slack_attempted_at=now,
            slack_completed_at=now,
        )

        queries = (
            {"course": "reliable agents"},
            {"course": "COURSE-PROVIDER-42"},
            {"cohort": "builders"},
            {"cohort": "COHORT-PROVIDER-7"},
            {"lifecycle": "removed"},
            {"status": "needs_attention"},
            {"failed_step": "welcome"},
            {
                "course": "agents",
                "cohort": "builders",
                "lifecycle": "removed",
                "status": "failed",
                "failed_step": "welcome",
            },
        )
        for query in queries:
            with self.subTest(query=query):
                ids = {
                    row["id"]
                    for row in self.client.get(
                        COLLECTION_URL, query, **self.auth()
                    ).json()["occurrences"]
                }
                self.assertIn(target.pk, ids)
        combined = self.client.get(
            COLLECTION_URL, queries[-1], **self.auth()
        ).json()
        self.assertEqual([row["id"] for row in combined["occurrences"]], [target.pk])

        for lifecycle in ("active", "removed", "legacy"):
            event = self.event(f"lifecycle-{lifecycle}", lifecycle=lifecycle)
            ids = {
                row["id"]
                for row in self.client.get(
                    COLLECTION_URL, {"lifecycle": lifecycle}, **self.auth()
                ).json()["occurrences"]
            }
            self.assertIn(event.pk, ids)

        for index, step in enumerate(STEP_NAMES):
            event = self.event(
                f"failed-step-{index}",
                **{f"{step}_status": MavenEnrollmentEvent.STEP_FAILED},
            )
            ids = {
                row["id"]
                for row in self.client.get(
                    COLLECTION_URL, {"failed_step": step}, **self.auth()
                ).json()["occurrences"]
            }
            self.assertIn(event.pk, ids)

    def test_exact_attention_boundary_reuses_shared_predicate(self):
        now = timezone.now()
        boundary = now - RUNNING_STEP_LEASE
        exact = self.event(
            "attention-exact",
            welcome_status=MavenEnrollmentEvent.STEP_FAILED,
            welcome_attempts=1,
            welcome_attempted_at=boundary,
            welcome_completed_at=boundary,
        )
        fresh = self.event(
            "attention-fresh",
            welcome_status=MavenEnrollmentEvent.STEP_FAILED,
            welcome_attempts=1,
            welcome_attempted_at=boundary + timedelta(microseconds=1),
            welcome_completed_at=boundary + timedelta(microseconds=1),
        )
        with patch("api.views.maven_occurrences.timezone.now", return_value=now):
            rows = self.client.get(
                COLLECTION_URL, {"status": "needs_attention"}, **self.auth()
            ).json()["occurrences"]
        self.assertIn(exact.pk, {row["id"] for row in rows})
        self.assertNotIn(fresh.pk, {row["id"] for row in rows})
        exact_row = next(row for row in rows if row["id"] == exact.pk)
        self.assertEqual(exact_row["needs_attention_steps"], ["welcome"])

    def test_invalid_filters_and_pagination_are_safe_422s(self):
        invalid = (
            ("lifecycle", "private-lifecycle-1568"),
            ("status", "private-status-1568"),
            ("failed_step", "private-step-1568"),
            ("limit", "private-limit-1568"),
            ("limit", "0"),
            ("limit", "-1"),
            ("offset", "private-offset-1568"),
            ("offset", "-1"),
        )
        for field, value in invalid:
            with self.subTest(field=field, value=value):
                response = self.client.get(
                    COLLECTION_URL, {field: value}, **self.auth()
                )
                self.assertEqual(response.status_code, 422)
                self.assertEqual(response.json()["code"], "validation_error")
                self.assertEqual(response.json()["details"]["field"], field)
                self.assertNotIn(value, response.content.decode())

    def test_valid_filter_with_no_matches_is_successful_empty_page(self):
        response = self.client.get(
            COLLECTION_URL, {"course": "absent-course"}, **self.auth()
        )
        self.assertEqual(
            response.json(),
            {
                "occurrences": [],
                "count": 0,
                "total_count": 0,
                "limit": 50,
                "offset": 0,
            },
        )


class MavenOccurrenceDetailAndPrivacyTest(MavenOccurrenceApiTestBase):
    def test_detail_has_exact_fields_five_steps_and_safe_errors(self):
        now = timezone.now()
        occurrence = self.event(
            "detail-shape",
            user=self.member,
            account_created=True,
            welcome_eligible=True,
            removed_at=None,
            override_status=MavenEnrollmentEvent.STEP_FAILED,
            override_attempts=MAX_STEP_ATTEMPTS,
            override_attempted_at=now - timedelta(minutes=20),
            override_completed_at=now - timedelta(minutes=20),
            override_error="RuntimeError",
            notification_status=MavenEnrollmentEvent.STEP_FAILED,
            notification_error=(
                "provider-private-1568 legacy-private-1568@example.com"
            ),
            slack_status=MavenEnrollmentEvent.STEP_SKIPPED,
            slack_error=SLACK_NOT_IN_WORKSPACE_NOTE,
        )
        body = self.client.get(self.detail_url(occurrence), **self.auth()).json()
        self.assertEqual(
            set(body),
            {
                "id",
                "user",
                "occurrence_email",
                "course",
                "cohort",
                "course_key",
                "cohort_key",
                "event_type",
                "lifecycle",
                "outcome",
                "failed_steps",
                "needs_attention_steps",
                "payload_redacted_at",
                "created_at",
                "updated_at",
                "account_created",
                "welcome_eligible",
                "removed_at",
                "steps",
            },
        )
        self.assertEqual(body["user"], {"id": self.member.pk, "email": self.member.email})
        self.assertEqual(body["failed_steps"], ["override", "notification"])
        self.assertEqual([step["name"] for step in body["steps"]], list(STEP_NAMES))
        override = body["steps"][0]
        self.assertEqual(override["last_error"], "RuntimeError")
        self.assertFalse(override["error_redacted"])
        self.assertTrue(override["needs_attention"])
        notification = body["steps"][1]
        self.assertEqual(notification["last_error"], "redacted")
        self.assertTrue(notification["error_redacted"])
        slack = body["steps"][2]
        self.assertEqual(slack["last_error"], SLACK_NOT_IN_WORKSPACE_NOTE)
        self.assertFalse(slack["error_redacted"])
        self.assertEqual(body["steps"][3]["last_error"], "")
        self.assertFalse(body["steps"][3]["error_redacted"])
        self.assertIsInstance(override["attempted_at"], str)
        self.assertIsNone(body["removed_at"])

    def test_redacted_email_stays_empty_and_private_storage_never_serializes(self):
        private_user = User.objects.create_user(
            email="canonical-visible-1568@example.com",
            first_name="Private Given 1568",
            last_name="Private Family 1568",
        )
        IntegrationSetting.objects.create(
            key="PRIVATE_1568", value="configured-private-1568"
        )
        occurrence = self.event(
            "privacy",
            user=private_user,
            email="legacy-private-1568@example.com",
            payload_redacted_at=timezone.now(),
            dedupe_key="dedupe-private-1568",
            identity_hash="identity-private-1568",
            payload={"private": "payload-private-1568"},
            welcome_status=MavenEnrollmentEvent.STEP_FAILED,
            welcome_error="provider-private-1568 legacy-private-1568@example.com",
        )
        CommunityAuditLog.objects.create(
            user=private_user,
            action="check",
            details="audit-private-1568 provider-private-1568",
        )

        list_response = self.client.get(COLLECTION_URL, **self.auth())
        detail_response = self.client.get(self.detail_url(occurrence), **self.auth())
        with patch("integrations.services.maven._send_welcome"):
            retry_response = self.client.post(
                self.retry_url(occurrence, "welcome"), **self.auth()
            )
        for response in (list_response, detail_response, retry_response):
            self.assert_private_markers_absent(response)
        detail = detail_response.json()
        self.assertEqual(detail["occurrence_email"], "")
        self.assertEqual(
            detail["user"],
            {"id": private_user.pk, "email": "canonical-visible-1568@example.com"},
        )
        self.assertEqual(detail["steps"][3]["last_error"], "redacted")

    def test_detail_unknown_is_structured_404_and_get_is_read_only(self):
        occurrence = self.event(
            "detail-read-only",
            user=self.member,
            welcome_status=MavenEnrollmentEvent.STEP_FAILED,
            welcome_attempts=2,
        )
        before = {
            "attempts": occurrence.welcome_attempts,
            "status": occurrence.welcome_status,
            "updated_at": occurrence.updated_at,
            "audits": CommunityAuditLog.objects.count(),
        }
        with patch("api.views.maven_occurrences.retry_occurrence_step") as retry:
            response = self.client.get(self.detail_url(occurrence), **self.auth())
        self.assertEqual(response.json()["id"], occurrence.pk)
        retry.assert_not_called()
        occurrence.refresh_from_db()
        self.assertEqual(occurrence.welcome_attempts, before["attempts"])
        self.assertEqual(occurrence.welcome_status, before["status"])
        self.assertEqual(occurrence.updated_at, before["updated_at"])
        self.assertEqual(CommunityAuditLog.objects.count(), before["audits"])

        missing = self.client.get(f"{COLLECTION_URL}/999999", **self.auth())
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.json()["code"], "maven_occurrence_not_found")


class MavenOccurrenceRetryTest(MavenOccurrenceApiTestBase):
    def _all_completed_event(self, key, **fields):
        defaults = {
            f"{name}_status": MavenEnrollmentEvent.STEP_SKIPPED
            for name in STEP_NAMES
        }
        defaults.update(fields)
        return self.event(key, user=self.member, **defaults)

    def test_pending_failed_exhausted_and_stale_running_are_forced_once(self):
        cases = (
            ("pending", MavenEnrollmentEvent.STEP_PENDING, 0, None),
            ("failed", MavenEnrollmentEvent.STEP_FAILED, 1, timezone.now()),
            ("exhausted", MavenEnrollmentEvent.STEP_FAILED, MAX_STEP_ATTEMPTS, timezone.now()),
            (
                "stale-running",
                MavenEnrollmentEvent.STEP_RUNNING,
                2,
                timezone.now() - RUNNING_STEP_LEASE,
            ),
        )
        for key, status, attempts, attempted_at in cases:
            with self.subTest(key=key):
                occurrence = self._all_completed_event(
                    key,
                    welcome_status=status,
                    welcome_attempts=attempts,
                    welcome_attempted_at=attempted_at,
                )
                with patch("integrations.services.maven._send_welcome") as send:
                    response = self.client.post(
                        self.retry_url(occurrence, "welcome"), **self.auth()
                    )
                self.assertEqual(
                    response.json()["retry"],
                    {"step": "welcome", "outcome": "succeeded", "attempted": True},
                )
                occurrence.refresh_from_db()
                self.assertEqual(occurrence.welcome_attempts, attempts + 1)
                self.assertEqual(send.call_count, 1)

    def test_each_allowed_step_uses_its_provider_once(self):
        for index, step in enumerate(STEP_NAMES):
            with self.subTest(step=step):
                occurrence = self._all_completed_event(
                    f"allowed-{index}",
                    **{f"{step}_status": MavenEnrollmentEvent.STEP_PENDING},
                )
                calls = []
                with ExitStack() as stack:
                    stack.enter_context(
                        patch(
                            "integrations.services.maven._grant_or_refresh_override",
                            side_effect=lambda *args, **kwargs: calls.append("override") or "ok",
                        )
                    )
                    stack.enter_context(
                        patch(
                            "integrations.services.maven._enrollment_notification_entitlement",
                            return_value=(Tier.objects.get(slug="main"), timezone.now()),
                        )
                    )
                    stack.enter_context(
                        patch(
                            "community.services.staff_notifications.notify_maven_enrollment",
                            side_effect=lambda *args, **kwargs: calls.append("notification") or True,
                        )
                    )
                    stack.enter_context(
                        patch(
                            "integrations.services.maven._invite_to_slack",
                            side_effect=lambda *args, **kwargs: (
                                calls.append("slack")
                                or (MavenEnrollmentEvent.STEP_SUCCEEDED, "")
                            ),
                        )
                    )
                    stack.enter_context(
                        patch(
                            "integrations.services.maven._send_welcome",
                            side_effect=lambda *args, **kwargs: calls.append("welcome"),
                        )
                    )
                    stack.enter_context(
                        patch(
                            "community.services.staff_notifications.notify_maven_cohort_removal",
                            side_effect=lambda *args, **kwargs: calls.append("removal"),
                        )
                    )
                    response = self.client.post(
                        self.retry_url(occurrence, step), **self.auth()
                    )
                self.assertEqual(response.json()["retry"]["outcome"], "succeeded")
                occurrence.refresh_from_db()
                self.assertEqual(getattr(occurrence, f"{step}_attempts"), 1)
                self.assertEqual(calls, [step])

    def test_controlled_skip_and_caught_provider_failure_are_truthful_200s(self):
        self.member.email_preferences = {"maven_emails": False}
        self.member.save(update_fields=["email_preferences"])
        skipped = self._all_completed_event(
            "controlled-skip", welcome_status=MavenEnrollmentEvent.STEP_PENDING
        )
        with patch("integrations.services.maven._send_welcome") as send:
            skipped_response = self.client.post(
                self.retry_url(skipped, "welcome"), **self.auth()
            )
        self.assertEqual(skipped_response.json()["retry"]["outcome"], "skipped")
        send.assert_not_called()

        self.member.email_preferences = {"maven_emails": True}
        self.member.save(update_fields=["email_preferences"])
        failed = self._all_completed_event(
            "provider-failure", welcome_status=MavenEnrollmentEvent.STEP_FAILED
        )
        with patch(
            "integrations.services.maven._send_welcome",
            side_effect=RuntimeError("provider-private-1568 legacy-private-1568@example.com"),
        ) as send:
            failed_response = self.client.post(
                self.retry_url(failed, "welcome"), **self.auth()
            )
        self.assertEqual(failed_response.json()["retry"]["outcome"], "failed")
        self.assertEqual(
            failed_response.json()["occurrence"]["steps"][3]["last_error"],
            "RuntimeError",
        )
        self.assert_private_markers_absent(failed_response)
        self.assertEqual(send.call_count, 1)

    def test_fresh_running_and_completed_states_return_conflict_without_calls(self):
        fresh = self._all_completed_event(
            "fresh-running",
            welcome_status=MavenEnrollmentEvent.STEP_RUNNING,
            welcome_attempts=2,
            welcome_attempted_at=timezone.now(),
        )
        succeeded = self._all_completed_event(
            "already-succeeded",
            welcome_status=MavenEnrollmentEvent.STEP_SUCCEEDED,
            welcome_attempts=4,
        )
        skipped = self._all_completed_event(
            "already-skipped",
            welcome_status=MavenEnrollmentEvent.STEP_SKIPPED,
            welcome_attempts=1,
        )
        with patch("integrations.services.maven._send_welcome") as send:
            fresh_response = self.client.post(
                self.retry_url(fresh, "welcome"), **self.auth()
            )
            succeeded_response = self.client.post(
                self.retry_url(succeeded, "welcome"), **self.auth()
            )
            skipped_response = self.client.post(
                self.retry_url(skipped, "welcome"), **self.auth()
            )
        self.assertEqual(fresh_response.status_code, 409)
        self.assertEqual(fresh_response.json()["code"], "maven_step_in_progress")
        self.assertEqual(fresh_response.json()["details"]["status"], "running")
        for response in (succeeded_response, skipped_response):
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.json()["code"], "maven_step_not_retryable")
        send.assert_not_called()
        for occurrence, attempts in ((fresh, 2), (succeeded, 4), (skipped, 1)):
            occurrence.refresh_from_db()
            self.assertEqual(occurrence.welcome_attempts, attempts)
        self.assertEqual(
            CommunityAuditLog.objects.filter(action="maven_step_retry").count(),
            3,
        )

    def test_successful_override_resumes_only_eligible_downstream_in_order(self):
        occurrence = self.event(
            "override-downstream",
            user=self.member,
            override_status=MavenEnrollmentEvent.STEP_FAILED,
            override_attempts=MAX_STEP_ATTEMPTS,
            notification_status=MavenEnrollmentEvent.STEP_PENDING,
            slack_status=MavenEnrollmentEvent.STEP_SUCCEEDED,
            welcome_status=MavenEnrollmentEvent.STEP_PENDING,
            removal_status=MavenEnrollmentEvent.STEP_SKIPPED,
        )
        calls = []
        with patch(
            "integrations.services.maven._grant_or_refresh_override",
            side_effect=lambda *args, **kwargs: calls.append("override") or "ok",
        ), patch(
            "integrations.services.maven._enrollment_notification_entitlement",
            return_value=(Tier.objects.get(slug="main"), timezone.now()),
        ), patch(
            "community.services.staff_notifications.notify_maven_enrollment",
            side_effect=lambda *args, **kwargs: calls.append("notification") or True,
        ), patch("integrations.services.maven._invite_to_slack") as slack, patch(
            "integrations.services.maven._send_welcome",
            side_effect=lambda *args, **kwargs: calls.append("welcome"),
        ):
            response = self.client.post(
                self.retry_url(occurrence, "override"), **self.auth()
            )
        self.assertEqual(response.json()["retry"]["outcome"], "succeeded")
        self.assertEqual(calls, ["override", "notification", "welcome"])
        slack.assert_not_called()
        occurrence.refresh_from_db()
        self.assertEqual(occurrence.override_attempts, MAX_STEP_ATTEMPTS + 1)
        self.assertEqual(occurrence.notification_attempts, 1)
        self.assertEqual(occurrence.slack_attempts, 0)
        self.assertEqual(occurrence.welcome_attempts, 1)

    def test_failed_override_does_not_run_downstream(self):
        occurrence = self.event(
            "override-failed",
            user=self.member,
            override_status=MavenEnrollmentEvent.STEP_FAILED,
            notification_status=MavenEnrollmentEvent.STEP_PENDING,
            slack_status=MavenEnrollmentEvent.STEP_PENDING,
            welcome_status=MavenEnrollmentEvent.STEP_PENDING,
        )
        with patch(
            "integrations.services.maven._grant_or_refresh_override",
            side_effect=RuntimeError("provider-private-1568"),
        ), patch(
            "community.services.staff_notifications.notify_maven_enrollment"
        ) as notify, patch("integrations.services.maven._invite_to_slack") as slack, patch(
            "integrations.services.maven._send_welcome"
        ) as welcome:
            response = self.client.post(
                self.retry_url(occurrence, "override"), **self.auth()
            )
        self.assertEqual(response.json()["retry"]["outcome"], "failed")
        notify.assert_not_called()
        slack.assert_not_called()
        welcome.assert_not_called()

    def test_invalid_step_and_missing_occurrence_have_no_retry_or_audit(self):
        occurrence = self._all_completed_event(
            "invalid-step", welcome_status=MavenEnrollmentEvent.STEP_FAILED
        )
        with patch("api.views.maven_occurrences.retry_occurrence_step") as retry:
            invalid = self.client.post(
                self.retry_url(occurrence, "private-step-1568"), **self.auth()
            )
            missing = self.client.post(
                f"{COLLECTION_URL}/999999/steps/welcome/retry", **self.auth()
            )
        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(invalid.json()["code"], "invalid_maven_step")
        self.assertEqual(invalid.json()["details"]["allowed"], sorted(STEP_NAMES))
        self.assertNotIn("private-step-1568", invalid.content.decode())
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.json()["code"], "maven_occurrence_not_found")
        retry.assert_not_called()
        self.assertFalse(CommunityAuditLog.objects.exists())

    def test_unexpected_pre_persistence_failure_is_generic_logged_safely_and_audited(self):
        occurrence = self._all_completed_event(
            "unexpected", welcome_status=MavenEnrollmentEvent.STEP_FAILED
        )
        with patch(
            "api.views.maven_occurrences.retry_occurrence_step",
            side_effect=RuntimeError(
                "provider-private-1568 legacy-private-1568@example.com"
            ),
        ), self.assertLogs("api.views.maven_occurrences", level="ERROR") as captured:
            response = self.client.post(
                self.retry_url(occurrence, "welcome"), **self.auth()
            )
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["code"], "maven_step_retry_failed")
        self.assert_private_markers_absent(response)
        logs = "\n".join(captured.output)
        for marker in PRIVATE_MARKERS:
            self.assertNotIn(marker, logs)
        audit = CommunityAuditLog.objects.get(action="maven_step_retry")
        self.assertEqual(audit.user, self.member)
        self.assertIn("outcome=unexpected_error", audit.details)

    def test_audit_subject_actor_and_details_are_privacy_bounded(self):
        linked = self._all_completed_event(
            "audit-linked", welcome_status=MavenEnrollmentEvent.STEP_FAILED
        )
        unlinked = self.event(
            "audit-unlinked",
            user=None,
            email="legacy-private-1568@example.com",
            course="Private course",
            cohort="Private cohort",
            removal_status=MavenEnrollmentEvent.STEP_FAILED,
        )
        with patch("integrations.services.maven._send_welcome"), patch(
            "community.services.staff_notifications.notify_maven_cohort_removal"
        ):
            self.client.post(self.retry_url(linked, "welcome"), **self.auth())
            self.client.post(self.retry_url(unlinked, "removal"), **self.auth())
        audits = list(
            CommunityAuditLog.objects.filter(action="maven_step_retry").order_by("id")
        )
        self.assertEqual(len(audits), 2)
        self.assertEqual(audits[0].user, self.member)
        self.assertEqual(audits[1].user, self.staff)
        self.assertIn("subject_user_id=unknown", audits[1].details)
        for audit in audits:
            self.assertIn("actor_token=maven-support", audit.details)
            self.assertIn("outcome=succeeded", audit.details)
            for forbidden in (
                "@",
                "Private course",
                "Private cohort",
                "provider-private-1568",
                self.staff_key,
            ):
                self.assertNotIn(forbidden, audit.details)

        nameless = Token.objects.create(user=self.staff, name="")
        nameless_key = nameless.key
        third = self._all_completed_event(
            "audit-prefix", welcome_status=MavenEnrollmentEvent.STEP_FAILED
        )
        with patch("integrations.services.maven._send_welcome"):
            self.client.post(
                self.retry_url(third, "welcome"), **self.auth(nameless_key)
            )
        prefix_audit = CommunityAuditLog.objects.filter(
            action="maven_step_retry"
        ).latest("id")
        self.assertIn(f"actor_token={nameless.key_prefix}", prefix_audit.details)
        self.assertNotIn(nameless_key, prefix_audit.details)


class MavenOccurrenceOpenApiTest(TestCase):
    def test_three_routes_filters_schemas_auth_and_errors_are_documented(self):
        document = build_spec(urlpatterns)
        paths = document["paths"]
        collection_path = "/api/integrations/maven/occurrences"
        detail_path = "/api/integrations/maven/occurrences/{occurrence_id}"
        retry_path = (
            "/api/integrations/maven/occurrences/{occurrence_id}/steps/{step}/retry"
        )
        self.assertEqual(set(paths[collection_path]), {"get"})
        self.assertEqual(set(paths[detail_path]), {"get"})
        self.assertEqual(set(paths[retry_path]), {"post"})
        self.assertEqual(paths[collection_path]["get"]["tags"], ["Maven Integrations"])
        self.assertEqual(document["security"], [{"tokenAuth": []}])

        params = {
            item["name"]: item["schema"]
            for item in paths[collection_path]["get"]["parameters"]
        }
        self.assertEqual(
            set(params),
            {
                "email",
                "course",
                "cohort",
                "lifecycle",
                "status",
                "failed_step",
                "limit",
                "offset",
            },
        )
        self.assertEqual(params["status"]["enum"], ["all", "failed", "needs_attention"])
        self.assertEqual(params["failed_step"]["enum"], list(STEP_NAMES))
        self.assertEqual(params["limit"]["maximum"], 200)

        retry = paths[retry_path]["post"]
        self.assertNotIn("requestBody", retry)
        self.assertEqual(
            set(retry["responses"]),
            {"200", "401", "404", "405", "409", "422", "500"},
        )
        self.assertIn(
            "maven_step_not_retryable", retry["responses"]["409"]["description"]
        )
        for status in ("401", "404", "405", "409", "422"):
            schema = retry["responses"][status]["content"]["application/json"]["schema"]
            self.assertEqual(schema, {"$ref": "#/components/schemas/ErrorResponse"})
        retry_properties = retry["responses"]["200"]["content"]["application/json"][
            "schema"
        ]["properties"]
        self.assertEqual(set(retry_properties), {"retry", "occurrence"})
        step_items = retry_properties["occurrence"]["properties"]["steps"]["items"]
        self.assertEqual(
            set(step_items["properties"]),
            {
                "name",
                "status",
                "attempts",
                "attempted_at",
                "completed_at",
                "needs_attention",
                "last_error",
                "error_redacted",
            },
        )
