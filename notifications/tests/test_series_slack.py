"""Tests for series-level Slack announcements (issue #868)."""

from datetime import time, timedelta
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings
from django.utils import timezone

from events.models import Event, EventSeries
from integrations.config import clear_config_cache
from notifications.services.slack_announcements import (
    SERIES_SESSION_DISPLAY_CAP,
    build_series_slack_blocks,
    post_series_slack_announcement,
)


def _make_series(**overrides):
    defaults = {
        'name': 'Weekly Build Club',
        'slug': 'weekly-build-club',
        'description': 'Ship something every week.',
        'start_time': time(18, 0),
        'timezone': 'Europe/Berlin',
    }
    defaults.update(overrides)
    return EventSeries.objects.create(**defaults)


def _make_session(series, position, days_ahead, **overrides):
    defaults = {
        'title': f'{series.name} — Session {position}',
        'slug': f'{series.slug}-session-{position}',
        'start_datetime': timezone.now() + timedelta(days=days_ahead),
        'status': 'upcoming',
        'required_level': 0,
        'event_series': series,
        'series_position': position,
    }
    defaults.update(overrides)
    return Event.objects.create(**defaults)


class BuildSeriesSlackBlocksTest(TestCase):
    """Block-building for the series Slack message."""

    def test_header_links_to_public_series_page(self):
        series = _make_series()
        s1 = _make_session(series, 1, 3)
        text, blocks = build_series_slack_blocks(series, [s1])
        self.assertIn('New event series', text)
        self.assertIn('Weekly Build Club', text)
        section = blocks[0]['text']['text']
        self.assertIn('*New event series:*', section)
        self.assertIn(series.get_absolute_url(), section)
        self.assertNotIn('/events/groups/weekly-build-club', section)

    def test_description_markdown_link_rendered_as_mrkdwn(self):
        """Issue #887: a markdown link in the series description must render
        as Slack mrkdwn, not raw ``[text](url)``."""
        series = _make_series(
            description='Catch up on the [kickoff](https://example.com/k).',
        )
        s1 = _make_session(series, 1, 3)
        _, blocks = build_series_slack_blocks(series, [s1])
        section = blocks[0]['text']['text']
        self.assertIn('<https://example.com/k|kickoff>', section)
        self.assertNotIn('[kickoff]', section)

    def test_lists_sessions_with_tz_strip(self):
        series = _make_series()
        s1 = _make_session(series, 1, 3)
        s2 = _make_session(series, 2, 10)
        _, blocks = build_series_slack_blocks(series, [s1, s2])
        section = blocks[0]['text']['text']
        self.assertIn(s1.title, section)
        self.assertIn(s2.title, section)
        # The fixed timezone strip uses the per-event helper labels.
        self.assertIn('UTC', section)
        self.assertIn('CET', section)

    def test_view_series_button_present(self):
        series = _make_series()
        s1 = _make_session(series, 1, 3)
        _, blocks = build_series_slack_blocks(series, [s1])
        button = blocks[1]['elements'][0]
        self.assertEqual(button['type'], 'button')
        self.assertEqual(button['text']['text'], 'View series')
        self.assertIn(series.get_absolute_url(), button['url'])
        self.assertNotIn('/events/groups/weekly-build-club', button['url'])

    def test_session_list_is_capped_with_overflow_line(self):
        series = _make_series()
        sessions = [
            _make_session(series, i, i)
            for i in range(1, SERIES_SESSION_DISPLAY_CAP + 4)
        ]
        _, blocks = build_series_slack_blocks(series, sessions)
        section = blocks[0]['text']['text']
        # Only the cap is shown verbatim; the overflow says "and M more".
        self.assertIn(sessions[0].title, section)
        self.assertNotIn(sessions[-1].title, section)
        remaining = len(sessions) - SERIES_SESSION_DISPLAY_CAP
        self.assertIn(f'and {remaining} more', section)


@override_settings(SLACK_ENABLED=True)
class PostSeriesSlackAnnouncementTest(TestCase):
    """End-to-end posting behaviour for the series announcement."""

    def setUp(self):
        clear_config_cache()

    @patch('notifications.services.slack_announcements.requests.post')
    def test_posts_one_message_for_whole_series(self, mock_post):
        mock_response = MagicMock()
        mock_response.json.return_value = {'ok': True}
        mock_post.return_value = mock_response

        series = _make_series()
        _make_session(series, 1, 3)
        _make_session(series, 2, 10)
        _make_session(series, 3, 17)

        with self.settings(
            SLACK_ENVIRONMENT='production',
            SLACK_BOT_TOKEN='xoxb-test',
            SLACK_ANNOUNCEMENTS_CHANNEL_ID='C123',
        ):
            result = post_series_slack_announcement(series)

        self.assertTrue(result)
        # One message, not one per session.
        mock_post.assert_called_once()
        self.assertEqual(mock_post.call_args.kwargs['json']['channel'], 'C123')

    @patch('notifications.services.slack_announcements.requests.post')
    def test_empty_series_does_not_post(self, mock_post):
        series = _make_series()
        # Only a past session -> not upcoming.
        _make_session(series, 1, -3, slug='past-session')

        with self.settings(
            SLACK_ENVIRONMENT='production',
            SLACK_BOT_TOKEN='xoxb-test',
            SLACK_ANNOUNCEMENTS_CHANNEL_ID='C123',
        ):
            result = post_series_slack_announcement(series)

        self.assertFalse(result)
        mock_post.assert_not_called()

    @patch('notifications.services.slack_announcements.requests.post')
    def test_draft_and_cancelled_sessions_excluded(self, mock_post):
        series = _make_series()
        _make_session(series, 1, 3, slug='draft-s', status='draft')
        _make_session(series, 2, 4, slug='cancelled-s', status='cancelled')

        with self.settings(
            SLACK_ENVIRONMENT='production',
            SLACK_BOT_TOKEN='xoxb-test',
            SLACK_ANNOUNCEMENTS_CHANNEL_ID='C123',
        ):
            result = post_series_slack_announcement(series)

        # No upcoming publishable sessions -> no post.
        self.assertFalse(result)
        mock_post.assert_not_called()

    @override_settings(SLACK_ENABLED=False)
    @patch('notifications.services.slack_announcements.requests.post')
    def test_skips_when_slack_disabled(self, mock_post):
        series = _make_series()
        _make_session(series, 1, 3)
        result = post_series_slack_announcement(series)
        self.assertFalse(result)
        mock_post.assert_not_called()

    @patch('notifications.services.slack_announcements.requests.post')
    def test_skips_when_unconfigured(self, mock_post):
        series = _make_series()
        _make_session(series, 1, 3)
        with self.settings(
            SLACK_ENVIRONMENT='production',
            SLACK_BOT_TOKEN='',
            SLACK_ANNOUNCEMENTS_CHANNEL_ID='C123',
        ):
            result = post_series_slack_announcement(series)
        self.assertFalse(result)
        mock_post.assert_not_called()

    @patch('notifications.services.slack_announcements.requests.post')
    def test_returns_false_on_slack_error(self, mock_post):
        mock_response = MagicMock()
        mock_response.json.return_value = {'ok': False, 'error': 'channel_not_found'}
        mock_post.return_value = mock_response

        series = _make_series()
        _make_session(series, 1, 3)
        with (
            self.settings(
                SLACK_ENVIRONMENT='production',
                SLACK_BOT_TOKEN='xoxb-test',
                SLACK_ANNOUNCEMENTS_CHANNEL_ID='C123',
            ),
            self.assertLogs(
                'notifications.services.slack_announcements', level='WARNING',
            ),
        ):
            result = post_series_slack_announcement(series)
        self.assertFalse(result)
