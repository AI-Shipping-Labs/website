"""Issue #963: the "set/update your timezone" line in event emails.

Each of the five future-time event email templates renders a single
contextual timezone line whose wording matches how ``format_user_datetime``
rendered the time (prominent "Set your timezone" on UTC fallback, quieter
"Change your timezone" when a zone is set), linking to the account
Display Preferences timezone control. ``post_event_followup`` (a recap
with no future time) must NOT carry the line.

These tests drive the production senders with SES disabled / boto3 mocked
and assert on the rendered HTML body, so a sender that forgets to pass
``timezone_help`` is caught here, not just at the helper.
"""

import email as email_lib
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from email_app.services.email_service import EmailService
from events.models import Event, EventRegistration, EventSeries
from events.services.host_invite import _send as send_host_invite
from events.services.registration_email import send_registration_confirmation
from events.services.series_invite import send_series_registration_invite
from events.tasks.notify_reschedule import send_reschedule_notice_one
from integrations.config import clear_config_cache
from payments.models import Tier

User = get_user_model()

ACCOUNT_FRAGMENT = "/account/#display-preferences-section"


def _html_from_raw(raw):
    msg = email_lib.message_from_string(raw)
    for part in msg.walk():
        if part.get_content_type() == "text/html":
            return part.get_payload(decode=True).decode("utf-8")
    raise AssertionError("no text/html part in message")


@override_settings(
    SITE_BASE_URL="https://env.example.com",
    SES_TRANSACTIONAL_FROM_EMAIL="noreply@aishippinglabs.com",
    AWS_SES_REGION="us-east-1",
    AWS_ACCESS_KEY_ID="test-key",
    AWS_SECRET_ACCESS_KEY="test-secret",
    SES_ENABLED=True,
)
class RegistrationEmailTimezoneLineTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        Tier.objects.get_or_create(
            slug="free", defaults={"name": "Free", "level": 0},
        )
        cls.event = Event.objects.create(
            slug="tz-line-event",
            title="Timezone Line Event",
            description="Event used in TZ line tests.",
            start_datetime=datetime(2026, 6, 1, 16, 0, tzinfo=UTC),
            end_datetime=datetime(2026, 6, 1, 17, 0, tzinfo=UTC),
            status="upcoming",
        )

    def setUp(self):
        clear_config_cache()

    def tearDown(self):
        clear_config_cache()

    def _capture_html(self, user):
        registration = EventRegistration.objects.create(
            event=self.event, user=user,
        )
        with patch("events.services.registration_email.boto3") as mock_boto3:
            client = mock_boto3.client.return_value
            client.send_email.return_value = {"MessageId": "tz-line-1"}
            send_registration_confirmation(registration)
            raw = client.send_email.call_args.kwargs["Content"]["Raw"]["Data"]
        return _html_from_raw(raw)

    def test_utc_fallback_recipient_sees_set_variant_and_link(self):
        user = User.objects.create_user(
            email="no-tz@example.com", preferred_timezone="",
        )
        html = self._capture_html(user)

        self.assertIn("Set your timezone", html)
        self.assertIn(f'href="https://env.example.com{ACCOUNT_FRAGMENT}"', html)
        # #666 regression guard: the time still renders with the UTC token.
        self.assertIn("16:00 UTC", html)

    def test_zoned_recipient_sees_change_variant_not_set(self):
        user = User.objects.create_user(
            email="ny@example.com", preferred_timezone="America/New_York",
        )
        html = self._capture_html(user)

        self.assertIn("Change your timezone", html)
        self.assertIn(f'href="https://env.example.com{ACCOUNT_FRAGMENT}"', html)
        self.assertNotIn("Set your timezone", html)
        # Time renders in the recipient's zone, not UTC.
        self.assertIn("America/New_York", html)
        self.assertNotIn("16:00 UTC", html)


@override_settings(
    SITE_BASE_URL="https://env.example.com",
    SES_TRANSACTIONAL_FROM_EMAIL="noreply@aishippinglabs.com",
    AWS_SES_REGION="us-east-1",
    AWS_ACCESS_KEY_ID="test-key",
    AWS_SECRET_ACCESS_KEY="test-secret",
    SES_ENABLED=True,
)
class RescheduleEmailTimezoneLineTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        Tier.objects.get_or_create(
            slug="free", defaults={"name": "Free", "level": 0},
        )

    def setUp(self):
        clear_config_cache()
        self.event = Event.objects.create(
            slug="tz-reschedule-event",
            title="Reschedule TZ Event",
            start_datetime=datetime(2026, 6, 8, 16, 0, tzinfo=UTC),
            end_datetime=datetime(2026, 6, 8, 17, 0, tzinfo=UTC),
            status="upcoming",
        )

    def tearDown(self):
        clear_config_cache()

    def _capture_html(self, user):
        EventRegistration.objects.create(event=self.event, user=user)
        old_start = "2026-06-01T16:00:00+00:00"
        with patch("events.services.registration_email.boto3") as mock_boto3:
            client = mock_boto3.client.return_value
            client.send_email.return_value = {"MessageId": "tz-resched-1"}
            send_reschedule_notice_one(self.event.pk, user.pk, old_start)
            raw = client.send_email.call_args.kwargs["Content"]["Raw"]["Data"]
        return _html_from_raw(raw)

    def test_one_line_covers_both_times_for_zoned_recipient(self):
        user = User.objects.create_user(
            email="berlin-resched@example.com",
            preferred_timezone="Europe/Berlin",
        )
        html = self._capture_html(user)

        # Exactly one timezone line for an email carrying two times.
        self.assertEqual(html.count("Change your timezone"), 1)
        self.assertNotIn("Set your timezone", html)
        self.assertIn(f'href="https://env.example.com{ACCOUNT_FRAGMENT}"', html)
        # Both the old and new times render in the recipient's zone.
        self.assertEqual(html.count("Europe/Berlin"), 2)
        self.assertNotIn("UTC", html)


@override_settings(
    SITE_BASE_URL="https://env.example.com",
    SES_TRANSACTIONAL_FROM_EMAIL="noreply@aishippinglabs.com",
    AWS_SES_REGION="us-east-1",
    AWS_ACCESS_KEY_ID="test-key",
    AWS_SECRET_ACCESS_KEY="test-secret",
    SES_ENABLED=True,
)
class HostInviteTimezoneLineTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        Tier.objects.get_or_create(
            slug="free", defaults={"name": "Free", "level": 0},
        )

    def setUp(self):
        clear_config_cache()
        self.event = Event.objects.create(
            slug="tz-host-event",
            title="Host TZ Event",
            start_datetime=datetime(2026, 6, 8, 16, 0, tzinfo=UTC),
            end_datetime=datetime(2026, 6, 8, 17, 0, tzinfo=UTC),
            status="upcoming",
        )

    def tearDown(self):
        clear_config_cache()

    def test_utc_fallback_host_sees_set_variant_and_link(self):
        # Host is a registered platform user with no timezone preference.
        User.objects.create_user(
            email="host@example.com", preferred_timezone="",
        )
        with patch("events.services.registration_email.boto3") as mock_boto3:
            client = mock_boto3.client.return_value
            client.send_email.return_value = {"MessageId": "tz-host-1"}
            send_host_invite(self.event, "host@example.com")
            raw = client.send_email.call_args.kwargs["Content"]["Raw"]["Data"]
        html = _html_from_raw(raw)

        self.assertIn("Set your timezone", html)
        self.assertIn(f'href="https://env.example.com{ACCOUNT_FRAGMENT}"', html)


@override_settings(
    SITE_BASE_URL="https://env.example.com",
    SES_TRANSACTIONAL_FROM_EMAIL="noreply@aishippinglabs.com",
    AWS_SES_REGION="us-east-1",
    AWS_ACCESS_KEY_ID="test-key",
    AWS_SECRET_ACCESS_KEY="test-secret",
    SES_ENABLED=True,
)
class SeriesRegistrationTimezoneLineTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        Tier.objects.get_or_create(
            slug="free", defaults={"name": "Free", "level": 0},
        )

    def setUp(self):
        clear_config_cache()
        self.series = EventSeries.objects.create(
            name="Weekly TZ Series",
            slug="weekly-tz-series",
            start_time=timezone.now().time(),
            timezone="Europe/Berlin",
        )
        base = datetime(2026, 6, 8, 16, 0, tzinfo=UTC)
        self.e1 = Event.objects.create(
            title="Session 1", slug="tz-series-1",
            start_datetime=base, end_datetime=base + timedelta(hours=1),
            status="upcoming", event_series=self.series, series_position=1,
        )
        self.e2 = Event.objects.create(
            title="Session 2", slug="tz-series-2",
            start_datetime=base + timedelta(days=7),
            end_datetime=base + timedelta(days=7, hours=1),
            status="upcoming", event_series=self.series, series_position=2,
        )

    def tearDown(self):
        clear_config_cache()

    def test_one_line_for_two_occurrences_utc_fallback(self):
        user = User.objects.create_user(
            email="series-no-tz@example.com", preferred_timezone="",
        )
        EventRegistration.objects.create(event=self.e1, user=user)
        EventRegistration.objects.create(event=self.e2, user=user)
        with patch("events.services.registration_email.boto3") as mock_boto3:
            client = mock_boto3.client.return_value
            client.send_email.return_value = {"MessageId": "tz-series-1"}
            send_series_registration_invite(
                user, self.series, [self.e1, self.e2],
            )
            raw = client.send_email.call_args.kwargs["Content"]["Raw"]["Data"]
        html = _html_from_raw(raw)

        # Both occurrences listed.
        self.assertIn("Session 1", html)
        self.assertIn("Session 2", html)
        # Exactly one timezone line covers all occurrences.
        self.assertEqual(html.count("Set your timezone"), 1)
        self.assertIn(f'href="https://env.example.com{ACCOUNT_FRAGMENT}"', html)


class PostEventFollowupNoTimezoneLineTest(TestCase):
    """The recap email renders no future time, so it must not carry the line."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            email="recap@example.com", preferred_timezone="",
        )

    def test_followup_body_has_no_timezone_line(self):
        _, body_html = EmailService()._render_template(
            "post_event_followup",
            self.user,
            {
                "event_title": "Past Event",
                "event_summary": "Thanks for joining.",
                "event_url": "https://env.example.com/events/past",
                "recording_url": "https://example.com/rec",
            },
        )

        self.assertNotIn("Set your timezone", body_html)
        self.assertNotIn("Change your timezone", body_html)
