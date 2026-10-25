"""Offline Relay helpers for Django and Playwright tests."""

from __future__ import annotations

import contextlib
from types import SimpleNamespace

from community_base.mail.relay import RelaySendResult

from email_app.relay_versions import RELAY_V2_TEMPLATE_KEYS


@contextlib.contextmanager
def relay_test_backend():
    """Keep compatibility suites on the already-installed offline Relay."""
    yield


class StubRelayClient:
    """Offline stand-in for the Relay mail client used in tests."""

    def __init__(self, message_id="relay-test-noop"):
        self.message_id = message_id
        self.calls = []

    def send(self, delivery, *, context=None):
        self.calls.append({"delivery": delivery, "context": context})
        _record_send(delivery, context or {}, self.message_id)
        return RelaySendResult(
            message_id=self.message_id,
            status="provider_accepted",
            template_version=delivery.template_version,
            idempotent_replay=False,
        )

    def messages_since(self, since):
        return ()


class StubRelayTemplateClient:
    """Offline Relay template API boundary with no local rendering."""

    def templates(self):
        return tuple(
            {
                "key": key,
                "name": key.replace("_", " ").replace(".", " ").title(),
                "subject": "Test Subject",
                "latest_version": 2,
                "updated_at": "",
            }
            for key in sorted(RELAY_V2_TEMPLATE_KEYS)
        )

    def template(self, template_key):
        return {
            "key": template_key,
            "name": template_key,
            "description": "",
            "subject": "Test Subject",
            "html_body": "",
            "text_body": "",
            "markdown_body": "Test body",
            "category": "transactional",
            "required_context": [],
            "example_context": {},
            "default_sender_id": "",
            "markdown_layout_context": True,
            "is_active": True,
            "latest_version": 2,
        }

    def save_draft(self, template_key, draft):
        return {"key": template_key, **draft}

    def publish(self, template_key):
        return 2

    def versions(self, template_key):
        return ({"template_key": template_key, "version": 2},)

    def preview(self, template_key, context, *, version=None, draft=False):
        subject = str(context.get("subject") or "Test Subject")
        return {
            "subject": subject,
            "html_body": "<!doctype html><html><body><p>Relay preview</p></body></html>",
            "text_body": "Relay preview",
            "template_key": template_key,
            "template_version": version or 2,
            "published": not draft,
            "missing_context": [],
        }


def _record_send(delivery, context, message_id):
    from community_base.kernel.hooks import resolve  # noqa: PLC0415
    from django.conf import settings  # noqa: PLC0415

    recorder_hook = settings.COMMUNITY_BASE.get("MAIL_SEND_RECORDER")
    if not recorder_hook:
        return
    recorder = resolve(recorder_hook) if isinstance(recorder_hook, str) else recorder_hook
    rendered = SimpleNamespace(subject=context.get("subject", "Test Subject"))
    result = SimpleNamespace(message_id=message_id)
    recorder(delivery, rendered, result)


_relay_patchers = []


def install_test_relay():
    """Point package mail at an offline Relay client for the test run."""
    global _relay_patchers
    from unittest.mock import patch  # noqa: PLC0415

    if _relay_patchers:
        return
    template_client = StubRelayTemplateClient()
    targets = (
        ("community_base.mail.backends.relay.configured_client", StubRelayClient()),
        ("email_app.services.relay_templates.configured_template_client", template_client),
        ("studio.views.campaigns.configured_template_client", template_client),
        ("studio.views.email_templates.configured_template_client", template_client),
    )
    for target, client in targets:
        patcher = patch(target, return_value=client)
        patcher.start()
        _relay_patchers.append(patcher)


def deliver_pending_mail():
    """Run pending durable mail through the offline Relay immediately."""
    from community_base.mail.jobs import deliver as deliver_job  # noqa: PLC0415
    from community_base.mail.models import EmailDelivery  # noqa: PLC0415

    pending = EmailDelivery.objects.filter(state=EmailDelivery.State.PENDING)
    for delivery in list(pending):
        deliver_job(None, {"delivery_id": str(delivery.id)})
