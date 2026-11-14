"""Tests for Studio Relay event notice status panel and campaign stubs."""

from datetime import UTC, datetime, time, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, tag
from django.urls import reverse

from email_app.models import EmailLog
from email_app.services.relay_campaigns import (
    RelayCampaignError,
    get_native_campaign_stats,
    submit_native_campaign,
)
from email_app.testing_relay_transactional import install_test_relay_transactional_transport
from events.models import Event, EventSeries

User = get_user_model()


@tag("core")
class StudioEventRelayNoticeStatusTest(TestCase):
    """Studio event edit displays sent/delivered/pending counts from Relay."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email="staff-relay-notice@test.com",
            is_staff=True,
        )
        cls.attendee = User.objects.create_user(
            email="attendee-relay-notice@test.com",
        )
        start = datetime(2026, 11, 24, 17, 0, tzinfo=UTC)
        cls.event = Event.objects.create(
            title="Evals in CI",
            slug="evals-in-ci-notice-status",
            start_datetime=start,
            end_datetime=start + timedelta(hours=1),
            ics_sequence=2,
        )
        EmailLog.objects.create(
            user=cls.attendee,
            event=cls.event,
            email_type="event_rescheduled",
            ses_message_id="cmp-123",
        )

    def test_studio_event_edit_renders_relay_notice_counts(self):
        self.client.force_login(self.staff)
        url = reverse("studio_event_edit", kwargs={"event_id": self.event.pk})

        with patch(
            "events.services.relay_event_notice.get_native_campaign_stats",
            return_value={
                "external_key": f"event-rescheduled-{self.event.pk}-seq-2",
                "sent_count": 12,
                "delivered_count": 10,
                "pending_count": 2,
                "recipient_count": 14,
            },
        ):
            response = self.client.get(url)

        self.assertContains(response, "relay-notice-status-panel")
        self.assertContains(response, "Reschedule notice")
        self.assertContains(response, "Sent: 12 /")
        self.assertContains(response, "Delivered: 10 /")
        self.assertContains(response, "Pending: 2")

    def test_studio_event_edit_handles_relay_error_gracefully(self):
        self.client.force_login(self.staff)
        url = reverse("studio_event_edit", kwargs={"event_id": self.event.pk})

        with patch(
            "events.services.relay_event_notice.get_native_campaign_stats",
            side_effect=RelayCampaignError("Relay unavailable"),
        ):
            response = self.client.get(url)

        self.assertNotContains(response, "relay-notice-status-panel", status_code=200)

    def test_offline_relay_campaign_stub_handles_submit_and_stats(self):
        from email_app import relay_transactional
        from email_app.services import relay_campaigns

        orig_t_req, orig_t_cfg = relay_transactional.requests, relay_transactional.get_config
        orig_c_req, orig_c_cfg = relay_campaigns.requests, relay_campaigns.get_config
        install_test_relay_transactional_transport()
        try:
            submit_result = submit_native_campaign(
                external_key="event-workshop-ready-stub-test",
                subject="Workshop ready",
                template_key="event_workshop_ready",
                recipients=[
                    {"email": "a@test.com", "source_object_key": "reg:1"},
                    {"email": "b@test.com", "source_object_key": "reg:2"},
                ],
            )
            stats = get_native_campaign_stats("event-workshop-ready-stub-test")
        finally:
            relay_transactional.requests = orig_t_req
            relay_transactional.get_config = orig_t_cfg
            relay_campaigns.requests = orig_c_req
            relay_campaigns.get_config = orig_c_cfg

        self.assertEqual(submit_result["recipient_count"], 2)
        self.assertEqual(stats["sent_count"], 0)
        self.assertEqual(stats["delivered_count"], 0)
        self.assertEqual(stats["pending_count"], 2)


@tag("core")
class StudioSeriesRelayNoticeStatusTest(TestCase):
    """Series update and cancellation campaigns appear in Relay notice status."""

    def test_series_update_and_cancellation_campaigns_listed(self):
        from events.services.relay_event_notice import get_event_notice_statuses

        series = EventSeries.objects.create(name="Office Hours", start_time=time(18, 0))
        start = datetime(2026, 11, 24, 17, 0, tzinfo=UTC)
        event = Event.objects.create(
            title="OH 1", slug="oh-1-series-notice", start_datetime=start,
            end_datetime=start + timedelta(hours=1), event_series=series,
            ics_sequence=3,
        )
        other = Event.objects.create(
            title="OH 2", slug="oh-2-series-notice", start_datetime=start,
            end_datetime=start + timedelta(hours=1), event_series=series,
        )
        user = User.objects.create_user(email="series-notice@test.com")
        update_key = f"series-update-{series.pk}-event-{event.pk}-seq-3"
        cancel_key = f"series-cancellation-{series.pk}-event-{event.pk}-seq-3"
        other_key = f"series-update-{series.pk}-event-{other.pk}-seq-0"
        for email_type, key in (
            ("series_update", update_key),
            ("series_cancellation", cancel_key),
            ("series_update", other_key),
        ):
            EmailLog.objects.create(
                user=user, email_type=email_type, ses_message_id=key,
                dedupe_key=f"{email_type}:{key}", recipient_email=user.email,
            )

        stats = {
            "sent_count": 1, "delivered_count": 1,
            "pending_count": 0, "recipient_count": 1,
        }
        with patch(
            "events.services.relay_event_notice.get_native_campaign_stats",
            return_value=stats,
        ):
            rows = get_event_notice_statuses(event)

        self.assertEqual(
            [(r["label"], r["external_key"]) for r in rows],
            [
                ("Series update notice", update_key),
                ("Series cancellation notice", cancel_key),
            ],
        )
