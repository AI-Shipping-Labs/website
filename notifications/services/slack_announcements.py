"""
Slack channel announcements for new content.

Posts formatted Block Kit messages to the #announcements channel
when new content is published.
"""

import logging

import requests

from community.slack_config import get_slack_announcements_channel_id
from events.services.display_time import format_event_tz_strip
from integrations.config import get_config, is_enabled, site_base_url

logger = logging.getLogger(__name__)


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
        tz_strip = format_event_tz_strip(
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


# Cap the session list so a long series cannot exceed Slack block limits.
SERIES_SESSION_DISPLAY_CAP = 10


def _series_upcoming_sessions(series):
    """Return the series' upcoming, non-draft, non-cancelled sessions.

    Ordered chronologically by ``start_datetime`` so the Slack list and
    the "+ M more" overflow line read in time order.
    """
    return [
        event
        for event in series.events.exclude(
            status__in=('draft', 'cancelled'),
        ).order_by('start_datetime')
        if event.is_upcoming
    ]


def build_series_slack_blocks(series, sessions):
    """Build Slack Block Kit blocks announcing a whole event series.

    Args:
        series: ``EventSeries`` instance.
        sessions: Pre-computed list of upcoming sessions (chronological).

    Returns:
        Tuple of ``(text_fallback, blocks_list)``.
    """
    site_url = site_base_url()
    full_url = f'{site_url}{series.get_absolute_url()}'

    title = series.name
    text_fallback = f'New event series: {title}'

    mrkdwn_text = f'*New event series:* <{full_url}|{title}>'

    description = (getattr(series, 'description', '') or '')[:200]
    if len(description) == 200:
        description += '...'
    if description:
        mrkdwn_text += f'\n\n{description}'

    # Session lines, capped so we never blow the Slack block limit.
    shown = sessions[:SERIES_SESSION_DISPLAY_CAP]
    session_lines = []
    for event in shown:
        tz_strip = format_event_tz_strip(
            getattr(event, 'start_datetime', None)
        )
        if tz_strip:
            session_lines.append(f'• {event.title} — 🗓 {tz_strip}')
        else:
            session_lines.append(f'• {event.title}')

    remaining = len(sessions) - len(shown)
    if remaining > 0:
        session_lines.append(f'… and {remaining} more')

    if session_lines:
        mrkdwn_text += '\n\n*Upcoming sessions:*\n' + '\n'.join(session_lines)

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
                        'text': 'View series',
                    },
                    'url': full_url,
                    'action_id': 'view_event_series',
                },
            ],
        },
    ]

    return text_fallback, blocks


def post_series_slack_announcement(series):
    """Post ONE Slack announcement for a whole event series.

    Lists the series' upcoming sessions (date/time in the fixed timezone
    strip) and links to the public series page. Reuses the same config
    gating, ``chat.postMessage`` transport, JSON/``ok`` validation and
    logging as :func:`post_slack_announcement`.

    Returns ``True`` when posted, ``False`` otherwise — including when
    Slack is disabled/unconfigured or the series has zero upcoming
    sessions (nothing to announce).
    """
    if not is_enabled('SLACK_ENABLED'):
        logger.debug('Skipping series Slack announcement: SLACK_ENABLED is not true')
        return False

    sessions = _series_upcoming_sessions(series)
    if not sessions:
        logger.info(
            'Skipping series Slack announcement for %s: no upcoming sessions',
            series.slug,
        )
        return False

    bot_token = get_config('SLACK_BOT_TOKEN')
    channel_id = _get_announcements_channel_id()

    if not bot_token or not channel_id:
        logger.info(
            'Skipping series Slack announcement for %s: bot_token or channel_id not configured',
            series.slug,
        )
        return False

    text_fallback, blocks = build_series_slack_blocks(series, sessions)

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
        logger.exception('Failed to post series Slack announcement for %s', series.slug)
        return False

    try:
        data = response.json()
    except ValueError:
        logger.exception(
            'Series Slack announcement returned invalid JSON for %s',
            series.slug,
        )
        return False

    if not isinstance(data, dict):
        logger.warning(
            'Series Slack announcement returned malformed JSON for %s',
            series.slug,
        )
        return False

    if not data.get('ok'):
        logger.warning(
            'Series Slack announcement failed for %s: %s',
            series.slug,
            data.get('error', 'unknown'),
        )
        return False

    logger.info('Posted series Slack announcement for %s: %s', series.slug, series.name)
    return True


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
