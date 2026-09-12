"""Durable-context URL contract for the package mail boundary (issue #1613).

Two layers live here:

- the site guard (``email_app.services.context_guard``) rejects URL-bearing
  durable context at ``send_package_mail`` before any row or job exists;
- the worker resolver (``email_app.hooks.resolve_auth_mail_context``)
  mints every rendered link from non-secret inputs and relations, so the
  stored context never changes and never carries a link.
"""

from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from community_base.mail.jobs import deliver as deliver_job
from community_base.mail.models import EmailDelivery
from community_base.mail.service import MailError
from django.conf import settings
from django.core.cache import cache
from django.test import TestCase, override_settings, tag
from django.utils import timezone

from accounts.models import EmailChangeRequest, User
from accounts.services.email_change import (
    confirm_email_change,
    hash_email_change_token,
    request_email_change,
)
from email_app.testing import StubSESClient

TEST_PURPOSE = "email_verification_signup"


def _drain(deliveries):
    """Deliver exactly the given deliveries with a local stub; return HTML."""

    stub = StubSESClient()
    with patch(
        "community_base.mail.backends.ses_local.configured_client",
        return_value=stub,
    ):
        for delivery in deliveries:
            deliver_job(None, {"delivery_id": str(delivery.id)})
    return [
        call["Content"]["Simple"]["Body"]["Html"]["Data"]
        for call in stub.calls
    ]


@tag("core")
@override_settings(SES_ENABLED=False)
class ContextGuardTest(TestCase):
    def _assert_rejected(self, context, path):
        from email_app.services.context_guard import ensure_no_rendered_urls

        with self.assertRaises(MailError) as caught:
            ensure_no_rendered_urls(TEST_PURPOSE, context)
        message = str(caught.exception)
        self.assertIn(TEST_PURPOSE, message)
        self.assertIn(path, message)

    def test_rejects_absolute_urls_top_level_and_nested(self):
        self._assert_rejected({"confirm_url": "https://x.test/a?token=t"}, "confirm_url")
        self._assert_rejected(
            {"items": [{"link": "https://x.test/a"}]},
            "items[0].link",
        )
        self._assert_rejected(
            {"deep": {"lists": [("u", "s3://bucket/key")]}},
            "deep.lists[0][1]",
        )

    def test_rejects_uri_variants_and_bearer_relative_links(self):
        for value in (
            "see https://x.test/a in prose",
            "//evil.test/path",
            "mailto:evil@test.com",
            "javascript:alert(1)",
            "/account/change-email/confirm?token=abc",
            "/next?code=abc",
            "/redirect?key=abc&x=1",
            "/sign?signature=abc",
            "/callback?access_token=abc",
            "https://bucket.s3.amazonaws.com/a?X-Amz-Signature=deadbeef",
        ):
            with self.subTest(value=value):
                self._assert_rejected({"value": value}, "value")

    def test_error_never_echoes_the_rejected_value(self):
        from email_app.services.context_guard import ensure_no_rendered_urls

        with self.assertRaises(MailError) as caught:
            ensure_no_rendered_urls(
                TEST_PURPOSE,
                {"link": "https://x.test/a?token=super-secret-token"},
            )
        self.assertNotIn("super-secret-token", str(caught.exception))

    def test_allows_non_link_data_and_resolver_inputs(self):
        from email_app.services.context_guard import ensure_no_rendered_urls

        ensure_no_rendered_urls(
            TEST_PURPOSE,
            {
                "return_path": "/account/?next=/dashboard",
                "old_email": "member@test.com",
                "expires_at": "2026-09-12T03:00:00+00:00",
                "ttl_days": 7,
                "support_id": 42,
                "nested": {"plain": "no links here", "count": 3},
            },
        )

    def test_guard_fires_before_any_durable_row_exists(self):
        from email_app.package_mail import send_package_mail

        user = User.objects.create_user(email="guard@test.com")
        with self.assertRaises(MailError):
            send_package_mail(
                user,
                TEST_PURPOSE,
                {"confirm_url": "https://x.test/a?token=t"},
            )
        self.assertFalse(EmailDelivery.objects.exists())


@tag("core")
@override_settings(SES_ENABLED=False)
class EmailChangeWorkerMintTest(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            email="old-worker@test.com",
            password="CorrectPass123!",
            account_activated=True,
            email_verified=True,
        )
        self.request_obj, self.token = request_email_change(
            self.user,
            "new-worker@test.com",
            "CorrectPass123!",
        )
        self.delivery = EmailDelivery.objects.get(
            idempotency_key=f"email-change-confirm:{self.request_obj.pk}",
        )

    def test_producer_stores_no_url_and_attaches_the_request(self):
        self.assertEqual(
            self.delivery.related_object_type,
            "accounts.emailchangerequest",
        )
        self.assertEqual(
            self.delivery.related_object_id,
            str(self.request_obj.pk),
        )
        self.assertEqual(self.delivery.recipient_email, "new-worker@test.com")
        context = self.delivery.context_data
        self.assertNotIn("confirm_url", context)
        self.assertEqual(set(context), {"old_email", "new_email", "expiry_hours"})

    def test_worker_mints_the_only_live_link_and_context_stays_frozen(self):
        stored_before = deepcopy(self.delivery.context_data)
        (html,) = _drain([self.delivery])

        self.assertIn("/account/change-email/confirm?token=", html)
        self.delivery.refresh_from_db()
        self.assertEqual(self.delivery.context_data, stored_before)

        result = confirm_email_change(self.token)
        self.assertTrue(result.success)

    def _confirm_delivery_for(self, request_row):
        from email_app.package_mail import send_package_mail

        return send_package_mail(
            self.user,
            "account_email_change_confirm",
            {
                "old_email": request_row.old_email,
                "new_email": request_row.new_email,
                "expiry_hours": 24,
            },
            recipient_email=request_row.new_email,
            idempotency_key=f"email-change-confirm:{request_row.pk}",
            related=request_row,
        )

    def test_binding_failures_fail_closed_without_transport(self):
        superseded = EmailChangeRequest.objects.create(
            user=self.user,
            old_email="old-worker@test.com",
            new_email="other-worker@test.com",
            token_hash=hash_email_change_token("superseded-unused-token"),
            expires_at=timezone.now() + timedelta(hours=24),
            last_sent_at=timezone.now(),
            invalidated_at=timezone.now(),
        )
        orphan_delivery = EmailDelivery.objects.create(
            purpose="account_email_change_confirm",
            template_key="account_email_change_confirm",
            recipient_email="new-worker@test.com",
            recipient_user=self.user,
            context_hash="0" * 64,
            context_data={"old_email": "old-worker@test.com"},
            idempotency_key="email-change-confirm:orphan",
            related_object_type="accounts.emailchangerequest",
            related_object_id="999999",
        )
        cases = [
            ("missing_request", orphan_delivery),
            ("superseded", self._confirm_delivery_for(superseded)),
        ]
        for reason, delivery in cases:
            with self.subTest(reason=reason):
                stub = StubSESClient()
                with patch(
                    "community_base.mail.backends.ses_local.configured_client",
                    return_value=stub,
                ):
                    with self.assertRaises(Exception) as caught:
                        deliver_job(None, {"delivery_id": str(delivery.id)})
                self.assertEqual(stub.calls, [])
                self.assertNotIn("token=", str(caught.exception))

    def test_expired_request_sends_nothing(self):
        EmailChangeRequest.objects.filter(pk=self.request_obj.pk).update(
            expires_at=timezone.now() - timedelta(minutes=1),
        )
        stub = StubSESClient()
        with patch(
            "community_base.mail.backends.ses_local.configured_client",
            return_value=stub,
        ):
            with self.assertRaises(Exception):
                deliver_job(None, {"delivery_id": str(self.delivery.id)})
        self.assertEqual(stub.calls, [])

    def test_legacy_random_token_digest_still_confirms(self):
        """Pre-deploy rows keep their random-token digest; the unchanged
        digest lookup accepts them for their original lifetime."""

        EmailChangeRequest.objects.filter(pk=self.request_obj.pk).update(
            invalidated_at=timezone.now(),
        )
        legacy_token = "legacy-random-token-value"
        legacy = EmailChangeRequest.objects.create(
            user=self.user,
            old_email="old-worker@test.com",
            new_email="legacy-worker@test.com",
            token_hash=hash_email_change_token(legacy_token),
            expires_at=timezone.now() + timedelta(hours=1),
            last_sent_at=timezone.now(),
        )
        # A pre-#1613 queued delivery carries the link in context and no
        # relation; the resolver passes it through for its lifetime.
        legacy_delivery = EmailDelivery.objects.create(
            purpose="account_email_change_confirm",
            template_key="account_email_change_confirm",
            sender_id="no-reply@aishippinglabs.com",
            recipient_email="legacy-worker@test.com",
            recipient_user=self.user,
            context_hash="1" * 64,
            context_data={
                "old_email": "old-worker@test.com",
                "new_email": "legacy-worker@test.com",
                "confirm_url": (
                    "https://aishippinglabs.com/account/change-email/confirm"
                    f"?token={legacy_token}"
                ),
            },
            idempotency_key=f"email-change-confirm:{legacy.pk}",
        )
        (html,) = _drain([legacy_delivery])
        self.assertIn(f"token={legacy_token}", html)

        result = confirm_email_change(legacy_token)
        self.assertTrue(result.success)
        legacy.refresh_from_db()
        self.assertIsNotNone(legacy.confirmed_at)


@tag("core")
@override_settings(SES_ENABLED=False)
class WorkerContextInjectionTest(TestCase):
    def test_worker_injects_site_url_without_storing_it(self):
        from email_app.hooks import resolve_auth_mail_context

        user = User.objects.create_user(email="siteurl@test.com")
        delivery = EmailDelivery.objects.create(
            purpose="email_verification_signup",
            template_key="email_verification_signup",
            recipient_email="siteurl@test.com",
            recipient_user=user,
            context_hash="2" * 64,
            context_data={"return_path": "", "ttl_days": 7},
            idempotency_key="site-url-injection",
        )
        stored = deepcopy(delivery.context_data)

        resolved = resolve_auth_mail_context(
            delivery=delivery,
            context=dict(delivery.context_data),
        )

        self.assertIn("site_url", resolved)
        self.assertTrue(resolved["site_url"].startswith("http"))
        self.assertIn("verify_url", resolved)
        delivery.refresh_from_db()
        self.assertEqual(delivery.context_data, stored)


class DirectPackageSendContractTest(TestCase):
    def test_production_code_sends_only_through_the_site_boundary(self):
        base = Path(settings.BASE_DIR)
        allowed = {"email_app/package_mail.py"}
        offenders = []
        for path in base.rglob("*.py"):
            relative = path.relative_to(base).as_posix()
            if relative in allowed:
                continue
            if any(
                part in {"tests", "playwright_tests", ".venv", "node_modules"}
                for part in path.parts
            ):
                continue
            if path.name.startswith("test_"):
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for line in text.splitlines():
                stripped = line.strip()
                if stripped.startswith("from community_base.mail import"):
                    names = stripped.split("import", 1)[1]
                    if any(
                        name.strip().split(" as ")[0].strip() == "send"
                        for name in names.split(",")
                    ):
                        offenders.append(relative)
        self.assertEqual(offenders, [])


@tag("core")
@override_settings(SES_ENABLED=False)
class NotificationWorkerMintTest(TestCase):
    """A1.2 slice 4: the notification sends (event reminder, workshop
    announcement, plan share) persist an empty durable context and the
    worker rebuilds every text field, the recipient-local time and every
    link from the delivery's ``related`` relation (issue #1613)."""

    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            email="notify-worker@test.com",
            password="CorrectPass123!",
            email_verified=True,
        )

    def _send(self, purpose, related, **kwargs):
        from email_app.package_mail import send_package_mail

        return send_package_mail(
            self.user,
            purpose,
            {},
            related=related,
            **kwargs,
        )

    def test_event_reminder_worker_mints_context_from_related_event(self):
        from datetime import datetime
        from datetime import timezone as dt_timezone

        from events.models import Event
        from integrations.config import site_base_url

        self.user.preferred_timezone = "Europe/Berlin"
        self.user.save(update_fields=["preferred_timezone"])
        start = datetime(2026, 6, 16, 16, 0, 0, tzinfo=dt_timezone.utc)
        event = Event.objects.create(
            title="Reminder Mint Event",
            slug="reminder-mint-event",
            start_datetime=start,
            status="upcoming",
        )
        delivery = self._send(
            "event_reminder",
            event,
            idempotency_key=f"event_reminder:{event.pk}:{self.user.pk}:24h",
        )

        self.assertEqual(delivery.context_data, {})
        self.assertEqual(delivery.related_object_type, "events.event")
        self.assertEqual(delivery.related_object_id, str(event.pk))

        (html,) = _drain([delivery])
        self.assertIn("Reminder Mint Event", html)
        self.assertIn("June 16, 2026, 18:00 Europe/Berlin", html)
        self.assertIn(
            f'href="{site_base_url()}{event.get_join_url()}"', html,
        )
        self.assertIn("Change your timezone", html)
        delivery.refresh_from_db()
        self.assertEqual(delivery.context_data, {})

    def test_workshop_announcement_worker_mints_context_from_related_workshop(
        self,
    ):
        from datetime import date

        from content.models import Workshop
        from integrations.config import site_base_url

        workshop = Workshop.objects.create(
            title="Mint Workshop", slug="mint-workshop",
            date=date(2026, 1, 1), status="published",
            description="Hands-on minting",
            landing_required_level=0,
        )
        delivery = self._send("workshop_announcement", workshop)

        self.assertEqual(delivery.context_data, {})
        self.assertEqual(delivery.related_object_type, "content.workshop")

        (html,) = _drain([delivery])
        self.assertIn("Mint Workshop", html)
        self.assertIn("Hands-on minting", html)
        self.assertIn(
            f'href="{site_base_url()}/workshops/mint-workshop"', html,
        )
        delivery.refresh_from_db()
        self.assertEqual(delivery.context_data, {})

    def test_plan_shared_worker_mints_context_from_related_plan(self):
        import datetime as dt

        from django.urls import reverse

        from integrations.config import site_base_url
        from plans.models import Plan, Sprint

        sprint = Sprint.objects.create(
            name="Mint Sprint", slug="mint-sprint",
            start_date=dt.date(2026, 5, 1),
        )
        plan = Plan.objects.create(member=self.user, sprint=sprint)
        delivery = self._send("plan_shared", plan)

        self.assertEqual(delivery.context_data, {})
        self.assertEqual(delivery.related_object_type, "plans.plan")

        (html,) = _drain([delivery])
        expected_url = (
            f"{site_base_url()}"
            f"{reverse('my_plan_detail', kwargs={'sprint_slug': 'mint-sprint', 'plan_id': plan.pk})}"
        )
        self.assertIn("Mint Sprint", html)
        self.assertIn(f'href="{expected_url}"', html)
        delivery.refresh_from_db()
        self.assertEqual(delivery.context_data, {})

    def test_missing_relations_fail_closed_without_transport(self):
        cases = [
            ("event_reminder", "events.event"),
            ("workshop_announcement", "content.workshop"),
            ("plan_shared", "plans.plan"),
        ]
        for purpose, related_type in cases:
            with self.subTest(purpose=purpose):
                delivery = EmailDelivery.objects.create(
                    purpose=purpose,
                    template_key=purpose,
                    recipient_email=self.user.email,
                    recipient_user=self.user,
                    context_hash="0" * 64,
                    context_data={},
                    idempotency_key=f"{purpose}:orphan",
                    related_object_type=related_type,
                    related_object_id="999999",
                )
                stub = StubSESClient()
                with patch(
                    "community_base.mail.backends.ses_local.configured_client",
                    return_value=stub,
                ):
                    with self.assertRaises(Exception):
                        deliver_job(
                            None, {"delivery_id": str(delivery.id)},
                        )
