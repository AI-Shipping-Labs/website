"""Offline Relay helpers for Django and Playwright tests."""

from __future__ import annotations

from community_base.mail.relay import RelaySendResult

from email_app.relay_versions import RELAY_TEMPLATE_KEYS


class StubRelayClient:
    """Offline stand-in for the Relay mail client used in tests."""

    def __init__(self, message_id="relay-test-noop"):
        self.message_id = message_id
        self.calls = []
        self.preview_calls = []

    def send(self, delivery, *, context=None):
        self.calls.append({"delivery": delivery, "context": context})
        return RelaySendResult(
            message_id=self.message_id,
            status="provider_accepted",
            template_version=delivery.template_version,
            idempotent_replay=False,
        )

    def messages_since(self, since):
        return ()

    def preview_template(self, template_key, context, *, version=None):
        self.preview_calls.append({
            "template_key": template_key,
            "context": dict(context),
            "version": version,
        })
        subject = context.get("subject") or "Test Subject"
        return {
            "subject": subject,
            "html_body": "<p>Relay preview</p>",
            "text_body": "Relay preview",
        }


class StubRelayTemplateClient:
    """Stateful offline Relay template API boundary with no local rendering."""

    def __init__(self):
        self.reset()

    def reset(self):
        """Restore deterministic state between tests that share the stub."""
        self.calls = []
        self.fail_operations = set()
        self._templates = {
            key: self._default_template(key)
            for key in sorted(RELAY_TEMPLATE_KEYS)
        }

    def _maybe_fail(self, operation):
        self.calls.append(operation)
        if operation in self.fail_operations:
            from email_app.services.relay_templates import (  # noqa: PLC0415
                RelayTemplateError,
            )

            raise RelayTemplateError(f"stub_{operation}_failed")

    @staticmethod
    def _default_template(template_key):
        return {
            "key": template_key,
            "name": template_key.replace("_", " ").replace(".", " ").title(),
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
            "latest_version": 1,
            "updated_at": "",
        }

    def templates(self):
        self._maybe_fail("templates")
        return tuple(dict(template) for template in self._templates.values())

    def template(self, template_key):
        self._maybe_fail("template")
        return dict(self._templates.setdefault(
            template_key, self._default_template(template_key),
        ))

    def save_draft(self, template_key, draft):
        self._maybe_fail("save_draft")
        saved = {"key": template_key, **draft}
        saved.setdefault("latest_version", self.template(template_key)["latest_version"])
        self._templates[template_key] = saved
        return dict(saved)

    def publish(self, template_key):
        self._maybe_fail("publish")
        template = self._templates.setdefault(
            template_key, self._default_template(template_key),
        )
        template["latest_version"] += 1
        return template["latest_version"]

    def versions(self, template_key):
        self._maybe_fail("versions")
        latest = self._templates.setdefault(
            template_key, self._default_template(template_key),
        )["latest_version"]
        return tuple(
            {"template_key": template_key, "version": version}
            for version in range(1, latest + 1)
        )

    def preview(self, template_key, context, *, version=None, draft=False):
        self._maybe_fail("preview")
        self.calls.append({
            "operation": "preview",
            "template_key": template_key,
            "context": dict(context),
            "version": version,
            "draft": draft,
        })
        template = self._templates.setdefault(
            template_key, self._default_template(template_key),
        )
        subject = str(context.get("subject") or "Test Subject")
        return {
            "subject": template.get("subject") or subject,
            "html_body": "<!doctype html><html><body><p>Relay preview</p></body></html>",
            "text_body": "Relay preview",
            "template_key": template_key,
            "template_version": version or template["latest_version"],
            "published": not draft,
            "missing_context": [],
        }


_relay_patchers = []
_relay_template_client = None


def get_test_relay_template_client():
    """Return the process-wide template stub installed by the test runner."""
    if _relay_template_client is None:
        raise RuntimeError("The test Relay template client is not installed")
    return _relay_template_client


def reset_test_relay_template_client():
    """Reset the installed template stub so browser tests cannot leak state."""
    get_test_relay_template_client().reset()


def install_test_relay():
    """Point package mail at an offline Relay client for the test run."""
    global _relay_patchers, _relay_template_client
    from unittest.mock import patch  # noqa: PLC0415

    if _relay_patchers:
        return
    template_client = StubRelayTemplateClient()
    _relay_template_client = template_client
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
