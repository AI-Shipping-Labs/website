"""Tests for ``email_app.services.preview_contexts.PREVIEW_CONTEXTS``.

The Studio email-template editor uses these placeholder dicts to render a
preview pane. If a template grows new template variables, the matching
preview-context entry MUST also grow them, otherwise the preview renders
the new fields as empty strings (e.g. ``href=""`` on an anchor) and the
operator can't visually verify the wiring.

This file pins the keys the ``event_registration`` template requires so a
future template change can't silently regress the Studio preview.
"""

from django.test import TestCase

from email_app.services.preview_contexts import (
    PREVIEW_CONTEXTS,
    get_preview_context,
)


class EventRegistrationPreviewContextTest(TestCase):
    """The event_registration preview must populate every template var."""

    def test_event_registration_includes_calendar_link_keys(self):
        ctx = PREVIEW_CONTEXTS['event_registration']
        for key in (
            'google_calendar_url',
            'outlook_calendar_url',
            'office365_calendar_url',
        ):
            self.assertIn(key, ctx, f'Missing preview key: {key}')
            self.assertTrue(
                ctx[key],
                f'Preview key {key} must be a non-empty placeholder URL',
            )

    def test_calendar_link_placeholders_point_at_real_hosts(self):
        """Operators should be able to hover a preview link and see the
        right vendor host. A meaningless dummy like ``https://example.com``
        wouldn't tell them whether the wiring is correct.
        """
        ctx = PREVIEW_CONTEXTS['event_registration']
        self.assertTrue(
            ctx['google_calendar_url'].startswith(
                'https://calendar.google.com/calendar/render'
            ),
        )
        self.assertTrue(
            ctx['outlook_calendar_url'].startswith(
                'https://outlook.live.com/calendar/0/deeplink/compose'
            ),
        )
        self.assertTrue(
            ctx['office365_calendar_url'].startswith(
                'https://outlook.office.com/calendar/0/deeplink/compose'
            ),
        )

    def test_join_url_matches_real_send_shape(self):
        """The real ``send_registration_confirmation`` builds
        ``{site}/events/{slug}/join`` — the preview should mirror that
        shape so an operator looking at the preview sees what users see.
        """
        ctx = PREVIEW_CONTEXTS['event_registration']
        self.assertIn('/events/', ctx['join_url'])
        self.assertTrue(ctx['join_url'].endswith('/join'))

    def test_get_preview_context_returns_all_calendar_keys(self):
        """``get_preview_context`` is the public accessor used by the
        Studio view. It must hand back the calendar keys too.
        """
        ctx = get_preview_context('event_registration')
        for key in (
            'google_calendar_url',
            'outlook_calendar_url',
            'office365_calendar_url',
        ):
            self.assertIn(key, ctx)
            self.assertTrue(ctx[key])


class CalendarInviteWeekdayPreviewContextTest(TestCase):
    """Issue #1071: calendar-invite previews must carry the weekday.

    The real send formats these emails with CALENDAR_INVITE_DATETIME_FORMAT
    (a leading weekday). The preview placeholders must mirror that shape so
    the operator preview matches what recipients actually receive.
    """

    def test_event_rescheduled_times_include_weekday(self):
        ctx = PREVIEW_CONTEXTS['event_rescheduled']
        # March 21 and March 28, 2026 are both Saturdays.
        self.assertTrue(
            ctx['old_event_datetime'].startswith('Saturday, '),
            ctx['old_event_datetime'],
        )
        self.assertTrue(
            ctx['new_event_datetime'].startswith('Saturday, '),
            ctx['new_event_datetime'],
        )

    def test_series_update_occurrences_include_weekday(self):
        ctx = PREVIEW_CONTEXTS['series_update']
        # Changed-occurrence framing with a weekday-stamped before/after.
        self.assertTrue(ctx['changed_occurrence'])
        self.assertIn('Thursday, ', ctx['occurrences_list'])
        self.assertIn('(was ', ctx['occurrences_list'])


class RecordingReadyPreviewContextTest(TestCase):
    """Issue #1134 (Phase B): the recording-ready preview renders the new
    "available to watch" watch-link variant without error."""

    def test_preview_context_has_watch_link_keys(self):
        ctx = PREVIEW_CONTEXTS['event_recording_ready']
        self.assertTrue(ctx['is_available_to_watch'])
        self.assertIn('/workshops/', ctx['watch_url'])
        self.assertIn('/video', ctx['watch_url'])
        self.assertNotIn('amazonaws', ctx['watch_url'])

    def test_preview_renders_watch_link_as_real_anchor(self):
        from types import SimpleNamespace

        from email_app.services.email_service import EmailService

        user = SimpleNamespace(
            email='host@example.com',
            first_name='Ada',
            last_name='',
            email_verified=True,
        )
        ctx = PREVIEW_CONTEXTS['event_recording_ready']
        subject, body_html = EmailService()._render_template(
            'event_recording_ready', user, ctx,
        )

        self.assertIn('available to watch', subject.lower())
        self.assertIn(f'href="{ctx["watch_url"]}"', body_html)
        self.assertIn('available to watch', body_html.lower())
        # No raw markdown link syntax may leak into the delivered HTML.
        self.assertNotIn('](', body_html)
