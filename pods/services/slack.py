"""Slack hand-off helpers for pods. No Slack API calls (issue #1918)."""

from urllib.parse import urlparse

from django.utils.text import slugify

from community.services.slack_links import build_slack_profile_url
from integrations.config import get_config

INVALID_LINK_MESSAGE = (
    'Paste a Slack channel link, for example '
    'https://yourworkspace.slack.com/archives/C0123456789.'
)


def suggested_channel_name(pod):
    """``pod-<id>-<slugified name>``, at most 80 characters."""
    slug = slugify(pod.name) or 'pod'
    return f'pod-{pod.pk}-{slug}'[:80].rstrip('-')


def clean_channel_url(raw):
    """Return the cleaned URL ('' clears it); raise ``ValueError`` if invalid."""
    value = (raw or '').strip()
    if not value:
        return ''
    if len(value) > 300:
        raise ValueError(INVALID_LINK_MESSAGE)
    parsed = urlparse(value)
    host = (parsed.hostname or '').lower()
    if parsed.scheme != 'https' or not host:
        raise ValueError(INVALID_LINK_MESSAGE)
    if host != 'app.slack.com' and not host.endswith('.slack.com'):
        raise ValueError(INVALID_LINK_MESSAGE)
    return value


def profile_url(user):
    """``Message on Slack`` target for a member, or ''."""
    if not getattr(user, 'slack_member', False) or not getattr(user, 'slack_user_id', ''):
        return ''
    return build_slack_profile_url(user.slack_user_id, get_config('SLACK_TEAM_ID', ''))
