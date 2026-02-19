"""Tests for Slack announcement posting."""

from datetime import date
from unittest.mock import patch, MagicMock

from django.test import TestCase, override_settings

from content.models import Article
from notifications.services.slack_announcements import (
    post_slack_announcement,
    _build_slack_blocks,
)


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
        article = Article.objects.create(
            title='Test', slug='test-truncate',
            date=date(2025, 1, 1),
            description='x' * 300,
        )
        _, blocks = _build_slack_blocks('article', article)
        text = blocks[0]['text']['text']
        self.assertIn('...', text)


class PostSlackAnnouncementTest(TestCase):
    """Tests for the post_slack_announcement function."""

    def test_skips_when_no_bot_token(self):
        article = Article.objects.create(
            title='Test', slug='test-no-token',
            date=date(2025, 1, 1),
        )
        with self.settings(SLACK_BOT_TOKEN='', SLACK_ANNOUNCEMENTS_CHANNEL_ID='C123'):
            result = post_slack_announcement('article', article)
            self.assertFalse(result)

    def test_skips_when_no_channel_id(self):
        article = Article.objects.create(
            title='Test', slug='test-no-channel',
            date=date(2025, 1, 1),
        )
        with self.settings(SLACK_BOT_TOKEN='xoxb-test', SLACK_ANNOUNCEMENTS_CHANNEL_ID=''):
            result = post_slack_announcement('article', article)
            self.assertFalse(result)

    @patch('notifications.services.slack_announcements.requests.post')
    def test_posts_to_slack_api(self, mock_post):
        mock_response = MagicMock()
        mock_response.json.return_value = {'ok': True}
        mock_post.return_value = mock_response

        article = Article.objects.create(
            title='Posted Article', slug='test-post',
            date=date(2025, 1, 1),
        )
        with self.settings(SLACK_BOT_TOKEN='xoxb-test', SLACK_ANNOUNCEMENTS_CHANNEL_ID='C123'):
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
        with self.settings(SLACK_BOT_TOKEN='xoxb-test', SLACK_ANNOUNCEMENTS_CHANNEL_ID='C123'):
            result = post_slack_announcement('article', article)

        self.assertFalse(result)

    @patch('notifications.services.slack_announcements.requests.post')
    def test_returns_false_on_exception(self, mock_post):
        mock_post.side_effect = Exception('Network error')
        article = Article.objects.create(
            title='Exception Article', slug='test-exception',
            date=date(2025, 1, 1),
        )
        with self.settings(SLACK_BOT_TOKEN='xoxb-test', SLACK_ANNOUNCEMENTS_CHANNEL_ID='C123'):
            result = post_slack_announcement('article', article)

        self.assertFalse(result)
