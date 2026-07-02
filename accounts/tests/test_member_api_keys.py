"""Tests for member-owned hashed API keys (issue #1111)."""

from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.test import RequestFactory, TestCase, tag
from django.utils import timezone

from accounts.auth import member_api_key_required
from accounts.gating import is_newsletter_only_user
from accounts.models import (
    SIGNUP_SOURCE_NEWSLETTER,
    MemberAPIKey,
    Token,
    User,
)


def _ok_view(request):
    return JsonResponse({
        "user": request.user.email,
        "key": request.member_api_key.name,
    })


@tag("core")
class MemberAPIKeyModelTest(TestCase):
    def test_create_for_user_returns_plaintext_and_stores_only_hash(self):
        user = User.objects.create_user(email="member-key@test.com")

        member_key, plaintext = MemberAPIKey.create_for_user(
            user=user,
            name="local codex",
        )

        self.assertTrue(plaintext.startswith("asl_member_"))
        self.assertNotEqual(member_key.key_hash, plaintext)
        self.assertNotIn(plaintext, member_key.key_hash)
        self.assertEqual(member_key.lookup_prefix, plaintext[:24])
        self.assertEqual(member_key.masked_prefix, f"{plaintext[:24]}...")
        self.assertEqual(
            member_key.scopes,
            ["plans:read", "plans:write_progress"],
        )

    def test_rejects_unsupported_scope(self):
        user = User.objects.create_user(email="bad-scope@test.com")

        with self.assertRaises(ValidationError):
            MemberAPIKey.objects.create(
                user=user,
                name="bad",
                key_hash="hash",
                lookup_prefix="asl_member_bad",
                scopes=["admin:write"],
            )

    def test_existing_operator_token_still_rejects_non_staff_owner(self):
        user = User.objects.create_user(email="nonstaff-token@test.com")

        with self.assertRaises(ValidationError):
            Token.objects.create(user=user, name="not allowed")


@tag("core")
class MemberAPIKeyAccountViewTest(TestCase):
    def test_account_empty_state_links_to_member_api_docs(self):
        user = User.objects.create_user(email="empty-member-key@test.com")
        self.client.force_login(user)

        response = self.client.get("/account/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-testid="member-api-keys-section"')
        self.assertContains(response, 'data-testid="member-api-keys-empty"')
        self.assertContains(response, "/member-api/docs")

    def test_newsletter_only_account_does_not_show_api_keys(self):
        user = User.objects.create_user(
            email="newsletter-only-key@test.com",
            signup_source=SIGNUP_SOURCE_NEWSLETTER,
            account_activated=False,
            email_verified=True,
        )
        self.assertTrue(is_newsletter_only_user(user))
        self.client.force_login(user)

        response = self.client.get("/account/")

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'data-testid="member-api-keys-section"')

    def test_create_key_shows_plaintext_once_and_persists_hash_only(self):
        user = User.objects.create_user(email="create-member-key@test.com")
        self.client.force_login(user)

        response = self.client.post(
            "/account/api/member-api-keys",
            {"name": "local codex"},
        )

        self.assertEqual(response.status_code, 201)
        key = MemberAPIKey.objects.get(user=user)
        plaintext = response.context["created_member_api_key"]
        self.assertTrue(plaintext.startswith("asl_member_"))
        self.assertContains(response, plaintext, status_code=201)
        self.assertContains(
            response,
            'data-testid="member-api-key-copy"',
            status_code=201,
        )
        self.assertNotEqual(key.key_hash, plaintext)
        self.assertNotIn(plaintext, key.key_hash)
        self.assertEqual(key.lookup_prefix, plaintext[:24])

        followup = self.client.get("/account/")
        self.assertEqual(followup.status_code, 200)
        self.assertNotContains(followup, plaintext)
        self.assertContains(followup, key.masked_prefix)

    def test_member_can_revoke_only_their_own_key(self):
        user_a = User.objects.create_user(email="key-owner-a@test.com")
        user_b = User.objects.create_user(email="key-owner-b@test.com")
        key_a, _ = MemberAPIKey.create_for_user(user=user_a, name="a")
        key_b, _ = MemberAPIKey.create_for_user(user=user_b, name="b")
        self.client.force_login(user_a)

        response = self.client.post(
            f"/account/api/member-api-keys/{key_b.id}/revoke",
        )

        self.assertEqual(response.status_code, 404)
        key_b.refresh_from_db()
        self.assertIsNone(key_b.revoked_at)

        response = self.client.post(
            f"/account/api/member-api-keys/{key_a.id}/revoke",
        )

        self.assertEqual(response.status_code, 302)
        key_a.refresh_from_db()
        self.assertIsNotNone(key_a.revoked_at)

    def test_anonymous_create_redirects_to_login(self):
        response = self.client.post(
            "/account/api/member-api-keys",
            {"name": "local codex"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response.url)


@tag("core")
class MemberAPIKeyAuthHelperTest(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.view = member_api_key_required("plans:read")(_ok_view)

    def test_valid_key_authenticates_and_updates_audit_fields(self):
        user = User.objects.create_user(email="auth-member-key@test.com")
        member_key, plaintext = MemberAPIKey.create_for_user(
            user=user,
            name="local tool",
        )
        request = self.factory.get(
            "/member-api/plans",
            HTTP_AUTHORIZATION=f"Token {plaintext}",
            REMOTE_ADDR="203.0.113.10",
        )
        before = timezone.now()

        response = self.view(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(request.user, user)
        member_key.refresh_from_db()
        self.assertIsNotNone(member_key.last_used_at)
        self.assertGreaterEqual(member_key.last_used_at, before)
        self.assertTrue(member_key.last_used_ip_hash)
        self.assertNotEqual(member_key.last_used_ip_hash, "203.0.113.10")

    def test_revoked_key_returns_json_401_and_does_not_set_user(self):
        user = User.objects.create_user(email="revoked-member-key@test.com")
        member_key, plaintext = MemberAPIKey.create_for_user(
            user=user,
            name="revoked",
        )
        member_key.revoke()
        request = self.factory.get(
            "/member-api/plans",
            HTTP_AUTHORIZATION=f"Token {plaintext}",
        )

        response = self.view(request)

        self.assertEqual(response.status_code, 401)
        self.assertJSONEqual(
            response.content,
            {"error": "Invalid member API key"},
        )
        self.assertFalse(hasattr(request, "member_api_key"))

    def test_operator_token_is_never_accepted_by_member_helper(self):
        staff = User.objects.create_user(
            email="staff-member-helper@test.com",
            is_staff=True,
        )
        token = Token.objects.create(user=staff, name="operator")
        request = self.factory.get(
            "/member-api/plans",
            HTTP_AUTHORIZATION=f"Token {token.key}",
        )

        response = self.view(request)

        self.assertEqual(response.status_code, 401)
        self.assertFalse(hasattr(request, "member_api_key"))

    def test_missing_or_wrong_scheme_returns_json_401(self):
        request = self.factory.get("/member-api/plans")
        response = self.view(request)
        self.assertEqual(response.status_code, 401)
        self.assertJSONEqual(
            response.content,
            {"error": "Member API key required"},
        )

        request = self.factory.get(
            "/member-api/plans",
            HTTP_AUTHORIZATION="Bearer asl_member_fake",
        )
        response = self.view(request)
        self.assertEqual(response.status_code, 401)


@tag("core")
class MemberAPIKeyIsolationTest(TestCase):
    def test_member_key_does_not_authenticate_staff_api_or_staff_surfaces(self):
        user = User.objects.create_user(email="isolation-member-key@test.com")
        _, plaintext = MemberAPIKey.create_for_user(user=user, name="member")

        openapi = self.client.get(
            "/api/openapi.json",
            HTTP_AUTHORIZATION=f"Token {plaintext}",
        )
        self.assertEqual(openapi.status_code, 401)

        docs = self.client.get(
            "/api/docs",
            HTTP_AUTHORIZATION=f"Token {plaintext}",
        )
        self.assertNotEqual(docs.status_code, 200)

        studio = self.client.get(
            "/studio/",
            HTTP_AUTHORIZATION=f"Bearer {plaintext}",
        )
        self.assertNotEqual(studio.status_code, 200)

        admin = self.client.get(
            "/admin/",
            HTTP_AUTHORIZATION=f"Bearer {plaintext}",
        )
        self.assertNotEqual(admin.status_code, 200)
