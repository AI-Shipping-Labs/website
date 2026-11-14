"""Test helper: stub the single Relay campaign submit request."""

from contextlib import contextmanager
from unittest.mock import patch

SUBMIT_TARGET = "email_app.services.campaign_submit.submit_native_campaign"
RECIPIENTS_TARGET = "email_app.services.campaign_submit.build_campaign_recipients"


@contextmanager
def stub_relay_submit(*, recipients=1, side_effect=None):
    """Yield the submit mock; the audience is stubbed to ``recipients`` rows."""
    rows = [
        {"email": f"stub-{i}@example.com", "source_object_key": f"website-user-{i}"}
        for i in range(recipients)
    ]
    with (
        patch(RECIPIENTS_TARGET, return_value=(rows, [])),
        patch(
            SUBMIT_TARGET,
            return_value={"queued": True},
            side_effect=side_effect,
        ) as submit,
    ):
        yield submit
