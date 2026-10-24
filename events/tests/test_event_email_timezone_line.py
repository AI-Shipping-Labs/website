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

from email_app.services.email_rendering import render_template
from events.models import Event, EventRegistration, EventSeries, SeriesRegistration
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

    def _capture_context(self, user):
        registration = EventRegistration.objects.create(
            event=self.event, user=user,
        )
        with patch(
            "events.services.registration_email._send_calendar_email",
        ) as mock_send:
            mock_send.return_value = "relay-tz-line-1"
            send_registration_confirmation(registration)
        return mock_send.call_args.kwargs["context"]

    def test_utc_fallback_recipient_sees_set_variant_and_link(self):
        user = User.objects.create_user(
            email="no-tz@example.com", preferred_timezone="",
        )
        context = self._capture_context(user)
        text = str(context)

        self.assertIn("Set your timezone", text)
        self.assertIn(f'https://env.example.com{ACCOUNT_FRAGMENT}', text)
        # #666 regression guard: the time still renders with the UTC token.
        self.assertIn("16:00 UTC", text)

    def test_zoned_recipient_sees_change_variant_not_set(self):
        user = User.objects.create_user(
            email="ny@example.com", preferred_timezone="America/New_York",
        )
        context = self._capture_context(user)
        text = str(context)

        self.assertIn("Change your timezone", text)
        self.assertIn(f'https://env.example.com{ACCOUNT_FRAGMENT}', text)
        self.assertNotIn("Set your timezone", text)
        # Time renders in the recipient's zone, not UTC.
        self.assertIn("America/New_York", text)
        self.assertNotIn("16:00 UTC", text)


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

    def _capture_context(self, user):
        EventRegistration.objects.create(event=self.event, user=user)
        old_start = "2026-06-01T16:00:00+00:00"
        # Issue #1632: stub the cancel token so the body is
        # deterministic — the real JWT's random base64url signature
        # contains the bare ``UTC`` trigram these assertions forbid about
        # once in 6,400 renders.
        with (
            patch(
                "events.tasks.notify_reschedule._send_calendar_email",
            ) as mock_send,
            patch(
                "events.tasks.notify_reschedule.generate_cancel_token",
                return_value="deterministic-cancel-token",
            ),
        ):
            mock_send.return_value = "relay-tz-resched-1"
            send_reschedule_notice_one(self.event.pk, user.pk, old_start)
        return mock_send.call_args.kwargs["context"]

    def test_one_line_covers_both_times_for_zoned_recipient(self):
        user = User.objects.create_user(
            email="berlin-resched@example.com",
            preferred_timezone="Europe/Berlin",
        )
        text = str(self._capture_context(user))

        # Exactly one timezone line for an email carrying two times.
        self.assertEqual(text.count("Change your timezone"), 1)
        self.assertNotIn("Set your timezone", text)
        self.assertIn(f'https://env.example.com{ACCOUNT_FRAGMENT}', text)
        # Both the old and new times render in the recipient's zone.
        self.assertEqual(text.count("Europe/Berlin"), 2)
        self.assertNotIn("UTC", text)


@override_settings(
    SITE_BASE_URL="https://env.example.com",
    SES_TRANSACTIONAL_FROM_EMAIL="noreply@aishippinglabs.com",
    AWS_SES_REGION="us-east-1",
    AWS_ACCESS_KEY_ID="test-key",
    AWS_SECRET_ACCESS_KEY="test-secret",
    SES_ENABLED=True,
)
class HostRegistrationTimezoneLineTest(TestCase):
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
        host = User.objects.create_user(
            email="host@example.com", preferred_timezone="",
        )
        self.event.host_email = host.email
        self.event.save(update_fields=["host_email"])
        registration = EventRegistration.objects.create(
            event=self.event,
            user=host,
        )
        with patch(
            "events.services.registration_email._send_calendar_email",
        ) as mock_send:
            mock_send.return_value = "relay-tz-host-1"
            send_registration_confirmation(registration)
        context = mock_send.call_args.kwargs["context"]
        text = str(context)

        self.assertIn("Set your timezone", text)
        self.assertIn(f'https://env.example.com{ACCOUNT_FRAGMENT}', text)
        self.assertTrue(context['is_host_registration'])


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
        SeriesRegistration.objects.create(series=self.series, user=user)
        with patch(
            "events.services.series_invite._send_calendar_email",
        ) as mock_send:
            mock_send.return_value = "relay-tz-series-1"
            send_series_registration_invite(
                user, self.series, [self.e1, self.e2],
            )
        text = str(mock_send.call_args.kwargs["context"])

        # Both occurrences listed.
        self.assertIn("Session 1", text)
        self.assertIn("Session 2", text)
        # Exactly one timezone line covers all occurrences.
        self.assertEqual(text.count("Set your timezone"), 1)
        self.assertIn(f'https://env.example.com{ACCOUNT_FRAGMENT}', text)


class PostEventFollowupNoTimezoneLineTest(TestCase):
    """The recap email renders no future time, so it must not carry the line."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            email="recap@example.com", preferred_timezone="",
        )

    def test_followup_body_has_no_timezone_line(self):
        _, body_html = render_template(
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
