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

Usage:
    uv run pytest playwright_tests/test_verify_email_footer.py -v
"""

import os
import re
from unittest.mock import patch

import pytest

from playwright_tests.conftest import (
    create_user as _create_user,
)
from playwright_tests.conftest import (
    ensure_tiers as _ensure_tiers,
)

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
from django.db import connection  # noqa: E402


def _capture_send_html(user, template_name, context):
    """Trigger an EmailService send with SES mocked, return the rendered HTML.

    Mirrors how the production code wraps a body in
    ``base_email.html``; bypassing SES means we get the exact bytes the
    recipient would have seen without making a real API call.
    """
    # Imported lazily so the module-level Django configuration is in
    # place before email_app is loaded.
    from email_app.services.email_service import EmailService

    captured = {}

    def fake_send(self_, to_email, subject, html_body, **kwargs):  # noqa: ARG001
        captured['html'] = html_body
        return 'fake-ses-id'

    service = EmailService()
    with patch.object(EmailService, '_send_ses', autospec=True, side_effect=fake_send):
        service.send(user, template_name, context)

    connection.close()
    return captured.get('html', '')


def _extract_verify_url(html):
    """Pull the verify URL out of the footer CTA paragraph.

    Anchored to ``verify-email-cta`` so we never accidentally pick up a
    body-level verify link (only the email-verification template embeds
    one, and that template opts out of the footer anyway, but the
    anchor keeps the test honest).
    """
    match = re.search(
        r'<p class="verify-email-cta">.*?<a href="([^"]+)"',
        html,
        re.DOTALL,
    )
    return match.group(1) if match else None


@pytest.mark.core
@pytest.mark.django_db(transaction=True)
class TestVerifyEmailFooterRoundTrip:
    """The verify URL embedded in an email footer actually verifies the user."""

    def test_unverified_subscriber_clicks_footer_verify_link(
        self, django_server, page,
    ):
        """Given an unverified Free user.
        1. Trigger a welcome email; capture rendered HTML (SES mocked).
        2. Extract the verify URL from the footer CTA.
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

        # Step 1 & 2: capture HTML and pull the verify URL out of the
        # footer CTA. Both must exist or the test stops here.
        html = _capture_send_html(
            user,
            "welcome",
            {"tier_name": "Free"},
        )
        assert '<p class="verify-email-cta">' in html, (
            "Footer verify CTA missing from rendered email"
        )
        verify_url = _extract_verify_url(html)
        assert verify_url is not None, (
            "Could not extract verify URL from footer CTA"
        )
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
