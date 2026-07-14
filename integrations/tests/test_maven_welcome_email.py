"""Maven welcome email content tests (issue #960)."""

import datetime
from urllib.parse import parse_qs, urlparse

import jwt
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import resolve, reverse

from accounts.utils.tokens import JWT_ALGORITHM
from email_app.services.email_service import EmailService
from integrations.services.maven import _welcome_context

User = get_user_model()


def _extract_token(url):
    return parse_qs(urlparse(url).query)["token"][0]


def _decode_user_action_token(token):
    return jwt.decode(
        token,
        settings.SECRET_KEY,
        algorithms=[JWT_ALGORITHM],
        options={"verify_exp": False},
    )


class MavenWelcomeEmailContentTest(TestCase):
    def test_welcome_email_is_course_framed_with_consent_and_opt_out(self):
        user = User.objects.create_user(
            email="enrollee@test.com", password="x", first_name="Sam",
        )
        context = _welcome_context(user, "LLM Zoomcamp")
        subject, body_html = EmailService()._render_template(
            "maven_welcome", user, context,
        )

        # Course-framed: names the course.
        self.assertIn("LLM Zoomcamp", subject)
        self.assertIn("LLM Zoomcamp", body_html)
        # Sign-in / set-password CTA.
        self.assertIn(context["sign_in_url"], body_html)
        self.assertIn(context["password_reset_url"], body_html)
        # Transparent notice that they were added for course communication.
        self.assertIn("community access", body_html.lower())
        self.assertIn("did not add you to any marketing", body_html.lower())
        # Opt-out link + reply-to-remove line.
        self.assertIn(context["opt_out_url"], body_html)
        self.assertIn("reply", body_html.lower())

    def test_opt_out_url_uses_scoped_maven_token_endpoint(self):
        user = User.objects.create_user(email="o@test.com", password="x")
        context = _welcome_context(user, "Course")
        self.assertIn("/api/maven-email-opt-out?token=", context["opt_out_url"])

        payload = _decode_user_action_token(_extract_token(context["opt_out_url"]))
        self.assertEqual(payload["user_id"], user.pk)
        self.assertEqual(payload["action"], "maven_email_opt_out")
        self.assertNotIn("exp", payload)

    def test_password_reset_url_uses_password_reset_token_with_day_expiry(self):
        started_at = datetime.datetime.now(datetime.timezone.utc)
        user = User.objects.create_user(email="reset@test.com", password="x")
        context = _welcome_context(user, "Course")
        payload = _decode_user_action_token(
            _extract_token(context["password_reset_url"])
        )
        expires_at = datetime.datetime.fromtimestamp(
            payload["exp"],
            tz=datetime.timezone.utc,
        )

        self.assertEqual(payload["user_id"], user.pk)
        self.assertEqual(payload["action"], "password_reset")
        self.assertGreater(
            expires_at,
            started_at + datetime.timedelta(hours=23, minutes=59),
        )
        self.assertLess(
            expires_at,
            started_at + datetime.timedelta(hours=24, minutes=1),
        )

    def test_welcome_context_uses_canonical_display_name(self):
        user = User.objects.create_user(
            email="ada@example.com",
            password="x",
            first_name="Ada",
            last_name="Lovelace",
        )

        context = _welcome_context(user, "Course")

        self.assertEqual(context["user_name"], "Ada Lovelace")

    def test_sign_in_url_points_to_resolvable_login_route(self):
        """Regression for #960: the sign-in link must be /accounts/login/,
        which actually resolves. The old /login/ raised Resolver404."""
        user = User.objects.create_user(email="signin@test.com", password="x")
        context = _welcome_context(user, "Course")

        path = urlparse(context["sign_in_url"]).path
        self.assertEqual(path, "/accounts/login/")
        # The path must resolve via Django's URL resolver (no 404).
        self.assertEqual(path, reverse("account_login"))
        self.assertIsNotNone(resolve(path))

    def test_rendered_email_contains_login_link_and_no_draft_comment(self):
        """The rendered welcome email links to /accounts/login/ and must not
        leak the DRAFT COPY authoring comment into the body (#960)."""
        user = User.objects.create_user(
            email="render@test.com", password="x", first_name="Sam",
        )
        context = _welcome_context(user, "LLM Zoomcamp")
        _subject, body_html = EmailService()._render_template(
            "maven_welcome", user, context,
        )

        self.assertIn('href="https://aishippinglabs.com/accounts/login/"', body_html)
        # The authoring comment must never reach the rendered output.
        self.assertNotIn("DRAFT COPY", body_html)
        self.assertNotIn("issue #960", body_html)
