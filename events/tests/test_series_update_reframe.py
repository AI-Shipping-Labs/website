"""Tests for the series-update reframe + calendar-invite weekday (issue #1071).

Covers:
- Threading ``old_start_iso`` through ``enqueue_series_update`` ->
  ``send_series_update`` -> ``send_series_update_to_subscribers``.
- ``_render_series_email`` for ``series_update`` with a changed occurrence +
  old start: the changed line shows old -> new, unchanged lines show a single
  time, the lead names the changed ``event_title``.
- No-old-start (auto-enroll addition) keeps the whole-series framing.
- A subscriber whose accessible list does not include the changed occurrence
  falls back to whole-series framing with no dangling before/after.
- ``CALENDAR_INVITE_DATETIME_FORMAT`` emits the weekday for both the
  timezone-set and the UTC-fallback recipient, and old/new render in one zone.
- ``send_reschedule_notice_one`` renders old/new with the weekday.
- The Studio reschedule path enqueues a series update with the ISO old start.
"""

import email as email_lib
from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings, tag
from django.utils import timezone

from accounts.services.timezones import (
    CALENDAR_INVITE_DATETIME_FORMAT,
    DEFAULT_USER_DATETIME_FORMAT,
    format_user_datetime,
)
from accounts.templatetags.date_formatting import user_event_datetime
from email_app.models import EmailLog
from events.models import (
    Event,
    EventRegistration,
    EventSeries,
    SeriesRegistration,
)
from events.services.series_invite import (
    _render_series_email,
    send_series_update_to_subscribers,
)
from tests.fixtures import TierSetupMixin

User = get_user_model()


def _make_series(**kwargs):
    defaults = {
        'name': 'LLM Zoomcamp 2026 office hours',
        'slug': 'llm-zoomcamp-office-hours',
        'start_time': timezone.now().time(),
        'timezone': 'Europe/Berlin',
    }
    defaults.update(kwargs)
    return EventSeries.objects.create(**defaults)


def _make_occurrence(series, *, offset_days, position, ics_sequence=0, slug=None):
    start = timezone.now() + timedelta(days=offset_days)
    return Event.objects.create(
        title='Office hours',
        slug=slug or f'{series.slug}-{position}',
        start_datetime=start,
        end_datetime=start + timedelta(hours=1),
        status='upcoming',
        ics_sequence=ics_sequence,
        event_series=series,
        series_position=position,
    )


def _html_from_raw(raw):
    msg = email_lib.message_from_string(raw)
    for part in msg.walk():
        if part.get_content_type() == 'text/html':
            return part.get_payload(decode=True).decode('utf-8')
    raise AssertionError('no text/html part in message')


@tag('core')
class CalendarInviteDatetimeFormatTest(TestCase):
    """The dedicated calendar-invite format carries a weekday, in one zone."""

    @classmethod
    def setUpTestData(cls):
        cls.berlin_user = User.objects.create_user(
            email='berlin@test.com', password='pass',
            preferred_timezone='Europe/Berlin',
        )
        cls.utc_user = User.objects.create_user(
            email='utc@test.com', password='pass', preferred_timezone='',
        )
        # 2026-06-25 16:00 UTC == 18:00 Europe/Berlin (a Thursday).
        cls.when = datetime(2026, 6, 25, 16, 0, tzinfo=UTC)

    def test_weekday_for_timezone_user(self):
        rendered = format_user_datetime(
            self.when, self.berlin_user, fmt=CALENDAR_INVITE_DATETIME_FORMAT,
        )
        self.assertEqual(
            rendered, 'Thursday, June 25, 2026, 18:00 Europe/Berlin',
        )

    def test_weekday_for_utc_fallback_user(self):
        rendered = format_user_datetime(
            self.when, self.utc_user, fmt=CALENDAR_INVITE_DATETIME_FORMAT,
        )
        self.assertEqual(rendered, 'Thursday, June 25, 2026, 16:00 UTC')

    def test_old_and_new_render_in_same_zone_for_utc_user(self):
        old = datetime(2026, 6, 24, 16, 0, tzinfo=UTC)  # Wednesday
        old_str = format_user_datetime(
            old, self.utc_user, fmt=CALENDAR_INVITE_DATETIME_FORMAT,
        )
        new_str = format_user_datetime(
            self.when, self.utc_user, fmt=CALENDAR_INVITE_DATETIME_FORMAT,
        )
        self.assertTrue(old_str.endswith('UTC'))
        self.assertTrue(new_str.endswith('UTC'))
        self.assertIn('Wednesday', old_str)
        self.assertIn('Thursday', new_str)


@tag('core')
class GlobalDisplayFormatStaysWeekdayFreeTest(TestCase):
    """The global display path must NOT gain a weekday (issue #1071 scope)."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            email='display@test.com', password='pass',
            preferred_timezone='Europe/Berlin',
        )
        cls.when = datetime(2026, 6, 25, 16, 0, tzinfo=UTC)  # Thursday

    def test_global_default_format_has_no_weekday(self):
        # The named global default must stay weekday-free — only the
        # dedicated calendar-invite format adds it.
        self.assertNotIn('%A', DEFAULT_USER_DATETIME_FORMAT)

    def test_format_user_datetime_default_omits_weekday(self):
        rendered = format_user_datetime(self.when, self.user)
        self.assertEqual(rendered, 'June 25, 2026, 18:00 Europe/Berlin')
        self.assertNotIn('Thursday', rendered)

    def test_user_event_datetime_tag_omits_weekday(self):
        # The {% user_event_datetime %} tag (event pages / dashboard) uses
        # the global default, so it must stay weekday-free.
        rendered = user_event_datetime(self.when, self.user)
        self.assertNotIn('Thursday', rendered)


@tag('core')
class RenderSeriesUpdateBodyTest(TierSetupMixin, TestCase):
    """_render_series_email branches on the changed occurrence + old start."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.user = User.objects.create_user(
            email='render@test.com', password='pass',
            preferred_timezone='Europe/Berlin',
        )

    def setUp(self):
        self.series = _make_series()
        self.changed = _make_occurrence(
            self.series, offset_days=7, position=1, ics_sequence=2,
            slug='oh-1',
        )
        self.other = _make_occurrence(
            self.series, offset_days=14, position=2, slug='oh-2',
        )
        # Old start = one day before the changed occurrence's new start.
        self.old_start = self.changed.start_datetime - timedelta(days=1)

    def test_registration_and_cancellation_emails_carry_weekday(self):
        # series_registration / series_cancellation reuse _render_series_email,
        # so their session times must also gain the weekday (issue #1071).
        expected_weekday = self.changed.start_datetime.astimezone(
            ZoneInfo('Europe/Berlin'),
        ).strftime('%A')
        _, reg_html = _render_series_email(
            'series_registration', self.user, self.series,
            [self.changed, self.other], 'series_registration',
        )
        self.assertIn(f'{expected_weekday}, ', reg_html)
        _, cancel_html = _render_series_email(
            'series_cancellation', self.user, self.series,
            [self.changed], 'series_cancellation',
        )
        self.assertIn(f'{expected_weekday}, ', cancel_html)

    def test_changed_occurrence_framing(self):
        subject, html = _render_series_email(
            'series_update', self.user, self.series,
            [self.changed, self.other], 'series_update',
            changed_event=self.changed, old_start=self.old_start,
        )
        # Subject names the moved session, not just the series.
        self.assertIn('Office hours', subject)
        self.assertIn('LLM Zoomcamp 2026 office hours', subject)
        # Lead copy names the changed session.
        self.assertIn('The time for', html)
        # Changed line shows old -> new ("was ..."); unchanged line does not.
        old_str = format_user_datetime(
            self.old_start, self.user, fmt=CALENDAR_INVITE_DATETIME_FORMAT,
        )
        self.assertIn(f'(was {old_str})', html)
        # Exactly one "(was " annotation — the unchanged occurrence has none.
        self.assertEqual(html.count('(was '), 1)
        # Weekday present on session times.
        weekday = self.changed.start_datetime.astimezone(
            ZoneInfo('Europe/Berlin'),
        ).strftime('%A')
        self.assertIn(f'{weekday}, ', html)

    def test_no_old_start_keeps_whole_series_framing(self):
        subject, html = _render_series_email(
            'series_update', self.user, self.series,
            [self.changed, self.other], 'series_update',
            changed_event=self.changed, old_start=None,
        )
        self.assertEqual(
            subject, 'Your LLM Zoomcamp 2026 office hours calendar '
            'invite has been updated',
        )
        self.assertIn("There's been a change to the", html)
        self.assertNotIn('(was ', html)

    def test_changed_occurrence_not_in_subscriber_list_falls_back(self):
        # Subscriber's accessible list excludes the changed occurrence.
        subject, html = _render_series_email(
            'series_update', self.user, self.series,
            [self.other], 'series_update',
            changed_event=self.changed, old_start=self.old_start,
        )
        # Whole-series framing, no dangling before/after.
        self.assertIn("There's been a change to the", html)
        self.assertNotIn('(was ', html)
        self.assertNotIn('Updated time for', subject)


@tag('core')
@override_settings(SES_ENABLED=True)
class SendSeriesUpdateWithOldStartTest(TierSetupMixin, TestCase):
    """The fan-out threads old_start_iso into the rendered body."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.alice = User.objects.create_user(
            email='alice@test.com', password='pass', email_verified=True,
            preferred_timezone='Europe/Berlin',
        )

    def setUp(self):
        self.series = _make_series()
        self.changed = _make_occurrence(
            self.series, offset_days=7, position=1, ics_sequence=2,
            slug='ohu-1',
        )
        self.other = _make_occurrence(
            self.series, offset_days=14, position=2, slug='ohu-2',
        )
        SeriesRegistration.objects.create(series=self.series, user=self.alice)
        EventRegistration.objects.create(event=self.changed, user=self.alice)
        EventRegistration.objects.create(event=self.other, user=self.alice)
        self.old_start = self.changed.start_datetime - timedelta(days=1)

    @patch('events.services.registration_email.boto3')
    def test_old_start_iso_renders_before_after(self, mock_boto3):
        client = mock_boto3.client.return_value
        client.send_email.return_value = {'MessageId': 'm'}

        sent = send_series_update_to_subscribers(
            self.changed, old_start_iso=self.old_start.isoformat(),
        )

        self.assertEqual(sent, 1)
        raw = client.send_email.call_args.kwargs['Content']['Raw']['Data']
        html = _html_from_raw(raw)
        self.assertIn('(was ', html)
        self.assertIn('The time for', html)

    @patch('events.services.registration_email.boto3')
    def test_no_old_start_iso_keeps_whole_series_framing(self, mock_boto3):
        client = mock_boto3.client.return_value
        client.send_email.return_value = {'MessageId': 'm'}

        sent = send_series_update_to_subscribers(self.changed)

        self.assertEqual(sent, 1)
        raw = client.send_email.call_args.kwargs['Content']['Raw']['Data']
        html = _html_from_raw(raw)
        self.assertNotIn('(was ', html)
        self.assertIn("There's been a change to the", html)


@tag('core')
class SeriesUpdatePreviewContextTest(TestCase):
    """The Studio preview context renders the template with no missing vars."""

    def test_series_update_preview_renders_with_before_after_and_weekday(self):
        # Mirror the Studio preview path (_render_preview_html), which fills
        # the file template body with the placeholder context.
        from email_app.services.preview_contexts import get_preview_context
        from studio.views.email_templates import (
            _read_file_template,
            _render_preview_html,
        )

        ctx = get_preview_context('series_update')
        self.assertTrue(ctx, 'series_update preview context must exist')
        subject, body_markdown = _read_file_template('series_update')
        html = _render_preview_html(
            'series_update', subject, body_markdown, footer_note='',
        )
        # No unresolved template variables left as literal placeholders.
        self.assertNotIn('{{', html)
        self.assertNotIn('{%', html)
        # Names the changed session, shows a before/after, has a weekday.
        self.assertIn('Office hours', html)
        self.assertIn('(was ', html)
        self.assertIn('Thursday, ', html)


@tag('core')
class EnqueueSeriesUpdateThreadingTest(TestCase):
    """enqueue/send wrappers forward old_start_iso to the service."""

    def test_enqueue_passes_old_start_iso_to_async_task(self):
        from events.tasks.notify_series_invite import enqueue_series_update

        with patch('jobs.tasks.async_task') as mock_async:
            enqueue_series_update(42, old_start_iso='2026-06-24T16:00:00+00:00')

        args = mock_async.call_args.args
        # Positional args: task path, event_id, user_ids, old_start_iso.
        self.assertEqual(args[0],
                         'events.tasks.notify_series_invite.send_series_update')
        self.assertEqual(args[1], 42)
        self.assertIsNone(args[2])
        self.assertEqual(args[3], '2026-06-24T16:00:00+00:00')

    def test_send_forwards_old_start_iso_to_service(self):
        from events.tasks.notify_series_invite import send_series_update

        series = _make_series()
        event = _make_occurrence(series, offset_days=7, position=1, slug='fwd-1')

        with patch(
            'events.services.series_invite.send_series_update_to_subscribers',
            return_value=3,
        ) as mock_service:
            result = send_series_update(
                event.pk, old_start_iso='2026-06-24T16:00:00+00:00',
            )

        self.assertEqual(result['count'], 3)
        self.assertEqual(
            mock_service.call_args.kwargs['old_start_iso'],
            '2026-06-24T16:00:00+00:00',
        )


@tag('core')
@override_settings(SES_ENABLED=True)
class RescheduleNoticeWeekdayTest(TestCase):
    """The per-event event_rescheduled email carries the weekday."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            email='resched@test.com', password='pass', email_verified=True,
            preferred_timezone='Europe/Berlin',
        )

    def setUp(self):
        start = datetime(2026, 6, 25, 16, 0, tzinfo=UTC)  # Thursday 18:00 Berlin
        self.event = Event.objects.create(
            title='Office hours',
            slug='resched-oh',
            start_datetime=start,
            end_datetime=start + timedelta(hours=1),
            status='upcoming',
            ics_sequence=2,
        )
        self.registration = EventRegistration.objects.create(
            event=self.event, user=self.user,
        )

    @patch('events.services.registration_email.boto3')
    def test_old_and_new_times_include_weekday(self, mock_boto3):
        from events.tasks.notify_reschedule import send_reschedule_notice_one

        client = mock_boto3.client.return_value
        client.send_email.return_value = {'MessageId': 'm'}
        old_start = datetime(2026, 6, 24, 16, 0, tzinfo=UTC)  # Wednesday

        result = send_reschedule_notice_one(
            self.event.pk, self.user.pk, old_start.isoformat(),
        )

        self.assertEqual(result['status'], 'sent')
        raw = client.send_email.call_args.kwargs['Content']['Raw']['Data']
        html = _html_from_raw(raw)
        self.assertIn('Wednesday, June 24, 2026, 18:00 Europe/Berlin', html)
        self.assertIn('Thursday, June 25, 2026, 18:00 Europe/Berlin', html)
        self.assertEqual(
            EmailLog.objects.filter(email_type='event_rescheduled').count(), 1,
        )


@tag('core')
class StudioRescheduleEnqueuesSeriesUpdateTest(TierSetupMixin, TestCase):
    """_maybe_notify_reschedule enqueues the series update with ISO old start."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.admin = User.objects.create_user(
            email='admin@test.com', password='pass', is_staff=True,
            is_superuser=True,
        )

    def test_series_reschedule_enqueues_update_with_old_start_iso(self):
        from studio.views.events import _maybe_notify_reschedule

        series = _make_series()
        old_start = timezone.now() + timedelta(days=7)
        new_start = old_start + timedelta(days=1)
        event = _make_occurrence(series, offset_days=8, position=1, slug='sre-1')
        event.start_datetime = new_start
        event.end_datetime = new_start + timedelta(hours=1)
        event.save(update_fields=['start_datetime', 'end_datetime'])

        request = type('Req', (), {})()
        with patch(
            'events.tasks.notify_series_invite.enqueue_series_update',
        ) as mock_enqueue, patch(
            'events.tasks.notify_reschedule.enqueue_reschedule_notice',
        ), patch('studio.views.events.messages'):
            _maybe_notify_reschedule(request, event, old_start)

        self.assertEqual(mock_enqueue.call_args.args[0], event.pk)
        self.assertEqual(
            mock_enqueue.call_args.kwargs['old_start_iso'],
            old_start.isoformat(),
        )
