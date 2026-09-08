"""Maven welcome email content tests (issue #960)."""

import datetime
from urllib.parse import parse_qs, urlparse

import jwt
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import resolve, reverse

from accounts.utils.tokens import JWT_ALGORITHM, resolve_password_reset_token
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
        # Issue #1593: the newsletter is offered, not asserted. The old copy
        # claimed the opposite ("we did NOT add you") and must not come back
        # in any form.
        self.assertNotIn("did not add", body_html.lower())
        self.assertIn("if you want to hear from us", body_html.lower())
        # Both tokened links + reply-to-remove line.
        self.assertIn(context["newsletter_opt_in_url"], body_html)
        self.assertIn(context["opt_out_url"], body_html)
        self.assertIn("reply", body_html.lower())

    def test_the_two_tokened_links_carry_distinct_and_accurate_labels(self):
        """Crossing these two links would put a false statement in the email.

        Issue #1593: "verify your email" must reach the opt-in that actually
        subscribes them, and "turn off course emails" must reach the scoped
        Maven opt-out. Each label has to describe what its own link does.
        """
        user = User.objects.create_user(email="news@test.com", password="x")
        context = _welcome_context(user, "Course")
        _subject, body_html = EmailService()._render_template(
            "maven_welcome", user, context,
        )

        opt_in_anchor = (
            f'<a href="{context["newsletter_opt_in_url"]}">verify your email</a>'
        )
        opt_out_anchor = (
            f'<a href="{context["opt_out_url"]}">turn off course emails</a>'
        )
        self.assertIn(opt_in_anchor, body_html)
        self.assertIn(opt_out_anchor, body_html)
        self.assertNotEqual(
            context["newsletter_opt_in_url"], context["opt_out_url"],
        )

    def test_newsletter_opt_in_url_uses_its_own_token_action(self):
        """A ``verify_email`` token must never be able to subscribe anyone."""
        started_at = datetime.datetime.now(datetime.timezone.utc)
        user = User.objects.create_user(email="nu@test.com", password="x")
        context = _welcome_context(user, "Course")
        self.assertIn(
            "/api/verify-and-subscribe?token=", context["newsletter_opt_in_url"],
        )

        payload = _decode_user_action_token(
            _extract_token(context["newsletter_opt_in_url"])
        )
        self.assertEqual(payload["user_id"], user.pk)
        self.assertEqual(payload["action"], "verify_and_subscribe")
        expires_at = datetime.datetime.fromtimestamp(
            payload["exp"],
            tz=datetime.timezone.utc,
        )
        # Thirty days, not a password-reset day. People act on a welcome email
        # late, and for an opt-in invitation the late click is the normal case
        # rather than an edge case (issue #1593).
        self.assertGreater(
            expires_at,
            started_at + datetime.timedelta(days=29, hours=23),
        )
        self.assertLess(
            expires_at,
            started_at + datetime.timedelta(days=30, minutes=1),
        )

    def test_welcome_offers_oauth_and_password_as_the_two_ways_in(self):
        """The copy promises social sign-in, so both routes must be linked."""
        user = User.objects.create_user(email="ways-in@test.com", password="x")
        context = _welcome_context(user, "Course")
        _subject, body_html = EmailService()._render_template(
            "maven_welcome", user, context,
        )

        self.assertIn("Google", body_html)
        self.assertIn("GitHub", body_html)
        self.assertIn("Slack", body_html)
        self.assertIn(f'<a href="{context["sign_in_url"]}">Sign in</a>', body_html)
        self.assertIn(
            f'<a href="{context["password_reset_url"]}">Set a password</a>',
            body_html,
        )

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
        resolved_user, _validated = resolve_password_reset_token(
            _extract_token(context["password_reset_url"])
        )
        self.assertEqual(resolved_user.pk, user.pk)
        self.assertGreater(
            expires_at,
            started_at + datetime.timedelta(hours=23, minutes=59),
        )
        self.assertLess(
            expires_at,
            started_at + datetime.timedelta(hours=24, minutes=1),
        )

    def test_named_enrollee_is_greeted_by_their_full_name(self):
        user = User.objects.create_user(
            email="ada@example.com",
            password="x",
            first_name="Ada",
            last_name="Lovelace",
        )

        context = _welcome_context(user, "Course")
        _subject, body_html = EmailService()._render_template(
            "maven_welcome", user, context,
        )

        self.assertIn("Hi Ada Lovelace,", body_html)

    def test_welcome_context_sets_no_user_name_key(self):
        """Issue #1591: caller context wins over the value EmailService
        injects, so an explicit ``user_name`` here would reinstate the
        email-handle greeting for nameless Maven enrollees."""
        user = User.objects.create_user(
            email="ada@example.com", password="x", first_name="Ada",
        )

        context = _welcome_context(user, "Course")

        self.assertNotIn("user_name", context)

    def test_nameless_enrollee_is_greeted_hi_there(self):
        """Maven regularly supplies no name at all. This is the exact email
        that opened issue #1591."""
        user = User.objects.create_user(
            email="x.arrieta@ibernova.com", password="x",
        )

        context = _welcome_context(user, "AI Engineering Buildcamp")
        _subject, body_html = EmailService()._render_template(
            "maven_welcome", user, context,
        )

        self.assertIn("Hi there,", body_html)
        self.assertNotIn("x.arrieta", body_html)

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
