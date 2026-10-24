"""Production event callers hand native payloads to Relay (#2006)."""

from datetime import timedelta
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings, tag
from django.utils import timezone
from icalendar import Calendar

from content.models import Workshop
from email_app.models import EmailLog, RelayTransactionalDelivery
from events.models import Event, EventRegistration, EventSeries, SeriesRegistration
from events.services.recording_ready_notification import notify_recording_ready
from events.services.registration_email import send_registration_confirmation
from events.services.series_invite import (
    send_series_cancellation_to_subscribers,
    send_series_registration_invite,
    send_series_update_to_subscribers,
)
from events.services.workshop_ready_notification import notify_workshop_ready
from events.tasks.notify_cancellation import send_cancellation_notice_one
from events.tasks.notify_reschedule import send_reschedule_notice_one
from notifications.models import EventReminderLog, Notification

User = get_user_model()


def relay_response(_url, *, json, **_kwargs):
    response = Mock(status_code=202)
    message_id = f"relay-{json['template_key']}-{RelayTransactionalDelivery.objects.count()}"
    response.json.return_value = {
        "message": {
            "id": message_id,
            "status": "queued",
            "template_key": json["template_key"],
            "idempotency_key": json["idempotency_key"],
        },
        "idempotent_replay": False,
    }
    return response


def make_event(slug, *, sequence=0, completed=False):
    start = timezone.now() + timedelta(days=7)
    status = "upcoming"
    if completed:
        start = timezone.now() - timedelta(hours=2)
        status = "completed"
    return Event.objects.create(
        title=f"Relay {slug}",
        slug=slug,
        description="Relay delivery integration event.",
        start_datetime=start,
        end_datetime=start + timedelta(hours=1),
        status=status,
        ics_sequence=sequence,
    )


def parsed_events(payload):
    alternative = payload["calendar_alternative"]
    calendar = Calendar.from_ical(alternative["content"])
    events = [component for component in calendar.walk() if component.name == "VEVENT"]
    return alternative, calendar, events


class RelayPayloadAssertions:
    def assert_calendar_payload(self, payload, event, method, sequence):
        alternative, calendar, events = parsed_events(payload)
        self.assertEqual(alternative["method"], method)
        self.assertEqual(alternative["name"], "event.ics")
        self.assertEqual(str(calendar.get("method")), method)
        self.assertEqual(len(events), 1)
        vevent = events[0]
        self.assertEqual(str(vevent.get("uid")), event.calendar_uid)
        self.assertEqual(int(vevent.get("sequence")), sequence)
        self.assertEqual(str(vevent.get("attendee")), f"mailto:{payload['email']}")
        if method == "CANCEL":
            self.assertEqual(str(vevent.get("status")), "CANCELLED")

    def assert_request(self, request, template, recipient, key):
        self.assertEqual(request.args[0], "https://relay.test/api/transactional/send")
        payload = request.kwargs["json"]
        self.assertEqual(payload["template_key"], template)
        self.assertEqual(payload["email"], recipient)
        self.assertEqual(payload["idempotency_key"], key)
        return payload


RELAY_SETTINGS = override_settings(
    RELAY_BASE_URL="https://relay.test",
    RELAY_API_KEY="test-relay-key",
    SITE_BASE_URL="https://aishippinglabs.test",
    SES_TRANSACTIONAL_FROM_EMAIL="AI Shipping Labs <content@aishippinglabs.com>",
)


@tag("core")
@RELAY_SETTINGS
class RelayCalendarCallerTest(RelayPayloadAssertions, TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="relay-attendee@example.com",
            first_name="Ada",
            preferred_timezone="UTC",
        )

    @patch("email_app.relay_transactional.requests.post", side_effect=relay_response)
    def test_registration_reschedule_and_cancellation_payloads(self, post):
        event = make_event("relay-event-lifecycle")
        registration = EventRegistration.objects.create(event=event, user=self.user)
        send_registration_confirmation(registration)
        send_registration_confirmation(registration)

        old_start = event.start_datetime
        new_start = old_start + timedelta(days=1)
        Event.objects.filter(pk=event.pk).update(
            start_datetime=new_start,
            end_datetime=new_start + timedelta(hours=1),
            ics_sequence=1,
        )
        event.refresh_from_db()
        send_reschedule_notice_one(event.pk, self.user.pk, old_start.isoformat())
        send_reschedule_notice_one(event.pk, self.user.pk, old_start.isoformat())

        Event.objects.filter(pk=event.pk).update(status="cancelled", ics_sequence=2)
        event.refresh_from_db()
        send_cancellation_notice_one(event.pk, self.user.pk)
        send_cancellation_notice_one(event.pk, self.user.pk)

        self.assertEqual(post.call_count, 3)
        self._assert_single_event_requests(post.call_args_list, event, registration)
        self.assertEqual(EmailLog.objects.filter(event=event).count(), 3)

    def _assert_single_event_requests(self, requests, event, registration):
        keys = [
            f"event-registration:{event.pk}:{self.user.pk}:{registration.pk}",
            f"event-rescheduled:{event.pk}:{self.user.pk}:1",
            f"event-cancelled:{event.pk}:{self.user.pk}:2",
        ]
        templates = ["event_registration", "event_rescheduled", "event_cancelled"]
        methods = ["REQUEST", "REQUEST", "CANCEL"]
        for index, request in enumerate(requests):
            payload = self.assert_request(request, templates[index], self.user.email, keys[index])
            self.assert_calendar_payload(payload, event, methods[index], index)
        self.assertEqual(requests[0].kwargs["json"]["context"]["user_name"], "Ada")
        self.assertIn("old_event_datetime", requests[1].kwargs["json"]["context"])
        self.assertIn("event_datetime", requests[2].kwargs["json"]["context"])

    @patch("email_app.relay_transactional.requests.post", side_effect=relay_response)
    def test_series_registration_update_and_cancellation_payloads(self, post):
        series = EventSeries.objects.create(
            name="Relay Series",
            slug="relay-series",
            start_time=timezone.now().time(),
            timezone="UTC",
        )
        event = make_event("relay-series-event", sequence=2)
        event.event_series = series
        event.series_position = 1
        event.save(update_fields=["event_series", "series_position"])
        series_registration = SeriesRegistration.objects.create(series=series, user=self.user)
        EventRegistration.objects.create(event=event, user=self.user)

        send_series_registration_invite(self.user, series, [event])
        send_series_registration_invite(self.user, series, [event])
        old_start = event.start_datetime - timedelta(days=1)
        send_series_update_to_subscribers(event, old_start_iso=old_start.isoformat())
        send_series_update_to_subscribers(event, old_start_iso=old_start.isoformat())
        Event.objects.filter(pk=event.pk).update(status="cancelled", ics_sequence=3)
        event.refresh_from_db()
        send_series_cancellation_to_subscribers(event)
        send_series_cancellation_to_subscribers(event)

        self.assertEqual(post.call_count, 3)
        self._assert_series_requests(post.call_args_list, series, event, series_registration)
        self.assertEqual(EmailLog.objects.filter(user=self.user).count(), 3)

    def _assert_series_requests(self, requests, series, event, registration):
        keys = [
            f"series-registration:{series.pk}:{self.user.pk}:{registration.pk}",
            f"series-update:{series.pk}:{self.user.pk}:{event.pk}:2",
            f"series-cancellation:{series.pk}:{self.user.pk}:{event.pk}:3",
        ]
        templates = ["series_registration", "series_update", "series_cancellation"]
        methods = ["REQUEST", "REQUEST", "CANCEL"]
        sequences = [2, 2, 3]
        for index, request in enumerate(requests):
            payload = self.assert_request(request, templates[index], self.user.email, keys[index])
            self.assert_calendar_payload(payload, event, methods[index], sequences[index])
            self.assertEqual(payload["context"]["series_name"], series.name)
        self.assertTrue(requests[1].kwargs["json"]["context"]["changed_occurrence"])


@tag("core")
@RELAY_SETTINGS
class RelayReadyCallerTest(RelayPayloadAssertions, TestCase):
    @patch("email_app.relay_transactional.requests.post", side_effect=relay_response)
    def test_recording_ready_native_context_log_and_dedupe(self, post):
        host = User.objects.create_user(email="relay-host@example.com", first_name="Grace")
        event = make_event("relay-recording-ready", completed=True)
        event.host_email = host.email
        event.recording_url = "https://zoom.example.test/recording"
        event.recording_s3_url = "https://private.example.test/recording.mp4"
        event.save(update_fields=["host_email", "recording_url", "recording_s3_url"])
        workshop = Workshop.objects.create(
            title="Published Relay Recording",
            slug="published-relay-recording",
            description="Recording notes.",
            date=timezone.localdate(),
            status="published",
            event=event,
        )

        first = notify_recording_ready(event)
        second = notify_recording_ready(event)

        self.assertEqual((first["status"], second["status"]), ("sent", "skipped"))
        self.assertEqual(post.call_count, 1)
        key = f"event_recording_ready:{event.pk}:user:{host.pk}"
        payload = self.assert_request(post.call_args, "event_recording_ready", host.email, key)
        self.assertNotIn("calendar_alternative", payload)
        self.assertEqual(payload["context"]["event_title"], event.title)
        self.assertEqual(payload["context"]["publish_state"], "Published and available to watch")
        self.assertTrue(payload["context"]["is_available_to_watch"])
        self.assertIn(workshop.get_absolute_url(), payload["context"]["watch_url"])
        log = EmailLog.objects.get(event=event, email_type="event_recording_ready")
        self.assertEqual(log.ses_message_id, "relay-event_recording_ready-1")

    @patch("email_app.relay_transactional.requests.post", side_effect=relay_response)
    def test_workshop_ready_native_context_log_notification_and_dedupe(self, post):
        member = User.objects.create_user(email="relay-member@example.com", first_name="Lin")
        event = make_event("relay-workshop-ready", completed=True)
        EventRegistration.objects.create(event=event, user=member)
        workshop = Workshop.objects.create(
            title="Relay Workshop Notes",
            slug="relay-workshop-notes",
            description="A complete workshop write-up.",
            date=timezone.localdate(),
            status="published",
            event=event,
        )

        first = notify_workshop_ready(event)
        second = notify_workshop_ready(event)

        self.assertEqual((first["emailed"], second["already_sent"]), (1, 1))
        self.assertEqual(post.call_count, 1)
        key = f"event_workshop_ready:{event.pk}:user:{member.pk}"
        payload = self.assert_request(post.call_args, "event_workshop_ready", member.email, key)
        self.assertNotIn("calendar_alternative", payload)
        self.assertEqual(payload["context"]["workshop_title"], workshop.title)
        self.assertEqual(payload["context"]["workshop_url"], f"https://aishippinglabs.test{workshop.get_absolute_url()}")
        log = EmailLog.objects.get(event=event, email_type="event_workshop_ready")
        self.assertEqual(log.ses_message_id, "relay-event_workshop_ready-1")
        self.assertEqual(EventReminderLog.objects.filter(event=event, user=member).count(), 1)
        self.assertEqual(Notification.objects.filter(user=member).count(), 1)
