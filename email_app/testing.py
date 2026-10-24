"""Test-support for the package mail adoption (plan issue A1.2).

Two problems appear once sends go through ``community_base.mail`` and both
are test-environment-only:

- The SES and Relay transports must never touch the network. The old EmailService
  short-circuited on the ``SES_ENABLED`` kill-switch with a synthetic
  ``ses-disabled-noop`` message id; under the package the transport lives in
  ``ses_local`` or ``relay``, so the test runner swaps its client for a stub that returns
  synthetic ids. Existing EmailLog assertions keep passing.
- Django ``TestCase`` wraps every test in an aborted transaction, so
  ``transaction.on_commit`` never fires and the dispatched
  ``cb_mail.deliver`` job would never run. ``deliver_pending_mail`` runs the
  pending deliveries synchronously so tests can assert EmailLog rows.
"""

from __future__ import annotations

from types import SimpleNamespace

from community_base.mail.relay import RelaySendResult


class StubSESClient:
    """Offline stand-in for the SES v2 client used by ``ses_local``."""

    def __init__(self, message_id="ses-disabled-noop"):
        self.message_id = message_id
        self.calls = []

    def send_email(self, **kwargs):
        self.calls.append(kwargs)
        return {"MessageId": self.message_id}


class StubRelayClient:
    """Offline stand-in for Relay mail client used in tests."""

    def __init__(self, message_id="relay-test-noop"):
        self.message_id = message_id
        self.calls = []

    def send(self, delivery, *, context=None):
        self.calls.append({"delivery": delivery, "context": context})

        # Trigger transitional MAIL_SEND_RECORDER if present so EmailLog assertions pass
        try:
            from community_base.kernel.hooks import resolve  # noqa: PLC0415
            from django.conf import settings  # noqa: PLC0415

            cb_conf = getattr(settings, "COMMUNITY_BASE", {})
            recorder_hook = cb_conf.get("MAIL_SEND_RECORDER")
            if recorder_hook:
                recorder = (
                    resolve(recorder_hook)
                    if isinstance(recorder_hook, str)
                    else recorder_hook
                )
                rendered = SimpleNamespace(
                    subject=(context or {}).get("subject", "Test Subject")
                )
                result = SimpleNamespace(message_id=self.message_id)
                recorder(delivery, rendered, result)
        except Exception:
            pass

        return RelaySendResult(
            message_id=self.message_id,
            status="provider_accepted",
            template_version=getattr(delivery, "template_version", 1) or 1,
            idempotent_replay=False,
        )

    def send_transactional(self, *args, **kwargs):
        self.calls.append({"args": args, "kwargs": kwargs})

        return RelaySendResult(
            message_id=self.message_id,
            status="provider_accepted",
            template_version=1,
            idempotent_replay=False,
        )

    def messages_since(self, since):
        return ()

    def get_template(self, key):
        return {"key": key, "latest_version": 1}


_patcher = None
_relay_patcher = None


def install_test_ses():
    """Point mail backends at offline test stubs for the whole test run."""

    global _patcher, _relay_patcher
    from unittest.mock import patch  # noqa: PLC0415

    if _patcher is None:
        _patcher = patch(
            "community_base.mail.backends.ses_local.configured_client",
            return_value=StubSESClient(),
        )
        _patcher.start()

    if _relay_patcher is None:
        _relay_patcher = patch(
            "community_base.mail.backends.relay.configured_client",
            return_value=StubRelayClient(),
        )
        _relay_patcher.start()


def deliver_pending_mail():
    """Run every pending mail delivery through its backend immediately."""

    from community_base.mail.jobs import deliver as deliver_job  # noqa: PLC0415
    from community_base.mail.models import EmailDelivery  # noqa: PLC0415

    pending = EmailDelivery.objects.filter(state=EmailDelivery.State.PENDING)
    for delivery in list(pending):
        deliver_job(None, {"delivery_id": str(delivery.id)})
