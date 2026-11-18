"""Relay campaign failure alerting independent of Relay (issue #2072).

``alert_relay_campaign_failure`` posts to the staff Slack channel first
because the fallback email travels through Relay itself; the email to
``dev@dtcdev.click`` is attempted only when Slack did not accept the post.
"""

from unittest.mock import patch

from django.core.cache import caches
from django.test import TestCase

from email_app.models import EmailCampaign
from email_app.services.relay_campaign_alerts import (
    RELAY_ALERT_CACHE_ALIAS,
    alert_event_notice_failure,
    alert_relay_campaign_failure,
)
from events.models import Event
from notifications.services.staff_slack import SlackPostResult


class RelayCampaignAlertTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.campaign = EmailCampaign.objects.create(
            subject="Alert Subject",
            body="Alert body.",
            status="sending",
        )

    def setUp(self):
        caches[RELAY_ALERT_CACHE_ALIAS].clear()

    def _alert(self, campaign=None, operation="queue"):
        return alert_relay_campaign_failure(
            campaign or self.campaign, operation, RuntimeError("relay timeout"),
        )

    @patch("email_app.services.relay_campaign_alerts.send_mail")
    @patch(
        "email_app.services.relay_campaign_alerts.slack_api_enabled",
        return_value=True,
    )
    @patch(
        "email_app.services.relay_campaign_alerts.staff_alert_channel_id",
        return_value="C123",
    )
    @patch(
        "email_app.services.relay_campaign_alerts.post_staff_slack_message",
        return_value=SlackPostResult(ok=True),
    )
    def test_slack_accepted_means_no_email(self, mock_post, _channel, _enabled, mock_mail):
        self.assertTrue(self._alert())

        self.assertEqual(mock_post.call_count, 1)
        channel_id, message = mock_post.call_args.args
        self.assertEqual(channel_id, "C123")
        self.assertIn("AISHL campaign queue failed", message["text"])
        self.assertEqual(message["blocks"][0]["type"], "section")
        mock_mail.assert_not_called()

    def test_relay_outage_reaches_slack_without_relay(self):
        """Slack is posted through slack.com directly, never through Relay."""
        with (
            patch(
                "email_app.services.relay_campaign_alerts.slack_api_enabled",
                return_value=True,
            ),
            patch(
                "email_app.services.relay_campaign_alerts.staff_alert_channel_id",
                return_value="C123",
            ),
            patch(
                "email_app.services.relay_campaign_alerts.post_staff_slack_message",
                return_value=SlackPostResult(ok=True),
            ) as mock_post,
            patch(
                "email_app.services.relay_campaign_alerts.send_mail",
            ) as mock_mail,
        ):
            self.assertTrue(self._alert(operation="stats refresh"))
        self.assertEqual(mock_post.call_count, 1)
        mock_mail.assert_not_called()

    def test_email_is_the_fallback_when_slack_is_disabled(self):
        with (
            patch(
                "email_app.services.relay_campaign_alerts.slack_api_enabled",
                return_value=False,
            ),
            patch("email_app.services.relay_campaign_alerts.send_mail", return_value=1) as mock_mail,
        ):
            self.assertTrue(self._alert())
        self.assertEqual(mock_mail.call_count, 1)
        self.assertEqual(mock_mail.call_args.kwargs["recipient_list"], ["dev@dtcdev.click"])

    def test_email_is_attempted_when_slack_has_no_channel(self):
        with (
            patch(
                "email_app.services.relay_campaign_alerts.slack_api_enabled",
                return_value=True,
            ),
            patch(
                "email_app.services.relay_campaign_alerts.staff_alert_channel_id",
                return_value="",
            ),
            patch("email_app.services.relay_campaign_alerts.send_mail", return_value=1) as mock_mail,
        ):
            self.assertTrue(self._alert())
        self.assertEqual(mock_mail.call_count, 1)

    def test_email_is_attempted_when_slack_rejects_the_post(self):
        with (
            patch(
                "email_app.services.relay_campaign_alerts.slack_api_enabled",
                return_value=True,
            ),
            patch(
                "email_app.services.relay_campaign_alerts.staff_alert_channel_id",
                return_value="C123",
            ),
            patch(
                "email_app.services.relay_campaign_alerts.post_staff_slack_message",
                return_value=SlackPostResult(ok=False, error="channel_not_found"),
            ),
            patch("email_app.services.relay_campaign_alerts.send_mail", return_value=1) as mock_mail,
        ):
            self.assertTrue(self._alert())
        self.assertEqual(mock_mail.call_count, 1)

    def test_every_route_failing_returns_false_without_raising(self):
        with (
            patch(
                "email_app.services.relay_campaign_alerts.slack_api_enabled",
                return_value=False,
            ),
            patch(
                "email_app.services.relay_campaign_alerts.send_mail",
                side_effect=RuntimeError("relay timeout"),
            ),
            self.assertLogs("email_app.services.relay_campaign_alerts", level="ERROR") as logs,
        ):
            self.assertFalse(self._alert())
        self.assertTrue(any("Every alert route failed" in line for line in logs.output))

    def test_cooldown_suppresses_repeat_alerts_for_the_same_campaign_and_operation(self):
        with (
            patch(
                "email_app.services.relay_campaign_alerts.slack_api_enabled",
                return_value=True,
            ),
            patch(
                "email_app.services.relay_campaign_alerts.staff_alert_channel_id",
                return_value="C123",
            ),
            patch(
                "email_app.services.relay_campaign_alerts.post_staff_slack_message",
                return_value=SlackPostResult(ok=True),
            ) as mock_post,
        ):
            self.assertTrue(self._alert(operation="queue"))
            self.assertFalse(self._alert(operation="queue"))
            self.assertEqual(mock_post.call_count, 1)

    def test_a_different_operation_or_campaign_still_alerts_within_the_cooldown(self):
        other = EmailCampaign.objects.create(
            subject="Other Subject", body="Other body.", status="sending",
        )
        with (
            patch(
                "email_app.services.relay_campaign_alerts.slack_api_enabled",
                return_value=True,
            ),
            patch(
                "email_app.services.relay_campaign_alerts.staff_alert_channel_id",
                return_value="C123",
            ),
            patch(
                "email_app.services.relay_campaign_alerts.post_staff_slack_message",
                return_value=SlackPostResult(ok=True),
            ) as mock_post,
        ):
            self.assertTrue(self._alert(operation="queue"))
            self.assertTrue(self._alert(operation="wave stats refresh"))
            self.assertTrue(self._alert(campaign=other, operation="queue"))
        self.assertEqual(mock_post.call_count, 3)

    def test_bypass_cooldown_delivers_every_time(self):
        with (
            patch(
                "email_app.services.relay_campaign_alerts.slack_api_enabled",
                return_value=True,
            ),
            patch(
                "email_app.services.relay_campaign_alerts.staff_alert_channel_id",
                return_value="C123",
            ),
            patch(
                "email_app.services.relay_campaign_alerts.post_staff_slack_message",
                return_value=SlackPostResult(ok=True),
            ) as mock_post,
        ):
            self.assertTrue(alert_relay_campaign_failure(
                self.campaign, "progress check", RuntimeError("x"), bypass_cooldown=True,
            ))
            self.assertTrue(alert_relay_campaign_failure(
                self.campaign, "progress check", RuntimeError("x"), bypass_cooldown=True,
            ))
        self.assertEqual(mock_post.call_count, 2)

    def test_an_unavailable_cache_fails_open_and_still_alerts(self):
        with (
            patch(
                "email_app.services.relay_campaign_alerts.slack_api_enabled",
                return_value=True,
            ),
            patch(
                "email_app.services.relay_campaign_alerts.staff_alert_channel_id",
                return_value="C123",
            ),
            patch(
                "email_app.services.relay_campaign_alerts.post_staff_slack_message",
                return_value=SlackPostResult(ok=True),
            ) as mock_post,
            patch(
                "email_app.services.relay_campaign_alerts.caches",
            ) as mock_caches,
        ):
            mock_caches.__getitem__.side_effect = RuntimeError("cache down")
            self.assertTrue(self._alert())
        self.assertEqual(mock_post.call_count, 1)

    def test_alert_event_notice_failure_posts_to_staff_slack_with_details(self):
        from datetime import timedelta

        from django.utils import timezone

        now = timezone.now()
        event = Event.objects.create(
            title="Event 77",
            slug="event-77-alert-test",
            start_datetime=now - timedelta(hours=2),
            end_datetime=now - timedelta(hours=1),
            status="completed",
            published=True,
        )
        with (
            patch(
                "email_app.services.relay_campaign_alerts.slack_api_enabled",
                return_value=True,
            ),
            patch(
                "email_app.services.relay_campaign_alerts.staff_alert_channel_id",
                return_value="C123",
            ),
            patch(
                "email_app.services.relay_campaign_alerts.post_staff_slack_message",
                return_value=SlackPostResult(ok=True),
            ) as mock_post,
        ):
            ok = alert_event_notice_failure(
                event,
                "recap email campaign",
                RuntimeError("POST /api/campaigns/k/submit returned HTTP 404"),
                external_key="event-recap-ready-77-abc",
                recipient_count=59,
            )

        self.assertTrue(ok)
        self.assertEqual(mock_post.call_count, 1)
        payload = mock_post.call_args.args[1]
        self.assertIn("event-recap-ready-77-abc", payload["text"])
        self.assertIn("59", payload["text"])
        self.assertIn("HTTP 404", payload["text"])
