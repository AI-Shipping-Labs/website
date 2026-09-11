"""Test-support for the package mail adoption (plan issue A1.2).

Two problems appear once sends go through ``community_base.mail`` and both
are test-environment-only:

- The SES transport must never touch the network. The old EmailService
  short-circuited on the ``SES_ENABLED`` kill-switch with a synthetic
  ``ses-disabled-noop`` message id; under the package the transport lives in
  ``ses_local``, so the test runner swaps its client for a stub that returns
  the same synthetic id. Existing EmailLog assertions keep passing.
- Django ``TestCase`` wraps every test in an aborted transaction, so
  ``transaction.on_commit`` never fires and the dispatched
  ``cb_mail.deliver`` job would never run. ``deliver_pending_mail`` runs the
  pending deliveries synchronously so tests can assert EmailLog rows.
"""

from __future__ import annotations


class StubSESClient:
    """Offline stand-in for the SES v2 client used by ``ses_local``."""

    def __init__(self, message_id="ses-disabled-noop"):
        self.message_id = message_id
        self.calls = []

    def send_email(self, **kwargs):
        self.calls.append(kwargs)
        return {"MessageId": self.message_id}


_patcher = None


def install_test_ses():
    """Point ``ses_local`` at :class:`StubSESClient` for the whole test run."""

    global _patcher
    if _patcher is not None:
        return
    from unittest.mock import patch  # noqa: PLC0415

    _patcher = patch(
        "community_base.mail.backends.ses_local.configured_client",
        return_value=StubSESClient(),
    )
    _patcher.start()


def deliver_pending_mail():
    """Run every pending mail delivery through its backend immediately."""

    from community_base.mail.jobs import deliver as deliver_job  # noqa: PLC0415
    from community_base.mail.models import EmailDelivery  # noqa: PLC0415

    pending = EmailDelivery.objects.filter(state=EmailDelivery.State.PENDING)
    for delivery in list(pending):
        deliver_job(None, {"delivery_id": str(delivery.id)})
