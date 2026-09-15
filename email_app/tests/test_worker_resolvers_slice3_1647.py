"""Worker resolvers for the slice-3 download and Maven mail (issue #1647).

The two producers persist scalar-only contexts plus the natural relation
(issue #1613). These tests drain real ``EmailDelivery`` rows through the
worker (``email_app.hooks.resolve_auth_mail_context``) and assert the
provider-visible email carries the exact links and tokens the old
synchronous send rendered — and that a stale relation fails closed with a
``PermanentJobError`` reason code instead of delivering a broken link.
"""

import datetime
from urllib.parse import parse_qs, urlparse

import jwt
from community_base.jobs.runner import PermanentJobError
from community_base.mail.jobs import deliver as deliver_job
from django.conf import settings
from django.test import TestCase, override_settings, tag
from django.utils import timezone

from accounts.models import User
from accounts.utils.tokens import (
    JWT_ALGORITHM,
    resolve_password_reset_token,
)
from content.models import Download, DownloadDeliveryGrant
from content.services.download_delivery import (
    get_delivery_token_ttl_hours,
    unpack_delivery_grant_token,
)
from email_app.package_mail import send_package_mail
from email_app.testing import StubSESClient
from integrations.config import clear_config_cache
from integrations.models import IntegrationSetting
from integrations.services.maven import (
    NEWSLETTER_OPT_IN_TOKEN_EXPIRY_HOURS,
    _welcome_context,
)

BASE_URL = "https://site.test"


def _drain_html(delivery):
    """Deliver one delivery through the real worker; return (subject, html)."""

    stub = StubSESClient()
    with patch_configured_client(stub):
        deliver_job(None, {"delivery_id": str(delivery.id)})
    assert len(stub.calls) == 1
    call = stub.calls[0]
    simple = call["Content"]["Simple"]
    return simple["Subject"]["Data"], simple["Body"]["Html"]["Data"]


def patch_configured_client(stub):
    from unittest.mock import patch  # noqa: PLC0415

    return patch(
        "community_base.mail.backends.ses_local.configured_client",
        return_value=stub,
    )


def _deliver_expect_permanent_error(test, delivery, reason):
    """Drain one delivery and assert it fails closed with ``reason``."""

    with patch_configured_client(StubSESClient()):
        with test.assertRaises(PermanentJobError) as caught:
            deliver_job(None, {"delivery_id": str(delivery.id)})
    test.assertEqual(caught.exception.code, reason)


def _decode_token(token):
    return jwt.decode(
        token,
        settings.SECRET_KEY,
        algorithms=[JWT_ALGORITHM],
        options={"verify_exp": False},
    )


def _extract_query_value(url, key):
    return parse_qs(urlparse(url).query)[key][0]


def make_download(**overrides):
    values = {
        "title": "AI field guide",
        "slug": "ai-field-guide",
        "description": "A practical field guide.",
        "file_url": (
            "https://downloads-test.s3.eu-central-1.amazonaws.com"
            "/downloads/ai-field-guide.pdf"
        ),
        "storage_key": "downloads/ai-field-guide.pdf",
        "file_type": "pdf",
        "asset_mime_type": "application/pdf",
        "file_size_bytes": 1024,
        "required_level": 0,
        "published": True,
    }
    values.update(overrides)
    return Download.objects.create(**values)


@tag("core")
@override_settings(SITE_BASE_URL=BASE_URL)
class DownloadDeliveryResolverTest(TestCase):
    def _send(self, user, download, **context_overrides):
        context = {
            "resource_title": download.title,
            "newsletter_opt_in": False,
            "surface": "detail",
        }
        context.update(context_overrides)
        return send_package_mail(
            user,
            "download_delivery",
            context,
            related=download,
        )

    def test_worker_mints_a_live_grant_link_for_a_verified_recipient(self):
        download = make_download()
        user = User.objects.create_user(
            email="verified-download@test.com",
            password="pw",
            email_verified=True,
        )

        delivery = self._send(
            user, download, newsletter_opt_in=True, surface="shortcode",
        )

        # No rendered link or TTL copy ever touches the durable row.
        for url_key in ("delivery_url", "expires_hours", "site_url"):
            self.assertNotIn(url_key, delivery.context_data)

        _subject, html = _drain_html(delivery)

        self.assertIn(
            f"{BASE_URL}/api/downloads/{download.slug}/file?grant=", html,
        )
        self.assertNotIn("/api/verify-email", html)
        self.assertIn("expires in 24 hours", html)
        # The worker minted a real, single-use grant from the relation and
        # the stored scalars.
        grant = DownloadDeliveryGrant.objects.get(user=user, download=download)
        self.assertTrue(grant.newsletter_opt_in)
        self.assertEqual(grant.surface, "shortcode")
        self.assertIsNone(grant.redeemed_at)
        grant_token = _extract_query_value(
            _grant_url_from_html(html), "grant",
        )
        grant_id, _secret = unpack_delivery_grant_token(grant_token)
        self.assertEqual(str(grant.pk), grant_id)

    def test_worker_mints_the_verification_link_for_an_unverified_recipient(self):
        download = make_download()
        user = User.objects.create_user(
            email="cold-download@test.com",
            password="pw",
            email_verified=False,
        )
        started_at = timezone.now()

        delivery = self._send(user, download)

        _subject, html = _drain_html(delivery)

        verify_url = _verify_url_from_html(html)
        self.assertTrue(verify_url.startswith(f"{BASE_URL}/api/verify-email?"))
        token = _extract_query_value(verify_url, "token")
        payload = _decode_token(token)
        self.assertEqual(payload["user_id"], user.pk)
        self.assertEqual(payload["action"], "verify_email")
        # The grant path rides inside the token as the post-verification
        # return path — never in the durable row.
        self.assertIn(
            f"/api/downloads/{download.slug}/file?grant=",
            payload["return_path"],
        )
        expires_at = datetime.datetime.fromtimestamp(
            payload["exp"], tz=datetime.timezone.utc,
        )
        ttl_hours = get_delivery_token_ttl_hours()
        self.assertGreater(
            expires_at,
            started_at + datetime.timedelta(hours=ttl_hours - 1),
        )
        self.assertLess(
            expires_at,
            started_at + datetime.timedelta(hours=ttl_hours + 1),
        )
        # The rendered copy quotes the same TTL the token carries.
        self.assertIn(f"expires in {ttl_hours} hours", html)
        # The grant itself was minted (and awaits the verified click).
        self.assertTrue(
            DownloadDeliveryGrant.objects.filter(
                user=user, download=download,
            ).exists(),
        )

    def test_deleted_download_fails_closed_with_reason(self):
        download = make_download()
        user = User.objects.create_user(
            email="stale-download@test.com",
            password="pw",
            email_verified=True,
        )
        delivery = self._send(user, download)
        download.delete()

        _deliver_expect_permanent_error(
            self, delivery, "download_delivery_download_missing",
        )

    def test_missing_recipient_user_fails_closed_with_reason(self):
        download = make_download()
        delivery = send_package_mail(
            None,
            "download_delivery",
            {"resource_title": download.title},
            recipient_email="orphan-download@test.com",
            related=download,
        )

        _deliver_expect_permanent_error(
            self, delivery, "download_delivery_user_missing",
        )


def _grant_url_from_html(html):
    import re  # noqa: PLC0415

    match = re.search(r'href="([^"]*/api/downloads/[^"]*file\?grant=[^"]+)"', html)
    assert match, "download_delivery renders no grant link"
    return match.group(1)


def _verify_url_from_html(html):
    import re  # noqa: PLC0415

    match = re.search(r'href="([^"]*/api/verify-email\?token=[^"]+)"', html)
    assert match, "download_delivery renders no verification link"
    return match.group(1)


@tag("core")
@override_settings(SITE_BASE_URL=BASE_URL)
class MavenWelcomeResolverTest(TestCase):
    def _send(self, user, course="Buildcamp", cohort=""):
        return send_package_mail(
            user,
            "maven_welcome",
            _welcome_context(course, cohort),
        )

    def test_worker_mints_every_welcome_link_and_token(self):
        started_at = timezone.now()
        user = User.objects.create_user(
            email="enrollee-links@test.com",
            password="pw",
            first_name="Ada",
            last_name="Lovelace",
        )

        delivery = self._send(user)

        # The durable row carries the course scalar only.
        self.assertEqual(
            delivery.context_data, {"course_name": "Buildcamp"},
        )

        subject, html = _drain_html(delivery)

        self.assertIn("You're enrolled in Buildcamp", subject)
        self.assertIn(f'href="{BASE_URL}/accounts/login/"', html)
        self.assertIn(f'href="{BASE_URL}/onboarding/"', html)
        self.assertIn(f'href="{BASE_URL}/community/slack"', html)

        reset_token = _extract_query_value(
            _url_from_html(html, "/api/password-reset?token="), "token",
        )
        reset_user, _payload = resolve_password_reset_token(reset_token)
        self.assertEqual(reset_user.pk, user.pk)

        opt_in_token = _extract_query_value(
            _url_from_html(html, "/api/verify-and-subscribe?token="), "token",
        )
        opt_in_payload = _decode_token(opt_in_token)
        self.assertEqual(opt_in_payload["user_id"], user.pk)
        self.assertEqual(opt_in_payload["action"], "verify_and_subscribe")
        expires_at = datetime.datetime.fromtimestamp(
            opt_in_payload["exp"], tz=datetime.timezone.utc,
        )
        self.assertGreater(
            expires_at,
            started_at + datetime.timedelta(
                hours=NEWSLETTER_OPT_IN_TOKEN_EXPIRY_HOURS - 1,
            ),
        )

        opt_out_token = _extract_query_value(
            _url_from_html(html, "/api/maven-email-opt-out?token="), "token",
        )
        opt_out_payload = _decode_token(opt_out_token)
        self.assertEqual(opt_out_payload["user_id"], user.pk)
        self.assertEqual(opt_out_payload["action"], "maven_email_opt_out")
        self.assertNotIn("exp", opt_out_payload)

    def test_nameless_enrollee_is_greeted_hi_there(self):
        user = User.objects.create_user(
            email="x.arrieta@ibernova.com", password="pw",
        )

        delivery = self._send(user, course="AI Engineering Buildcamp")

        _subject, html = _drain_html(delivery)

        self.assertIn("Hi there,", html)
        self.assertNotIn("x.arrieta", html)

    def test_named_enrollee_is_greeted_by_full_name(self):
        user = User.objects.create_user(
            email="ada-full@test.com",
            password="pw",
            first_name="Ada",
            last_name="Lovelace",
        )

        delivery = self._send(user)

        _subject, html = _drain_html(delivery)

        self.assertIn("Hi Ada Lovelace,", html)

    def test_cohort_fallback_flows_through_the_scalar(self):
        user = User.objects.create_user(
            email="cohort-fallback@test.com", password="pw",
        )

        delivery = self._send(user, course="", cohort="Cohort 1")

        self.assertEqual(delivery.context_data, {"course_name": "Cohort 1"})
        subject, _html = _drain_html(delivery)
        self.assertIn("You're enrolled in Cohort 1", subject)

    def test_course_channel_setting_is_read_at_delivery_time(self):
        user = User.objects.create_user(
            email="channel@test.com", password="pw",
        )
        IntegrationSetting.objects.update_or_create(
            key="MAVEN_COURSE_SLACK_CHANNEL",
            defaults={"value": "#ai-engineering-buildcamp"},
        )
        clear_config_cache()
        self.addCleanup(clear_config_cache)

        delivery = self._send(user)

        self.assertNotIn("course_channel", delivery.context_data)
        _subject, html = _drain_html(delivery)
        self.assertIn(
            "compare notes in #ai-engineering-buildcamp.", html,
        )

    def test_missing_recipient_user_fails_closed_with_reason(self):
        delivery = send_package_mail(
            None,
            "maven_welcome",
            {"course_name": "Buildcamp"},
            recipient_email="orphan-welcome@test.com",
        )

        _deliver_expect_permanent_error(
            self, delivery, "maven_welcome_user_missing",
        )


def _url_from_html(html, prefix):
    import re  # noqa: PLC0415

    match = re.search(rf'href="([^"]*{re.escape(prefix)}[^"]+)"', html)
    assert match, f"maven_welcome renders no {prefix} link"
    return match.group(1)
