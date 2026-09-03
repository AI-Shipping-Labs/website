"""Live Slack announcement contract (relocated from Playwright, issue #1480).

Posts a test message to the #integration-tests channel (C0AHN84QNP3)
using ``post_slack_announcement()``, verifies the ``chat.postMessage``
response confirms success, then deletes the message via ``chat.delete``.

This is not a hermetic Django unit test. It is skipped unless the
operator opts in *and* Slack credentials are present. It is not part of
default Django, Playwright, or deploy gates.

Usage::

    make test-live-slack-announcement
"""

from __future__ import annotations

import os
import re
from unittest.mock import patch

import pytest
import requests
from django.conf import settings

from notifications.services.slack_announcements import post_slack_announcement

DEFAULT_SLACK_TEST_CHANNEL = "C0AHN84QNP3"
LIVE_SLACK_ANNOUNCEMENT_OPT_IN_ENV = "RUN_LIVE_SLACK_ANNOUNCEMENT"

_TOKEN_RE = re.compile(r"xox[a-zA-Z]?-[A-Za-z0-9-]+")
_BEARER_RE = re.compile(r"(?i)Bearer\s+\S+")

pytestmark = [
    pytest.mark.live_slack_announcement,
    pytest.mark.skipif(
        os.environ.get(LIVE_SLACK_ANNOUNCEMENT_OPT_IN_ENV) != "1",
        reason=(
            "Live Slack announcement test is opt-in; run "
            "`make test-live-slack-announcement`"
        ),
    ),
    pytest.mark.skipif(
        not os.environ.get("SLACK_BOT_TOKEN")
        or not os.environ.get("SLACK_TEST_ANNOUNCEMENTS_CHANNEL_ID"),
        reason=(
            "SLACK_BOT_TOKEN and SLACK_TEST_ANNOUNCEMENTS_CHANNEL_ID must be "
            "set; skipping real Slack integration test"
        ),
    ),
]


def secret_safe_text(value: object) -> str:
    """Return ``value`` with Slack tokens and bearer credentials redacted."""
    text = "" if value is None else str(value)
    text = _BEARER_RE.sub("Bearer [redacted]", text)
    return _TOKEN_RE.sub("[redacted]", text)


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

    original_token = settings.SLACK_BOT_TOKEN
    original_channel = settings.SLACK_ANNOUNCEMENTS_CHANNEL_ID
    original_env = settings.SLACK_ENVIRONMENT
    original_test_channel = settings.SLACK_TEST_ANNOUNCEMENTS_CHANNEL_ID
    env_token = os.environ["SLACK_BOT_TOKEN"]
    test_channel = os.environ["SLACK_TEST_ANNOUNCEMENTS_CHANNEL_ID"]

    assert test_channel == DEFAULT_SLACK_TEST_CHANNEL, (
        "Real Slack integration tests must target #integration-tests "
        f"({DEFAULT_SLACK_TEST_CHANNEL}), got {secret_safe_text(test_channel)}"
    )

    captured_responses = []
    original_requests_post = requests.post

    def _capturing_post(*args, **kwargs):
        try:
            response = original_requests_post(*args, **kwargs)
        except Exception as exc:
            raise AssertionError(secret_safe_text(exc)) from None
        url = args[0] if args else kwargs.get("url", "")
        if "chat.postMessage" in str(url):
            captured_responses.append(response.json())
        return response

    try:
        settings.SLACK_ENABLED = True
        settings.SLACK_ENVIRONMENT = "test"
        settings.SLACK_BOT_TOKEN = env_token
        settings.SLACK_TEST_ANNOUNCEMENTS_CHANNEL_ID = test_channel

        content = _FakeContent(
            title="Integration Test Message",
            description="Automated test - this message will be deleted shortly.",
        )
        with patch(
            "notifications.services.slack_announcements.requests.post",
            side_effect=_capturing_post,
        ):
            result = post_slack_announcement("article", content)

        assert result is True, "post_slack_announcement() should return True on success"

        assert len(captured_responses) == 1, (
            f"Expected 1 captured chat.postMessage response, got {len(captured_responses)}"
        )
        post_data = captured_responses[0]
        assert post_data.get("ok") is True, (
            "chat.postMessage response was not ok: "
            f"{secret_safe_text(post_data.get('error', 'unknown'))}"
        )
        assert post_data.get("channel") == test_channel, (
            "Message posted to wrong channel: "
            f"{secret_safe_text(post_data.get('channel'))}"
        )

        message = post_data.get("message", {})
        assert "Integration Test Message" in message.get("text", ""), (
            "Posted message text does not contain expected content"
        )

        message_ts = post_data.get("ts") or message.get("ts")
        assert message_ts, "Could not extract message timestamp from response"

        try:
            delete_response = requests.post(
                "https://slack.com/api/chat.delete",
                json={
                    "channel": test_channel,
                    "ts": message_ts,
                },
                headers={
                    "Authorization": f"Bearer {env_token}",
                    "Content-Type": "application/json; charset=utf-8",
                },
                timeout=10,
            )
        except Exception as exc:
            raise AssertionError(secret_safe_text(exc)) from None
        delete_data = delete_response.json()
        assert delete_data.get("ok"), (
            "chat.delete failed: "
            f"{secret_safe_text(delete_data.get('error', 'unknown'))}"
        )

    finally:
        settings.SLACK_ENABLED = False
        settings.SLACK_ENVIRONMENT = original_env
        settings.SLACK_BOT_TOKEN = original_token
        settings.SLACK_ANNOUNCEMENTS_CHANNEL_ID = original_channel
        settings.SLACK_TEST_ANNOUNCEMENTS_CHANNEL_ID = original_test_channel
