"""Tests for the User Management API (issue #764).

Covers the happy path + 4xx error matrix for all 7 endpoints. Audit-log
behaviour lives in ``test_users_audit.py`` so each module stays focused.
"""

import json
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db.models.query import QuerySet
from django.test import TestCase
from django.utils import timezone

from accounts.models import Token
from email_app.models import EmailLog, SesEvent
from payments.models import Tier

User = get_user_model()


class _UserApiBase(TestCase):
    """Shared fixtures: a staff user with a token plus a target user."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="staff@test.com",
            password="testpass",
            is_staff=True,
        )
        cls.token = Token.objects.create(user=cls.staff, name="ops-laptop")
        cls.main_tier = Tier.objects.filter(slug="main").first() or Tier.objects.create(
            slug="main", name="Main", level=20,
        )

    def _auth_headers(self):
        return {"HTTP_AUTHORIZATION": f"Token {self.token.key}"}

    def _make_user(self, email="alice@test.com", **kwargs):
        return User.objects.create_user(email=email, password=None, **kwargs)


# ---------------------------------------------------------------------------
# GET /api/users/<email>  -- single-user state
# ---------------------------------------------------------------------------


class UserDetailGetTest(_UserApiBase):
    def test_get_existing_user_returns_full_payload(self):
        user = self._make_user(
            email="alice@test.com",
            first_name="Alice",
            last_name="Doe",
            tier=self.main_tier,
            stripe_customer_id="cus_xyz",
            subscription_id="sub_xyz",
            email_verified=True,
        )
        user.tags = ["sprint:may-2026"]
        user.slack_member = True
        user.slack_user_id = "U01ABCDEF"
        user.save(update_fields=[
            "tags", "slack_member", "slack_user_id",
        ])
        response = self.client.get(
            f"/api/users/{user.email}",
            **self._auth_headers(),
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["email"], "alice@test.com")
        self.assertEqual(body["first_name"], "Alice")
        self.assertEqual(body["last_name"], "Doe")
        self.assertEqual(body["display_name"], "Alice Doe")
        self.assertEqual(body["tier"], {"slug": "main", "level": 20})
        self.assertFalse(body["tier_override_active"])
        self.assertFalse(body["unsubscribed"])
        self.assertEqual(body["soft_bounce_count"], 0)
        self.assertEqual(body["bounce_state"], "none")
        self.assertTrue(body["email_verified"])
        self.assertEqual(body["tags"], ["sprint:may-2026"])
        self.assertTrue(body["slack_member"])
        self.assertEqual(body["slack_user_id"], "U01ABCDEF")
        self.assertEqual(body["stripe_customer_id"], "cus_xyz")
        self.assertEqual(body["subscription_id"], "sub_xyz")

    def test_unknown_email_returns_404_with_user_not_found_code(self):
        response = self.client.get(
            "/api/users/nobody@test.com",
            **self._auth_headers(),
        )
        self.assertEqual(response.status_code, 404)
        body = response.json()
        self.assertEqual(body["error"], "User not found")
        self.assertEqual(body["code"], "user_not_found")

    def test_email_lookup_is_case_insensitive(self):
        user = self._make_user(email="MixedCase@Test.Com")
        # Django's ``UserManager.normalize_email`` lowercases the domain
        # part. ``email__iexact`` is what makes the lookup tolerant of
        # the caller's casing in either direction.
        response = self.client.get(
            "/api/users/mixedcase@test.com",
            **self._auth_headers(),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["email"], user.email)
        # The actual stored email keeps the local-part casing but the
        # domain is lowercased.
        self.assertEqual(user.email, "MixedCase@test.com")

    def test_bounce_state_permanent_reflects_structured_field(self):
        user = self._make_user(email="bounced@test.com")
        user.bounce_state = User.BounceState.PERMANENT
        user.save(update_fields=["bounce_state"])
        response = self.client.get(
            f"/api/users/{user.email}",
            **self._auth_headers(),
        )
        self.assertEqual(response.json()["bounce_state"], "permanent")

    def test_bounce_state_soft_reflects_structured_field(self):
        user = self._make_user(email="soft@test.com")
        user.bounce_state = User.BounceState.SOFT
        user.save(update_fields=["bounce_state"])
        response = self.client.get(
            f"/api/users/{user.email}",
            **self._auth_headers(),
        )
        self.assertEqual(response.json()["bounce_state"], "soft")

    def test_soft_bounce_count_is_surfaced(self):
        # ``soft_bounce_count`` is independent of ``bounce_state`` -- it
        # tracks the running count of soft bounces seen, while
        # ``bounce_state`` is the tri-state flag. Both ship in the payload.
        user = self._make_user(email="softcount@test.com")
        user.soft_bounce_count = 2
        user.save(update_fields=["soft_bounce_count"])
        response = self.client.get(
            f"/api/users/{user.email}",
            **self._auth_headers(),
        )
        self.assertEqual(response.json()["soft_bounce_count"], 2)

    def test_bounce_state_reflects_what_ses_handler_writes(self):
        """SES permanent-bounce handler writes ``bounce_state="permanent"`` --
        the API serializer must surface that value (not the legacy tag)."""
        user = self._make_user(email="bouncy@test.com")
        # Simulate what the SES webhook handler does on a permanent bounce
        # (see ``api/tests/test_ses_events.py`` lines 332/364/391 -- the
        # handler writes ``bounce_state`` directly and does NOT touch the
        # legacy ``"bounced"`` tag any more).
        user.bounce_state = User.BounceState.PERMANENT
        user.unsubscribed = True
        user.bounce_recorded_at = timezone.now()
        user.save(update_fields=[
            "bounce_state", "unsubscribed", "bounce_recorded_at",
        ])
        # Regression guard: if anyone re-introduces the tag-derived path
        # "as a fallback", this assertion would fail loudly because the
        # serializer would then return "permanent" via the tag instead of
        # the field, and the test below would still pass but for the
        # wrong reason. Pinning the tag absent makes the test honest.
        self.assertNotIn("bounced", user.tags)

        response = self.client.get(
            f"/api/users/{user.email}",
            **self._auth_headers(),
        )
        self.assertEqual(response.json()["bounce_state"], "permanent")

    def test_anonymous_returns_401_not_redirect(self):
        self._make_user(email="alice@test.com")
        response = self.client.get("/api/users/alice@test.com")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(
            response.json(),
            {"error": "Authentication token required"},
        )

    def test_non_staff_token_returns_401(self):
        non_staff_user = User.objects.create_user(
            email="member@test.com",
            password=None,
        )
        # Build a token directly (bypassing the manager's staff check)
        # to model the legacy case where the owner has been demoted.
        bad_token = Token(
            key="non-staff-token-key-1234567890",
            user=non_staff_user,
            name="legacy",
        )
        Token.objects.bulk_create([bad_token])
        self._make_user(email="alice@test.com")
        response = self.client.get(
            "/api/users/alice@test.com",
            HTTP_AUTHORIZATION=f"Token {bad_token.key}",
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"error": "Invalid token"})

    def test_wrong_method_returns_405(self):
        self._make_user(email="alice@test.com")
        response = self.client.put(
            "/api/users/alice@test.com",
            data="{}",
            content_type="application/json",
            **self._auth_headers(),
        )
        self.assertEqual(response.status_code, 405)


# ---------------------------------------------------------------------------
# GET /api/users  -- search / list
# ---------------------------------------------------------------------------


class UsersCollectionTest(_UserApiBase):
    def test_empty_q_returns_newest_users(self):
        self._make_user(email="oldest@test.com")
        self._make_user(email="newer@test.com")
        response = self.client.get(
            "/api/users",
            **self._auth_headers(),
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        # staff + 2 created users = 3 total
        self.assertEqual(body["count"], 3)
        self.assertEqual(body["limit"], 50)
        emails = [u["email"] for u in body["users"]]
        # Order is newest first (-date_joined). The two created users
        # come after the staff fixture by definition.
        self.assertIn("newer@test.com", emails)
        self.assertIn("oldest@test.com", emails)
        # Compact rows should NOT include ``tags``.
        self.assertNotIn("tags", body["users"][0])

    def test_search_matches_email(self):
        self._make_user(email="alice@test.com")
        self._make_user(email="bob@test.com")
        response = self.client.get(
            "/api/users?q=alice",
            **self._auth_headers(),
        )
        body = response.json()
        emails = {u["email"] for u in body["users"]}
        self.assertIn("alice@test.com", emails)
        self.assertNotIn("bob@test.com", emails)

    def test_search_matches_first_or_last_name(self):
        self._make_user(
            email="alice@test.com",
            first_name="Alice",
            last_name="Doe",
        )
        self._make_user(email="bob@test.com")
        response = self.client.get(
            "/api/users?q=Doe",
            **self._auth_headers(),
        )
        emails = {u["email"] for u in response.json()["users"]}
        self.assertIn("alice@test.com", emails)
        self.assertNotIn("bob@test.com", emails)

    def test_search_matches_stripe_customer_id(self):
        self._make_user(
            email="paying@test.com", stripe_customer_id="cus_AAA123",
        )
        self._make_user(email="other@test.com")
        response = self.client.get(
            "/api/users?q=cus_AAA",
            **self._auth_headers(),
        )
        body = response.json()
        emails = {u["email"] for u in body["users"]}
        self.assertEqual(emails, {"paying@test.com"})
        self.assertEqual(body["count"], 1)

    def test_search_matches_slack_user_id(self):
        user = self._make_user(email="slacker@test.com")
        user.slack_user_id = "U01ABCDEF"
        user.save(update_fields=["slack_user_id"])
        response = self.client.get(
            "/api/users?q=U01ABCDEF",
            **self._auth_headers(),
        )
        emails = {u["email"] for u in response.json()["users"]}
        self.assertEqual(emails, {"slacker@test.com"})

    def test_search_matches_tag_substring(self):
        user = self._make_user(email="tagged@test.com")
        user.tags = ["sprint:may-2026"]
        user.save(update_fields=["tags"])
        self._make_user(email="other@test.com")
        response = self.client.get(
            "/api/users?q=may",
            **self._auth_headers(),
        )
        emails = {u["email"] for u in response.json()["users"]}
        self.assertIn("tagged@test.com", emails)
        self.assertNotIn("other@test.com", emails)

    def test_limit_above_max_is_clamped_to_200(self):
        response = self.client.get(
            "/api/users?limit=300",
            **self._auth_headers(),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["limit"], 200)

    def test_limit_non_integer_returns_422(self):
        response = self.client.get(
            "/api/users?limit=abc",
            **self._auth_headers(),
        )
        self.assertEqual(response.status_code, 422)
        body = response.json()
        self.assertEqual(body["code"], "validation_error")
        self.assertEqual(body["details"]["field"], "limit")

    def test_since_bad_format_returns_422(self):
        response = self.client.get(
            "/api/users?since=not-a-date",
            **self._auth_headers(),
        )
        self.assertEqual(response.status_code, 422)
        body = response.json()
        self.assertEqual(body["code"], "validation_error")
        self.assertEqual(body["details"]["field"], "since")

    def test_since_filters_by_date_joined(self):
        from urllib.parse import quote
        old_user = self._make_user(email="old@test.com")
        User.objects.filter(pk=old_user.pk).update(
            date_joined=timezone.now() - timedelta(days=30)
        )
        self._make_user(email="new@test.com")
        # ``+`` in the ISO timezone offset must be URL-encoded to survive
        # the GET query parsing -- ``+`` decodes to a space otherwise.
        since = quote((timezone.now() - timedelta(days=1)).isoformat())
        response = self.client.get(
            f"/api/users?since={since}",
            **self._auth_headers(),
        )
        emails = {u["email"] for u in response.json()["users"]}
        self.assertIn("new@test.com", emails)
        self.assertNotIn("old@test.com", emails)


# ---------------------------------------------------------------------------
# GET /api/users/<email>/ses-events
# ---------------------------------------------------------------------------


class UserSesEventsTest(_UserApiBase):
    def test_returns_events_filtered_by_user_fk(self):
        user = self._make_user(email="alice@test.com")
        other = self._make_user(email="bob@test.com")
        SesEvent.objects.create(
            message_id="msg-1",
            event_type=SesEvent.EVENT_TYPE_BOUNCE_PERMANENT,
            raw_payload={"x": 1},
            recipient_email="alice@test.com",
            user=user,
            bounce_type="Permanent",
        )
        SesEvent.objects.create(
            message_id="msg-2",
            event_type=SesEvent.EVENT_TYPE_DELIVERY,
            raw_payload={},
            recipient_email="bob@test.com",
            user=other,
        )
        response = self.client.get(
            "/api/users/alice@test.com/ses-events",
            **self._auth_headers(),
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["ses_events"][0]["message_id"], "msg-1")
        # ``raw_payload`` must NOT be in the API row.
        self.assertNotIn("raw_payload", body["ses_events"][0])

    def test_type_filter_invalid_returns_422_with_allowed_list(self):
        self._make_user(email="alice@test.com")
        response = self.client.get(
            "/api/users/alice@test.com/ses-events?type=invalid",
            **self._auth_headers(),
        )
        self.assertEqual(response.status_code, 422)
        body = response.json()
        self.assertEqual(body["code"], "validation_error")
        # The allowed list must include the canonical SES event types.
        allowed = body["details"]["allowed"]
        self.assertIn("bounce_permanent", allowed)
        self.assertIn("complaint", allowed)
        self.assertIn("delivery", allowed)

    def test_type_filter_valid_narrows_to_that_type(self):
        user = self._make_user(email="alice@test.com")
        SesEvent.objects.create(
            message_id="b-1",
            event_type=SesEvent.EVENT_TYPE_BOUNCE_PERMANENT,
            raw_payload={},
            recipient_email="alice@test.com",
            user=user,
        )
        SesEvent.objects.create(
            message_id="d-1",
            event_type=SesEvent.EVENT_TYPE_DELIVERY,
            raw_payload={},
            recipient_email="alice@test.com",
            user=user,
        )
        response = self.client.get(
            "/api/users/alice@test.com/ses-events?type=bounce_permanent",
            **self._auth_headers(),
        )
        body = response.json()
        self.assertEqual(body["count"], 1)
        self.assertEqual(
            body["ses_events"][0]["event_type"], "bounce_permanent",
        )

    def test_unknown_email_returns_404(self):
        response = self.client.get(
            "/api/users/nobody@test.com/ses-events",
            **self._auth_headers(),
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], "user_not_found")


# ---------------------------------------------------------------------------
# GET /api/users/<email>/email-log
# ---------------------------------------------------------------------------


class UserEmailLogTest(_UserApiBase):
    def test_returns_email_logs_for_user(self):
        user = self._make_user(email="alice@test.com")
        EmailLog.objects.create(
            user=user,
            email_type="welcome",
            ses_message_id="ses-1",
        )
        response = self.client.get(
            "/api/users/alice@test.com/email-log",
            **self._auth_headers(),
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["email_logs"][0]["email_type"], "welcome")
        self.assertEqual(body["email_logs"][0]["ses_message_id"], "ses-1")
        # The derived ``disposition`` must be present.
        self.assertEqual(body["email_logs"][0]["disposition"], "sent")

    def test_kind_filter_matches_exact_email_type(self):
        user = self._make_user(email="alice@test.com")
        EmailLog.objects.create(user=user, email_type="campaign")
        EmailLog.objects.create(user=user, email_type="welcome")
        response = self.client.get(
            "/api/users/alice@test.com/email-log?kind=campaign",
            **self._auth_headers(),
        )
        body = response.json()
        self.assertEqual(body["count"], 1)
        self.assertEqual(
            body["email_logs"][0]["email_type"], "campaign",
        )

    def test_kind_filter_unknown_value_returns_empty(self):
        # ``email_type`` is not a closed enum at the model layer, so a
        # typo is a quiet empty page (not a 422).
        user = self._make_user(email="alice@test.com")
        EmailLog.objects.create(user=user, email_type="campaign")
        response = self.client.get(
            "/api/users/alice@test.com/email-log?kind=does-not-exist",
            **self._auth_headers(),
        )
        body = response.json()
        self.assertEqual(body["count"], 0)
        self.assertEqual(body["email_logs"], [])

    def test_disposition_reflects_strongest_signal(self):
        user = self._make_user(email="alice@test.com")
        # opened > sent
        opened = EmailLog.objects.create(
            user=user,
            email_type="campaign",
            opens=2,
            opened_at=timezone.now(),
        )
        # clicked > opened
        clicked = EmailLog.objects.create(
            user=user,
            email_type="campaign",
            clicks=1,
            clicked_at=timezone.now(),
            opens=1,
            opened_at=timezone.now(),
        )
        # bounced > clicked
        bounced = EmailLog.objects.create(
            user=user,
            email_type="campaign",
            bounced_at=timezone.now(),
        )
        # complained > bounced
        complained = EmailLog.objects.create(
            user=user,
            email_type="campaign",
            complained_at=timezone.now(),
        )
        response = self.client.get(
            "/api/users/alice@test.com/email-log",
            **self._auth_headers(),
        )
        body = response.json()
        rows_by_id = {row["id"]: row for row in body["email_logs"]}
        self.assertEqual(rows_by_id[opened.id]["disposition"], "opened")
        self.assertEqual(rows_by_id[clicked.id]["disposition"], "clicked")
        self.assertEqual(rows_by_id[bounced.id]["disposition"], "bounced")
        self.assertEqual(
            rows_by_id[complained.id]["disposition"], "complained",
        )

    def test_unknown_email_returns_404(self):
        response = self.client.get(
            "/api/users/nobody@test.com/email-log",
            **self._auth_headers(),
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], "user_not_found")


# ---------------------------------------------------------------------------
# PATCH /api/users/<email>
# ---------------------------------------------------------------------------


class UserPatchTest(_UserApiBase):
    def _patch(self, email, payload):
        return self.client.patch(
            f"/api/users/{email}",
            data=json.dumps(payload),
            content_type="application/json",
            **self._auth_headers(),
        )

    def test_patch_unsubscribed_true_flips_bit(self):
        user = self._make_user(email="alice@test.com")
        self.assertFalse(user.unsubscribed)
        response = self._patch("alice@test.com", {"unsubscribed": True})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["unsubscribed"])
        user.refresh_from_db()
        self.assertTrue(user.unsubscribed)

    def test_patch_lock_does_not_join_nullable_tier(self):
        """Regression: Postgres rejects FOR UPDATE on nullable outer joins."""
        self._make_user(email="alice@test.com")
        original_select_for_update = QuerySet.select_for_update
        original_select_related = QuerySet.select_related

        def mark_for_update(queryset, *args, **kwargs):
            locked = original_select_for_update(queryset, *args, **kwargs)
            locked._test_for_update = True
            return locked

        def reject_nullable_tier_join(queryset, *fields):
            if getattr(queryset, "_test_for_update", False) and "tier" in fields:
                raise AssertionError(
                    "locked user write must not select_related nullable tier"
                )
            return original_select_related(queryset, *fields)

        with (
            patch.object(QuerySet, "select_for_update", mark_for_update),
            patch.object(QuerySet, "select_related", reject_nullable_tier_join),
        ):
            response = self._patch("alice@test.com", {"unsubscribed": True})

        self.assertEqual(response.status_code, 200)

    def test_patch_unsubscribed_idempotent_returns_200(self):
        user = self._make_user(email="alice@test.com", unsubscribed=True)
        response = self._patch("alice@test.com", {"unsubscribed": True})
        self.assertEqual(response.status_code, 200)
        user.refresh_from_db()
        self.assertTrue(user.unsubscribed)

    def test_patch_email_verified_true_clears_ttl(self):
        user = self._make_user(email="alice@test.com")
        user.email_verified = False
        user.verification_expires_at = timezone.now() + timedelta(days=1)
        user.save(update_fields=["email_verified", "verification_expires_at"])

        response = self._patch("alice@test.com", {"email_verified": True})
        self.assertEqual(response.status_code, 200)
        user.refresh_from_db()
        self.assertTrue(user.email_verified)
        self.assertIsNone(user.verification_expires_at)

    def test_patch_email_verified_false_returns_422(self):
        user = self._make_user(email="alice@test.com", email_verified=True)
        response = self._patch("alice@test.com", {"email_verified": False})
        self.assertEqual(response.status_code, 422)
        body = response.json()
        self.assertEqual(body["code"], "verification_demote_forbidden")
        user.refresh_from_db()
        self.assertTrue(user.email_verified)

    def test_patch_unknown_field_returns_422_unknown_field(self):
        self._make_user(email="alice@test.com")
        response = self._patch("alice@test.com", {"tier": "premium"})
        self.assertEqual(response.status_code, 422)
        body = response.json()
        self.assertEqual(body["code"], "unknown_field")
        self.assertEqual(body["details"]["field"], "tier")

    def test_patch_unknown_email_returns_404(self):
        response = self._patch("nobody@test.com", {"unsubscribed": True})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], "user_not_found")

    def test_patch_anonymous_returns_401(self):
        self._make_user(email="alice@test.com")
        response = self.client.patch(
            "/api/users/alice@test.com",
            data=json.dumps({"unsubscribed": True}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 401)


# ---------------------------------------------------------------------------
# POST /api/users/<email>/tags  +  DELETE /api/users/<email>/tags/<tag>
# ---------------------------------------------------------------------------


class UserTagsTest(_UserApiBase):
    def _post_tag(self, email, payload):
        return self.client.post(
            f"/api/users/{email}/tags",
            data=json.dumps(payload),
            content_type="application/json",
            **self._auth_headers(),
        )

    def _delete_tag(self, email, tag):
        return self.client.delete(
            f"/api/users/{email}/tags/{tag}",
            **self._auth_headers(),
        )

    def test_post_adds_tag_idempotently(self):
        user = self._make_user(email="alice@test.com")
        response = self._post_tag("alice@test.com", {"tag": "early-adopter"})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["email"], "alice@test.com")
        self.assertIn("early-adopter", body["tags"])
        user.refresh_from_db()
        self.assertIn("early-adopter", user.tags)

        # Re-adding is a no-op (still 200).
        response = self._post_tag("alice@test.com", {"tag": "early-adopter"})
        self.assertEqual(response.status_code, 200)
        user.refresh_from_db()
        # Tag still present, not duplicated.
        self.assertEqual(user.tags.count("early-adopter"), 1)

    def test_post_empty_tag_returns_422_invalid_tag(self):
        self._make_user(email="alice@test.com")
        response = self._post_tag("alice@test.com", {"tag": "   "})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "invalid_tag")

    def test_post_normalizes_input(self):
        self._make_user(email="alice@test.com")
        response = self._post_tag("alice@test.com", {"tag": "My Cool Tag"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("my-cool-tag", response.json()["tags"])

    def test_post_unknown_email_returns_404(self):
        response = self._post_tag("nobody@test.com", {"tag": "x"})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], "user_not_found")

    def test_delete_removes_tag_idempotently(self):
        user = self._make_user(email="alice@test.com")
        user.tags = ["sprint:may-2026", "wave-2"]
        user.save(update_fields=["tags"])

        response = self._delete_tag("alice@test.com", "wave-2")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["tags"], ["sprint:may-2026"])

        # Second DELETE is still 200, no error.
        response = self._delete_tag("alice@test.com", "wave-2")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["tags"], ["sprint:may-2026"])

    def test_delete_unknown_email_returns_404(self):
        response = self._delete_tag("nobody@test.com", "x")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], "user_not_found")

    def test_post_anonymous_returns_401_json(self):
        self._make_user(email="alice@test.com")
        response = self.client.post(
            "/api/users/alice@test.com/tags",
            data=json.dumps({"tag": "x"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 401)
        # 401 must be JSON, not an HTML redirect to /accounts/login/.
        self.assertEqual(
            response.json(),
            {"error": "Authentication token required"},
        )

    def test_post_wrong_method_returns_405(self):
        self._make_user(email="alice@test.com")
        response = self.client.get(
            "/api/users/alice@test.com/tags",
            **self._auth_headers(),
        )
        self.assertEqual(response.status_code, 405)


# ---------------------------------------------------------------------------
# Auth matrix sanity (mirrors other API tests)
# ---------------------------------------------------------------------------


class UserApiAuthMatrixTest(_UserApiBase):
    def test_get_with_session_only_returns_401_not_html(self):
        # Pure session (no Authorization header) must NOT be enough --
        # token_required gates this; the caller is a script.
        self.client.force_login(self.staff)
        self._make_user(email="alice@test.com")
        response = self.client.get("/api/users/alice@test.com")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(
            response.json(),
            {"error": "Authentication token required"},
        )
