"""
Slack channel announcements for new content.

Posts formatted Block Kit messages to the #announcements channel
when new content is published.
"""

import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


def _get_announcements_channel_id():
    """Return the Slack announcements channel ID from settings."""
    return getattr(settings, 'SLACK_ANNOUNCEMENTS_CHANNEL_ID', '')


def _build_slack_blocks(content_type, content):
    """Build Slack Block Kit blocks for a content announcement.

    Args:
        content_type: One of 'article', 'course', 'event', 'recording',
                     'download', 'poll'.
        content: The content model instance.

    Returns:
        Tuple of (text_fallback, blocks_list).
    """
    site_url = getattr(settings, 'SITE_URL', 'https://aishippinglabs.com')
    url = content.get_absolute_url() if hasattr(content, 'get_absolute_url') else ''
    full_url = f'{site_url}{url}'

    type_labels = {
        'article': 'New article',
        'course': 'New course',
        'event': 'Upcoming event',
        'recording': 'New recording',
        'download': 'New download',
        'poll': 'New poll',
    }
    type_label = type_labels.get(content_type, 'New content')
    title = content.title

    # Build description
    description = getattr(content, 'description', '')
    if not description:
        description = getattr(content, 'content_markdown', '')
    description = (description or '')[:200]
    if len(description) == 200:
        description += '...'

    text_fallback = f'{type_label}: {title}'

    # Block Kit formatted message
    mrkdwn_text = f'*{type_label}:* <{full_url}|{title}>'
    if description:
        mrkdwn_text += f'\n\n{description}'

    blocks = [
        {
            'type': 'section',
            'text': {
                'type': 'mrkdwn',
                'text': mrkdwn_text,
            },
        },
        {
            'type': 'actions',
            'elements': [
                {
                    'type': 'button',
                    'text': {
                        'type': 'plain_text',
                        'text': f'View {type_label.split(" ", 1)[-1].title()}',
                    },
                    'url': full_url,
                    'action_id': f'view_{content_type}',
                },
            ],
        },
    ]

    return text_fallback, blocks


def post_slack_announcement(content_type, content):
    """Post a Block Kit formatted announcement to the Slack #announcements channel.

    Args:
        content_type: Content type string.
        content: Content model instance.

    Returns:
        True if posted successfully, False otherwise.
    """
    bot_token = getattr(settings, 'SLACK_BOT_TOKEN', '')
    channel_id = _get_announcements_channel_id()

    if not bot_token or not channel_id:
        logger.info(
            'Skipping Slack announcement for %s: bot_token or channel_id not configured',
            content_type,
        )
        return False

    text_fallback, blocks = _build_slack_blocks(content_type, content)

    try:
        response = requests.post(
            'https://slack.com/api/chat.postMessage',
            json={
                'channel': channel_id,
                'text': text_fallback,
                'blocks': blocks,
            },
            headers={
                'Authorization': f'Bearer {bot_token}',
                'Content-Type': 'application/json; charset=utf-8',
            },
            timeout=10,
        )

        data = response.json()
        if not data.get('ok'):
            logger.warning(
                'Slack announcement failed for %s: %s',
                content_type, data.get('error', 'unknown'),
            )
            return False

        logger.info('Posted Slack announcement for %s: %s', content_type, content.title)
        return True

    except Exception:
        logger.exception('Failed to post Slack announcement for %s', content_type)
        return False
