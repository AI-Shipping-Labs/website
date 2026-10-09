"""Staff Slack alert for member comments on staff content (issue #1926).

When a non-staff member comments on or replies in a course lesson, a
homework step thread, or a workshop tutorial page, one Block Kit message
is posted to a private staff Slack channel so the whole team sees the
question. This is the team feed; the personal owner bell and email
(issues #1341, #1361, #1895) are separate and unchanged.

Settings (``slack`` group, editable in Studio):

- ``STAFF_COMMENT_NOTIFY_ENABLED`` -- default-on kill switch.
- ``STAFF_COMMENT_NOTIFY_CHANNEL_ID`` -- target channel; blank falls back
  to ``STAFF_SIGNUP_NOTIFY_CHANNEL_ID``.

The post also needs ``SLACK_ENABLED`` and ``SLACK_BOT_TOKEN``. Every
failure is logged with the comment id and swallowed: the comment, the
owner bell, and the HTTP response to the commenter never depend on Slack.
"""

import logging

from accounts.utils.display import display_name
from community.slack_config import slack_api_enabled
from content.models import Unit, WorkshopPage
from integrations.config import get_config, site_base_url
from notifications.services.notification_service import (
    HomeworkStepCommentContext,
    _comment_email_parent_title,
    _resolve_commented_content,
    content_comment_urls,
)
from notifications.services.staff_slack import (
    escape_slack_text,
    message_payload,
    mrkdwn,
    post_staff_slack_message,
    staff_alert_channel_id,
)

logger = logging.getLogger(__name__)

PREVIEW_MAX_CHARS = 300


def staff_comment_alerts_enabled():
    """Default-on read of ``STAFF_COMMENT_NOTIFY_ENABLED``.

    ``is_enabled`` hardcodes a ``'false'`` fallback, so the default-on
    contract reads the raw value with a ``'true'`` default instead.
    """
    raw = get_config('STAFF_COMMENT_NOTIFY_ENABLED', 'true')
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in ('true', '1', 'yes')


def staff_comment_channel_id():
    """Return the comment-alert channel, falling back to the signup feed."""
    return staff_alert_channel_id()


def comment_preview(body):
    """Collapse whitespace and cut the body to 300 characters plus ``…``.

    Truncation happens on the raw text so the limit counts what the
    member wrote; the caller escapes the result.
    """
    text = ' '.join((body or '').split())
    if len(text) > PREVIEW_MAX_CHARS:
        text = text[:PREVIEW_MAX_CHARS] + '…'
    return text


def _surface_label(content):
    if isinstance(content, HomeworkStepCommentContext):
        return 'Homework step'
    if isinstance(content, Unit):
        return 'Course lesson'
    if isinstance(content, WorkshopPage):
        return 'Workshop page'
    return None


def build_staff_comment_message(comment, content, content_title):
    """Return the ``chat.postMessage`` payload minus ``channel``.

    Returns ``None`` when the content is not an alerting surface or has no
    thread URL.
    """
    surface = _surface_label(content)
    parent_title = _comment_email_parent_title(content)
    urls = content_comment_urls(content)
    if surface is None or parent_title is None or not urls:
        return None

    base = site_base_url().rstrip('/')
    thread_url = f'{base}{urls[0]}'
    author = comment.user
    studio_user_url = f'{base}/studio/users/{author.pk}/'

    is_reply = comment.parent_id is not None
    if is_reply:
        parent_author = escape_slack_text(display_name(comment.parent.user))
        action = f'replied to {parent_author} on'
        fallback = f'New reply on {content_title}'
    else:
        action = 'commented on'
        fallback = f'New comment on {content_title}'

    headline = (
        f'<{studio_user_url}|{escape_slack_text(display_name(author))}> '
        f'({escape_slack_text(author.email)}) {action} '
        f'<{thread_url}|{escape_slack_text(parent_title)}: '
        f'{escape_slack_text(content_title)}>'
    )
    preview = escape_slack_text(comment_preview(comment.body))

    blocks = [
        {'type': 'section', 'text': mrkdwn(headline)},
        {
            'type': 'context',
            'elements': [mrkdwn(surface)],
        },
    ]
    if preview:
        blocks.append(
            {'type': 'section', 'text': mrkdwn(f'>{preview}')},
        )
    blocks.append({
        'type': 'actions',
        'elements': [{
            'type': 'button',
            'text': {'type': 'plain_text', 'text': 'Open discussion'},
            'url': thread_url,
        }],
    })
    return message_payload(escape_slack_text(fallback), blocks)


def notify_staff_of_comment(comment):
    """Post one staff-channel alert for ``comment``; never raises.

    Returns ``True`` only when Slack accepted the post.
    """
    try:
        return _notify_staff_of_comment(comment)
    except Exception:
        logger.exception(
            'Staff comment Slack alert failed for comment %s', comment.pk,
        )
        return False


def _notify_staff_of_comment(comment):
    if comment.user.is_staff:
        return False
    if not staff_comment_alerts_enabled():
        logger.debug(
            'Staff comment alert skipped for comment %s: '
            'STAFF_COMMENT_NOTIFY_ENABLED is off', comment.pk,
        )
        return False
    if not slack_api_enabled():
        logger.debug(
            'Staff comment alert skipped for comment %s: Slack API disabled',
            comment.pk,
        )
        return False
    channel_id = staff_comment_channel_id()
    if not channel_id:
        logger.info(
            'Staff comment alert skipped for comment %s: no '
            'STAFF_COMMENT_NOTIFY_CHANNEL_ID or STAFF_SIGNUP_NOTIFY_CHANNEL_ID',
            comment.pk,
        )
        return False

    content, content_title = _resolve_commented_content(comment.content_id)
    if content is None:
        return False
    message = build_staff_comment_message(comment, content, content_title)
    if message is None:
        return False

    result = post_staff_slack_message(channel_id, message)
    if result.exception is not None:
        logger.error(
            'Staff comment Slack alert failed for comment %s (channel=%s)',
            comment.pk, channel_id, exc_info=result.exception,
        )
        return False
    if not result.ok:
        logger.error(
            'Staff comment Slack alert rejected for comment %s (channel=%s): %s',
            comment.pk, channel_id, result.error,
        )
        return False

    logger.info(
        'Posted staff comment Slack alert for comment %s to channel=%s',
        comment.pk, channel_id,
    )
    return True
