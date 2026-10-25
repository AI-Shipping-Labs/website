"""Relay context and token coverage for Maven welcome mail (issue #960)."""

import datetime
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import jwt
from community_base.mail.jobs import deliver as deliver_job
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import resolve, reverse

from accounts.utils.tokens import JWT_ALGORITHM, resolve_password_reset_token
from email_app.package_mail import send_package_mail
from email_app.services.footer_context import VERIFY_FOOTER_TOKEN_EXPIRY_HOURS
from email_app.testing import StubRelayClient
from integrations.services.maven import (
    NEWSLETTER_OPT_IN_TOKEN_EXPIRY_HOURS,
    _welcome_context,
)

User = get_user_model()


def _relay_context(user, course="Course"):
    """Send one welcome and return the context resolved for Relay."""
    delivery = send_package_mail(user, "maven_welcome", _welcome_context(course))
    stub = StubRelayClient()
    with patch(
        "community_base.mail.backends.relay.configured_client",
        return_value=stub,
    ):
        deliver_job(None, {"delivery_id": str(delivery.id)})
    assert len(stub.calls) == 1, "maven_welcome was not sent"
    return stub.calls[0]["context"]


def _extract_token(url):
    return parse_qs(urlparse(url).query)["token"][0]


def _decode_user_action_token(token):
    return jwt.decode(
        token,
        settings.SECRET_KEY,
        algorithms=[JWT_ALGORITHM],
        options={"verify_exp": False},
    )


class MavenWelcomeRelayContextTest(TestCase):
    def test_unverified_welcome_has_only_the_consent_bearing_verify_link(self):
        user = User.objects.create_user(
            email="unverified@test.com", password="x", email_verified=False,
        )

        context = _relay_context(user)

        self.assertIn(
            "/api/verify-and-subscribe?token=",
            context["newsletter_opt_in_url"],
        )
        self.assertEqual(context["verify_email_url"], "")

    def test_worker_resolves_all_member_links(self):
        user = User.objects.create_user(
            email="enrollee@test.com", password="x", first_name="Sam",
        )

        context = _relay_context(user, "LLM Zoomcamp")

        self.assertEqual(context["course_name"], "LLM Zoomcamp")
        self.assertEqual(context["user_name"], "Sam")
        self.assertTrue(context["sign_in_url"].endswith("/accounts/login/"))
        self.assertTrue(context["onboarding_url"].endswith("/onboarding/"))
        self.assertTrue(context["slack_join_url"].endswith("/community/slack"))
        self.assertIn("/api/password-reset?token=", context["password_reset_url"])
        self.assertIn(
            "/api/verify-and-subscribe?token=", context["newsletter_opt_in_url"],
        )
        self.assertIn("/api/maven-email-opt-out?token=", context["opt_out_url"])

    def test_newsletter_opt_in_url_uses_scoped_long_lived_token(self):
        started_at = datetime.datetime.now(datetime.timezone.utc)
        user = User.objects.create_user(email="nu@test.com", password="x")

        token = _extract_token(_relay_context(user)["newsletter_opt_in_url"])
        payload = _decode_user_action_token(token)

        self.assertEqual(payload["user_id"], user.pk)
        self.assertEqual(payload["action"], "verify_and_subscribe")
        expires_at = datetime.datetime.fromtimestamp(
            payload["exp"], tz=datetime.timezone.utc,
        )
        self.assertGreater(
            expires_at, started_at + datetime.timedelta(days=29, hours=23),
        )
        self.assertLess(
            expires_at, started_at + datetime.timedelta(days=30, minutes=1),
        )
        self.assertGreater(
            NEWSLETTER_OPT_IN_TOKEN_EXPIRY_HOURS,
            VERIFY_FOOTER_TOKEN_EXPIRY_HOURS,
        )

    def test_opt_out_url_uses_scoped_maven_token(self):
        user = User.objects.create_user(email="o@test.com", password="x")

        token = _extract_token(_relay_context(user)["opt_out_url"])
        payload = _decode_user_action_token(token)

        self.assertEqual(payload["user_id"], user.pk)
        self.assertEqual(payload["action"], "maven_email_opt_out")
        self.assertNotIn("exp", payload)

    def test_password_reset_url_uses_day_lived_reset_token(self):
        started_at = datetime.datetime.now(datetime.timezone.utc)
        user = User.objects.create_user(email="reset@test.com", password="x")

        token = _extract_token(_relay_context(user)["password_reset_url"])
        payload = _decode_user_action_token(token)
        expires_at = datetime.datetime.fromtimestamp(
            payload["exp"], tz=datetime.timezone.utc,
        )

        self.assertEqual(payload["user_id"], user.pk)
        self.assertEqual(payload["action"], "password_reset")
        resolved_user, _validated = resolve_password_reset_token(token)
        self.assertEqual(resolved_user.pk, user.pk)
        self.assertGreater(
            expires_at, started_at + datetime.timedelta(hours=23, minutes=59),
        )
        self.assertLess(
            expires_at, started_at + datetime.timedelta(hours=24, minutes=1),
        )

    def test_worker_greeting_uses_name_or_safe_fallback(self):
        named = User.objects.create_user(
            email="ada@example.com", password="x",
            first_name="Ada", last_name="Lovelace",
        )
        nameless = User.objects.create_user(
            email="x.arrieta@ibernova.com", password="x",
        )

        self.assertEqual(_relay_context(named)["user_name"], "Ada Lovelace")
        self.assertEqual(_relay_context(nameless)["user_name"], "there")
        self.assertNotIn("user_name", _welcome_context("Course"))

    def test_sign_in_url_points_to_resolvable_login_route(self):
        user = User.objects.create_user(email="signin@test.com", password="x")

        path = urlparse(_relay_context(user)["sign_in_url"]).path

        self.assertEqual(path, reverse("account_login"))
        self.assertIsNotNone(resolve(path))
