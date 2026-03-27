"""
Playwright/pytest integration test for real Slack posting (Issue #120).

Posts a test message to the #integration-tests channel (C0AHN84QNP3)
using post_slack_announcement(), verifies the chat.postMessage response
confirms success, then deletes the message via chat.delete.

Skipped when SLACK_BOT_TOKEN env var is not set.

Usage:
    uv run pytest playwright_tests/test_slack_integration.py -v
"""

import os
from unittest.mock import patch

import pytest
import requests
from django.conf import settings

from notifications.services.slack_announcements import post_slack_announcement

SLACK_TEST_CHANNEL = "C0AHN84QNP3"

# Skip the entire module if SLACK_BOT_TOKEN is not available in env
pytestmark = pytest.mark.skipif(
    not os.environ.get("SLACK_BOT_TOKEN"),
    reason="SLACK_BOT_TOKEN env var not set; skipping real Slack integration test",
)


class _FakeContent:
    """Minimal content object to satisfy post_slack_announcement()."""

    def __init__(self, title, description=""):
        self.title = title
        self.description = description

    def get_absolute_url(self):
        return "/integration-test"


@pytest.mark.django_db
def test_post_slack_announcement_real():
    """Post a real message to the Slack #integration-tests channel, verify, and delete it."""

    # Temporarily set real Slack credentials from env vars
    original_token = settings.SLACK_BOT_TOKEN
    original_channel = settings.SLACK_ANNOUNCEMENTS_CHANNEL_ID
    env_token = os.environ["SLACK_BOT_TOKEN"]

    # We need to capture the chat.postMessage response to get the message
    # timestamp (ts) for deletion. Wrap requests.post to intercept it.
    captured_responses = []
    original_requests_post = requests.post

    def _capturing_post(*args, **kwargs):
        response = original_requests_post(*args, **kwargs)
        url = args[0] if args else kwargs.get("url", "")
        if "chat.postMessage" in str(url):
            captured_responses.append(response.json())
        return response

    try:
        settings.SLACK_BOT_TOKEN = env_token
        settings.SLACK_ANNOUNCEMENTS_CHANNEL_ID = SLACK_TEST_CHANNEL

        # Post a message using the real function, capturing the API response
        content = _FakeContent(
            title="Integration Test Message",
            description="Automated test - this message will be deleted shortly.",
        )
        with patch("notifications.services.slack_announcements.requests.post",
                    side_effect=_capturing_post):
            result = post_slack_announcement("article", content)

        # Verify post_slack_announcement returned success
        assert result is True, "post_slack_announcement() should return True on success"

        # Verify the Slack API response confirmed the message was posted
        assert len(captured_responses) == 1, (
            f"Expected 1 captured chat.postMessage response, got {len(captured_responses)}"
        )
        post_data = captured_responses[0]
        assert post_data.get("ok") is True, (
            f"chat.postMessage response was not ok: {post_data.get('error', 'unknown')}"
        )
        assert post_data.get("channel") == SLACK_TEST_CHANNEL, (
            f"Message posted to wrong channel: {post_data.get('channel')}"
        )

        # The response contains the message text in the fallback
        message = post_data.get("message", {})
        assert "Integration Test Message" in message.get("text", ""), (
            "Posted message text does not contain expected content"
        )

        message_ts = post_data.get("ts") or message.get("ts")
        assert message_ts, "Could not extract message timestamp from response"

        # Delete the test message to keep the channel clean
        delete_response = requests.post(
            "https://slack.com/api/chat.delete",
            json={
                "channel": SLACK_TEST_CHANNEL,
                "ts": message_ts,
            },
            headers={
                "Authorization": f"Bearer {env_token}",
                "Content-Type": "application/json; charset=utf-8",
            },
            timeout=10,
        )
        delete_data = delete_response.json()
        assert delete_data.get("ok"), (
            f"chat.delete failed: {delete_data.get('error', 'unknown')}"
        )

    finally:
        # Restore original settings
        settings.SLACK_BOT_TOKEN = original_token
        settings.SLACK_ANNOUNCEMENTS_CHANNEL_ID = original_channel
