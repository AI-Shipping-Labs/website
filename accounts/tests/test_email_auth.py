"""Tests for email+password authentication (issue #94).

Tests cover:
- POST /api/register: email+password user creation
- POST /api/login: email+password login
- GET /api/verify-email?token={jwt}: email verification
- POST /api/password-reset-request: password reset email request
- GET/POST /api/password-reset: password reset flow
- POST /account/api/change-password: change password
- Registration page template
- Email verification banner on account page
"""

import json
import datetime

import jwt
from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import User


JWT_ALGORITHM = "HS256"


def _make_verification_token(user_id, expired=False):
    """Create a verification JWT token for testing."""
    if expired:
        exp = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=1)
    else:
        exp = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=24)
    payload = {
        "user_id": user_id,
        "action": "verify_email",
        "exp": exp,
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=JWT_ALGORITHM)


def _make_password_reset_token(user_id, expired=False):
    """Create a password reset JWT token for testing."""
    if expired:
        exp = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=1)
    else:
        exp = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1)
    payload = {
        "user_id": user_id,
        "action": "password_reset",
        "exp": exp,
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=JWT_ALGORITHM)


# ── Registration API ──────────────────────────────────────────────────


class RegisterAPITest(TestCase):
    """Tests for POST /api/register."""

    url = "/api/register"

    def _post(self, data):
        return self.client.post(
            self.url,
            data=json.dumps(data),
            content_type="application/json",
        )

    def test_register_creates_user(self):
        """Valid registration creates a new user."""
        resp = self._post({"email": "new@example.com", "password": "secure1234"})
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertEqual(data["status"], "ok")
        self.assertTrue(User.objects.filter(email="new@example.com").exists())

    def test_register_user_has_password(self):
        """Registered user has a usable password."""
        self._post({"email": "pwd@example.com", "password": "secure1234"})
        user = User.objects.get(email="pwd@example.com")
        self.assertTrue(user.check_password("secure1234"))

    def test_register_email_verified_false(self):
        """New user has email_verified=False."""
        self._post({"email": "unverified@example.com", "password": "secure1234"})
        user = User.objects.get(email="unverified@example.com")
        self.assertFalse(user.email_verified)

    def test_register_tier_is_free(self):
        """New user gets free tier."""
        self._post({"email": "free@example.com", "password": "secure1234"})
        user = User.objects.get(email="free@example.com")
        self.assertIsNotNone(user.tier)
        self.assertEqual(user.tier.slug, "free")

    def test_register_duplicate_email_returns_400(self):
        """Cannot register with an email that already exists."""
        User.objects.create_user(email="dupe@example.com", password="existing1234")
        resp = self._post({"email": "dupe@example.com", "password": "newpass1234"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("already exists", resp.json()["error"])

    def test_register_missing_email_returns_400(self):
        resp = self._post({"password": "secure1234"})
        self.assertEqual(resp.status_code, 400)

    def test_register_missing_password_returns_400(self):
        resp = self._post({"email": "no-pwd@example.com"})
        self.assertEqual(resp.status_code, 400)

    def test_register_short_password_returns_400(self):
        """Password must be at least 8 characters."""
        resp = self._post({"email": "short@example.com", "password": "short"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("8 characters", resp.json()["error"])

    def test_register_empty_email_returns_400(self):
        resp = self._post({"email": "", "password": "secure1234"})
        self.assertEqual(resp.status_code, 400)

    def test_register_invalid_json_returns_400(self):
        resp = self.client.post(
            self.url,
            data="not json",
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_register_get_not_allowed(self):
        """GET method is not allowed on register endpoint."""
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 405)

    def test_register_normalizes_email(self):
        """Email is normalized (lowercase domain)."""
        self._post({"email": "User@EXAMPLE.COM", "password": "secure1234"})
        self.assertTrue(User.objects.filter(email="user@example.com").exists())

    def test_register_url_name(self):
        url = reverse("api_register")
        self.assertEqual(url, "/api/register")

    def test_register_returns_success_message(self):
        resp = self._post({"email": "msg@example.com", "password": "secure1234"})
        data = resp.json()
        self.assertIn("message", data)
        self.assertIn("verify", data["message"].lower())


# ── Email Verification API ────────────────────────────────────────────


class VerifyEmailAPITest(TestCase):
    """Tests for GET /api/verify-email?token={jwt}."""

    url = "/api/verify-email"

    def test_verify_sets_email_verified_true(self):
        """Valid token sets email_verified to True."""
        user = User.objects.create_user(email="verify@example.com", password="test1234")
        self.assertFalse(user.email_verified)

        token = _make_verification_token(user.pk)
        resp = self.client.get(f"{self.url}?token={token}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ok")

        user.refresh_from_db()
        self.assertTrue(user.email_verified)

    def test_verify_already_verified_user_succeeds(self):
        """Verifying an already-verified user still returns success."""
        user = User.objects.create_user(email="already@example.com", password="test1234")
        user.email_verified = True
        user.save(update_fields=["email_verified"])

        token = _make_verification_token(user.pk)
        resp = self.client.get(f"{self.url}?token={token}")
        self.assertEqual(resp.status_code, 200)

    def test_verify_expired_token_returns_400(self):
        """Expired token returns 400."""
        user = User.objects.create_user(email="expired@example.com", password="test1234")
        token = _make_verification_token(user.pk, expired=True)
        resp = self.client.get(f"{self.url}?token={token}")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("expired", resp.json()["error"].lower())

    def test_verify_invalid_token_returns_400(self):
        """Garbage token returns 400."""
        resp = self.client.get(f"{self.url}?token=invalid_garbage")
        self.assertEqual(resp.status_code, 400)

    def test_verify_missing_token_returns_400(self):
        """No token parameter returns 400."""
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 400)

    def test_verify_wrong_action_returns_400(self):
        """Token with wrong action type returns 400."""
        user = User.objects.create_user(email="wrong-action@example.com", password="test1234")
        payload = {
            "user_id": user.pk,
            "action": "password_reset",  # wrong action
            "exp": datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(hours=24),
        }
        token = jwt.encode(payload, settings.SECRET_KEY, algorithm=JWT_ALGORITHM)
        resp = self.client.get(f"{self.url}?token={token}")
        self.assertEqual(resp.status_code, 400)

    def test_verify_nonexistent_user_returns_404(self):
        """Token for non-existent user returns 404."""
        token = _make_verification_token(99999)
        resp = self.client.get(f"{self.url}?token={token}")
        self.assertEqual(resp.status_code, 404)

    def test_verify_url_name(self):
        url = reverse("api_verify_email")
        self.assertEqual(url, "/api/verify-email")


# ── Login API ─────────────────────────────────────────────────────────


class LoginAPITest(TestCase):
    """Tests for POST /api/login."""

    url = "/api/login"

    def setUp(self):
        self.user = User.objects.create_user(
            email="login@example.com", password="correct1234"
        )

    def _post(self, data):
        return self.client.post(
            self.url,
            data=json.dumps(data),
            content_type="application/json",
        )

    def test_login_with_valid_credentials(self):
        """Valid email+password returns 200 and logs user in."""
        resp = self._post({"email": "login@example.com", "password": "correct1234"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ok")

    def test_login_authenticates_session(self):
        """After login, user is authenticated in the session."""
        self._post({"email": "login@example.com", "password": "correct1234"})
        resp = self.client.get("/account/")
        self.assertEqual(resp.status_code, 200)

    def test_login_wrong_password_returns_401(self):
        """Wrong password returns 401."""
        resp = self._post({"email": "login@example.com", "password": "wrongpass"})
        self.assertEqual(resp.status_code, 401)

    def test_login_nonexistent_user_returns_401(self):
        """Non-existent email returns 401."""
        resp = self._post({"email": "nobody@example.com", "password": "whatever"})
        self.assertEqual(resp.status_code, 401)

    def test_login_missing_email_returns_400(self):
        resp = self._post({"password": "something"})
        self.assertEqual(resp.status_code, 400)

    def test_login_missing_password_returns_400(self):
        resp = self._post({"email": "login@example.com"})
        self.assertEqual(resp.status_code, 400)

    def test_login_invalid_json_returns_400(self):
        resp = self.client.post(
            self.url, data="not json", content_type="application/json"
        )
        self.assertEqual(resp.status_code, 400)

    def test_login_get_not_allowed(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 405)

    def test_login_case_insensitive_email(self):
        """Login works with different email casing."""
        resp = self._post({"email": "LOGIN@EXAMPLE.COM", "password": "correct1234"})
        # Django's ModelBackend is case-sensitive by default; our authenticate
        # lowercases the email, so this should work if the stored email matches.
        # The stored email is "login@example.com" (normalized domain).
        # We send "login@example.com" after lowercasing.
        self.assertEqual(resp.status_code, 200)

    def test_login_url_name(self):
        url = reverse("api_login")
        self.assertEqual(url, "/api/login")


# ── Password Reset Request API ────────────────────────────────────────


class PasswordResetRequestAPITest(TestCase):
    """Tests for POST /api/password-reset-request."""

    url = "/api/password-reset-request"

    def _post(self, data):
        return self.client.post(
            self.url,
            data=json.dumps(data),
            content_type="application/json",
        )

    def test_reset_request_for_existing_user_returns_200(self):
        """Request with existing email returns 200."""
        User.objects.create_user(email="reset@example.com", password="test1234")
        resp = self._post({"email": "reset@example.com"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ok")

    def test_reset_request_for_nonexistent_email_returns_200(self):
        """Request with non-existent email still returns 200 (no reveal)."""
        resp = self._post({"email": "nobody@example.com"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ok")

    def test_reset_request_missing_email_returns_400(self):
        resp = self._post({})
        self.assertEqual(resp.status_code, 400)

    def test_reset_request_empty_email_returns_400(self):
        resp = self._post({"email": ""})
        self.assertEqual(resp.status_code, 400)

    def test_reset_request_invalid_json_returns_400(self):
        resp = self.client.post(
            self.url, data="not json", content_type="application/json"
        )
        self.assertEqual(resp.status_code, 400)

    def test_reset_request_get_not_allowed(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 405)

    def test_reset_request_url_name(self):
        url = reverse("api_password_reset_request")
        self.assertEqual(url, "/api/password-reset-request")


# ── Password Reset API ───────────────────────────────────────────────


class PasswordResetAPITest(TestCase):
    """Tests for GET/POST /api/password-reset."""

    url = "/api/password-reset"

    def setUp(self):
        self.user = User.objects.create_user(
            email="resetpw@example.com", password="oldpass1234"
        )

    def _post(self, data):
        return self.client.post(
            self.url,
            data=json.dumps(data),
            content_type="application/json",
        )

    def test_get_renders_form_with_valid_token(self):
        """GET with valid token renders password reset template."""
        token = _make_password_reset_token(self.user.pk)
        resp = self.client.get(f"{self.url}?token={token}")
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "accounts/password_reset.html")
        self.assertEqual(resp.context["token"], token)

    def test_get_shows_error_for_expired_token(self):
        """GET with expired token shows error message."""
        token = _make_password_reset_token(self.user.pk, expired=True)
        resp = self.client.get(f"{self.url}?token={token}")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("error", resp.context)
        self.assertIn("expired", resp.context["error"].lower())

    def test_get_shows_error_for_invalid_token(self):
        """GET with invalid token shows error message."""
        resp = self.client.get(f"{self.url}?token=garbage")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("error", resp.context)

    def test_get_missing_token_returns_400(self):
        """GET without token returns 400."""
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 400)

    def test_post_resets_password(self):
        """POST with valid token and new_password resets the password."""
        token = _make_password_reset_token(self.user.pk)
        resp = self._post({"token": token, "new_password": "newpass1234"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ok")

        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("newpass1234"))
        self.assertFalse(self.user.check_password("oldpass1234"))

    def test_post_expired_token_returns_400(self):
        token = _make_password_reset_token(self.user.pk, expired=True)
        resp = self._post({"token": token, "new_password": "newpass1234"})
        self.assertEqual(resp.status_code, 400)

    def test_post_invalid_token_returns_400(self):
        resp = self._post({"token": "garbage", "new_password": "newpass1234"})
        self.assertEqual(resp.status_code, 400)

    def test_post_missing_token_returns_400(self):
        resp = self._post({"new_password": "newpass1234"})
        self.assertEqual(resp.status_code, 400)

    def test_post_missing_password_returns_400(self):
        token = _make_password_reset_token(self.user.pk)
        resp = self._post({"token": token})
        self.assertEqual(resp.status_code, 400)

    def test_post_short_password_returns_400(self):
        token = _make_password_reset_token(self.user.pk)
        resp = self._post({"token": token, "new_password": "short"})
        self.assertEqual(resp.status_code, 400)

    def test_post_wrong_action_token_returns_400(self):
        """Token with verify_email action cannot be used for password reset."""
        token = _make_verification_token(self.user.pk)
        resp = self._post({"token": token, "new_password": "newpass1234"})
        self.assertEqual(resp.status_code, 400)

    def test_post_nonexistent_user_returns_404(self):
        token = _make_password_reset_token(99999)
        resp = self._post({"token": token, "new_password": "newpass1234"})
        self.assertEqual(resp.status_code, 404)

    def test_post_invalid_json_returns_400(self):
        resp = self.client.post(
            self.url, data="not json", content_type="application/json"
        )
        self.assertEqual(resp.status_code, 400)

    def test_url_name(self):
        url = reverse("api_password_reset")
        self.assertEqual(url, "/api/password-reset")


# ── Change Password API ──────────────────────────────────────────────


class ChangePasswordAPITest(TestCase):
    """Tests for POST /account/api/change-password."""

    url = "/account/api/change-password"

    def setUp(self):
        self.user = User.objects.create_user(
            email="changepw@example.com", password="oldpass1234"
        )
        self.client.force_login(self.user)

    def _post(self, data):
        return self.client.post(
            self.url,
            data=json.dumps(data),
            content_type="application/json",
        )

    def test_change_password_succeeds(self):
        """Valid current + new password changes the password."""
        resp = self._post(
            {"current_password": "oldpass1234", "new_password": "newpass5678"}
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ok")

        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("newpass5678"))

    def test_wrong_current_password_returns_400(self):
        resp = self._post(
            {"current_password": "wrongpass", "new_password": "newpass5678"}
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("incorrect", resp.json()["error"].lower())

    def test_missing_current_password_returns_400(self):
        resp = self._post({"new_password": "newpass5678"})
        self.assertEqual(resp.status_code, 400)

    def test_missing_new_password_returns_400(self):
        resp = self._post({"current_password": "oldpass1234"})
        self.assertEqual(resp.status_code, 400)

    def test_short_new_password_returns_400(self):
        resp = self._post(
            {"current_password": "oldpass1234", "new_password": "short"}
        )
        self.assertEqual(resp.status_code, 400)

    def test_session_remains_valid_after_change(self):
        """User stays logged in after password change."""
        self._post(
            {"current_password": "oldpass1234", "new_password": "newpass5678"}
        )
        resp = self.client.get("/account/")
        self.assertEqual(resp.status_code, 200)

    def test_logged_out_redirects(self):
        """Logged-out user gets redirect."""
        self.client.logout()
        resp = self._post(
            {"current_password": "oldpass1234", "new_password": "newpass5678"}
        )
        self.assertEqual(resp.status_code, 302)

    def test_get_not_allowed(self):
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 405)

    def test_invalid_json_returns_400(self):
        resp = self.client.post(
            self.url, data="not json", content_type="application/json"
        )
        self.assertEqual(resp.status_code, 400)

    def test_url_name(self):
        url = reverse("account_change_password")
        self.assertEqual(url, "/account/api/change-password")


# ── Registration Page ─────────────────────────────────────────────────


class RegisterPageTest(TestCase):
    """Tests for the registration page template."""

    def test_register_page_returns_200(self):
        resp = self.client.get("/accounts/register/")
        self.assertEqual(resp.status_code, 200)

    def test_register_page_uses_correct_template(self):
        resp = self.client.get("/accounts/register/")
        self.assertTemplateUsed(resp, "accounts/register.html")

    def test_register_page_contains_form(self):
        resp = self.client.get("/accounts/register/")
        content = resp.content.decode()
        self.assertIn("register-form", content)

    def test_register_page_contains_email_input(self):
        resp = self.client.get("/accounts/register/")
        content = resp.content.decode()
        self.assertIn("register-email", content)

    def test_register_page_contains_password_input(self):
        resp = self.client.get("/accounts/register/")
        content = resp.content.decode()
        self.assertIn("register-password", content)

    def test_register_page_contains_oauth_links(self):
        resp = self.client.get("/accounts/register/")
        content = resp.content.decode()
        self.assertIn("Sign up with Google", content)
        self.assertIn("Sign up with GitHub", content)

    def test_register_page_links_to_login(self):
        resp = self.client.get("/accounts/register/")
        content = resp.content.decode()
        self.assertIn("/accounts/login/", content)

    def test_register_page_redirects_authenticated_user(self):
        user = User.objects.create_user(email="auth@example.com")
        self.client.force_login(user)
        resp = self.client.get("/accounts/register/")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, "/")

    def test_register_url_name(self):
        url = reverse("account_register")
        self.assertEqual(url, "/accounts/register/")

    def test_register_shortcut_redirects(self):
        """GET /register redirects to /accounts/register/."""
        resp = self.client.get("/register")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, "/accounts/register/")


# ── Login Page Updates ────────────────────────────────────────────────


class LoginPageEmailPasswordTest(TestCase):
    """Tests that login page now includes email+password form."""

    def test_login_page_contains_email_input(self):
        resp = self.client.get("/accounts/login/")
        content = resp.content.decode()
        self.assertIn("login-email", content)

    def test_login_page_contains_password_input(self):
        resp = self.client.get("/accounts/login/")
        content = resp.content.decode()
        self.assertIn("login-password", content)

    def test_login_page_contains_login_form(self):
        resp = self.client.get("/accounts/login/")
        content = resp.content.decode()
        self.assertIn("login-form", content)

    def test_login_page_contains_register_link(self):
        resp = self.client.get("/accounts/login/")
        content = resp.content.decode()
        self.assertIn("Create account", content)

    def test_login_page_still_has_oauth_buttons(self):
        resp = self.client.get("/accounts/login/")
        content = resp.content.decode()
        self.assertIn("Sign in with Google", content)
        self.assertIn("Sign in with GitHub", content)

    def test_login_page_contains_divider(self):
        resp = self.client.get("/accounts/login/")
        content = resp.content.decode()
        self.assertIn("or continue with", content)


# ── Email Verification Banner ─────────────────────────────────────────


class EmailVerificationBannerTest(TestCase):
    """Tests for the email verification banner on the account page."""

    def test_unverified_user_sees_banner(self):
        """Unverified user sees the verification banner."""
        user = User.objects.create_user(
            email="unverified@example.com", password="test1234"
        )
        self.client.force_login(user)
        resp = self.client.get("/account/")
        content = resp.content.decode()
        self.assertIn("email-verification-banner", content)
        self.assertIn("Verify your email", content)

    def test_verified_user_does_not_see_banner(self):
        """Verified user does not see the verification banner."""
        user = User.objects.create_user(
            email="verified@example.com", password="test1234"
        )
        user.email_verified = True
        user.save(update_fields=["email_verified"])
        self.client.force_login(user)
        resp = self.client.get("/account/")
        content = resp.content.decode()
        self.assertNotIn("email-verification-banner", content)


# ── Account Page Change Password Section ──────────────────────────────


class AccountPageChangePasswordSectionTest(TestCase):
    """Tests for the change password section on the account page."""

    def test_change_password_section_exists(self):
        user = User.objects.create_user(
            email="cpw@example.com", password="test1234"
        )
        self.client.force_login(user)
        resp = self.client.get("/account/")
        content = resp.content.decode()
        self.assertIn("change-password-section", content)
        self.assertIn("Change Password", content)

    def test_change_password_form_exists(self):
        user = User.objects.create_user(
            email="cpw2@example.com", password="test1234"
        )
        self.client.force_login(user)
        resp = self.client.get("/account/")
        content = resp.content.decode()
        self.assertIn("change-password-form", content)
        self.assertIn("current-password", content)
        self.assertIn("new-password", content)


# ── Works Alongside OAuth ─────────────────────────────────────────────


class OAuthCoexistenceTest(TestCase):
    """Tests that email+password auth works alongside existing OAuth."""

    def test_email_registered_user_can_login(self):
        """User registered via email can authenticate with password."""
        User.objects.create_user(
            email="emailuser@example.com", password="mypass1234"
        )
        resp = self.client.post(
            "/api/login",
            data=json.dumps(
                {"email": "emailuser@example.com", "password": "mypass1234"}
            ),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)

    def test_oauth_user_without_password_cannot_login_with_password(self):
        """User without usable password (OAuth only) gets 401 on login API."""
        User.objects.create_user(email="oauthuser@example.com")
        resp = self.client.post(
            "/api/login",
            data=json.dumps(
                {"email": "oauthuser@example.com", "password": "anything"}
            ),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 401)

    def test_same_user_model_used(self):
        """Both email+password and OAuth users share the same User model."""
        from django.conf import settings
        self.assertEqual(settings.AUTH_USER_MODEL, "accounts.User")


# ── Unverified Users Access ───────────────────────────────────────────


class UnverifiedUserAccessTest(TestCase):
    """Tests that unverified users have same access as free tier."""

    def test_unverified_user_can_access_public_pages(self):
        """Unverified user can access the homepage."""
        user = User.objects.create_user(
            email="unv@example.com", password="test1234"
        )
        self.assertFalse(user.email_verified)
        self.client.force_login(user)
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)

    def test_unverified_user_can_access_account_page(self):
        """Unverified user can access their account page."""
        user = User.objects.create_user(
            email="unv2@example.com", password="test1234"
        )
        self.client.force_login(user)
        resp = self.client.get("/account/")
        self.assertEqual(resp.status_code, 200)
