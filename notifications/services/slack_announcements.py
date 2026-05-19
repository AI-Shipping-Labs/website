"""
Slack channel announcements for new content.

Posts formatted Block Kit messages to the #announcements channel
when new content is published.
"""

import logging
from zoneinfo import ZoneInfo

import requests

from community.slack_config import get_slack_announcements_channel_id
from integrations.config import get_config, is_enabled, site_base_url

logger = logging.getLogger(__name__)


# Issue #691: Fixed multi-zone strip for Slack event announcements (west to
# east). ``Europe/Berlin`` is the representative city for CET — DST flips it
# to CEST automatically in summer; the visible token in the message stays
# ``CET`` by design (no per-recipient context in channel broadcasts).
_SLACK_EVENT_TZ_STRIP = (
    ('NYC', 'America/New_York'),
    ('UTC', 'UTC'),
    ('CET', 'Europe/Berlin'),
    ('IST', 'Asia/Kolkata'),
)


def _format_event_time_strip(start_datetime):
    """Return a fixed multi-zone time strip for Slack event announcements.

    Format: ``"Thu, May 21 · 10:00 NYC · 14:00 UTC · 16:00 CET · 19:30 IST"``.

    The date label is anchored to NYC (the westernmost zone in the strip);
    readers in later zones may see "their" clock time pointing to the next
    day, which is an unavoidable property of a single-date label spanning
    many timezones. The strip omits the year (Slack reminders fire within
    the 24h window before an event, so the year is unambiguous).

    Returns ``None`` when ``start_datetime`` is falsy.
    """
    if not start_datetime:
        return None
    if start_datetime.tzinfo is None:
        start_datetime = start_datetime.replace(tzinfo=ZoneInfo('UTC'))
    date_in_nyc = start_datetime.astimezone(ZoneInfo('America/New_York'))
    date_part = date_in_nyc.strftime('%a, %b %d')
    times = [
        f'{start_datetime.astimezone(ZoneInfo(iana)).strftime("%H:%M")} {label}'
        for label, iana in _SLACK_EVENT_TZ_STRIP
    ]
    return f'{date_part} · ' + ' · '.join(times)


def _get_announcements_channel_id():
    """Return the Slack announcements channel ID from settings."""
    return get_slack_announcements_channel_id()


def _build_slack_blocks(content_type, content):
    """Build Slack Block Kit blocks for a content announcement.

    Args:
        content_type: One of 'article', 'course', 'event', 'recording',
                     'download', 'poll'.
        content: The content model instance.

    Returns:
        Tuple of (text_fallback, blocks_list).
    """
    site_url = site_base_url()
    url = content.get_absolute_url() if hasattr(content, 'get_absolute_url') else ''
    full_url = f'{site_url}{url}'

    type_labels = {
        'article': 'New article',
        'course': 'New course',
        'event': 'Upcoming event',
        'recording': 'New recording',
        'download': 'New download',
        'poll': 'New poll',
        'workshop': 'New workshop',
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
    if content_type == 'event':
        tz_strip = _format_event_time_strip(
            getattr(content, 'start_datetime', None)
        )
        if tz_strip:
            mrkdwn_text += f'\n🗓 {tz_strip}'
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
    if not is_enabled('SLACK_ENABLED'):
        logger.debug('Skipping Slack announcement: SLACK_ENABLED is not true')
        return False

    bot_token = get_config('SLACK_BOT_TOKEN')
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
    except requests.exceptions.RequestException:
        logger.exception('Failed to post Slack announcement for %s', content_type)
        return False

    try:
        data = response.json()
    except ValueError:
        logger.exception(
            'Slack announcement returned invalid JSON for %s',
            content_type,
        )
        return False

    if not isinstance(data, dict):
        logger.warning(
            'Slack announcement returned malformed JSON for %s',
            content_type,
        )
        return False

    if not data.get('ok'):
        logger.warning(
            'Slack announcement failed for %s: %s',
            content_type,
            data.get('error', 'unknown'),
        )
        return False

    logger.info('Posted Slack announcement for %s: %s', content_type, content.title)
    return True
