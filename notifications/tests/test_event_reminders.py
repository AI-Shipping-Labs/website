"""Tests for the event reminder background job.

All tests use freezegun to fix the clock so that time-window logic
(23h45m-24h15m for the 24h window and 15m-30m for the 20-minute
window, per issues #706 and #1001) is deterministic.
"""

from datetime import datetime, timedelta
from datetime import timezone as dt_tz
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from freezegun import freeze_time

from events.models import Event, EventRegistration
from events.services.host_registration import maybe_register_host_as_attendee
from notifications.models import EventReminderLog, Notification
from notifications.services.event_reminders import check_event_reminders

User = get_user_model()

# Fix a reference time for all tests
FROZEN_NOW = datetime(2026, 6, 15, 12, 0, 0, tzinfo=dt_tz.utc)


@patch('email_app.services.email_service.EmailService._send_ses',
       return_value='ses-msg-test')
class CheckEventRemindersTest(TestCase):
    """Tests for the check_event_reminders background job.

    Every test freezes time to FROZEN_NOW so window calculations are exact.
    The class-level ``_send_ses`` patch keeps the email path from talking
    to SES — individual tests assert on EmailLog or on the mock as needed.
    """

    def setUp(self):
        self.user = User.objects.create_user(
            email='testuser@example.com', password='testpass123',
        )
        self.user2 = User.objects.create_user(
            email='testuser2@example.com', password='testpass123',
        )

    @freeze_time(FROZEN_NOW)
    @patch('notifications.services.slack_announcements.post_slack_announcement')
    def test_24h_reminder_for_registered_users(self, mock_slack, mock_ses):
        """Events starting in ~24h should get reminders for registered users."""
        event = Event.objects.create(
            title='24h Event', slug='event-24h',
            start_datetime=FROZEN_NOW + timedelta(hours=24),
            status='upcoming',
        )
        EventRegistration.objects.create(event=event, user=self.user)
        EventRegistration.objects.create(event=event, user=self.user2)

        check_event_reminders()

        self.assertEqual(Notification.objects.count(), 2)
        notif = Notification.objects.filter(user=self.user).first()
        self.assertIn('24 hours', notif.title)
        self.assertEqual(notif.notification_type, 'event_reminder')

    @freeze_time(FROZEN_NOW)
    @patch('notifications.services.slack_announcements.post_slack_announcement')
    def test_auto_registered_host_receives_24h_reminder(
        self, mock_slack, mock_ses,
    ):
        """An auto-registered host flows through the normal reminder job."""
        event = Event.objects.create(
            title='Host Reminder Event',
            slug='host-reminder-event',
            start_datetime=FROZEN_NOW + timedelta(hours=24),
            status='upcoming',
            host_email=self.user.email,
        )
        maybe_register_host_as_attendee(event)

        check_event_reminders()

        self.assertEqual(Notification.objects.filter(user=self.user).count(), 1)
        self.assertEqual(
            EventReminderLog.objects.filter(
                event=event,
                user=self.user,
                interval='24h',
            ).count(),
            1,
        )

    @freeze_time(FROZEN_NOW)
    @patch('notifications.services.slack_announcements.post_slack_announcement')
    def test_20m_reminder_for_registered_users(self, mock_slack, mock_ses):
        """Events starting in ~20 min should get reminders for registered users."""
        event = Event.objects.create(
            title='20m Event', slug='event-20m',
            start_datetime=FROZEN_NOW + timedelta(minutes=20),
            status='upcoming',
        )
        EventRegistration.objects.create(event=event, user=self.user)

        check_event_reminders()

        self.assertEqual(Notification.objects.count(), 1)
        notif = Notification.objects.first()
        self.assertIn('20 minutes', notif.title)
        self.assertEqual(notif.notification_type, 'event_reminder')

    @freeze_time(FROZEN_NOW)
    @patch('notifications.services.slack_announcements.post_slack_announcement')
    def test_auto_registered_host_and_attendee_get_one_20m_reminder_each(
        self, mock_slack, mock_ses,
    ):
        """Host auto-registration must not double-remind the host."""
        event = Event.objects.create(
            title='Host And Attendee 20m Event',
            slug='host-and-attendee-20m-event',
            start_datetime=FROZEN_NOW + timedelta(minutes=20),
            status='upcoming',
            host_email=self.user.email,
        )
        maybe_register_host_as_attendee(event)
        EventRegistration.objects.create(event=event, user=self.user2)

        check_event_reminders()
        check_event_reminders()

        self.assertEqual(Notification.objects.count(), 2)
        self.assertEqual(
            EventReminderLog.objects.filter(
                event=event,
                interval='20m',
            ).count(),
            2,
        )
        self.assertEqual(
            EventReminderLog.objects.filter(
                event=event,
                user=self.user,
                interval='20m',
            ).count(),
            1,
        )
        self.assertEqual(
            EventReminderLog.objects.filter(
                event=event,
                user=self.user2,
                interval='20m',
            ).count(),
            1,
        )

    @freeze_time(FROZEN_NOW)
    @patch('notifications.services.slack_announcements.post_slack_announcement')
    def test_old_1h_window_no_longer_fires(self, mock_slack, mock_ses):
        """Issue #706: events at +1h must NOT trigger a reminder anymore."""
        event = Event.objects.create(
            title='1h Event', slug='event-1h',
            start_datetime=FROZEN_NOW + timedelta(hours=1),
            status='upcoming',
        )
        EventRegistration.objects.create(event=event, user=self.user)

        check_event_reminders()

        self.assertEqual(Notification.objects.count(), 0)
        self.assertEqual(EventReminderLog.objects.count(), 0)

    @freeze_time(FROZEN_NOW)
    @patch('notifications.services.slack_announcements.post_slack_announcement')
    def test_no_reminder_for_unregistered_users(self, mock_slack, mock_ses):
        """Users who have not registered should not get reminders."""
        Event.objects.create(
            title='Event No Reg', slug='event-no-reg',
            start_datetime=FROZEN_NOW + timedelta(hours=24),
            status='upcoming',
        )
        # No registrations

        check_event_reminders()

        self.assertEqual(Notification.objects.count(), 0)

    @freeze_time(FROZEN_NOW)
    @patch('notifications.services.slack_announcements.post_slack_announcement')
    def test_no_reminder_for_draft_events(self, mock_slack, mock_ses):
        """Draft events should not trigger reminders."""
        event = Event.objects.create(
            title='Draft Event', slug='event-draft',
            start_datetime=FROZEN_NOW + timedelta(hours=24),
            status='draft',
        )
        EventRegistration.objects.create(event=event, user=self.user)

        check_event_reminders()

        self.assertEqual(Notification.objects.count(), 0)

    @freeze_time(FROZEN_NOW)
    @patch('notifications.services.slack_announcements.post_slack_announcement')
    def test_deduplication_no_double_reminders(self, mock_slack, mock_ses):
        """Running the job twice should not create duplicate reminders."""
        event = Event.objects.create(
            title='Dedup Event', slug='event-dedup',
            start_datetime=FROZEN_NOW + timedelta(hours=24),
            status='upcoming',
        )
        EventRegistration.objects.create(event=event, user=self.user)

        check_event_reminders()
        check_event_reminders()

        self.assertEqual(Notification.objects.count(), 1)
        # One per-user 24h row; the 24h_slack guard row is per-event.
        self.assertEqual(
            EventReminderLog.objects.filter(interval='24h').count(), 1,
        )
        self.assertEqual(
            EventReminderLog.objects.filter(interval='24h_slack').count(), 1,
        )

    @freeze_time(FROZEN_NOW)
    @patch('notifications.services.slack_announcements.post_slack_announcement')
    def test_event_outside_window_gets_no_reminder(self, mock_slack, mock_ses):
        """Events not in the 24h or 20-min window should not get reminders."""
        # Event in 12 hours - outside both windows
        event = Event.objects.create(
            title='12h Event', slug='event-12h',
            start_datetime=FROZEN_NOW + timedelta(hours=12),
            status='upcoming',
        )
        EventRegistration.objects.create(event=event, user=self.user)

        check_event_reminders()

        self.assertEqual(Notification.objects.count(), 0)

    @freeze_time(FROZEN_NOW)
    @patch('notifications.services.slack_announcements.post_slack_announcement')
    def test_24h_reminder_posts_to_slack(self, mock_slack, mock_ses):
        """24h reminders should post to Slack."""
        event = Event.objects.create(
            title='Slack Event', slug='event-slack',
            start_datetime=FROZEN_NOW + timedelta(hours=24),
            status='upcoming',
        )
        EventRegistration.objects.create(event=event, user=self.user)

        check_event_reminders()

        mock_slack.assert_called_once_with('event', event)
        # The per-event guard row was written so future ticks skip the post.
        self.assertEqual(
            EventReminderLog.objects.filter(
                event=event, interval='24h_slack', user__isnull=True,
            ).count(),
            1,
        )

    @patch('notifications.services.slack_announcements.post_slack_announcement')
    def test_24h_channel_post_fires_once_across_two_cron_ticks(
        self, mock_slack, mock_ses,
    ):
        """Issue #887 regression: the 24h channel announcement window
        (23h45m-24h15m) is 30 min wide, so an event ~24h out matches on
        TWO consecutive 15-min cron ticks. The channel post must fire
        exactly ONCE thanks to the per-event 24h_slack guard row.
        """
        start = datetime(2026, 6, 16, 12, 0, 0, tzinfo=dt_tz.utc)
        event = Event.objects.create(
            title='Two Tick Event', slug='event-two-tick',
            start_datetime=start,
            status='upcoming',
        )
        EventRegistration.objects.create(event=event, user=self.user)

        # Tick 1: clock is 24h00m before start (well inside the window).
        with freeze_time(start - timedelta(hours=24)):
            check_event_reminders()
        # Tick 2: 15 minutes later, clock is 23h45m before start — still
        # inside the window (the overlap that caused the double-post bug).
        with freeze_time(start - timedelta(hours=23, minutes=45)):
            check_event_reminders()

        # Exactly one channel announcement despite both ticks matching.
        mock_slack.assert_called_once_with('event', event)
        self.assertEqual(
            EventReminderLog.objects.filter(
                event=event, interval='24h_slack',
            ).count(),
            1,
        )
        # Per-user reminder still deduped to a single bell + log row.
        self.assertEqual(Notification.objects.count(), 1)
        self.assertEqual(
            EventReminderLog.objects.filter(
                event=event, interval='24h', user=self.user,
            ).count(),
            1,
        )

    @patch('notifications.services.slack_announcements.post_slack_announcement')
    def test_24h_channel_post_skipped_when_guard_row_exists(
        self, mock_slack, mock_ses,
    ):
        """A pre-existing 24h_slack guard row (e.g. from an earlier tick
        in a prior process) suppresses the channel post entirely."""
        start = datetime(2026, 6, 16, 12, 0, 0, tzinfo=dt_tz.utc)
        event = Event.objects.create(
            title='Pre-guarded Event', slug='event-pre-guarded',
            start_datetime=start,
            status='upcoming',
        )
        EventRegistration.objects.create(event=event, user=self.user)
        EventReminderLog.objects.create(
            event=event, user=None, interval='24h_slack',
        )

        with freeze_time(start - timedelta(hours=24)):
            check_event_reminders()

        mock_slack.assert_not_called()

    @freeze_time(FROZEN_NOW)
    @patch('notifications.services.slack_announcements.post_slack_announcement')
    def test_20m_reminder_does_not_post_to_slack(self, mock_slack, mock_ses):
        """20-min reminders should NOT post to Slack (per spec: avoid noise)."""
        event = Event.objects.create(
            title='20m Event No Slack', slug='event-20m-no-slack',
            start_datetime=FROZEN_NOW + timedelta(minutes=20),
            status='upcoming',
        )
        EventRegistration.objects.create(event=event, user=self.user)

        check_event_reminders()

        mock_slack.assert_not_called()

    @freeze_time(FROZEN_NOW)
    @patch('notifications.services.slack_announcements.post_slack_announcement')
    def test_event_at_edge_of_24h_window_start(self, mock_slack, mock_ses):
        """Event at exactly 23h45m from now is inside the 24h window."""
        event = Event.objects.create(
            title='Edge Start', slug='event-edge-start',
            start_datetime=FROZEN_NOW + timedelta(hours=23, minutes=45),
            status='upcoming',
        )
        EventRegistration.objects.create(event=event, user=self.user)

        check_event_reminders()

        self.assertEqual(Notification.objects.count(), 1)

    @freeze_time(FROZEN_NOW)
    @patch('notifications.services.slack_announcements.post_slack_announcement')
    def test_event_just_outside_24h_window(self, mock_slack, mock_ses):
        """Event at 23h44m from now is outside the 24h window."""
        event = Event.objects.create(
            title='Outside Window', slug='event-outside',
            start_datetime=FROZEN_NOW + timedelta(hours=23, minutes=44),
            status='upcoming',
        )
        EventRegistration.objects.create(event=event, user=self.user)

        check_event_reminders()

        self.assertEqual(Notification.objects.count(), 0)

    @freeze_time(FROZEN_NOW)
    @patch('notifications.services.slack_announcements.post_slack_announcement')
    def test_event_at_edge_of_20m_window_end(self, mock_slack, mock_ses):
        """Event at exactly 30m from now is inside the 20-min window.

        Issue #1001: the window was widened from 25m to 30m (15 min wide,
        == the */15 tick interval) so no start-minute falls between ticks.
        """
        event = Event.objects.create(
            title='20m Edge End', slug='event-20m-edge-end',
            start_datetime=FROZEN_NOW + timedelta(minutes=30),
            status='upcoming',
        )
        EventRegistration.objects.create(event=event, user=self.user)

        check_event_reminders()

        self.assertEqual(Notification.objects.count(), 1)

    @freeze_time(FROZEN_NOW)
    @patch('notifications.services.slack_announcements.post_slack_announcement')
    def test_event_at_edge_of_20m_window_start(self, mock_slack, mock_ses):
        """Event at exactly 15m from now is inside the 20-min window."""
        event = Event.objects.create(
            title='20m Edge Start', slug='event-20m-edge-start',
            start_datetime=FROZEN_NOW + timedelta(minutes=15),
            status='upcoming',
        )
        EventRegistration.objects.create(event=event, user=self.user)

        check_event_reminders()

        self.assertEqual(Notification.objects.count(), 1)

    @freeze_time(FROZEN_NOW)
    @patch('notifications.services.slack_announcements.post_slack_announcement')
    def test_event_just_outside_20m_window_end(self, mock_slack, mock_ses):
        """Event at 31m from now is outside the (widened) 20-min window."""
        event = Event.objects.create(
            title='20m Outside End', slug='event-20m-outside-end',
            start_datetime=FROZEN_NOW + timedelta(minutes=31),
            status='upcoming',
        )
        EventRegistration.objects.create(event=event, user=self.user)

        check_event_reminders()

        self.assertEqual(Notification.objects.count(), 0)

    @freeze_time(FROZEN_NOW)
    @patch('notifications.services.slack_announcements.post_slack_announcement')
    def test_event_just_outside_20m_window_start(self, mock_slack, mock_ses):
        """Event at 14m from now is outside the 20-min window."""
        event = Event.objects.create(
            title='20m Outside Start', slug='event-20m-outside-start',
            start_datetime=FROZEN_NOW + timedelta(minutes=14),
            status='upcoming',
        )
        EventRegistration.objects.create(event=event, user=self.user)

        check_event_reminders()

        self.assertEqual(Notification.objects.count(), 0)

    # ------------------------------------------------------------------
    # Issue #706: email channel coverage
    # ------------------------------------------------------------------

    @freeze_time(FROZEN_NOW)
    @patch('notifications.services.slack_announcements.post_slack_announcement')
    def test_24h_window_sends_email_to_each_registered_user(
        self, mock_slack, mock_ses,
    ):
        """24h reminder must create bell + EmailLog for every registration."""
        from email_app.models import EmailLog

        event = Event.objects.create(
            title='Email 24h Event', slug='email-24h-event',
            start_datetime=FROZEN_NOW + timedelta(hours=24),
            status='upcoming',
        )
        EventRegistration.objects.create(event=event, user=self.user)
        EventRegistration.objects.create(event=event, user=self.user2)

        check_event_reminders()

        self.assertEqual(Notification.objects.count(), 2)
        emails = EmailLog.objects.filter(email_type='event_reminder')
        self.assertEqual(emails.count(), 2)
        self.assertEqual(
            set(emails.values_list('user__email', flat=True)),
            {'testuser@example.com', 'testuser2@example.com'},
        )

    @freeze_time(FROZEN_NOW)
    @patch('notifications.services.slack_announcements.post_slack_announcement')
    def test_20m_window_sends_email_to_each_registered_user(
        self, mock_slack, mock_ses,
    ):
        """20-min reminder must create bell + EmailLog for every registration."""
        from email_app.models import EmailLog

        event = Event.objects.create(
            title='Email 20m Event', slug='email-20m-event',
            start_datetime=FROZEN_NOW + timedelta(minutes=20),
            status='upcoming',
        )
        EventRegistration.objects.create(event=event, user=self.user)

        check_event_reminders()

        self.assertEqual(Notification.objects.count(), 1)
        self.assertEqual(
            EmailLog.objects.filter(email_type='event_reminder').count(),
            1,
        )

    @freeze_time(FROZEN_NOW)
    @patch('notifications.services.slack_announcements.post_slack_announcement')
    def test_email_dedup_across_runs(self, mock_slack, mock_ses):
        """Running the job twice in the same window creates exactly one
        Notification, one EmailLog, and one EventReminderLog row per
        (event, user, interval)."""
        from email_app.models import EmailLog

        event = Event.objects.create(
            title='Dedup Email Event', slug='dedup-email-event',
            start_datetime=FROZEN_NOW + timedelta(hours=24),
            status='upcoming',
        )
        EventRegistration.objects.create(event=event, user=self.user)

        check_event_reminders()
        check_event_reminders()

        self.assertEqual(Notification.objects.count(), 1)
        self.assertEqual(
            EventReminderLog.objects.filter(interval='24h').count(), 1,
        )
        self.assertEqual(
            EmailLog.objects.filter(email_type='event_reminder').count(),
            1,
        )

    @freeze_time(FROZEN_NOW)
    @patch('notifications.services.slack_announcements.post_slack_announcement')
    def test_email_failure_does_not_block_bell_or_dedup(
        self, mock_slack, mock_ses,
    ):
        """If EmailService.send raises, the Notification + EventReminderLog
        rows persist and create_event_reminder still returns the Notification.
        The next run must NOT re-attempt (dedup row is the gate)."""
        from email_app.models import EmailLog
        from email_app.services.email_service import EmailService

        event = Event.objects.create(
            title='SES Down Event', slug='ses-down-event',
            start_datetime=FROZEN_NOW + timedelta(hours=24),
            status='upcoming',
        )
        EventRegistration.objects.create(event=event, user=self.user)

        with patch.object(
            EmailService, 'send', side_effect=Exception('SES down'),
        ), self.assertLogs(
            'notifications.services.notification_service', level='ERROR',
        ) as logs:
            check_event_reminders()

        # Bell + dedup row persisted despite the SES failure.
        self.assertEqual(Notification.objects.count(), 1)
        self.assertEqual(
            EventReminderLog.objects.filter(interval='24h').count(), 1,
        )
        # No EmailLog row — the send raised before reaching the log write.
        self.assertEqual(
            EmailLog.objects.filter(email_type='event_reminder').count(),
            0,
        )
        # Exception was logged via logger.exception (level=ERROR).
        self.assertTrue(
            any('Failed to send event_reminder email' in line
                for line in logs.output),
            f'Expected ERROR log; got {logs.output!r}',
        )

        # A second tick inside the same window must dedupe — no retry.
        with patch.object(
            EmailService, 'send', side_effect=Exception('SES down'),
        ):
            check_event_reminders()
        self.assertEqual(Notification.objects.count(), 1)
        self.assertEqual(
            EventReminderLog.objects.filter(interval='24h').count(), 1,
        )

    @freeze_time(FROZEN_NOW)
    @patch('notifications.services.slack_announcements.post_slack_announcement')
    def test_email_body_renders_join_url_as_event_url(
        self, mock_slack, mock_ses,
    ):
        """The email body has a single CTA: ``event_url`` points to the
        ``/events/<slug>/join`` redirect (prefixed with site_base_url). The
        ``/events/<id>/<slug>`` detail page URL must NOT appear."""
        from integrations.config import site_base_url

        event = Event.objects.create(
            title='URL Event', slug='url-event-slug',
            start_datetime=FROZEN_NOW + timedelta(hours=24),
            status='upcoming',
        )
        EventRegistration.objects.create(event=event, user=self.user)

        check_event_reminders()

        # mock_ses is called once: positional args (to_email, subject, html_body)
        self.assertEqual(mock_ses.call_count, 1)
        html_body = mock_ses.call_args[0][2]

        base = site_base_url()
        # Single CTA: event_url is the platform-side join redirect.
        self.assertIn(f'{base}/events/url-event-slug/join', html_body)
        # The detail page URL must not leak into the reminder body.
        self.assertNotIn(f'{base}{event.get_absolute_url()}', html_body)

    @patch('notifications.services.slack_announcements.post_slack_announcement')
    def test_email_body_renders_event_datetime_in_user_timezone(
        self, mock_slack, mock_ses,
    ):
        """Two users with different timezones must see their own offset
        in the rendered body (issue #666 guardrail still active)."""
        self.user.preferred_timezone = 'Europe/Berlin'
        self.user.save(update_fields=['preferred_timezone'])
        self.user2.preferred_timezone = 'America/New_York'
        self.user2.save(update_fields=['preferred_timezone'])

        # Start at 16:00 UTC so Berlin (DST = +2 in June) renders 18:00
        # and New York (DST = -4 in June) renders 12:00.
        start = datetime(2026, 6, 16, 16, 0, 0, tzinfo=dt_tz.utc)
        event = Event.objects.create(
            title='TZ Event', slug='tz-event',
            start_datetime=start,
            status='upcoming',
        )
        EventRegistration.objects.create(event=event, user=self.user)
        EventRegistration.objects.create(event=event, user=self.user2)

        # Freeze the clock 20 minutes before start so we exercise the
        # 20-min reminder window (AC #6).
        with freeze_time(start - timedelta(minutes=20)):
            check_event_reminders()

        self.assertEqual(mock_ses.call_count, 2)
        rendered_by_email = {
            call.args[0]: call.args[2] for call in mock_ses.call_args_list
        }
        self.assertIn(
            '18:00 Europe/Berlin',
            rendered_by_email['testuser@example.com'],
        )
        self.assertIn(
            '12:00 America/New_York',
            rendered_by_email['testuser2@example.com'],
        )

    @patch('notifications.services.slack_announcements.post_slack_announcement')
    def test_reminder_carries_timezone_line_utc_fallback(
        self, mock_slack, mock_ses,
    ):
        """Issue #963: a UTC-fallback recipient's reminder body carries the
        prominent "Set your timezone" line and the account timezone link."""
        from integrations.config import site_base_url
        from notifications.services.notification_service import (
            NotificationService,
        )

        self.user.preferred_timezone = ''
        self.user.save(update_fields=['preferred_timezone'])
        event = Event.objects.create(
            title='TZ Line Reminder', slug='tz-line-reminder',
            start_datetime=FROZEN_NOW + timedelta(hours=24),
            status='upcoming',
        )
        EventRegistration.objects.create(event=event, user=self.user)

        NotificationService.create_event_reminder(
            event, self.user, '24h', 'Reminder', 'Soon',
        )

        self.assertEqual(mock_ses.call_count, 1)
        html_body = mock_ses.call_args[0][2]
        base = site_base_url()
        self.assertIn('Set your timezone', html_body)
        self.assertIn(
            f'href="{base}/account/#display-preferences-section"', html_body,
        )

    @patch('notifications.services.slack_announcements.post_slack_announcement')
    def test_reminder_carries_timezone_line_zoned(
        self, mock_slack, mock_ses,
    ):
        """Issue #963: a zoned recipient's reminder body carries the quieter
        "Change your timezone" line, not the UTC-fallback variant."""
        from notifications.services.notification_service import (
            NotificationService,
        )

        self.user.preferred_timezone = 'Europe/Berlin'
        self.user.save(update_fields=['preferred_timezone'])
        event = Event.objects.create(
            title='TZ Line Reminder Zoned', slug='tz-line-reminder-zoned',
            start_datetime=FROZEN_NOW + timedelta(hours=24),
            status='upcoming',
        )
        EventRegistration.objects.create(event=event, user=self.user)

        NotificationService.create_event_reminder(
            event, self.user, '24h', 'Reminder', 'Soon',
        )

        self.assertEqual(mock_ses.call_count, 1)
        html_body = mock_ses.call_args[0][2]
        self.assertIn('Change your timezone', html_body)
        self.assertNotIn('Set your timezone', html_body)

    @freeze_time(FROZEN_NOW)
    @patch('notifications.services.slack_announcements.post_slack_announcement')
    def test_unsubscribed_user_still_receives_event_reminder(
        self, mock_slack, mock_ses,
    ):
        """event_reminder is transactional — unsubscribed users still get it."""
        from email_app.models import EmailLog

        self.user.unsubscribed = True
        self.user.save(update_fields=['unsubscribed'])

        event = Event.objects.create(
            title='Unsub Event', slug='unsub-event',
            start_datetime=FROZEN_NOW + timedelta(hours=24),
            status='upcoming',
        )
        EventRegistration.objects.create(event=event, user=self.user)

        check_event_reminders()

        self.assertEqual(Notification.objects.filter(user=self.user).count(), 1)
        self.assertEqual(
            EmailLog.objects.filter(
                user=self.user, email_type='event_reminder',
            ).count(),
            1,
        )


@patch('email_app.services.email_service.EmailService._send_ses',
       return_value='ses-msg-test')
@patch('notifications.services.slack_announcements.post_slack_announcement')
class OffMinuteCadenceTest(TestCase):
    """Issue #1001: at the restored ``*/15`` cadence AND a 20m window widened
    to 15 min (15-30, == the tick interval) the reminder windows catch events
    starting at an ARBITRARY minute-of-hour, not just the band the broken
    hourly (``0 * * * *``) cron could hit, and with no between-tick gaps.

    Every scenario freezes ``now`` at a real ``*/15`` tick (a minute that is
    a multiple of 15) and places the event at an off-minute start, proving
    that the tick whose window covers the event delivers the reminder exactly
    once. These tests would all FAIL under the hourly cadence because the
    job would only ever run at ``:00``.
    """

    def setUp(self):
        self.user = User.objects.create_user(
            email='offminute@example.com', password='testpass123',
        )

    def _tick_in_window(self, start, lead_min, lead_max):
        """Return the real ``*/15`` tick whose clock lands in the reminder
        window ``[start - lead_max, start - lead_min]``.

        The cron only fires when ``now`` is a multiple of 15 minutes, so we
        scan the ``*/15`` boundaries around the window and return the one
        that actually sits inside it — the instant the job runs in prod.
        The window is at least 15 min wide (== the tick spacing), so at
        least one boundary always lands inside it (never zero) — issue
        #1001. We return the earliest such tick.
        """
        win_start = start - timedelta(minutes=lead_max)
        win_end = start - timedelta(minutes=lead_min)
        # Snap win_start UP to the next */15 boundary.
        minute_up = -(-win_start.minute // 15) * 15  # ceil to multiple of 15
        if minute_up == 60:
            candidate = (win_start + timedelta(hours=1)).replace(
                minute=0, second=0, microsecond=0,
            )
        else:
            candidate = win_start.replace(
                minute=minute_up, second=0, microsecond=0,
            )
        assert win_start <= candidate <= win_end, (
            f'no */15 tick in window [{win_start}, {win_end}]; got {candidate}'
        )
        return candidate

    def _tick_24h(self, start):
        return self._tick_in_window(start, lead_min=23 * 60 + 45, lead_max=24 * 60 + 15)

    def _tick_20m(self, start):
        # Window widened to 15-30 min (issue #1001) so every start-minute is
        # covered by at least one */15 tick.
        return self._tick_in_window(start, lead_min=15, lead_max=30)

    def test_20m_reminder_fires_for_off_minute_starts(self, mock_slack, mock_ses):
        """Events starting at off-minutes spanning all four */15 tick buckets
        each get exactly one 20m reminder when the */15 tick lands inside the
        15-30 min window.

        Under hourly cadence (job only at :00) the :07, :38 and :52 events
        would NEVER be in-window and would get no 20m reminder at all — this
        is the core #1001 regression the */15 restore fixes.

        Crucially this set includes the start-minutes (:11, :27, :42, :58)
        that fell in the gap BETWEEN two consecutive */15 ticks when the
        window was only 10 min wide (15-25 min): with the */15 cadence alone
        those ~27% of events still got NO 20m reminder. Widening the window
        to 15 min (15-30, == the tick interval) closes that gap, so every
        one of these minutes is now covered by exactly one tick.
        """
        # :07/:23/:38/:52 were covered by the old 10-min window; :11/:27/:42/
        # :58 are the formerly-uncovered between-tick gap minutes.
        offsets = [7, 11, 23, 27, 38, 42, 52, 58]
        for minute in offsets:
            with self.subTest(start_minute=minute):
                start = datetime(2026, 6, 15, 14, minute, 0, tzinfo=dt_tz.utc)
                event = Event.objects.create(
                    title=f'Off {minute}', slug=f'off-20m-{minute}',
                    start_datetime=start, status='upcoming',
                )
                EventRegistration.objects.create(event=event, user=self.user)

                # The real */15 tick that lands inside the 15-30 min window.
                tick = self._tick_20m(start)
                delta = (start - tick).total_seconds() / 60
                # Sanity: the snapped tick is genuinely inside the 15-30 window.
                self.assertGreaterEqual(delta, 15)
                self.assertLessEqual(delta, 30)

                with freeze_time(tick):
                    check_event_reminders()

                self.assertEqual(
                    EventReminderLog.objects.filter(
                        event=event, user=self.user, interval='20m',
                    ).count(),
                    1,
                    f'event at :{minute:02d} should have one 20m reminder',
                )
                self.assertEqual(
                    Notification.objects.filter(
                        user=self.user, notification_type='event_reminder',
                    ).filter(title__icontains=event.title).count(),
                    1,
                )

    def test_24h_reminder_fires_for_off_minute_starts(self, mock_slack, mock_ses):
        """Events starting 24h out at :07 and :30 each get exactly one 24h
        reminder — minutes the hourly :00 tick's 24h window would skip."""
        from email_app.models import EmailLog

        sent_so_far = 0
        for minute in (7, 30):
            with self.subTest(start_minute=minute):
                start = datetime(2026, 6, 16, 9, minute, 0, tzinfo=dt_tz.utc)
                event = Event.objects.create(
                    title=f'Off24 {minute}', slug=f'off-24h-{minute}',
                    start_datetime=start, status='upcoming',
                )
                EventRegistration.objects.create(event=event, user=self.user)

                tick = self._tick_24h(start)
                with freeze_time(tick):
                    check_event_reminders()

                self.assertEqual(
                    EventReminderLog.objects.filter(
                        event=event, user=self.user, interval='24h',
                    ).count(),
                    1,
                )
                # Exactly one new reminder email was sent this iteration.
                sent_so_far += 1
                self.assertEqual(
                    EmailLog.objects.filter(
                        user=self.user, email_type='event_reminder',
                    ).count(),
                    sent_so_far,
                )

    def test_user_receives_both_reminders_each_once(self, mock_slack, mock_ses):
        """For one off-minute event a registered user gets BOTH the 24h and
        the 20m reminder, each exactly once: two EventReminderLog rows, two
        notifications, two emails — evaluated across the two distinct ticks."""
        from email_app.models import EmailLog

        start = datetime(2026, 6, 16, 17, 38, 0, tzinfo=dt_tz.utc)
        event = Event.objects.create(
            title='Both Reminders', slug='both-reminders',
            start_datetime=start, status='upcoming',
        )
        EventRegistration.objects.create(event=event, user=self.user)

        # Run at the 24h tick, then at the 20m tick.
        with freeze_time(self._tick_24h(start)):
            check_event_reminders()
        with freeze_time(self._tick_20m(start)):
            check_event_reminders()

        self.assertEqual(
            set(
                EventReminderLog.objects.filter(
                    event=event, user=self.user,
                ).values_list('interval', flat=True)
            ),
            {'24h', '20m'},
        )
        self.assertEqual(
            Notification.objects.filter(
                user=self.user, notification_type='event_reminder',
            ).count(),
            2,
        )
        self.assertEqual(
            EmailLog.objects.filter(
                user=self.user, email_type='event_reminder',
            ).count(),
            2,
        )

    def test_no_duplicate_across_two_consecutive_overlapping_ticks(
        self, mock_slack, mock_ses,
    ):
        """An event whose start is covered by TWO consecutive */15 ticks'
        windows still yields exactly one 24h and one 20m reminder per user
        (EventReminderLog get_or_create dedup), and the Slack announcement
        fires at most once."""
        # Start at :00 so both the 24h window (30 min wide) and the widened
        # 20m window (15 min wide, issue #1001) span two consecutive */15
        # ticks — the worst case for the EventReminderLog dedup.
        start = datetime(2026, 6, 16, 13, 0, 0, tzinfo=dt_tz.utc)
        event = Event.objects.create(
            title='Overlap Event', slug='overlap-event',
            start_datetime=start, status='upcoming',
        )
        EventRegistration.objects.create(event=event, user=self.user)

        # 24h window (23h45m..24h15m) covered by ticks at start-24h00m and
        # start-23h45m.
        with freeze_time(start - timedelta(hours=24)):
            check_event_reminders()
        with freeze_time(start - timedelta(hours=23, minutes=45)):
            check_event_reminders()
        # 20m window (15m..30m wide, issue #1001) is now covered by BOTH the
        # tick at start-30m (delta 30, inclusive end) and the tick at
        # start-15m (delta 15, inclusive start). Both ticks see the event, so
        # this exercises the EventReminderLog (event, user, '20m') dedup that
        # must collapse the overlap to a single reminder.
        with freeze_time(start - timedelta(minutes=30)):
            check_event_reminders()
        with freeze_time(start - timedelta(minutes=15)):
            check_event_reminders()

        self.assertEqual(
            EventReminderLog.objects.filter(
                event=event, user=self.user, interval='24h',
            ).count(),
            1,
        )
        self.assertEqual(
            EventReminderLog.objects.filter(
                event=event, user=self.user, interval='20m',
            ).count(),
            1,
        )
        self.assertEqual(
            Notification.objects.filter(
                user=self.user, notification_type='event_reminder',
            ).count(),
            2,
        )
        # Slack announcement (24h only) fired at most once across all ticks.
        mock_slack.assert_called_once_with('event', event)
        self.assertEqual(
            EventReminderLog.objects.filter(
                event=event, interval='24h_slack',
            ).count(),
            1,
        )
