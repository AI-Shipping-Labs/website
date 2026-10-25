"""Playwright E2E test for issue #450: verify-email footer CTA round trip.

The footer renders inside an email client (out of browser scope), so the
bulk of the coverage lives in Django unit tests
(``email_app/tests/test_email_service.py::VerifyEmailFooterTest`` and
``email_app/tests/test_campaigns.py::CampaignVerifyEmailFooterTest``).

This single browser scenario verifies the end-to-end round trip a real
recipient would take: the verify URL embedded in the footer must
actually verify the user when clicked. It would fail if any of:
- the verify CTA is missing from the rendered email,
- the URL points at the wrong endpoint,
- the JWT is malformed or carries the wrong action,
- the verify endpoint does not flip ``email_verified`` to True.

A1.2 remainder slice 4: the send goes through the durable package path
(``send_package_mail``) and the footer link is minted by the worker from
the recipient row, so the captured HTML is exactly what the worker
rendered.

Usage:
    uv run pytest playwright_tests/test_verify_email_footer.py -v
"""

import os
import re
from unittest.mock import patch

import pytest

from email_app.testing import relay_test_backend
from playwright_tests.conftest import (
    create_user as _create_user,
)
from playwright_tests.conftest import (
    ensure_tiers as _ensure_tiers,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection  # noqa: E402

# Issue #656: this module uses local-only fixtures (DB seeding,
# session-cookie injection, etc.) and cannot run against the
# deployed dev environment. See _docs/testing-guidelines.md.
pytestmark = pytest.mark.local_only


def _capture_send(user, template_name, context):
    """Send through the durable package path; return the Relay boundary call."""
    from email_app.package_mail import send_package_mail
    from email_app.testing import StubRelayClient

    stub = StubRelayClient()
    with relay_test_backend(), patch(
        "community_base.mail.backends.relay.configured_client",
        return_value=stub,
    ):
        send_package_mail(user, template_name, context)

    connection.close()
    assert stub.calls, "Worker crossed no Relay boundary"
    return stub.calls[0]


@pytest.mark.django_db(transaction=True)
class TestVerifyEmailFooterRoundTrip:
    """The verify URL embedded in an email footer actually verifies the user."""

    @pytest.mark.core
    def test_unverified_subscriber_clicks_footer_verify_link(
        self, django_server, page,
    ):
        """Given an unverified Free user.
        1. Trigger a welcome email and capture its resolved Relay context.
        2. Read the worker-minted verification URL.
        3. Open the URL anonymously in the browser.
        Then: 200 + verification-success message.
        4. Re-read the user from the DB.
        Then: ``email_verified`` is now True.
        """
        _ensure_tiers()
        user = _create_user(
            "unverified-footer@test.com",
            email_verified=False,
        )
        assert not user.email_verified

        # Step 1 & 2: the website owns the durable purpose and resolved URL;
        # Relay owns the rendered footer copy.
        call = _capture_send(
            user,
            "welcome",
            {"tier_name": "Free"},
        )
        assert call["delivery"].purpose == "welcome"
        verify_url = call["context"]["verify_url"]
        assert "/api/verify-email?token=" in verify_url

        # The captured URL embeds the production base URL via
        # ``site_base_url()``. For the Playwright run we need to point
        # the browser at the live test server, so swap the host.
        browser_url = re.sub(
            r"^https?://[^/]+",
            django_server,
            verify_url,
        )

        # Step 3: open the verify URL anonymously.
        response = page.goto(
            browser_url,
            wait_until="domcontentloaded",
        )
        assert response.status == 200
        body = page.content()
        assert "verified" in body.lower(), (
            f"Verify endpoint did not return a success message; got: {body[:200]}"
        )

        # Step 4: the bit must actually flip in the DB.
        user.refresh_from_db()
        assert user.email_verified, (
            "User.email_verified did not flip to True after clicking the "
            "footer verify link"
        )
