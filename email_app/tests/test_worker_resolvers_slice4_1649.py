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
from email_app.testing import StubRelayClient
from integrations.config import clear_config_cache
from integrations.models import IntegrationSetting

BASE_URL = "https://site.test"


def _drain_context(delivery):
    """Deliver one row through the worker and return Relay's resolved context."""


    stub = StubRelayClient()
    with (
        patch(
            "community_base.mail.backends.relay.configured_client",
            return_value=stub,
        ),
    ):
        deliver_job(None, {"delivery_id": str(delivery.id)})
    assert len(stub.calls) == 1
    call = stub.calls[0]
    assert call["delivery"].pk == delivery.pk
    return call["context"]


def _drain_expect_permanent_error(test, delivery, reason):
    """Drain one delivery and assert it fails closed with ``reason``."""

    with patch(
        "community_base.mail.backends.relay.configured_client",
        return_value=StubRelayClient(),
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

        context = _drain_context(delivery)
        self.assertEqual(context["verify_url"], context["download_url"])
        payload = _extract_token_payload(context["verify_url"])
        self.assertEqual(payload["action"], "verify_email")
        self.assertEqual(
            payload["return_path"], "/downloads/ai-cheat-sheet/file",
        )
        self.assertEqual(context["resource_title"], "your resource")
        self.assertEqual(context["user_name"], "Ada")

    def test_nameless_member_gets_the_1591_greeting(self):
        member = self._member("nameless.magnet@test.com")
        delivery = self._send(member, {
            "resource_title": "your resource",
            "return_path": "/downloads/ai-cheat-sheet/file",
            "ttl_days": 7,
        })

        context = _drain_context(delivery)
        self.assertEqual(context["user_name"], "there")

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

        context = _drain_context(delivery)
        self.assertEqual(context["sign_in_url"], f"{BASE_URL}/login/")
        match = re.search(r"token=([A-Za-z0-9_.\-]+)", context["password_reset_url"])
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
        self.assertEqual(context["user_name"], "Grace")
        self.assertEqual(context["import_tags"], "datatalks-alumni")

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

        context = _drain_context(delivery)
        self.assertEqual(context["user_name"], "there")

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

        context = _drain_context(delivery)
        match = re.search(r"token=([A-Za-z0-9_.\-]+)", context["verify_url"])
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

        context = _drain_context(delivery)
        self.assertEqual(context["user_name"], "there")
        self.assertIn("/api/password-reset?token=", context["reset_url"])

    def test_named_member_keeps_their_name(self):
        member = User.objects.create_user(
            email="named.auth@test.com", first_name="Ada",
        )
        delivery = send_package_mail(member, "password_reset", {})

        context = _drain_context(delivery)
        self.assertEqual(context["user_name"], "Ada")


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
            {"event_title": "AI Shipping Workshop", "user_name": "Operator"},
            category=STUDIO_TEST_SEND_CATEGORY,
        )

        self.assertEqual(delivery.category, STUDIO_TEST_SEND_CATEGORY)
        context = _drain_context(delivery)
        self.assertEqual(context["event_title"], "AI Shipping Workshop")
        self.assertEqual(context["user_name"], "Operator")
        self.assertNotIn("recap_url", delivery.context_data)
        self.assertNotIn("recap_url", context)

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

        context = _drain_context(delivery)
        self.assertTrue(context["verify_url"].startswith(
            "https://override.example.com/api/verify-email?token=",
        ))
        self.assertEqual(context["verify_url"], context["download_url"])

    def test_subscribe_verify_link_strips_the_override(self):
        _set_site_url_override("https://override.example.com/")
        member = User.objects.create_user(email="slash.subscribe@test.com")
        delivery = send_package_mail(
            member, "email_verification_subscribe", {"ttl_days": 7},
        )

        context = _drain_context(delivery)
        self.assertTrue(context["verify_url"].startswith(
            "https://override.example.com/api/verify-email?token=",
        ))

    def test_password_reset_link_strips_the_override(self):
        _set_site_url_override("https://override.example.com/")
        member = User.objects.create_user(email="slash.reset@test.com")
        delivery = send_package_mail(member, "password_reset", {})

        context = _drain_context(delivery)
        self.assertTrue(context["reset_url"].startswith(
            "https://override.example.com/api/password-reset?token=",
        ))
