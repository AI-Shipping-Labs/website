"""Maven welcome email content tests (issue #960).

Since A1.2 slice 3 the welcome is a durable package send: the producer
persists the course scalar only and the worker resolver mints every link
and token at delivery time (issue #1647). So these tests drain a real
``EmailDelivery`` through the worker and assert on the exact HTML SES
would receive — the same provider-visible surface the old synchronous
tests pinned.
"""

import datetime
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import jwt
from community_base.mail.jobs import deliver as deliver_job
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import resolve, reverse

from accounts.utils.tokens import (
    JWT_ALGORITHM,
    resolve_password_reset_token,
)
from email_app.package_mail import send_package_mail
from email_app.services.email_service import VERIFY_FOOTER_TOKEN_EXPIRY_HOURS
from email_app.testing import StubSESClient
from integrations.services.maven import (
    NEWSLETTER_OPT_IN_TOKEN_EXPIRY_HOURS,
    _welcome_context,
)

User = get_user_model()


def _welcome_html(user, course="Course"):
    """Send one welcome through the package and drain the worker."""

    delivery = send_package_mail(
        user, "maven_welcome", _welcome_context(course),
    )
    stub = StubSESClient()
    with patch(
        "community_base.mail.backends.ses_local.configured_client",
        return_value=stub,
    ):
        deliver_job(None, {"delivery_id": str(delivery.id)})
    assert len(stub.calls) == 1, "maven_welcome was not sent"
    simple = stub.calls[0]["Content"]["Simple"]
    return simple["Subject"]["Data"], simple["Body"]["Html"]["Data"]


def _welcome_url(body_html, prefix):
    import re

    match = re.search(rf'href="([^"]*{re.escape(prefix)}[^"]*)"', body_html)
    assert match, f"maven_welcome renders no {prefix} link"
    return match.group(1)


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
        subject, body_html = _welcome_html(user, "LLM Zoomcamp")

        # Course-framed: names the course.
        self.assertIn("LLM Zoomcamp", subject)
        self.assertIn("LLM Zoomcamp", body_html)
        # Sign-in / set-password CTA.
        self.assertIn("/accounts/login/", body_html)
        self.assertIn("/api/password-reset?token=", body_html)
        # Transparent notice that they were added for course communication.
        self.assertIn("community access", body_html.lower())
        # Issue #1593: the newsletter is offered, not asserted. The old copy
        # claimed the opposite ("we did NOT add you") and must not come back
        # in any form.
        self.assertNotIn("did not add", body_html.lower())
        self.assertIn("if you want to hear from us", body_html.lower())
        # Both tokened links + reply-to-remove line.
        self.assertIn("/api/verify-and-subscribe?token=", body_html)
        self.assertIn("/api/maven-email-opt-out?token=", body_html)
        self.assertIn("reply", body_html.lower())

    def test_the_two_tokened_links_carry_distinct_and_accurate_labels(self):
        """Crossing these two links would put a false statement in the email.

        Issue #1593: "verify your email" must reach the opt-in that actually
        subscribes them, and "turn off course emails" must reach the scoped
        Maven opt-out. Each label has to describe what its own link does.
        """
        user = User.objects.create_user(email="news@test.com", password="x")
        _subject, body_html = _welcome_html(user)

        opt_in_url = _welcome_url(body_html, "/api/verify-and-subscribe?token=")
        opt_out_url = _welcome_url(body_html, "/api/maven-email-opt-out?token=")
        self.assertIn(f'<a href="{opt_in_url}">verify your email</a>', body_html)
        self.assertIn(
            f'<a href="{opt_out_url}">turn off course emails</a>', body_html,
        )
        self.assertNotEqual(opt_in_url, opt_out_url)

    def test_newsletter_opt_in_url_uses_its_own_token_action(self):
        """A ``verify_email`` token must never be able to subscribe anyone."""
        started_at = datetime.datetime.now(datetime.timezone.utc)
        user = User.objects.create_user(email="nu@test.com", password="x")
        _subject, body_html = _welcome_html(user)
        opt_in_url = _welcome_url(body_html, "/api/verify-and-subscribe?token=")
        self.assertIn("/api/verify-and-subscribe?token=", opt_in_url)

        payload = _decode_user_action_token(_extract_token(opt_in_url))
        self.assertEqual(payload["user_id"], user.pk)
        self.assertEqual(payload["action"], "verify_and_subscribe")
        expires_at = datetime.datetime.fromtimestamp(
            payload["exp"],
            tz=datetime.timezone.utc,
        )
        # Thirty days. An unsolicited welcome is opened on the reader's
        # schedule, not ours, and this token has no resend path (issue #1593).
        self.assertGreater(
            expires_at,
            started_at + datetime.timedelta(days=29, hours=23),
        )
        self.assertLess(
            expires_at,
            started_at + datetime.timedelta(days=30, minutes=1),
        )
        # The consent-bearing link must not be the first one in the email to
        # die. Before #1593 it expired in 24h while the incidental footer
        # verify link lived 7 days, so from day two the only working "verify"
        # link in the message was the one that does not subscribe.
        self.assertGreater(
            NEWSLETTER_OPT_IN_TOKEN_EXPIRY_HOURS,
            VERIFY_FOOTER_TOKEN_EXPIRY_HOURS,
        )

    def test_welcome_offers_oauth_and_password_as_the_two_ways_in(self):
        """The copy promises social sign-in, so both routes must be linked."""
        user = User.objects.create_user(email="ways-in@test.com", password="x")
        _subject, body_html = _welcome_html(user)

        self.assertIn("Google", body_html)
        self.assertIn("GitHub", body_html)
        self.assertIn("Slack", body_html)
        sign_in_url = _welcome_url(body_html, "/accounts/login/")
        reset_url = _welcome_url(body_html, "/api/password-reset?token=")
        self.assertIn(f'<a href="{sign_in_url}">Sign in</a>', body_html)
        self.assertIn(
            f'<a href="{reset_url}">Set a password</a>', body_html,
        )

    def test_opt_out_url_uses_scoped_maven_token_endpoint(self):
        user = User.objects.create_user(email="o@test.com", password="x")
        _subject, body_html = _welcome_html(user)
        opt_out_url = _welcome_url(body_html, "/api/maven-email-opt-out?token=")
        self.assertIn("/api/maven-email-opt-out?token=", opt_out_url)

        payload = _decode_user_action_token(_extract_token(opt_out_url))
        self.assertEqual(payload["user_id"], user.pk)
        self.assertEqual(payload["action"], "maven_email_opt_out")
        self.assertNotIn("exp", payload)

    def test_password_reset_url_uses_password_reset_token_with_day_expiry(self):
        started_at = datetime.datetime.now(datetime.timezone.utc)
        user = User.objects.create_user(email="reset@test.com", password="x")
        _subject, body_html = _welcome_html(user)
        reset_token = _extract_token(
            _welcome_url(body_html, "/api/password-reset?token="),
        )
        payload = _decode_user_action_token(reset_token)
        expires_at = datetime.datetime.fromtimestamp(
            payload["exp"],
            tz=datetime.timezone.utc,
        )

        self.assertEqual(payload["user_id"], user.pk)
        self.assertEqual(payload["action"], "password_reset")
        resolved_user, _validated = resolve_password_reset_token(reset_token)
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

        _subject, body_html = _welcome_html(user)

        self.assertIn("Hi Ada Lovelace,", body_html)

    def test_welcome_context_sets_no_user_name_key(self):
        """Issue #1591: the greeting is resolved by the worker from the
        recipient (``greeting_name``), so an explicit ``user_name`` in the
        durable context would reinstate the email-handle greeting for
        nameless Maven enrollees."""
        user = User.objects.create_user(
            email="ada@example.com", password="x", first_name="Ada",
        )

        context = _welcome_context("Course")

        self.assertNotIn("user_name", context)
        # And the worker injects the #1591 greeting at delivery time.
        _subject, body_html = _welcome_html(user)
        self.assertIn("Hi Ada,", body_html)

    def test_nameless_enrollee_is_greeted_hi_there(self):
        """Maven regularly supplies no name at all. This is the exact email
        that opened issue #1591."""
        user = User.objects.create_user(
            email="x.arrieta@ibernova.com", password="x",
        )

        _subject, body_html = _welcome_html(
            user, "AI Engineering Buildcamp",
        )

        self.assertIn("Hi there,", body_html)
        self.assertNotIn("x.arrieta", body_html)

    def test_sign_in_url_points_to_resolvable_login_route(self):
        """Regression for #960: the sign-in link must be /accounts/login/,
        which actually resolves. The old /login/ raised Resolver404."""
        user = User.objects.create_user(email="signin@test.com", password="x")
        _subject, body_html = _welcome_html(user)

        path = urlparse(_welcome_url(body_html, "/accounts/login/")).path
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
        _subject, body_html = _welcome_html(user, "LLM Zoomcamp")

        self.assertIn('href="https://aishippinglabs.com/accounts/login/"', body_html)
        # The authoring comment must never reach the rendered output.
        self.assertNotIn("DRAFT COPY", body_html)
        self.assertNotIn("issue #960", body_html)
