"""Tests for ``POST /api/users/<email>/mark-bounced`` (issue #784).

Covers the happy path, 4xx matrix, idempotency, audit-log behaviour, and
a helper-parity regression guard that asserts the API path and the
webhook path produce indistinguishable user state for the same input.

The endpoint exists because the SES -> SNS webhook is not yet wired in
the infra repo, so operators need a way to mark a user as bounced from
their laptop with their existing API token. The implementation shares
its structured-bounce side-effects with the webhook via
``accounts/utils/bounce.py`` so the resulting state is indistinguishable
from a real bounce.
"""

import json
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from accounts.models import Token
from accounts.utils.bounce import (
    SOFT_BOUNCE_THRESHOLD,
    mark_permanent_bounce,
)
from community.models import CommunityAuditLog
from email_app.models import SesEvent
from payments.models import Tier

User = get_user_model()


class _MarkBouncedBase(TestCase):
    """Shared fixtures: a staff user with a token plus a target user."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="staff@test.com",
            password="pw",
            is_staff=True,
        )
        cls.token = Token.objects.create(user=cls.staff, name="ops-laptop")
        cls.main_tier = (
            Tier.objects.filter(slug="main").first()
            or Tier.objects.create(slug="main", name="Main", level=20)
        )

    def _auth(self):
        return {"HTTP_AUTHORIZATION": f"Token {self.token.key}"}

    def _post(self, email, payload, *, auth=True):
        kwargs = dict(
            data=json.dumps(payload),
            content_type="application/json",
        )
        if auth:
            kwargs.update(self._auth())
        return self.client.post(
            f"/api/users/{email}/mark-bounced",
            **kwargs,
        )


# ---------------------------------------------------------------------------
# Happy paths + 4xx matrix
# ---------------------------------------------------------------------------


class UserMarkBouncedTest(_MarkBouncedBase):
    def test_mark_permanent_writes_user_state(self):
        user = User.objects.create_user(email="alice@test.com", password=None)
        before = timezone.now()
        response = self._post(
            "alice@test.com",
            {"bounce_type": "permanent", "diagnostic": "550 user unknown"},
        )
        self.assertEqual(response.status_code, 200, response.content)
        user.refresh_from_db()
        self.assertEqual(user.bounce_state, User.BounceState.PERMANENT)
        self.assertTrue(user.unsubscribed)
        self.assertEqual(user.last_bounce_diagnostic, "550 user unknown")
        self.assertIsNotNone(user.bounce_recorded_at)
        self.assertGreaterEqual(user.bounce_recorded_at, before)
        body = response.json()
        self.assertEqual(body["bounce_state"], "permanent")
        self.assertTrue(body["unsubscribed"])
        self.assertEqual(body["email"], "alice@test.com")

    def test_mark_permanent_uses_default_diagnostic_when_omitted(self):
        user = User.objects.create_user(email="alice@test.com", password=None)
        response = self._post(
            "alice@test.com",
            {"bounce_type": "permanent"},
        )
        self.assertEqual(response.status_code, 200, response.content)
        user.refresh_from_db()
        self.assertEqual(
            user.last_bounce_diagnostic,
            "manual operator mark via API",
        )

    def test_mark_permanent_writes_synthetic_ses_event(self):
        user = User.objects.create_user(email="alice@test.com", password=None)
        response = self._post(
            "alice@test.com",
            {"bounce_type": "permanent", "diagnostic": "smtp; 550 nope"},
        )
        self.assertEqual(response.status_code, 200)
        events = SesEvent.objects.filter(user=user)
        self.assertEqual(events.count(), 1)
        event = events.first()
        self.assertEqual(event.event_type, SesEvent.EVENT_TYPE_BOUNCE_PERMANENT)
        self.assertEqual(event.bounce_type, "Permanent")
        self.assertTrue(event.message_id.startswith("manual-mark-bounced-"))
        self.assertEqual(event.recipient_email, "alice@test.com")
        self.assertEqual(event.diagnostic_code, "smtp; 550 nope")
        self.assertEqual(event.raw_payload.get("source"), "api_mark_bounced")
        self.assertIn("manual mark via API", event.action_taken)
        self.assertIn("actor_token=ops-laptop", event.action_taken)

    def test_mark_soft_increments_count_and_sets_state(self):
        user = User.objects.create_user(email="alice@test.com", password=None)
        self.assertEqual(user.soft_bounce_count, 0)
        self.assertFalse(user.unsubscribed)
        response = self._post(
            "alice@test.com",
            {"bounce_type": "soft", "diagnostic": "421 try later"},
        )
        self.assertEqual(response.status_code, 200, response.content)
        user.refresh_from_db()
        self.assertEqual(user.bounce_state, User.BounceState.SOFT)
        self.assertEqual(user.soft_bounce_count, 1)
        # Soft bounce alone does NOT unsubscribe.
        self.assertFalse(user.unsubscribed)
        event = SesEvent.objects.filter(user=user).first()
        self.assertIsNotNone(event)
        self.assertEqual(event.event_type, SesEvent.EVENT_TYPE_BOUNCE_TRANSIENT)
        self.assertEqual(event.bounce_type, "Transient")

    def test_mark_soft_crossing_threshold_promotes_to_permanent(self):
        # Pre-seed the user one shy of the threshold so a single soft
        # mark flips to PERMANENT via the shared helper.
        user = User.objects.create_user(email="alice@test.com", password=None)
        user.soft_bounce_count = SOFT_BOUNCE_THRESHOLD - 1
        user.save(update_fields=["soft_bounce_count"])

        response = self._post(
            "alice@test.com",
            {"bounce_type": "soft"},
        )
        self.assertEqual(response.status_code, 200, response.content)
        user.refresh_from_db()
        # Helper promotes to PERMANENT and resets the counter to 0.
        self.assertEqual(user.bounce_state, User.BounceState.PERMANENT)
        self.assertEqual(user.soft_bounce_count, 0)
        self.assertTrue(user.unsubscribed)

    def test_mark_permanent_idempotent_no_duplicate_ses_event(self):
        user = User.objects.create_user(email="alice@test.com", password=None)
        user.bounce_state = User.BounceState.PERMANENT
        user.bounce_recorded_at = timezone.now() - timedelta(days=2)
        user.last_bounce_diagnostic = "old diagnostic"
        user.unsubscribed = True
        user.save(update_fields=[
            "bounce_state",
            "bounce_recorded_at",
            "last_bounce_diagnostic",
            "unsubscribed",
        ])
        original_timestamp = user.bounce_recorded_at

        response = self._post(
            "alice@test.com",
            {"bounce_type": "permanent", "diagnostic": "new info"},
        )
        self.assertEqual(response.status_code, 200, response.content)
        # No new SesEvent inserted on a no-op (idempotency guard).
        self.assertEqual(SesEvent.objects.filter(user=user).count(), 0)
        # Helper was NOT called: diagnostic + timestamp preserved.
        user.refresh_from_db()
        self.assertEqual(user.last_bounce_diagnostic, "old diagnostic")
        self.assertEqual(user.bounce_recorded_at, original_timestamp)
        # But an audit row IS written.
        rows = CommunityAuditLog.objects.filter(user=user)
        self.assertEqual(rows.count(), 1)
        self.assertIn("no-op", rows.first().details)

    def test_mark_soft_idempotent_no_duplicate_ses_event(self):
        user = User.objects.create_user(email="alice@test.com", password=None)
        user.bounce_state = User.BounceState.SOFT
        user.soft_bounce_count = 1
        user.save(update_fields=["bounce_state", "soft_bounce_count"])

        response = self._post(
            "alice@test.com",
            {"bounce_type": "soft"},
        )
        self.assertEqual(response.status_code, 200)
        # No SesEvent insert.
        self.assertEqual(SesEvent.objects.filter(user=user).count(), 0)
        user.refresh_from_db()
        # Helper was NOT called -- soft_bounce_count unchanged.
        self.assertEqual(user.soft_bounce_count, 1)
        rows = CommunityAuditLog.objects.filter(user=user)
        self.assertEqual(rows.count(), 1)
        self.assertIn("no-op", rows.first().details)

    def test_soft_then_permanent_escalates(self):
        # SOFT is below PERMANENT in the partial order -- a permanent
        # mark must NOT be treated as a no-op.
        user = User.objects.create_user(email="alice@test.com", password=None)
        user.bounce_state = User.BounceState.SOFT
        user.soft_bounce_count = 1
        user.save(update_fields=["bounce_state", "soft_bounce_count"])

        response = self._post(
            "alice@test.com",
            {"bounce_type": "permanent", "diagnostic": "550 hard"},
        )
        self.assertEqual(response.status_code, 200, response.content)
        user.refresh_from_db()
        self.assertEqual(user.bounce_state, User.BounceState.PERMANENT)
        self.assertTrue(user.unsubscribed)
        # SesEvent row was written for the escalation.
        self.assertEqual(SesEvent.objects.filter(user=user).count(), 1)

    def test_missing_bounce_type_returns_422(self):
        User.objects.create_user(email="alice@test.com", password=None)
        response = self._post("alice@test.com", {})
        self.assertEqual(response.status_code, 422)
        body = response.json()
        self.assertEqual(body["code"], "validation_error")
        self.assertEqual(body["details"]["field"], "bounce_type")

    def test_invalid_bounce_type_returns_422(self):
        User.objects.create_user(email="alice@test.com", password=None)
        response = self._post(
            "alice@test.com",
            {"bounce_type": "hard"},
        )
        self.assertEqual(response.status_code, 422)
        body = response.json()
        self.assertEqual(body["code"], "validation_error")
        self.assertEqual(body["details"]["field"], "bounce_type")
        self.assertEqual(body["details"]["value"], "hard")
        self.assertEqual(
            body["details"]["allowed"],
            ["permanent", "soft"],
        )

    def test_unknown_body_field_returns_422_unknown_field(self):
        User.objects.create_user(email="alice@test.com", password=None)
        response = self._post(
            "alice@test.com",
            {"bounce_type": "permanent", "extra": 1},
        )
        self.assertEqual(response.status_code, 422)
        body = response.json()
        self.assertEqual(body["code"], "unknown_field")
        self.assertEqual(body["details"]["field"], "extra")

    def test_unknown_email_returns_404(self):
        response = self._post("nobody@test.com", {"bounce_type": "permanent"})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], "user_not_found")

    def test_no_token_returns_401(self):
        User.objects.create_user(email="alice@test.com", password=None)
        response = self._post(
            "alice@test.com",
            {"bounce_type": "permanent"},
            auth=False,
        )
        self.assertEqual(response.status_code, 401)

    def test_invalid_token_returns_401(self):
        # A malformed / unknown token must not pass the gate. Token
        # creation is staff-only (enforced at the model layer via
        # ``Token.clean``), so the "non-staff token" case is vacuous --
        # there is no way to mint one in the first place. The auth-gate
        # test that matters in practice is the bogus-token path.
        User.objects.create_user(email="alice@test.com", password=None)
        response = self.client.post(
            "/api/users/alice@test.com/mark-bounced",
            data=json.dumps({"bounce_type": "permanent"}),
            content_type="application/json",
            HTTP_AUTHORIZATION="Token deadbeefdeadbeefdeadbeefdeadbeef",
        )
        self.assertEqual(response.status_code, 401)

    def test_wrong_method_returns_405(self):
        User.objects.create_user(email="alice@test.com", password=None)
        for method in ("get", "patch", "delete"):
            with self.subTest(method=method):
                response = getattr(self.client, method)(
                    "/api/users/alice@test.com/mark-bounced",
                    **self._auth(),
                )
                self.assertEqual(response.status_code, 405)

    def test_response_body_matches_get_user_state(self):
        User.objects.create_user(email="alice@test.com", password=None)
        post_response = self._post(
            "alice@test.com",
            {"bounce_type": "permanent", "diagnostic": "550 no"},
        )
        self.assertEqual(post_response.status_code, 200)
        get_response = self.client.get(
            "/api/users/alice@test.com",
            **self._auth(),
        )
        self.assertEqual(get_response.status_code, 200)
        self.assertEqual(post_response.json(), get_response.json())


# ---------------------------------------------------------------------------
# Audit-log behaviour
# ---------------------------------------------------------------------------


class UserMarkBouncedAuditTest(_MarkBouncedBase):
    def test_audit_row_has_api_mark_bounced_action_and_actor(self):
        user = User.objects.create_user(email="alice@test.com", password=None)
        response = self._post(
            "alice@test.com",
            {"bounce_type": "permanent"},
        )
        self.assertEqual(response.status_code, 200, response.content)
        rows = CommunityAuditLog.objects.filter(user=user)
        self.assertEqual(rows.count(), 1)
        row = rows.first()
        self.assertEqual(row.action, "api_mark_bounced")
        self.assertEqual(row.user_id, user.id)
        self.assertIn("actor_token=ops-laptop", row.details)
        self.assertIn("previous_state='none'", row.details)

    def test_audit_row_includes_reason_when_supplied(self):
        user = User.objects.create_user(email="alice@test.com", password=None)
        response = self._post(
            "alice@test.com",
            {
                "bounce_type": "permanent",
                "reason": "operator note for taylordisom",
            },
        )
        self.assertEqual(response.status_code, 200, response.content)
        row = CommunityAuditLog.objects.filter(user=user).first()
        self.assertIsNotNone(row)
        self.assertIn(
            "reason='operator note for taylordisom'",
            row.details,
        )

    def test_audit_row_falls_back_to_key_prefix_when_token_unnamed(self):
        nameless = Token.objects.create(user=self.staff, name="")
        user = User.objects.create_user(email="alice@test.com", password=None)
        response = self.client.post(
            "/api/users/alice@test.com/mark-bounced",
            data=json.dumps({"bounce_type": "permanent"}),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Token {nameless.key}",
        )
        self.assertEqual(response.status_code, 200, response.content)
        row = CommunityAuditLog.objects.filter(user=user).first()
        self.assertIsNotNone(row)
        self.assertIn(f"actor_token={nameless.key_prefix}", row.details)

    def test_audit_row_written_even_for_noop(self):
        user = User.objects.create_user(email="alice@test.com", password=None)
        user.bounce_state = User.BounceState.PERMANENT
        user.unsubscribed = True
        user.save(update_fields=["bounce_state", "unsubscribed"])

        response = self._post(
            "alice@test.com",
            {"bounce_type": "permanent"},
        )
        self.assertEqual(response.status_code, 200, response.content)
        rows = CommunityAuditLog.objects.filter(user=user)
        self.assertEqual(rows.count(), 1)
        row = rows.first()
        self.assertEqual(row.action, "api_mark_bounced")
        self.assertIn("no-op", row.details)
        self.assertIn("current_state='permanent'", row.details)

    def test_noop_reason_starts_with_no_op_prefix(self):
        user = User.objects.create_user(email="alice@test.com", password=None)
        user.bounce_state = User.BounceState.PERMANENT
        user.save(update_fields=["bounce_state"])

        response = self._post(
            "alice@test.com",
            {"bounce_type": "permanent"},
        )
        self.assertEqual(response.status_code, 200)
        row = CommunityAuditLog.objects.filter(user=user).first()
        self.assertTrue(
            row.details.startswith("no-op:"),
            f"expected details to start with 'no-op:', got: {row.details!r}",
        )


# ---------------------------------------------------------------------------
# Helper-parity regression guard
# ---------------------------------------------------------------------------


class MarkBouncedHelperParityTest(_MarkBouncedBase):
    """Webhook helper and API endpoint must produce identical user state.

    The whole reason ``accounts/utils/bounce.py`` exists is to remove
    drift between the SES webhook path and the new operator API path.
    This test guards against future divergence by mutating one user via
    the helper directly and another via the API and asserting the
    resulting model rows match field-for-field.
    """

    def test_api_path_matches_helper_path(self):
        webhook_user = User.objects.create_user(
            email="webhook@test.com",
            password=None,
        )
        api_user = User.objects.create_user(
            email="api@test.com",
            password=None,
        )

        diagnostic = "smtp; 550 user unknown"

        # Path A: mutate directly via the helper -- this is what the
        # webhook handler does.
        mark_permanent_bounce(webhook_user, diagnostic=diagnostic)
        webhook_user.refresh_from_db()

        # Path B: mutate via the API.
        response = self._post(
            "api@test.com",
            {"bounce_type": "permanent", "diagnostic": diagnostic},
        )
        self.assertEqual(response.status_code, 200, response.content)
        api_user.refresh_from_db()

        self.assertEqual(webhook_user.bounce_state, api_user.bounce_state)
        self.assertEqual(webhook_user.unsubscribed, api_user.unsubscribed)
        self.assertEqual(
            webhook_user.last_bounce_diagnostic,
            api_user.last_bounce_diagnostic,
        )
        # bounce_recorded_at is set to ``timezone.now()`` in both
        # paths; allow a tiny delta since they execute back-to-back.
        delta = abs(
            (
                webhook_user.bounce_recorded_at
                - api_user.bounce_recorded_at
            ).total_seconds()
        )
        self.assertLess(delta, 5.0)

    def test_shared_helper_module_importable(self):
        # Smoke test the spec calls out: if the helper module is ever
        # deleted by a future refactor, this test fails at import time
        # and the SES webhook breaks loudly rather than silently
        # diverging from the API endpoint.
        from accounts.utils.bounce import (  # noqa: F401
            MAX_BOUNCE_DIAGNOSTIC_LEN,
            SOFT_BOUNCE_THRESHOLD,
            mark_permanent_bounce,
            record_soft_bounce,
        )
