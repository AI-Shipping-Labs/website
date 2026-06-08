"""Tests for Slack announcement posting."""

from datetime import date, datetime
from datetime import timezone as dt_tz
from unittest.mock import MagicMock, patch

import requests
from django.test import TestCase, override_settings

from content.models import Article, Course, Download, Workshop
from events.models import Event
from integrations.config import clear_config_cache
from integrations.models import IntegrationSetting
from notifications.services.slack_announcements import (
    _build_slack_blocks,
    _truncate_description,
    markdown_to_mrkdwn,
    post_slack_announcement,
)
from voting.models import Poll


class MarkdownToMrkdwnTest(TestCase):
    """Issue #887: convert Markdown descriptions to Slack mrkdwn so links
    and emphasis render instead of showing raw ``[text](url)``/``**bold**``.
    """

    def test_link_converted_to_mrkdwn_angle_form(self):
        out = markdown_to_mrkdwn(
            'See [previous workshop](https://example.com/w) for details.'
        )
        self.assertIn('<https://example.com/w|previous workshop>', out)
        self.assertNotIn('[previous workshop]', out)
        self.assertNotIn('](https://example.com/w)', out)

    def test_multiple_links_all_converted(self):
        out = markdown_to_mrkdwn(
            '[a](https://x.com/a) and [b](https://y.com/b)'
        )
        self.assertEqual(out, '<https://x.com/a|a> and <https://y.com/b|b>')

    def test_bold_double_asterisk_converted(self):
        self.assertEqual(markdown_to_mrkdwn('**bold** word'), '*bold* word')

    def test_bold_double_underscore_converted(self):
        self.assertEqual(markdown_to_mrkdwn('__bold__ word'), '*bold* word')

    def test_heading_marker_stripped(self):
        self.assertEqual(markdown_to_mrkdwn('# Title here'), 'Title here')

    def test_image_syntax_stripped(self):
        out = markdown_to_mrkdwn('Look ![alt](https://cdn.example.com/a.png) here')
        self.assertNotIn('![alt]', out)
        self.assertNotIn('https://cdn.example.com/a.png', out)

    def test_plain_text_unchanged(self):
        text = 'Just a normal description with no markdown.'
        self.assertEqual(markdown_to_mrkdwn(text), text)

    def test_empty_input_returned_as_is(self):
        self.assertEqual(markdown_to_mrkdwn(''), '')
        self.assertIsNone(markdown_to_mrkdwn(None))

    def test_inline_code_preserved(self):
        # Single backticks are valid Slack mrkdwn — leave them alone.
        out = markdown_to_mrkdwn('Run `make test` first')
        self.assertIn('`make test`', out)


class TruncateDescriptionTest(TestCase):
    """Issue #887: truncate BEFORE converting so a converted ``<url|text>``
    link is never sliced mid-token, and never cut inside ``[text](url)``."""

    def test_short_text_converted_without_ellipsis(self):
        out = _truncate_description('See [w](https://x.com/w).')
        self.assertEqual(out, 'See <https://x.com/w|w>.')
        self.assertNotIn('…', out)

    def test_long_text_truncated_with_ellipsis(self):
        out = _truncate_description('word ' * 100)
        self.assertTrue(out.endswith('…'))
        self.assertLessEqual(len(out.rstrip('…')), 200)

    def test_truncation_does_not_split_a_markdown_link(self):
        # Pad so the budget boundary lands in the middle of the link, then
        # confirm the link is either fully kept (converted) or fully dropped
        # — never left as a half-rendered fragment.
        prefix = 'x ' * 95  # 190 chars, boundary falls inside the link
        out = _truncate_description(
            prefix + '[click here](https://example.com/landing-page)'
        )
        self.assertNotIn('[click here]', out)
        self.assertNotIn('](https://', out)
        # No dangling unmatched angle-link fragment.
        self.assertEqual(out.count('<'), out.count('|'))


class BuildSlackBlocksTest(TestCase):
    """Tests for Slack Block Kit message building."""

    def test_builds_article_blocks(self):
        article = Article.objects.create(
            title='Test Article', slug='test-article',
            date=date(2025, 1, 1), description='A great article.',
        )
        text, blocks = _build_slack_blocks('article', article)
        self.assertIn('New article', text)
        self.assertIn('Test Article', text)
        self.assertEqual(len(blocks), 2)
        self.assertEqual(blocks[0]['type'], 'section')
        self.assertEqual(blocks[1]['type'], 'actions')

    def test_blocks_contain_mrkdwn(self):
        article = Article.objects.create(
            title='Test', slug='test-mrkdwn',
            date=date(2025, 1, 1), description='Desc.',
        )
        _, blocks = _build_slack_blocks('article', article)
        section = blocks[0]
        self.assertEqual(section['text']['type'], 'mrkdwn')
        self.assertIn('*New article:*', section['text']['text'])
        self.assertIn('Desc.', section['text']['text'])

    def test_blocks_contain_button(self):
        article = Article.objects.create(
            title='Test', slug='test-button',
            date=date(2025, 1, 1),
        )
        _, blocks = _build_slack_blocks('article', article)
        button = blocks[1]['elements'][0]
        self.assertEqual(button['type'], 'button')
        self.assertIn('/blog/test-button', button['url'])

    def test_truncates_long_description(self):
        # Use spaces so the word-boundary backoff has somewhere to cut;
        # the converted description ends with the … ellipsis (issue #887).
        article = Article.objects.create(
            title='Test', slug='test-truncate',
            date=date(2025, 1, 1),
            description='word ' * 60,
        )
        _, blocks = _build_slack_blocks('article', article)
        text = blocks[0]['text']['text']
        self.assertIn('…', text)
        # Truncated at/under the 200-char budget, plus the ellipsis.
        description_part = text.split('\n\n', 1)[1]
        self.assertLessEqual(len(description_part), 201)

    def test_build_slack_blocks_workshop_label(self):
        """text_fallback starts with 'New workshop:' and mrkdwn_text starts
        with '*New workshop:*' (issue #647)."""
        workshop = Workshop.objects.create(
            title='RAG Bootcamp', slug='rag-bootcamp',
            date=date(2026, 1, 1), status='published',
            description='Hands-on RAG.',
            landing_required_level=0,
            pages_required_level=10,
            recording_required_level=20,
        )
        text_fallback, blocks = _build_slack_blocks('workshop', workshop)
        self.assertEqual(text_fallback, 'New workshop: RAG Bootcamp')
        self.assertTrue(
            blocks[0]['text']['text'].startswith('*New workshop:*'),
            f'mrkdwn_text should start with *New workshop:* but was: '
            f'{blocks[0]["text"]["text"]!r}',
        )


class EventSlackBlocksTimeStripTest(TestCase):
    """Tests that `_build_slack_blocks('event', ...)` injects the TZ strip
    line between the title and description (issue #691)."""

    def test_event_block_contains_summer_tz_strip(self):
        event = Event.objects.create(
            title='Solving a Real AI Engineer Take-Home Assignment Live',
            slug='ai-take-home-live',
            start_datetime=datetime(2026, 5, 21, 14, 0, 0, tzinfo=dt_tz.utc),
            status='upcoming',
            description='What do AI engineer take-home assignments look like?',
        )
        _, blocks = _build_slack_blocks('event', event)
        text = blocks[0]['text']['text']
        self.assertIn(
            '🗓 Thu, May 21 · 10:00 NYC · 14:00 UTC · 16:00 CET · 19:30 IST',
            text,
        )

    def test_event_tz_strip_positioned_between_title_and_description(self):
        """The strip sits after the title line and before the description,
        separated by `\\n\\n` from the description."""
        event = Event.objects.create(
            title='Positioning Test', slug='positioning-test',
            start_datetime=datetime(2026, 5, 21, 14, 0, 0, tzinfo=dt_tz.utc),
            status='upcoming',
            description='The description body.',
        )
        _, blocks = _build_slack_blocks('event', event)
        text = blocks[0]['text']['text']
        expected = (
            '*Upcoming event:* '
            f'<https://aishippinglabs.com{event.get_absolute_url()}|'
            'Positioning Test>'
            '\n🗓 Thu, May 21 · 10:00 NYC · 14:00 UTC · 16:00 CET · 19:30 IST'
            '\n\nThe description body.'
        )
        self.assertEqual(text, expected)

    def test_event_dst_boundary_cest(self):
        """An event on the EU spring-forward Sunday renders CEST."""
        event = Event.objects.create(
            title='DST Spring Forward', slug='dst-spring-forward',
            start_datetime=datetime(2026, 3, 29, 9, 0, 0, tzinfo=dt_tz.utc),
            status='upcoming',
        )
        _, blocks = _build_slack_blocks('event', event)
        text = blocks[0]['text']['text']
        self.assertIn('11:00 CET', text)
        self.assertNotIn('10:00 CET', text)

    def test_event_description_markdown_link_rendered_as_mrkdwn(self):
        """Issue #887: a markdown link in the event description must render
        as a Slack mrkdwn link, not raw ``[text](url)``."""
        event = Event.objects.create(
            title='Markdown Desc Event', slug='markdown-desc-event',
            start_datetime=datetime(2026, 5, 21, 14, 0, 0, tzinfo=dt_tz.utc),
            status='upcoming',
            description='Watch the [previous workshop](https://example.com/w).',
        )
        _, blocks = _build_slack_blocks('event', event)
        text = blocks[0]['text']['text']
        self.assertIn('<https://example.com/w|previous workshop>', text)
        self.assertNotIn('[previous workshop]', text)

    def test_event_winter_block_contains_tz_strip(self):
        """A winter event renders CET (standard time) as UTC+1."""
        event = Event.objects.create(
            title='Winter Event', slug='winter-event',
            start_datetime=datetime(2026, 1, 15, 9, 0, 0, tzinfo=dt_tz.utc),
            status='upcoming',
        )
        _, blocks = _build_slack_blocks('event', event)
        text = blocks[0]['text']['text']
        self.assertIn(
            '🗓 Thu, Jan 15 · 04:00 NYC · 09:00 UTC · 10:00 CET · 14:30 IST',
            text,
        )

    def test_event_missing_start_datetime_omits_tz_strip(self):
        """Defensive: an event with start_datetime=None renders normally
        without a TZ line and without raising."""
        event = Event.objects.create(
            title='No Time Event', slug='no-time-event',
            start_datetime=datetime(2026, 5, 21, 14, 0, 0, tzinfo=dt_tz.utc),
            status='upcoming',
            description='Description.',
        )
        # Bypass the NOT NULL constraint on the in-memory instance only;
        # `_build_slack_blocks` must tolerate this defensively.
        event.start_datetime = None
        _, blocks = _build_slack_blocks('event', event)
        text = blocks[0]['text']['text']
        self.assertNotIn('🗓', text)
        self.assertNotIn(' NYC', text)
        self.assertIn('*Upcoming event:*', text)
        self.assertIn('Description.', text)

    def test_external_host_event_renders_same_tz_strip(self):
        """An external-host event (e.g. Maven) uses the same start_datetime
        source and must render the strip identically."""
        event = Event.objects.create(
            title='External Maven Event', slug='external-maven-event',
            start_datetime=datetime(2026, 5, 21, 14, 0, 0, tzinfo=dt_tz.utc),
            status='upcoming',
            external_host='maven',
            description='A Maven-hosted course session.',
        )
        _, blocks = _build_slack_blocks('event', event)
        text = blocks[0]['text']['text']
        self.assertIn(
            '🗓 Thu, May 21 · 10:00 NYC · 14:00 UTC · 16:00 CET · 19:30 IST',
            text,
        )


class NonEventContentTypesUnchangedTest(TestCase):
    """Snapshot-style tests asserting non-event content types do not gain
    a TZ strip line (issue #691)."""

    def _section_text(self, content_type, content):
        _, blocks = _build_slack_blocks(content_type, content)
        return blocks[0]['text']['text']

    def test_article_text_has_no_tz_strip(self):
        article = Article.objects.create(
            title='Snap Article', slug='snap-article',
            date=date(2026, 5, 21), description='Article body.',
        )
        text = self._section_text('article', article)
        self.assertNotIn('🗓', text)
        self.assertNotIn(' NYC', text)
        self.assertEqual(
            text,
            '*New article:* '
            f'<https://aishippinglabs.com{article.get_absolute_url()}|'
            'Snap Article>'
            '\n\nArticle body.',
        )

    def test_course_text_has_no_tz_strip(self):
        course = Course.objects.create(
            title='Snap Course', slug='snap-course',
            status='published', required_level=0,
            description='Course body.',
        )
        text = self._section_text('course', course)
        self.assertNotIn('🗓', text)
        self.assertNotIn(' NYC', text)

    def test_recording_text_has_no_tz_strip(self):
        """A recording is an Event row with status='completed' rendered via
        the 'recording' content type. Even though the underlying model has
        start_datetime, the 'recording' branch must not insert the TZ strip
        (only the 'event' branch does)."""
        recording = Event.objects.create(
            title='Snap Recording', slug='snap-recording',
            start_datetime=datetime(2026, 5, 21, 14, 0, 0, tzinfo=dt_tz.utc),
            status='completed',
            recording_url='https://youtube.com/watch?v=test',
            published=True,
            description='Recording body.',
        )
        text = self._section_text('recording', recording)
        self.assertNotIn('🗓', text)
        self.assertNotIn(' NYC', text)
        self.assertTrue(text.startswith('*New recording:*'))

    def test_download_text_has_no_tz_strip(self):
        download = Download.objects.create(
            title='Snap Download', slug='snap-download',
            file_url='https://example.com/file.pdf',
            published=True, required_level=0,
            description='Download body.',
        )
        text = self._section_text('download', download)
        self.assertNotIn('🗓', text)
        self.assertNotIn(' NYC', text)

    def test_poll_text_has_no_tz_strip(self):
        poll = Poll.objects.create(title='Snap Poll', status='open')
        text = self._section_text('poll', poll)
        self.assertNotIn('🗓', text)
        self.assertNotIn(' NYC', text)

    def test_workshop_text_has_no_tz_strip(self):
        workshop = Workshop.objects.create(
            title='Snap Workshop', slug='snap-workshop',
            date=date(2026, 5, 21), status='published',
            description='Workshop body.',
            landing_required_level=0,
            pages_required_level=10,
            recording_required_level=20,
        )
        text = self._section_text('workshop', workshop)
        self.assertNotIn('🗓', text)
        self.assertNotIn(' NYC', text)


@override_settings(SLACK_ENABLED=True)
class PostSlackAnnouncementTest(TestCase):
    """Tests for the post_slack_announcement function."""

    def setUp(self):
        clear_config_cache()

    def test_skips_when_no_bot_token(self):
        article = Article.objects.create(
            title='Test', slug='test-no-token',
            date=date(2025, 1, 1),
        )
        with self.settings(
            SLACK_ENVIRONMENT='production',
            SLACK_BOT_TOKEN='',
            SLACK_ANNOUNCEMENTS_CHANNEL_ID='C123',
        ):
            result = post_slack_announcement('article', article)
            self.assertFalse(result)

    def test_skips_when_no_channel_id(self):
        article = Article.objects.create(
            title='Test', slug='test-no-channel',
            date=date(2025, 1, 1),
        )
        with self.settings(
            SLACK_ENVIRONMENT='production',
            SLACK_BOT_TOKEN='xoxb-test',
            SLACK_ANNOUNCEMENTS_CHANNEL_ID='',
        ):
            result = post_slack_announcement('article', article)
            self.assertFalse(result)

    @patch('notifications.services.slack_announcements.requests.post')
    def test_development_without_dev_channel_skips(self, mock_post):
        article = Article.objects.create(
            title='Dev Skip Article', slug='test-dev-skip',
            date=date(2025, 1, 1),
        )
        with self.settings(
            SLACK_ENVIRONMENT='development',
            SLACK_BOT_TOKEN='xoxb-test',
            SLACK_ANNOUNCEMENTS_CHANNEL_ID='CPROD',
            SLACK_DEV_ANNOUNCEMENTS_CHANNEL_ID='',
        ):
            result = post_slack_announcement('article', article)

        self.assertFalse(result)
        mock_post.assert_not_called()

    @patch('notifications.services.slack_announcements.requests.post')
    def test_development_posts_to_dev_channel(self, mock_post):
        mock_response = MagicMock()
        mock_response.json.return_value = {'ok': True}
        mock_post.return_value = mock_response

        article = Article.objects.create(
            title='Dev Posted Article', slug='test-dev-post',
            date=date(2025, 1, 1),
        )
        with self.settings(
            SLACK_ENVIRONMENT='development',
            SLACK_BOT_TOKEN='xoxb-test',
            SLACK_ANNOUNCEMENTS_CHANNEL_ID='CPROD',
            SLACK_DEV_ANNOUNCEMENTS_CHANNEL_ID='CDEV',
        ):
            result = post_slack_announcement('article', article)

        self.assertTrue(result)
        self.assertEqual(mock_post.call_args.kwargs['json']['channel'], 'CDEV')

    @patch('notifications.services.slack_announcements.requests.post')
    def test_posts_to_slack_api(self, mock_post):
        mock_response = MagicMock()
        mock_response.json.return_value = {'ok': True}
        mock_post.return_value = mock_response

        article = Article.objects.create(
            title='Posted Article', slug='test-post',
            date=date(2025, 1, 1),
        )
        with self.settings(
            SLACK_ENVIRONMENT='production',
            SLACK_BOT_TOKEN='xoxb-test',
            SLACK_ANNOUNCEMENTS_CHANNEL_ID='C123',
        ):
            result = post_slack_announcement('article', article)

        self.assertTrue(result)
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        self.assertIn('C123', call_kwargs.kwargs['json']['channel'])

    @patch('notifications.services.slack_announcements.requests.post')
    def test_returns_false_on_slack_error(self, mock_post):
        mock_response = MagicMock()
        mock_response.json.return_value = {'ok': False, 'error': 'channel_not_found'}
        mock_post.return_value = mock_response

        article = Article.objects.create(
            title='Error Article', slug='test-error',
            date=date(2025, 1, 1),
        )
        with (
            self.settings(
                SLACK_ENVIRONMENT='production',
                SLACK_BOT_TOKEN='xoxb-test',
                SLACK_ANNOUNCEMENTS_CHANNEL_ID='C123',
            ),
            self.assertLogs('notifications.services.slack_announcements', level='WARNING'),
        ):
            result = post_slack_announcement('article', article)

        self.assertFalse(result)

    @patch('notifications.services.slack_announcements.requests.post')
    def test_returns_false_on_exception(self, mock_post):
        mock_post.side_effect = requests.exceptions.Timeout('Network error')
        article = Article.objects.create(
            title='Exception Article', slug='test-exception',
            date=date(2025, 1, 1),
        )
        with (
            self.settings(
                SLACK_ENVIRONMENT='production',
                SLACK_BOT_TOKEN='xoxb-test',
                SLACK_ANNOUNCEMENTS_CHANNEL_ID='C123',
            ),
            self.assertLogs('notifications.services.slack_announcements', level='ERROR'),
        ):
            result = post_slack_announcement('article', article)

        self.assertFalse(result)

    @patch('notifications.services.slack_announcements.requests.post')
    def test_returns_false_on_invalid_json(self, mock_post):
        mock_response = MagicMock()
        mock_response.json.side_effect = ValueError('invalid json')
        mock_post.return_value = mock_response

        article = Article.objects.create(
            title='Invalid Json Article',
            slug='test-invalid-json',
            date=date(2025, 1, 1),
        )
        with (
            self.settings(
                SLACK_ENVIRONMENT='production',
                SLACK_BOT_TOKEN='xoxb-test',
                SLACK_ANNOUNCEMENTS_CHANNEL_ID='C123',
            ),
            self.assertLogs(
                'notifications.services.slack_announcements',
                level='ERROR',
            ) as logs,
        ):
            result = post_slack_announcement('article', article)

        self.assertFalse(result)
        self.assertIn(
            'Slack announcement returned invalid JSON for article',
            '\n'.join(logs.output),
        )

    @patch('notifications.services.slack_announcements.requests.post')
    def test_returns_false_on_malformed_json_shape(self, mock_post):
        mock_response = MagicMock()
        mock_response.json.return_value = []
        mock_post.return_value = mock_response

        article = Article.objects.create(
            title='Malformed Json Article',
            slug='test-malformed-json',
            date=date(2025, 1, 1),
        )
        with (
            self.settings(
                SLACK_ENVIRONMENT='production',
                SLACK_BOT_TOKEN='xoxb-test',
                SLACK_ANNOUNCEMENTS_CHANNEL_ID='C123',
            ),
            self.assertLogs(
                'notifications.services.slack_announcements',
                level='WARNING',
            ) as logs,
        ):
            result = post_slack_announcement('article', article)

        self.assertFalse(result)
        self.assertIn(
            'Slack announcement returned malformed JSON for article',
            '\n'.join(logs.output),
        )


@override_settings(SITE_BASE_URL='https://env.example.com')
class SlackAnnouncementSiteUrlOverrideTest(TestCase):
    """Slack announcement URLs respect the Studio override (issue #435)."""

    def setUp(self):
        clear_config_cache()

    def tearDown(self):
        clear_config_cache()

    def test_announcement_blocks_use_db_override_for_content_url(self):
        IntegrationSetting.objects.create(
            key='SITE_BASE_URL',
            value='https://override.example.com',
            group='site',
        )
        clear_config_cache()
        article = Article.objects.create(
            title='Override Slack', slug='override-slack',
            date=date(2025, 1, 1),
        )
        _, blocks = _build_slack_blocks('article', article)
        button = blocks[1]['elements'][0]
        self.assertEqual(
            button['url'],
            f'https://override.example.com{article.get_absolute_url()}',
        )
        self.assertNotIn(
            'env.example.com', blocks[0]['text']['text'],
        )

    def test_announcement_blocks_fall_back_to_settings(self):
        article = Article.objects.create(
            title='Env Slack', slug='env-slack',
            date=date(2025, 1, 1),
        )
        _, blocks = _build_slack_blocks('article', article)
        button = blocks[1]['elements'][0]
        self.assertEqual(
            button['url'],
            f'https://env.example.com{article.get_absolute_url()}',
        )
