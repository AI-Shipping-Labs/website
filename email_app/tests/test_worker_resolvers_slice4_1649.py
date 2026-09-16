"""Worker resolvers for the slice-4 operator surfaces (issue #1649).

The newsletter, the imported-member welcome and the Studio test send
persist scalar-only contexts (#1613). These tests drain real
``EmailDelivery`` rows through the worker
(``email_app.hooks.resolve_auth_mail_context``) and assert the
provider-visible email carries the exact links the old synchronous send
rendered — and that a delivery without its recipient fails closed with a
``PermanentJobError`` reason code instead of mailing dead links.
"""

import datetime
import re
from unittest.mock import patch

import jwt
from community_base.jobs.runner import PermanentJobError
from community_base.mail.jobs import deliver as deliver_job
from django.conf import settings
from django.test import TestCase, override_settings, tag

from accounts.models import User
from accounts.utils.tokens import JWT_ALGORITHM
from email_app.hooks import STUDIO_TEST_SEND_CATEGORY
from email_app.package_mail import send_package_mail
from email_app.testing import StubSESClient
from integrations.config import clear_config_cache
from integrations.models import IntegrationSetting

BASE_URL = "https://site.test"


def _drain_html(delivery):
    """Deliver one delivery through the real worker; return (subject, html)."""

    stub = StubSESClient()
    with patch(
        "community_base.mail.backends.ses_local.configured_client",
        return_value=stub,
    ):
        deliver_job(None, {"delivery_id": str(delivery.id)})
    assert len(stub.calls) == 1
    call = stub.calls[0]
    simple = call["Content"]["Simple"]
    return simple["Subject"]["Data"], simple["Body"]["Html"]["Data"]


def _drain_expect_permanent_error(test, delivery, reason):
    """Drain one delivery and assert it fails closed with ``reason``."""

    with patch(
        "community_base.mail.backends.ses_local.configured_client",
        return_value=StubSESClient(),
    ):
        with test.assertRaises(PermanentJobError) as caught:
            deliver_job(None, {"delivery_id": str(delivery.id)})
    test.assertEqual(caught.exception.code, reason)


def _extract_token_payload(url):
    token = re.search(r"token=([A-Za-z0-9_.\-]+)", url).group(1)
    return jwt.decode(
        token,
        settings.SECRET_KEY,
        algorithms=[JWT_ALGORITHM],
        options={"verify_exp": False},
    )


def _set_site_url_override(value):
    """Store a ``SITE_BASE_URL`` DB override exactly as entered.

    The Studio package-settings form saves the value verbatim, so a
    trailing slash reaches the worker resolvers (issue #1649 QA).
    """

    IntegrationSetting.objects.create(
        key="SITE_BASE_URL", value=value, group="site",
    )
    clear_config_cache()


@tag("core")
@override_settings(SITE_BASE_URL=BASE_URL)
class LeadMagnetResolverTest(TestCase):
    def _member(self, email, **fields):
        return User.objects.create_user(
            email=email, password="pw", **fields,
        )

    def _send(self, user, context):
        return send_package_mail(user, "lead_magnet_delivery", context)

    def test_worker_mints_verify_and_download_links_with_redirect(self):
        member = self._member("magnet@test.com", first_name="Ada")
        delivery = self._send(member, {
            "resource_title": "your resource",
            "return_path": "/downloads/ai-cheat-sheet/file",
            "ttl_days": 7,
        })

        # No bearer link ever touches the durable row (#1613).
        for url_key in ("verify_url", "download_url"):
            self.assertNotIn(url_key, delivery.context_data)

        _subject, html = _drain_html(delivery)
        verify_urls = re.findall(
            rf"{re.escape(BASE_URL)}/api/verify-email\?token=[A-Za-z0-9_.\-]+",
            html,
        )
        # The body carries the download CTA and the verify link points at
        # the same redirect.
        self.assertGreaterEqual(len(verify_urls), 1)
        payload = _extract_token_payload(verify_urls[0])
        self.assertEqual(payload["action"], "verify_email")
        self.assertEqual(
            payload["return_path"], "/downloads/ai-cheat-sheet/file",
        )
        self.assertIn("your resource", html)
        self.assertIn("Hi Ada,", html)

    def test_nameless_member_gets_the_1591_greeting(self):
        member = self._member("nameless.magnet@test.com")
        delivery = self._send(member, {
            "resource_title": "your resource",
            "return_path": "/downloads/ai-cheat-sheet/file",
            "ttl_days": 7,
        })

        _subject, html = _drain_html(delivery)
        self.assertIn("Hi there,", html)
        self.assertNotIn("Hi nameless.magnet,", html)
        self.assertNotIn("Hi ,", html)

    def test_delivery_without_recipient_fails_closed(self):
        delivery = send_package_mail(
            None,
            "lead_magnet_delivery",
            {"resource_title": "your resource", "ttl_days": 7},
            recipient_email="surrogate@test.com",
        )
        _drain_expect_permanent_error(
            self, delivery, "lead_magnet_recipient_missing",
        )


@tag("core")
@override_settings(SITE_BASE_URL=BASE_URL)
class WelcomeImportedResolverTest(TestCase):
    def _send(self, user, context):
        return send_package_mail(user, "welcome_imported", context)

    def test_worker_mints_reset_and_signin_links(self):
        user = User.objects.create_user(
            email="imported@test.com", first_name="Grace",
        )
        delivery = self._send(user, {
            "source_label": "DataTalks Club",
            "import_tags": "datatalks-alumni",
            "is_course_db_import": False,
            "is_slack_import": False,
            "course_slugs": [],
            "course_slug_list": "",
        })

        self.assertNotIn("password_reset_url", delivery.context_data)
        self.assertNotIn("sign_in_url", delivery.context_data)

        _subject, html = _drain_html(delivery)
        self.assertIn(f"{BASE_URL}/login/", html)
        match = re.search(
            rf"{re.escape(BASE_URL)}/api/password-reset\?token=([A-Za-z0-9_.\-]+)",
            html,
        )
        self.assertIsNotNone(match)
        payload = jwt.decode(
            match.group(1),
            settings.SECRET_KEY,
            algorithms=[JWT_ALGORITHM],
            options={"verify_exp": False},
        )
        self.assertEqual(payload["action"], "password_reset")
        expires_at = datetime.datetime.fromtimestamp(
            payload["exp"], tz=datetime.timezone.utc,
        )
        # The imported-member mint used a one-hour token; the resolver
        # keeps it.
        lifetime = expires_at - datetime.datetime.now(datetime.timezone.utc)
        self.assertGreater(lifetime, datetime.timedelta(minutes=59))
        self.assertLess(lifetime, datetime.timedelta(hours=1, minutes=1))
        self.assertIn("Hi Grace,", html)
        self.assertIn("datatalks-alumni", html)

    def test_nameless_imported_member_gets_the_1591_greeting(self):
        user = User.objects.create_user(email="nameless.import@test.com")
        delivery = self._send(user, {
            "source_label": "Manual / self signup",
            "import_tags": "",
            "is_course_db_import": False,
            "is_slack_import": False,
            "course_slugs": [],
            "course_slug_list": "",
        })

        _subject, html = _drain_html(delivery)
        self.assertIn("Hi there,", html)
        self.assertNotIn("Hi nameless.import,", html)

    def test_delivery_without_recipient_fails_closed(self):
        delivery = send_package_mail(
            None,
            "welcome_imported",
            {"source_label": "DataTalks Club"},
            recipient_email="surrogate@test.com",
        )
        _drain_expect_permanent_error(
            self, delivery, "welcome_imported_user_missing",
        )


@tag("core")
@override_settings(SITE_BASE_URL=BASE_URL)
class SubscribeVerificationResolverTest(TestCase):
    def test_worker_mints_the_subscribe_verify_link(self):
        member = User.objects.create_user(
            email="subscribe-render@test.com",
        )
        delivery = send_package_mail(
            member,
            "email_verification_subscribe",
            {"ttl_days": 7},
        )

        self.assertNotIn("verify_url", delivery.context_data)

        _subject, html = _drain_html(delivery)
        match = re.search(
            rf"{re.escape(BASE_URL)}/api/verify-email\?token=([A-Za-z0-9_.\-]+)",
            html,
        )
        self.assertIsNotNone(match)
        payload = jwt.decode(
            match.group(1),
            settings.SECRET_KEY,
            algorithms=[JWT_ALGORITHM],
            options={"verify_exp": False},
        )
        self.assertEqual(payload["action"], "verify_email")
        self.assertNotIn("return_path", payload)
        expires_at = datetime.datetime.fromtimestamp(
            payload["exp"], tz=datetime.timezone.utc,
        )
        # The newsletter mint used a 24-hour token; the resolver keeps it.
        self.assertLess(
            expires_at - datetime.datetime.now(datetime.timezone.utc),
            datetime.timedelta(hours=25),
        )
        self.assertGreater(
            expires_at - datetime.datetime.now(datetime.timezone.utc),
            datetime.timedelta(hours=23),
        )


@tag("core")
@override_settings(SITE_BASE_URL=BASE_URL)
class AuthLinkGreetingTest(TestCase):
    """The auth sends keep the issue #1591 greeting rule in the worker.

    The synchronous renderer injected ``greeting_name`` (never an email
    handle, ``there`` when nameless) for every template; the package's
    built-in display name has no nameless fallback, so the auth resolver
    restores it — a nameless member must receive ``Hi there,``, not
    ``Hi ,``.
    """

    def test_password_reset_greets_a_nameless_member(self):
        member = User.objects.create_user(email="nameless.auth@test.com")
        delivery = send_package_mail(member, "password_reset", {})

        _subject, html = _drain_html(delivery)
        self.assertIn("Hi there,", html)
        self.assertNotIn("Hi nameless.auth,", html)
        self.assertNotIn("Hi ,", html)
        self.assertIn("/api/password-reset?token=", html)

    def test_named_member_keeps_their_name(self):
        member = User.objects.create_user(
            email="named.auth@test.com", first_name="Ada",
        )
        delivery = send_package_mail(member, "password_reset", {})

        _subject, html = _drain_html(delivery)
        self.assertIn("Hi Ada,", html)
        self.assertNotIn("Hi there,", html)


@tag("core")
@override_settings(SITE_BASE_URL=BASE_URL)
class StudioTestSendCategoryTest(TestCase):
    """The test-send category keeps a probe delivery deliverable.

    Relation-dependent purposes fail closed for real sends (a stale
    relation must never deliver a broken link), but a Studio test send
    has no relation at all — its delivery must still land so the
    operator learns the deliverability outcome instead of stranding a
    permanently failing row.
    """

    def test_test_send_of_relation_purpose_delivers_without_minting(self):
        member = User.objects.create_user(
            email="operator-probe@test.com", first_name="Operator",
        )
        delivery = send_package_mail(
            member,
            "event_recap_ready",
            {"event_title": "AI Shipping Workshop"},
            category=STUDIO_TEST_SEND_CATEGORY,
        )

        self.assertEqual(delivery.category, STUDIO_TEST_SEND_CATEGORY)
        subject, html = _drain_html(delivery)
        self.assertIn("AI Shipping Workshop", html)
        self.assertIn("Hi Operator,", html)
        self.assertNotIn("recap_url", delivery.context_data)
        # No relation-minted link renders.
        self.assertNotIn("/events/", html)

    def test_same_purpose_without_the_category_fails_closed(self):
        member = User.objects.create_user(email="real-send@test.com")
        delivery = send_package_mail(
            member,
            "event_recap_ready",
            {"event_title": "AI Shipping Workshop"},
        )
        _drain_expect_permanent_error(
            self, delivery, "recap_event_missing",
        )


@tag("core")
@override_settings(SITE_BASE_URL=BASE_URL)
class TrailingSlashSiteUrlOverrideTest(TestCase):
    """A trailing-slash ``SITE_BASE_URL`` override must not double the slash.

    Nothing normalizes the override at save time, so the worker
    resolvers can see ``https://host/``; every minted link must
    ``rstrip('/')`` the base, or the rendered URL becomes
    ``https://host//api/...`` (issue #1649 QA).
    """

    def setUp(self):
        clear_config_cache()

    def tearDown(self):
        clear_config_cache()

    def test_lead_magnet_verify_and_download_links_strip_the_override(self):
        _set_site_url_override("https://override.example.com/")
        member = User.objects.create_user(
            email="slash.magnet@test.com", first_name="Ada",
        )
        delivery = send_package_mail(member, "lead_magnet_delivery", {
            "resource_title": "your resource",
            "return_path": "/downloads/ai-cheat-sheet/file",
            "ttl_days": 7,
        })

        _subject, html = _drain_html(delivery)
        # Scoped to the minted link: this template's footer also renders
        # ``{{ site_url }}/tutorials/`` from the render-only injection,
        # which is not the resolver mint under test.
        self.assertIn(
            "https://override.example.com/api/verify-email?token=", html,
        )
        self.assertNotIn(
            "https://override.example.com//api/verify-email?token=", html,
        )

    def test_subscribe_verify_link_strips_the_override(self):
        _set_site_url_override("https://override.example.com/")
        member = User.objects.create_user(email="slash.subscribe@test.com")
        delivery = send_package_mail(
            member, "email_verification_subscribe", {"ttl_days": 7},
        )

        _subject, html = _drain_html(delivery)
        self.assertIn(
            "https://override.example.com/api/verify-email?token=", html,
        )
        self.assertNotIn("example.com//", html)

    def test_password_reset_link_strips_the_override(self):
        _set_site_url_override("https://override.example.com/")
        member = User.objects.create_user(email="slash.reset@test.com")
        delivery = send_package_mail(member, "password_reset", {})

        _subject, html = _drain_html(delivery)
        self.assertIn(
            "https://override.example.com/api/password-reset?token=", html,
        )
        self.assertNotIn("example.com//", html)
