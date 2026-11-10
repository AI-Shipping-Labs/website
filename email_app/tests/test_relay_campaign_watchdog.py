"""Stall watchdog and progress clock for native Relay campaigns (issue #2072).

``relay_campaign_state`` never advances past ``queued`` by itself, so the
watchdog asks Relay whether a send is moving. These tests drive
``run_relay_campaign_watchdog`` and the progress helpers against a mocked
``get_native_campaign_stats``.
"""

from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from email_app.models import EmailCampaign
from email_app.services.relay_campaigns import RelayCampaignApiError
from email_app.tasks.relay_campaign_watchdog import (
    DEFAULT_STALL_TIMEOUT_MINUTES,
    run_relay_campaign_watchdog,
    stall_timeout_minutes,
)
from jobs.schedule_reconciliation import SCHEDULE_DEFINITIONS
from tests.fixtures import TierSetupMixin, create_user_with_membership

STATS_SOURCE = "email_app.services.relay_campaigns.get_native_campaign_stats"


def native(key="aishl-campaign-1", status="sending", sent=0, **extra):
    payload = {
        "status": status,
        "recipient_count": 10,
        "skipped_count": 0,
        "sent_count": sent,
        "delivered_count": sent,
        "unique_open_count": 0,
        "open_count": 0,
        "unique_click_count": 0,
        "click_count": 0,
        "unsubscribe_count": 0,
        "bounce_count": 0,
        "complaint_count": 0,
        "send_halted": False,
        "send_halt_reason": "",
    }
    payload.update(extra)
    return payload


def read_result(status="sending", sent=0, **extra):
    return native(status=status, sent=sent, **extra)


class RelayWatchdogTestBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.campaign = EmailCampaign.objects.create(
            subject="Watchdog Subject",
            body="Watchdog body.",
            status="sending",
            audience_snapshotted_at=timezone.now(),
        )

    def setUp(self):
        self.campaign.refresh_from_db()

    def _stale(self, minutes=None):
        minutes = DEFAULT_STALL_TIMEOUT_MINUTES + 10 if minutes is None else minutes
        self.campaign.relay_campaign_progress_at = (
            timezone.now() - timedelta(minutes=minutes)
        )
        self.campaign.save(update_fields=["relay_campaign_progress_at"])


class StallTimeoutSettingTest(RelayWatchdogTestBase):
    def test_setting_defaults_and_falls_back_to_30(self):
        cases = {
            None: DEFAULT_STALL_TIMEOUT_MINUTES,
            "": DEFAULT_STALL_TIMEOUT_MINUTES,
            "abc": DEFAULT_STALL_TIMEOUT_MINUTES,
            "0": DEFAULT_STALL_TIMEOUT_MINUTES,
            "-5": DEFAULT_STALL_TIMEOUT_MINUTES,
            "15": 15,
        }
        for raw, expected in cases.items():
            with (
                self.subTest(raw=raw),
                patch(
                    "email_app.tasks.relay_campaign_watchdog.get_config",
                    return_value=raw,
                ),
            ):
                self.assertEqual(stall_timeout_minutes(), expected)

    def test_watchdog_is_registered_every_five_minutes(self):
        entry = next(
            item for item in SCHEDULE_DEFINITIONS if item.name == "relay-campaign-watchdog"
        )
        self.assertEqual(entry.func, "email_app.tasks.relay_campaign_watchdog.run_relay_campaign_watchdog")
        self.assertEqual(entry.cron, "*/5 * * * *")


class WatchdogPendingTest(RelayWatchdogTestBase):
    def test_stale_pending_campaign_times_out_without_calling_relay(self):
        self.campaign.relay_campaign_state = "pending"
        self.campaign.save(update_fields=["relay_campaign_state"])
        self._stale()

        with (
            patch(STATS_SOURCE, side_effect=AssertionError("no Relay read expected")),
            patch(
                "email_app.services.relay_campaign_alerts.slack_api_enabled",
                return_value=False,
            ),
            patch(
                "email_app.services.relay_campaign_alerts.send_mail", return_value=1,
            ) as mock_mail,
        ):
            result = run_relay_campaign_watchdog()

        self.assertEqual(result, {"checked": 1, "failed": 0})
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.relay_campaign_state, "timed_out")
        self.assertEqual(self.campaign.status, "needs_attention")
        self.assertIn("No progress for 30 minutes", self.campaign.relay_campaign_error)
        self.assertIn("Last Relay status: not queued, 0 sent", self.campaign.relay_campaign_error)
        self.assertIn("Check Relay campaign (not assigned)", self.campaign.relay_campaign_error)
        self.assertEqual(mock_mail.call_count, 1)

    def test_fresh_pending_campaign_is_left_alone(self):
        self.campaign.relay_campaign_state = "pending"
        self.campaign.relay_campaign_progress_at = timezone.now()
        self.campaign.save(update_fields=[
            "relay_campaign_state", "relay_campaign_progress_at",
        ])

        with patch(STATS_SOURCE, side_effect=AssertionError("no Relay read expected")):
            result = run_relay_campaign_watchdog()

        self.assertEqual(result, {"checked": 1, "failed": 0})
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.relay_campaign_state, "pending")

    def test_missing_clock_is_started_without_a_false_alarm(self):
        self.campaign.relay_campaign_state = "pending"
        self.campaign.relay_campaign_progress_at = None
        self.campaign.save(update_fields=[
            "relay_campaign_state", "relay_campaign_progress_at",
        ])

        with patch(STATS_SOURCE, side_effect=AssertionError("no Relay read expected")):
            run_relay_campaign_watchdog()

        self.campaign.refresh_from_db()
        self.assertIsNotNone(self.campaign.relay_campaign_progress_at)
        self.assertEqual(self.campaign.relay_campaign_state, "pending")


class WatchdogQueuedTest(RelayWatchdogTestBase):
    def _queued(self, sent=0):
        self.campaign.relay_campaign_state = "queued"
        self.campaign.relay_campaign_key = f"aishl-campaign-{self.campaign.pk}"
        self.campaign.relay_campaign_keys = [self.campaign.relay_campaign_key]
        self.campaign.relay_campaign_progress_at = (
            timezone.now() - timedelta(minutes=45)
        )
        self.campaign.relay_campaign_stats = {"sent_count": sent}
        self.campaign.save(update_fields=[
            "relay_campaign_state", "relay_campaign_key", "relay_campaign_keys",
            "relay_campaign_progress_at", "relay_campaign_stats",
        ])

    def test_campaign_relay_reports_sent_settles_without_alert(self):
        self._queued()
        with (
            patch(STATS_SOURCE, return_value=read_result(status="sent", sent=10)),
            patch(
                "email_app.services.relay_campaign_alerts.send_mail",
            ) as mock_mail,
        ):
            run_relay_campaign_watchdog()
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.relay_campaign_state, "sent")
        self.assertEqual(self.campaign.status, "sent")
        self.assertEqual(self.campaign.relay_campaign_error, "")
        mock_mail.assert_not_called()

    def test_relay_failed_or_cancelled_is_raised_immediately(self):
        self._queued()
        with (
            patch(STATS_SOURCE, return_value=read_result(status="failed")),
            patch(
                "email_app.services.relay_campaign_alerts.slack_api_enabled",
                return_value=False,
            ),
            patch(
                "email_app.services.relay_campaign_alerts.send_mail", return_value=1,
            ),
        ):
            run_relay_campaign_watchdog()
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.relay_campaign_state, "relay_failed")
        self.assertEqual(self.campaign.status, "needs_attention")
        self.assertIn("Relay reported campaign", self.campaign.relay_campaign_error)
        self.assertIn("as failed", self.campaign.relay_campaign_error)

    def test_no_progress_past_the_timeout_times_out_with_a_named_key(self):
        self._queued(sent=3)
        with (
            patch(STATS_SOURCE, return_value=read_result(status="sending", sent=3)),
            patch(
                "email_app.services.relay_campaign_alerts.slack_api_enabled",
                return_value=False,
            ),
            patch(
                "email_app.services.relay_campaign_alerts.send_mail", return_value=1,
            ),
        ):
            run_relay_campaign_watchdog()
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.relay_campaign_state, "timed_out")
        self.assertIn("No progress for 30 minutes", self.campaign.relay_campaign_error)
        self.assertIn("Last Relay status: sending, 3 sent", self.campaign.relay_campaign_error)

    def test_recent_progress_stays_queued(self):
        self._queued()
        self.campaign.relay_campaign_progress_at = timezone.now() - timedelta(minutes=5)
        self.campaign.save(update_fields=["relay_campaign_progress_at"])
        with patch(STATS_SOURCE, return_value=read_result(status="sending", sent=3)):
            run_relay_campaign_watchdog()
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.relay_campaign_state, "queued")

    def test_a_paused_campaign_never_times_out_and_gets_a_fresh_clock(self):
        self._queued()
        stale = timezone.now() - timedelta(minutes=120)
        self.campaign.relay_campaign_progress_at = stale
        self.campaign.save(update_fields=["relay_campaign_progress_at"])
        with patch(
            STATS_SOURCE,
            return_value=read_result(status="sending", send_halted=True, send_halt_reason="paused"),
        ):
            run_relay_campaign_watchdog()
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.relay_campaign_state, "queued")
        self.assertGreater(self.campaign.relay_campaign_progress_at, stale)

    def test_relay_unreadable_past_the_timeout_uses_the_unreadable_message(self):
        self._queued()
        with (
            patch(STATS_SOURCE, side_effect=RelayCampaignApiError("HTTP 503")),
            patch(
                "email_app.services.relay_campaign_alerts.slack_api_enabled",
                return_value=False,
            ),
            patch(
                "email_app.services.relay_campaign_alerts.send_mail", return_value=1,
            ),
        ):
            run_relay_campaign_watchdog()
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.relay_campaign_state, "timed_out")
        self.assertIn("Relay could not be read", self.campaign.relay_campaign_error)


class WatchdogAttentionTest(RelayWatchdogTestBase):
    def _attention(self, state="timed_out", baseline=3):
        self.campaign.relay_campaign_state = state
        self.campaign.relay_campaign_key = f"aishl-campaign-{self.campaign.pk}"
        self.campaign.relay_campaign_keys = [self.campaign.relay_campaign_key]
        self.campaign.status = "needs_attention"
        self.campaign.relay_campaign_error = "stalled"
        self.campaign.relay_campaign_attention_sent_count = baseline
        self.campaign.relay_campaign_attention_alerted_keys = [self.campaign.relay_campaign_key]
        self.campaign.save(update_fields=[
            "relay_campaign_state", "status", "relay_campaign_error",
            "relay_campaign_attention_sent_count", "relay_campaign_attention_alerted_keys",
            "relay_campaign_key", "relay_campaign_keys",
        ])

    def test_attention_clears_to_sent_when_relay_reports_all_sent(self):
        self._attention()
        with (
            patch(STATS_SOURCE, return_value=read_result(status="sent", sent=10)),
            patch(
                "email_app.services.relay_campaign_alerts.send_mail",
            ) as mock_mail,
        ):
            run_relay_campaign_watchdog()
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.relay_campaign_state, "sent")
        self.assertEqual(self.campaign.status, "sent")
        self.assertEqual(self.campaign.relay_campaign_error, "")
        self.assertIsNone(self.campaign.relay_campaign_attention_sent_count)
        mock_mail.assert_not_called()

    def test_attention_clears_to_queued_when_progress_resumed(self):
        self._attention(baseline=3)
        with (
            patch(STATS_SOURCE, return_value=read_result(status="sending", sent=7)),
            patch(
                "email_app.services.relay_campaign_alerts.send_mail",
            ) as mock_mail,
        ):
            run_relay_campaign_watchdog()
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.relay_campaign_state, "queued")
        self.assertEqual(self.campaign.relay_campaign_error, "")
        self.assertNotEqual(self.campaign.status, "needs_attention")
        mock_mail.assert_not_called()

    def test_timed_out_campaign_relay_reports_failed_upgrades_to_relay_failed(self):
        self._attention(state="timed_out")
        with (
            patch(STATS_SOURCE, return_value=read_result(status="cancelled")),
            patch(
                "email_app.services.relay_campaign_alerts.slack_api_enabled",
                return_value=False,
            ),
            patch(
                "email_app.services.relay_campaign_alerts.send_mail", return_value=1,
            ),
        ):
            run_relay_campaign_watchdog()
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.relay_campaign_state, "relay_failed")
        self.assertIn("as cancelled", self.campaign.relay_campaign_error)

    def test_attention_without_progress_does_not_alert_a_second_time(self):
        self._attention()
        with (
            patch(STATS_SOURCE, return_value=read_result(status="sending", sent=3)),
            patch(
                "email_app.services.relay_campaign_alerts.slack_api_enabled",
                return_value=False,
            ),
            patch(
                "email_app.services.relay_campaign_alerts.send_mail", return_value=1,
            ) as mock_mail,
        ):
            run_relay_campaign_watchdog()
            run_relay_campaign_watchdog()
        self.assertEqual(mock_mail.call_count, 0)
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.relay_campaign_state, "timed_out")

    def test_a_failed_alert_is_retried_on_the_next_run(self):
        self._attention()
        self.campaign.relay_campaign_attention_alerted_keys = []
        self.campaign.save(update_fields=["relay_campaign_attention_alerted_keys"])
        with (
            patch(STATS_SOURCE, return_value=read_result(status="sending", sent=3)),
            patch(
                "email_app.services.relay_campaign_alerts.slack_api_enabled",
                return_value=False,
            ),
            patch(
                "email_app.services.relay_campaign_alerts.send_mail",
                side_effect=[RuntimeError("relay down"), 1],
            ) as mock_mail,
        ):
            run_relay_campaign_watchdog()
            run_relay_campaign_watchdog()
        self.assertEqual(mock_mail.call_count, 2)
        self.campaign.refresh_from_db()
        self.assertEqual(
            self.campaign.relay_campaign_attention_alerted_keys,
            [self.campaign.relay_campaign_key],
        )

    def test_attention_state_survives_a_successful_stats_read(self):
        """A Studio page view must not clear or overwrite the attention message."""
        self._attention()
        with patch(STATS_SOURCE, return_value=read_result(status="sending", sent=3)):
            from email_app.services.relay_campaigns import refresh_campaign_relay_stats_loud

            refresh_campaign_relay_stats_loud(self.campaign)
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.relay_campaign_state, "timed_out")
        self.assertEqual(self.campaign.status, "needs_attention")
        self.assertEqual(self.campaign.relay_campaign_error, "stalled")


class WatchdogIsolationTest(RelayWatchdogTestBase):
    def test_one_campaign_failing_does_not_stop_the_others(self):
        first = self.campaign
        first.relay_campaign_state = "queued"
        first.relay_campaign_key = f"aishl-campaign-{first.pk}"
        first.relay_campaign_keys = [first.relay_campaign_key]
        first.relay_campaign_progress_at = timezone.now() - timedelta(minutes=45)
        first.save(update_fields=[
            "relay_campaign_state", "relay_campaign_key", "relay_campaign_keys",
            "relay_campaign_progress_at",
        ])
        second = EmailCampaign.objects.create(
            subject="Second Subject",
            body="Second body.",
            status="sent",
        )
        second.relay_campaign_state = "queued"
        second.relay_campaign_key = f"aishl-campaign-{second.pk}"
        second.relay_campaign_keys = [second.relay_campaign_key]
        second.relay_campaign_progress_at = timezone.now() - timedelta(minutes=45)
        second.save(update_fields=[
            "relay_campaign_state", "relay_campaign_key", "relay_campaign_keys",
            "relay_campaign_progress_at",
        ])

        def stats_side_effect(key, session=None, allow_not_found=False):
            if key == first.relay_campaign_key:
                raise RelayCampaignApiError("save blew up")
            return read_result(status="sent", sent=10)

        with (
            patch(STATS_SOURCE, side_effect=stats_side_effect),
            patch(
                "email_app.tasks.relay_campaign_watchdog.refresh_campaign_status",
                side_effect=RuntimeError("status boom"),
            ),
            patch(
                "email_app.services.relay_campaign_alerts.slack_api_enabled",
                return_value=False,
            ),
            patch(
                "email_app.services.relay_campaign_alerts.send_mail", return_value=1,
            ),
        ):
            result = run_relay_campaign_watchdog()

        self.assertEqual(result["checked"], 2)
        second.refresh_from_db()
        self.assertEqual(second.relay_campaign_state, "sent")


class WatchdogSelectionTest(TierSetupMixin, RelayWatchdogTestBase):
    def test_failed_campaign_with_pending_deliveries_is_watched_and_times_out(self):
        user = create_user_with_membership(
            email=f"watchdog-{self.campaign.pk}@test.com",
            tier=self.free_tier,
            email_verified=True,
            unsubscribed=False,
        )
        from email_app.models import CampaignDelivery

        CampaignDelivery.objects.create(
            campaign=self.campaign,
            user=user,
            recipient_user_pk=user.pk,
            recipient_email=user.email,
            state=CampaignDelivery.State.PENDING,
        )
        self.campaign.status = "failed"
        self.campaign.relay_campaign_state = "failed"
        self.campaign.relay_campaign_progress_at = timezone.now() - timedelta(minutes=45)
        self.campaign.save(update_fields=[
            "status", "relay_campaign_state", "relay_campaign_progress_at",
        ])

        with (
            patch(STATS_SOURCE, side_effect=AssertionError("no Relay read expected")),
            patch(
                "email_app.services.relay_campaign_alerts.slack_api_enabled",
                return_value=False,
            ),
            patch(
                "email_app.services.relay_campaign_alerts.send_mail", return_value=1,
            ),
        ):
            result = run_relay_campaign_watchdog()

        self.assertEqual(result, {"checked": 1, "failed": 0})
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.relay_campaign_state, "timed_out")
        delivery_count = CampaignDelivery.objects.filter(
            campaign=self.campaign, state=CampaignDelivery.State.PENDING,
        ).count()
        self.assertEqual(delivery_count, 1)


class ProgressClockTest(RelayWatchdogTestBase):
    def test_begin_native_queue_starts_the_clock_on_a_new_key_only(self):
        from email_app.services.relay_campaign_progress import (
            begin_native_queue,
            mark_native_queue_accepted,
            record_native_queue_failure,
        )

        now = timezone.now()
        self.campaign.relay_campaign_state = "failed"
        self.campaign.save(update_fields=["relay_campaign_state"])

        begin_native_queue(self.campaign, f"aishl-campaign-{self.campaign.pk}")
        self.assertIsNotNone(self.campaign.relay_campaign_progress_at)

        # A retry of the same key must not restart the clock.
        self.campaign.relay_campaign_progress_at = now - timedelta(minutes=10)
        self.campaign.save(update_fields=["relay_campaign_progress_at"])
        begin_native_queue(self.campaign, f"aishl-campaign-{self.campaign.pk}")
        self.assertEqual(
            self.campaign.relay_campaign_progress_at, now - timedelta(minutes=10),
        )

        # A queue failure keeps the clock and the row readable.
        record_native_queue_failure(
            self.campaign, RelayCampaignApiError("HTTP 503"),
        )
        self.assertEqual(self.campaign.relay_campaign_state, "failed")
        self.assertEqual(self.campaign.status, "needs_attention")
        self.assertIn("HTTP 503", self.campaign.relay_campaign_error)

        # Acceptance clears the attention state and error.
        mark_native_queue_accepted(self.campaign, already_queued=False)
        self.assertEqual(self.campaign.relay_campaign_state, "queued")
        self.assertEqual(self.campaign.relay_campaign_error, "")
        self.assertIsNone(self.campaign.relay_campaign_attention_sent_count)

    def test_begin_native_queue_keeps_an_attention_state_until_success(self):
        from email_app.services.relay_campaign_progress import begin_native_queue

        self.campaign.relay_campaign_state = "timed_out"
        self.campaign.relay_campaign_error = "stalled"
        self.campaign.save(update_fields=["relay_campaign_state", "relay_campaign_error"])

        begin_native_queue(self.campaign, f"aishl-campaign-{self.campaign.pk}-2")
        self.assertEqual(self.campaign.relay_campaign_state, "timed_out")
        self.assertEqual(self.campaign.relay_campaign_error, "stalled")
        self.assertIn(
            f"aishl-campaign-{self.campaign.pk}-2", self.campaign.relay_campaign_keys,
        )

    def test_stats_refresh_stamps_the_clock_when_the_sent_total_grows(self):
        from email_app.services.relay_campaign_progress import (
            refresh_campaign_relay_stats,
        )

        self.campaign.relay_campaign_key = f"aishl-campaign-{self.campaign.pk}"
        self.campaign.relay_campaign_keys = [self.campaign.relay_campaign_key]
        self.campaign.relay_campaign_progress_at = (
            timezone.now() - timedelta(hours=1)
        )
        self.campaign.save(update_fields=[
            "relay_campaign_key", "relay_campaign_keys", "relay_campaign_progress_at",
        ])
        old = self.campaign.relay_campaign_progress_at

        with patch(STATS_SOURCE, return_value=read_result(status="sending", sent=5)):
            totals = refresh_campaign_relay_stats(self.campaign)
        self.assertEqual(totals["sent_count"], 5)
        self.campaign.refresh_from_db()
        stamped = self.campaign.relay_campaign_progress_at
        self.assertGreater(stamped, old)

        # A read without growth must not move the clock again.
        with patch(STATS_SOURCE, return_value=read_result(status="sending", sent=5)):
            refresh_campaign_relay_stats(self.campaign)
        self.campaign.refresh_from_db()
        self.assertEqual(self.campaign.relay_campaign_progress_at, stamped)

    def test_relay_failed_status_is_reported_by_the_aggregate_stats(self):
        from email_app.services.relay_campaign_progress import (
            refresh_campaign_relay_stats,
        )

        self.campaign.relay_campaign_key = f"aishl-campaign-{self.campaign.pk}"
        self.campaign.relay_campaign_keys = [self.campaign.relay_campaign_key]
        self.campaign.save(update_fields=["relay_campaign_key", "relay_campaign_keys"])

        with patch(STATS_SOURCE, return_value=read_result(status="cancelled", sent=1)):
            totals = refresh_campaign_relay_stats(self.campaign)
        self.assertEqual(totals["status"], "cancelled")

        with patch(STATS_SOURCE, return_value=read_result(status="sent", sent=9)):
            totals = refresh_campaign_relay_stats(self.campaign)
        self.assertEqual(totals["status"], "sent")

    def test_campaigns_without_a_relay_key_are_not_read(self):
        from email_app.services.relay_campaign_progress import (
            refresh_campaign_relay_stats,
        )

        self.assertIsNone(refresh_campaign_relay_stats(self.campaign))
