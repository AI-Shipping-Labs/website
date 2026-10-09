"""Shared staff-channel Slack posting (issues #1926, #1927).

One ``chat.postMessage`` call used by every staff Slack alert: the member
comment alert (#1926) and the daily stale pod request alert (#1927). The
helpers here never raise and never log: the post returns a
``SlackPostResult`` and each caller logs failures in its own wording.

Gates (``SLACK_ENABLED``, ``SLACK_BOT_TOKEN``, per-alert kill switches and
the channel ID) stay with each caller.
"""

from dataclasses import dataclass

import requests

from integrations.config import get_config

SLACK_POST_MESSAGE_URL = 'https://slack.com/api/chat.postMessage'
# (connect, read): keeps the whole call bounded near five seconds.
SLACK_TIMEOUT_SECONDS = (3.05, 5)


def staff_alert_channel_id():
    """The staff alert channel, falling back to the signup feed.

    ``STAFF_COMMENT_NOTIFY_CHANNEL_ID`` first, then
    ``STAFF_SIGNUP_NOTIFY_CHANNEL_ID``; ``''`` when both are blank.
    """
    channel = str(get_config('STAFF_COMMENT_NOTIFY_CHANNEL_ID', '') or '').strip()
    if channel:
        return channel
    return str(get_config('STAFF_SIGNUP_NOTIFY_CHANNEL_ID', '') or '').strip()


def escape_slack_text(value):
    """Escape the three control characters Slack mrkdwn interprets.

    Escaping ``&``, ``<`` and ``>`` stops user text such as ``<!channel>``
    or ``<https://x|y>`` from becoming a mention or an injected link.
    """
    return (
        str(value or '')
        .replace('&', '&amp;')
        .replace('<', '&lt;')
        .replace('>', '&gt;')
    )


def mrkdwn(text):
    """A ``verbatim`` mrkdwn text object.

    ``verbatim: true`` stops Slack from auto-parsing plain ``@channel``,
    ``@here``, ``@everyone``, channel names, and bare URLs in member text;
    the explicit ``<url|label>`` links built by callers still render.
    """
    return {'type': 'mrkdwn', 'text': text, 'verbatim': True}


def message_payload(text, blocks):
    """``chat.postMessage`` payload minus ``channel``, with link parsing off."""
    return {
        'text': text,
        'blocks': blocks,
        'unfurl_links': False,
        'unfurl_media': False,
        'link_names': False,
        'parse': 'none',
    }


@dataclass(frozen=True)
class SlackPostResult:
    """Outcome of one ``chat.postMessage`` call.

    ``ok`` is true only when Slack accepted the post. On failure either
    ``exception`` (transport error or non-JSON body) or ``error`` (Slack's
    ``error`` field, ``'unknown'`` or ``'non-dict'``) is set. Callers own
    their log wording, so they log from this result.
    """

    ok: bool
    error: str = ''
    exception: BaseException | None = None


def post_staff_slack_message(channel_id, message):
    """POST ``message`` to ``channel_id``; never raises."""
    try:
        response = requests.post(
            SLACK_POST_MESSAGE_URL,
            json={'channel': channel_id, **message},
            headers={
                'Authorization': f'Bearer {get_config("SLACK_BOT_TOKEN")}',
                'Content-Type': 'application/json; charset=utf-8',
            },
            timeout=SLACK_TIMEOUT_SECONDS,
        )
        data = response.json()
    except (requests.exceptions.RequestException, ValueError) as exc:
        return SlackPostResult(ok=False, exception=exc)

    if not isinstance(data, dict) or not data.get('ok'):
        error = data.get('error', 'unknown') if isinstance(data, dict) else 'non-dict'
        return SlackPostResult(ok=False, error=error)
    return SlackPostResult(ok=True)
