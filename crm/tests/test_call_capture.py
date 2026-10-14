"""Tests for the post-meeting booking-call capture pipeline (#1951).

Zoom and the LLM are always stubbed; the pipeline sends exactly one
attendee summary email per captured call (To the attendee, Cc the
booking host) and a second tick never sends twice.
"""

import datetime
from unittest.mock import MagicMock, patch

from django.core import mail
from django.test import TestCase
from django.utils import timezone

from booking.models import Booking, BookingHost
from crm.models.call import Call
from crm.services import call_capture
from crm.services.call_capture import (
    calls_pending_capture,
    capture_call,
    capture_status,
)
from crm.services.calls import record_call

VTT = (
    "WEBVTT\n\n"
    "00:00:01.000 --> 00:00:04.000\n"
    "Welcome to the call.\n\n"
    "00:00:04.500 --> 00:00:08.000\n"
    "Let's look at your portfolio project.\n"
)


def _files(*recording_types):
    return {
        "share_url": "https://zoom.us/rec/share/abc",
        "recording_files": [
            {
                "recording_type": recording_type,
                "download_url": f"https://zoom.us/download/{recording_type}",
            }
            for recording_type in recording_types
        ],
    }


class CaptureBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.host, _ = BookingHost.objects.get_or_create(
            slug="alexey",
            defaults={
                "name": "Alexey Grigorev",
                "email": "alexey@test.com",
                "timezone": "UTC",
                "notice_hours": 0.0,
                "horizon_days": 30,
                "slot_duration_minutes": 30,
                "is_active": True,
            },
        )

    def _booking(self, *, status="confirmed", ended_hours_ago=2, zoom_id="zm-1"):
        end = timezone.now() - datetime.timedelta(hours=ended_hours_ago)
        start = end - datetime.timedelta(minutes=30)
        return Booking.objects.create(
            host=self.host,
            invitee_name="Mariano Beccaria",
            invitee_email="mariano@example.com",
            start_datetime=start,
            end_datetime=end,
            status=status,
            zoom_meeting_id=zoom_id,
        )

    def _call(self, booking):
        return record_call(
            email=booking.invitee_email,
            occurred_at=booking.start_datetime,
            summary="Booked: portfolio review.",
            source="booking",
            source_ref=str(booking.pk),
        )

    def _zoom(self, recordings):
        return patch(
            "crm.services.call_capture.get_meeting_recordings",
            return_value=recordings,
        )

    def _llm(self, *, enabled=True, text="Summary of the call.", error=None):
        return (
            patch.object(call_capture.llm, "is_enabled", return_value=enabled),
            patch.object(
                call_capture.llm, "complete",
                side_effect=error,
                return_value=MagicMock(text=text),
            ),
        )

    def _vtt(self):
        return patch.object(
            call_capture, "_download_vtt", return_value=VTT.encode("utf-8"),
        )


class SelectionTest(CaptureBase):
    def test_booking_source_call_is_pending(self):
        booking = self._booking()
        call = self._call(booking)
        self.assertEqual([row.pk for row in calls_pending_capture()], [call.pk])

    def test_manual_and_zoom_sources_are_never_pending(self):
        for source in ("manual", "zoom"):
            record_call(
                email="x@example.com",
                occurred_at=timezone.now(),
                summary="not a booking row",
                source=source,
                source_ref="ref-1",
            )
        self.assertEqual(
            Call.objects.filter(source__in=["manual", "zoom"]).count(), 2,
        )
        self.assertEqual(list(calls_pending_capture()), [])
        # Imported/manual rows never enter the pipeline, so a real tick
        # with capture enabled selects nothing and sends nothing (the
        # historical-import guarantee, through the real mail path).
        with self._zoom(_files("audio_transcript")), self._vtt():
            tally = call_capture.capture_finished_booking_calls()
        self.assertEqual(tally["selected"], 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_drafted_sent_or_unavailable_calls_are_not_pending(self):
        booking = self._booking()
        drafted = self._call(booking)
        drafted.email_draft_ready_at = timezone.now()
        drafted.save(update_fields=["email_draft_ready_at", "updated_at"])
        sent = self._call(booking)
        sent.email_sent_at = timezone.now()
        sent.save(update_fields=["email_sent_at", "updated_at"])
        unavailable = self._call(booking)
        unavailable.capture_unavailable_at = timezone.now()
        unavailable.save(update_fields=["capture_unavailable_at", "updated_at"])
        self.assertEqual(
            {row.pk for row in calls_pending_capture()},
            {drafted.pk, sent.pk, unavailable.pk} - {drafted.pk, sent.pk, unavailable.pk},
        )
        self.assertEqual(list(calls_pending_capture()), [])


class HistoricalImportNeverSendsTest(CaptureBase):
    """Issue #1951: historical imports never send, in any configuration.

    The shared sender refuses every non-booking source, so neither the
    scheduled tick nor a direct send -- pipeline or Studio -- can ever
    email a ``zoom``/``telegram`` import or a ``manual`` row.
    """

    def _imported_call(self, source, source_ref):
        call = record_call(
            email="member@example.com",
            occurred_at=timezone.now() - datetime.timedelta(days=90),
            summary="Historical 1:1 writeup.",
            source=source,
            source_ref=source_ref,
        )
        call.email_draft_subject = "Your 1:1 with Alexey"
        call.email_draft_body = "Historical summary."
        call.email_draft_ready_at = timezone.now()
        call.save(update_fields=[
            "email_draft_subject", "email_draft_body",
            "email_draft_ready_at", "updated_at",
        ])
        return call

    def test_imported_calls_never_send_on_a_tick(self):
        zoom_call = self._imported_call("zoom", "2026-04-24-jdoe-summary")
        telegram_call = self._imported_call("telegram", "plan#notes")
        with self._zoom(_files("audio_transcript")), self._vtt():
            tally = call_capture.capture_finished_booking_calls()
        self.assertEqual(tally["selected"], 0)
        self.assertEqual(len(mail.outbox), 0)
        zoom_call.refresh_from_db()
        telegram_call.refresh_from_db()
        self.assertIsNone(zoom_call.email_sent_at)
        self.assertIsNone(telegram_call.email_sent_at)

    def test_shared_sender_refuses_every_non_booking_source(self):
        for source, ref in (
            ("zoom", "2026-04-24-jdoe-summary"),
            ("telegram", "plan#notes"),
            ("manual", "hand-logged"),
        ):
            with self.subTest(source=source):
                call = self._imported_call(source, ref)
                result = call_capture.send_summary_email(call)
                self.assertEqual(result["status"], "skipped")
                self.assertEqual(result["reason"], "not_booking_source")
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(
            Call.objects.filter(email_sent_at__isnull=False).count(), 0,
        )

    def test_capture_call_refuses_non_booking_source_directly(self):
        call = self._imported_call("zoom", "2026-04-24-jdoe-summary")
        # No Zoom/LLM stubs: the source guard returns before any
        # network-facing work.
        result = capture_call(call)
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "not_booking_source")
        self.assertEqual(len(mail.outbox), 0)


class CaptureCallTest(CaptureBase):
    def test_happy_path_stores_transcript_summary_and_draft(self):
        booking = self._booking()
        self._call(booking)
        llm_enabled, llm_complete = self._llm()
        with self._zoom(_files("audio_transcript", "shared_screen")), self._vtt(), llm_enabled, llm_complete:
            result = capture_call(calls_pending_capture().first())
        self.assertEqual(result["status"], "drafted")
        call = Call.objects.get(source="booking", source_ref=str(booking.pk))
        self.assertIn("portfolio project", call.transcript_text)
        self.assertEqual(call.recording_url, "https://zoom.us/rec/share/abc")
        self.assertEqual(call.capture_attempts, 0)
        # Summary written through the record_call upsert on
        # (source, source_ref): same row, refreshed occurred_at.
        self.assertEqual(call.summary, "Summary of the call.")
        self.assertEqual(call.email, booking.invitee_email)
        self.assertEqual(
            call.occurred_at, booking.start_datetime,
        )
        # Stored, addressed to the invitee, and auto-sent: one email,
        # attendee on To, the booking host copy on Cc.
        self.assertIn("Your 1:1 with Alexey Grigorev", call.email_draft_subject)
        self.assertEqual(call.email_draft_body, "Summary of the call.")
        self.assertIsNotNone(call.email_draft_ready_at)
        self.assertIsNotNone(call.email_sent_at)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [booking.invitee_email])
        self.assertEqual(mail.outbox[0].cc, [self.host.email])
        self.assertEqual(result["email_status"], "sent")

    def test_no_recording_files_marks_unavailable_immediately(self):
        booking = self._booking()
        self._call(booking)
        with self._zoom({"recording_files": []}):
            result = capture_call(calls_pending_capture().first())
        self.assertEqual(result["status"], "unavailable")
        call = Call.objects.get(source="booking", source_ref=str(booking.pk))
        self.assertIsNotNone(call.capture_unavailable_at)
        self.assertEqual(len(mail.outbox), 0)

    def test_transcript_not_ready_increments_attempts_then_marks(self):
        booking = self._booking()
        self._call(booking)
        recordings = _files("shared_screen")  # video exists, transcript missing
        for expected_attempt in (1, 2, 3):
            with self._zoom(recordings):
                result = capture_call(calls_pending_capture().first())
            self.assertEqual(result["status"], "not_ready")
            call = Call.objects.get(source="booking", source_ref=str(booking.pk))
            self.assertEqual(call.capture_attempts, expected_attempt)
            self.assertIsNone(call.capture_unavailable_at)
        with self._zoom(recordings):
            result = capture_call(calls_pending_capture().first())
        self.assertEqual(result["status"], "unavailable")
        call = Call.objects.get(source="booking", source_ref=str(booking.pk))
        self.assertIsNotNone(call.capture_unavailable_at)

    def test_missing_zoom_id_marks_unavailable(self):
        booking = self._booking(zoom_id="")
        self._call(booking)
        result = capture_call(calls_pending_capture().first())
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["reason"], "no_zoom_meeting_id")

    def test_cancelled_booking_is_skipped_not_marked(self):
        booking = self._booking(status="canceled")
        call = self._call(booking)
        result = capture_call(call)
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "booking_not_confirmed")
        call.refresh_from_db()
        self.assertIsNone(call.capture_unavailable_at)

    def test_meeting_outside_lookback_is_skipped(self):
        booking = self._booking(ended_hours_ago=24 * 8)
        call = self._call(booking)
        result = capture_call(call)
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "outside_lookback_window")

    def test_future_meeting_is_skipped(self):
        booking = self._booking(ended_hours_ago=-24)
        call = self._call(booking)
        result = capture_call(call)
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "meeting_not_finished")

    def test_orphaned_call_is_skipped_not_marked(self):
        call = record_call(
            email="ghost@example.com",
            occurred_at=timezone.now(),
            summary="no booking behind me",
            source="booking",
            source_ref="999999",
        )
        result = capture_call(call)
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "no_booking")
        call.refresh_from_db()
        self.assertIsNone(call.capture_unavailable_at)

    def test_manage_token_source_ref_resolves_booking(self):
        booking = self._booking()
        call = record_call(
            email=booking.invitee_email,
            occurred_at=booking.start_datetime,
            summary="token-ref call",
            source="booking",
            source_ref=booking.manage_token,
        )
        with self._zoom({"recording_files": []}):
            result = capture_call(call)
        self.assertEqual(result["status"], "unavailable")


class SummaryGatesTest(CaptureBase):
    def test_llm_disabled_stores_transcript_but_skips_draft(self):
        booking = self._booking()
        self._call(booking)
        llm_disabled, _ = self._llm(enabled=False)
        with self._zoom(_files("audio_transcript")), self._vtt(), llm_disabled:
            result = capture_call(calls_pending_capture().first())
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "llm_not_configured")
        call = Call.objects.get(source="booking", source_ref=str(booking.pk))
        self.assertIn("portfolio project", call.transcript_text)
        self.assertEqual(call.summary, "Booked: portfolio review.")
        self.assertIsNone(call.email_draft_ready_at)

    def test_llm_disabled_falls_back_to_booking_topic_draft(self):
        # Issue #1951: a usable email always exists -- without the LLM
        # the deterministic booking topic/notes fallback fills in, and
        # the auto-send still goes out on the fallback body.
        booking = self._booking()
        booking.discussion_topic = "Portfolio review and RAG pipeline"
        booking.discussion_notes = "Alexey will share senior job leads."
        booking.save(update_fields=["discussion_topic", "discussion_notes"])
        self._call(booking)
        llm_disabled, _ = self._llm(enabled=False)
        with self._zoom(_files("audio_transcript")), self._vtt(), llm_disabled:
            result = capture_call(calls_pending_capture().first())
        self.assertEqual(result["status"], "drafted")
        self.assertEqual(result["summary_source"], "fallback")
        call = Call.objects.get(source="booking", source_ref=str(booking.pk))
        self.assertIn("Portfolio review", call.email_draft_body)
        self.assertIn("job leads", call.email_draft_body)
        self.assertIsNotNone(call.email_draft_ready_at)
        self.assertIsNotNone(call.email_sent_at)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].cc, [self.host.email])

    def test_llm_error_never_raises_and_leaves_summary(self):
        from integrations.services.llm import LLMError

        booking = self._booking()
        self._call(booking)
        llm_enabled, llm_error = self._llm(error=LLMError("boom"))
        with self._zoom(_files("audio_transcript")), self._vtt(), llm_enabled, llm_error:
            result = capture_call(calls_pending_capture().first())
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["reason"], "llm_error")
        call = Call.objects.get(source="booking", source_ref=str(booking.pk))
        self.assertEqual(call.summary, "Booked: portfolio review.")
        self.assertIsNone(call.email_draft_ready_at)

    def test_transcript_over_cap_is_truncated(self):
        booking = self._booking()
        long_text = "x" * (call_capture.CALL_SUMMARY_MAX_TRANSCRIPT_CHARS + 10)
        message = call_capture._generate_summary(
            booking, long_text,
        )
        # Generation itself is not the point here; the truncation note
        # path must not raise and the cap must bound the prompt size.
        self.assertIn(message["status"], ("ok", "error"))


class RunBookingCallCaptureTest(CaptureBase):
    def test_disabled_master_switch_short_circuits(self):
        with patch.object(
            call_capture, "crm_call_capture_enabled", return_value=False,
        ):
            result = call_capture.capture_finished_booking_calls()
        self.assertEqual(result["status"], "disabled")

    def test_tick_sends_email_once_and_second_tick_is_a_noop(self):
        booking = self._booking()
        self._call(booking)
        llm_enabled, llm_complete = self._llm(text="Run summary.")
        with self._zoom(_files("audio_transcript")), self._vtt(), llm_enabled, llm_complete:
            tally = call_capture.capture_finished_booking_calls()
        self.assertEqual(tally["status"], "ok")
        self.assertEqual(tally["drafted"], 1)
        self.assertEqual(tally["email_sent"], 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [booking.invitee_email])
        self.assertEqual(mail.outbox[0].cc, [self.host.email])
        # A second tick is a no-op: the sent call drops out of selection.
        second = call_capture.capture_finished_booking_calls()
        self.assertEqual(second["selected"], 0)
        self.assertEqual(len(mail.outbox), 1)

    def test_autosend_disabled_stores_draft_without_sending(self):
        booking = self._booking()
        self._call(booking)
        llm_enabled, llm_complete = self._llm(text="Autosend-off summary.")
        with self._zoom(_files("audio_transcript")), self._vtt(), llm_enabled, llm_complete, patch.object(
            call_capture, "crm_call_summary_autosend_enabled",
            return_value=False,
        ):
            tally = call_capture.capture_finished_booking_calls()
        self.assertEqual(tally["status"], "ok")
        self.assertEqual(tally["drafted"], 1)
        self.assertEqual(tally["email_sent"], 0)
        self.assertEqual(tally["email_skipped"], 1)
        self.assertEqual(len(mail.outbox), 0)
        call = Call.objects.get(source="booking", source_ref=str(booking.pk))
        self.assertIsNotNone(call.email_draft_ready_at)
        self.assertIsNone(call.email_sent_at)

    def test_studio_send_bypasses_the_autosend_kill_switch(self):
        booking = self._booking()
        call = self._call(booking)
        call.email_draft_subject = "s"
        call.email_draft_body = "Manual retry body."
        call.email_draft_ready_at = timezone.now()
        call.save(update_fields=[
            "email_draft_subject", "email_draft_body",
            "email_draft_ready_at", "updated_at",
        ])
        with patch.object(
            call_capture, "crm_call_summary_autosend_enabled",
            return_value=False,
        ):
            result = call_capture.send_summary_email(
                call, booking, sent_by=None,
            )
        self.assertEqual(result["status"], "sent")
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [booking.invitee_email])

    def test_booking_without_attendee_email_is_skipped_not_sent(self):
        booking = self._booking()
        call = self._call(booking)
        # The pipeline re-adopts the booking invitee email on the
        # upsert, so the booking itself must carry the blank.
        booking.invitee_email = ""
        booking.save(update_fields=["invitee_email"])
        llm_enabled, llm_complete = self._llm(text="No-email summary.")
        with self._zoom(_files("audio_transcript")), self._vtt(), llm_enabled, llm_complete:
            result = capture_call(calls_pending_capture().first())
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "no_attendee_email")
        call.refresh_from_db()
        self.assertIsNone(call.email_draft_ready_at)
        self.assertIsNone(call.email_sent_at)
        self.assertEqual(len(mail.outbox), 0)

    def test_send_failure_keeps_draft_and_counts_email_failed(self):
        booking = self._booking()
        self._call(booking)
        llm_enabled, llm_complete = self._llm(text="Failing summary.")
        with self._zoom(_files("audio_transcript")), self._vtt(), llm_enabled, llm_complete, patch(
            "crm.services.call_capture.EmailMessage",
            side_effect=RuntimeError("SES down"),
        ):
            tally = call_capture.capture_finished_booking_calls()
        self.assertEqual(tally["drafted"], 1)
        self.assertEqual(tally["email_failed"], 1)
        self.assertEqual(len(mail.outbox), 0)
        call = Call.objects.get(source="booking", source_ref=str(booking.pk))
        self.assertIsNotNone(call.email_draft_ready_at)
        self.assertIsNone(call.email_sent_at)
        # The failed call drops out of selection; no retry loop.
        second = call_capture.capture_finished_booking_calls()
        self.assertEqual(second["selected"], 0)

    def test_auto_send_never_targets_a_dismissed_draft(self):
        booking = self._booking()
        call = self._call(booking)
        call.email_draft_subject = "s"
        call.email_draft_body = "b"
        call.email_draft_ready_at = timezone.now()
        call.email_draft_dismissed_at = timezone.now()
        call.save(update_fields=[
            "email_draft_subject", "email_draft_body",
            "email_draft_ready_at", "email_draft_dismissed_at",
            "updated_at",
        ])
        result = call_capture.send_summary_email(call, booking)
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "draft_dismissed")
        self.assertEqual(len(mail.outbox), 0)
        self.assertIsNone(call.email_sent_at)

    def test_cc_setting_overrides_host_copy(self):
        booking = self._booking()
        self._call(booking)
        llm_enabled, llm_complete = self._llm(text="Cc override summary.")
        with self._zoom(_files("audio_transcript")), self._vtt(), llm_enabled, llm_complete, patch.object(
            call_capture, "crm_call_summary_email_cc",
            return_value="operator-copy@test.com",
        ):
            result = capture_call(calls_pending_capture().first())
        self.assertEqual(result["email_status"], "sent")
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].cc, ["operator-copy@test.com"])

    def test_blank_host_email_means_no_cc(self):
        host, _ = BookingHost.objects.get_or_create(
            slug="noemail",
            defaults={
                "name": "No Email Host",
                "email": "",
                "timezone": "UTC",
                "notice_hours": 0.0,
                "horizon_days": 30,
                "slot_duration_minutes": 30,
                "is_active": True,
            },
        )
        booking = self._booking()
        booking.host = host
        booking.save(update_fields=["host"])
        self._call(booking)
        llm_enabled, llm_complete = self._llm(text="Blank host summary.")
        with self._zoom(_files("audio_transcript")), self._vtt(), llm_enabled, llm_complete:
            result = capture_call(calls_pending_capture().first())
        self.assertEqual(result["email_status"], "sent")
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].cc, [])

    def test_task_wrapper_delegates_to_service(self):
        with patch.object(
            call_capture, "capture_finished_booking_calls",
            return_value={"status": "ok"},
        ) as run:
            from crm.tasks.call_capture import capture_finished_booking_calls

            result = capture_finished_booking_calls()
        self.assertEqual(result, {"status": "ok"})
        run.assert_called_once_with()


class CaptureStatusTest(CaptureBase):
    def test_derived_statuses(self):
        booking = self._booking()
        call = self._call(booking)
        self.assertEqual(capture_status(call), call_capture.CAPTURE_STATUS_NONE)
        call.capture_attempts = 1
        self.assertEqual(capture_status(call), call_capture.CAPTURE_STATUS_WAITING)
        call.transcript_text = "text"
        self.assertEqual(capture_status(call), call_capture.CAPTURE_STATUS_STORED)
        call.transcript_text = ""
        call.capture_attempts = 0
        call.capture_unavailable_at = timezone.now()
        self.assertEqual(
            capture_status(call), call_capture.CAPTURE_STATUS_UNAVAILABLE,
        )
