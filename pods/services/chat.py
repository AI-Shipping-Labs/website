"""Pod chat rules shared by the member page, Studio and the staff API (issue #1920).

A small plain-text chat for pod members. Staff read it and moderate it by
hiding messages (soft, reversible), the same pattern as ``comments`` (#1894).
No live updates and no per-message notifications: the only unread signal is
the ``N new messages`` line on the member's own Pods tab rows, driven by
``PodMembership.chat_read_at``.

``PODS_CHAT_ENABLED`` switches the member side off (no chat section, no
unread line, posting 404s); Studio and the staff API keep working.
"""

from datetime import timedelta
from types import SimpleNamespace

from django.db.models import Count, IntegerField, OuterRef, Subquery
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.utils.html import urlize
from django.utils.safestring import mark_safe

from accounts.services.timezones import format_user_datetime
from pods.models import POD_MESSAGE_MAX_LENGTH, PodMembership, PodMessage
from pods.services import config as pods_config
from pods.services.people import display_name

MEMBER_VISIBLE_LIMIT = 50
STUDIO_VISIBLE_LIMIT = 200
RATE_WINDOW = timedelta(minutes=60)
MEMBER_TIME_FORMAT = '%b %d, %H:%M'

MSG_NOT_A_MEMBER = 'Only pod members can post in this chat.'
MSG_EMPTY = 'Write a message first.'
MSG_TOO_LONG = f'Messages can be up to {POD_MESSAGE_MAX_LENGTH} characters.'
MSG_RATE_LIMITED = 'You are sending messages quickly. Try again in a few minutes.'
MSG_DISABLED = 'Pod chat is switched off.'

CODE_NOT_A_MEMBER = 'not_a_member'
CODE_EMPTY = 'empty_message'
CODE_TOO_LONG = 'message_too_long'
CODE_RATE_LIMITED = 'rate_limited'
CODE_DISABLED = 'chat_disabled'

# Same inline-link styling as the other in-text links on the pod page
# (``pod-slack-open``, ``pod-request-browse``).
BODY_LINK_CLASS = (
    'font-medium text-accent underline underline-offset-4 hover:text-accent/80 '
    'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent '
    'focus-visible:ring-offset-2 focus-visible:ring-offset-background'
)

# Validation failures keep the draft; the other codes are refusals.
DRAFT_CODES = (CODE_EMPTY, CODE_TOO_LONG, CODE_RATE_LIMITED)


class ChatError(Exception):
    """A chat rule violation with a member-facing message and a stable code."""

    def __init__(self, message, code):
        super().__init__(message)
        self.message = message
        self.code = code


def chat_enabled():
    return pods_config.chat_enabled()


def _membership(pod, user):
    if not getattr(user, 'is_authenticated', False):
        return None
    return PodMembership.objects.filter(pod=pod, user=user).first()


def visible_messages(pod):
    return PodMessage.objects.filter(pod=pod, hidden_at__isnull=True)


def clean_body(raw):
    """Return the stripped body or raise ``ChatError`` (empty, too long)."""
    body = (raw or '').strip()
    if not body:
        raise ChatError(MSG_EMPTY, CODE_EMPTY)
    if len(body) > POD_MESSAGE_MAX_LENGTH:
        raise ChatError(MSG_TOO_LONG, CODE_TOO_LONG)
    return body


def recent_message_count(author, now=None):
    """The author's messages across every pod in the last 60 minutes."""
    now = now or timezone.now()
    return PodMessage.objects.filter(author=author, created_at__gt=now - RATE_WINDOW).count()


def post_message(pod, author, raw_body, *, now=None):
    """Create a message by a current pod member.

    Order: kill switch, membership, body, hourly limit. Posting also marks
    the chat read for the author. Sends no notification of any kind.
    """
    now = now or timezone.now()
    if not chat_enabled():
        raise ChatError(MSG_DISABLED, CODE_DISABLED)
    membership = _membership(pod, author)
    if membership is None:
        raise ChatError(MSG_NOT_A_MEMBER, CODE_NOT_A_MEMBER)
    body = clean_body(raw_body)
    if recent_message_count(author, now) >= pods_config.chat_messages_per_hour():
        raise ChatError(MSG_RATE_LIMITED, CODE_RATE_LIMITED)
    message = PodMessage.objects.create(pod=pod, author=author, body=body)
    PodMembership.objects.filter(pk=membership.pk).update(chat_read_at=timezone.now())
    return message


def mark_read(pod, user, *, now=None):
    """Set ``chat_read_at`` for ``user``'s membership; no-op for non-members."""
    if not getattr(user, 'is_authenticated', False):
        return 0
    return PodMembership.objects.filter(pod=pod, user=user).update(chat_read_at=now or timezone.now())


def hide_message(message, actor, *, now=None):
    """Hide a message (staff). Idempotent: keeps the first hide's who and when."""
    if message.hidden_at is None:
        message.hidden_at = now or timezone.now()
        message.hidden_by = actor
        message.save(update_fields=['hidden_at', 'hidden_by'])
    return message


def restore_message(message):
    """Restore a hidden message (staff). Idempotent."""
    if message.hidden_at is not None or message.hidden_by_id is not None:
        message.hidden_at = None
        message.hidden_by = None
        message.save(update_fields=['hidden_at', 'hidden_by'])
    return message


def render_body(body):
    """Escaped plain text with URLs turned into ``rel="nofollow"`` links.

    No markdown. ``urlize`` escapes everything that is not a link, so the
    only ``<a `` sequences in its output are the links it built; the class
    is added to those.
    """
    html = urlize(body, nofollow=True, autoescape=True)
    return mark_safe(html.replace('<a href=', f'<a class="{BODY_LINK_CLASS}" href='))  # noqa: S308


def unread_counts(pod_ids, viewer):
    """``{pod_id: count}`` of unread messages for the viewer's own pods.

    Counts visible messages by other people created after the viewer's
    ``chat_read_at`` (or ``joined_at`` when never read). One query for any
    number of pods. Pods the viewer is not a member of are absent.
    """
    pod_ids = list(pod_ids)
    if not pod_ids or not getattr(viewer, 'is_authenticated', False):
        return {}
    unread = (
        PodMessage.objects.filter(
            pod_id=OuterRef('pod_id'),
            hidden_at__isnull=True,
            created_at__gt=Coalesce(OuterRef('chat_read_at'), OuterRef('joined_at')),
        )
        .exclude(author_id=viewer.pk)
        .order_by()
        .values('pod_id')
        .annotate(total=Count('pk'))
        .values('total')
    )
    rows = (
        PodMembership.objects.filter(user_id=viewer.pk, pod_id__in=pod_ids)
        .annotate(unread=Coalesce(Subquery(unread, output_field=IntegerField()), 0))
        .values_list('pod_id', 'unread')
    )
    return {pod_id: count for pod_id, count in rows if count}


def unread_label(count):
    if not count:
        return ''
    return '1 new message' if count == 1 else f'{count} new messages'


def member_chat(pod, viewer, *, is_member, viewer_zone):
    """Context for the member pod page Chat section.

    Only for pod members and staff; the caller checks the kill switch.
    Shows the latest 50 visible messages, oldest first.
    """
    latest = list(
        visible_messages(pod).select_related('author').order_by('-created_at', '-pk')[:MEMBER_VISIBLE_LIMIT + 1]
    )
    truncated = len(latest) > MEMBER_VISIBLE_LIMIT
    latest = list(reversed(latest[:MEMBER_VISIBLE_LIMIT]))
    zone_viewer = SimpleNamespace(preferred_timezone=viewer_zone or 'UTC')
    rows = [{
        'id': message.pk,
        'author_name': display_name(message.author),
        'is_viewer': message.author_id == viewer.pk,
        'time_label': format_user_datetime(message.created_at, zone_viewer, fmt=MEMBER_TIME_FORMAT),
        'time_iso': message.created_at.isoformat(),
        'body_html': render_body(message.body),
    } for message in latest]
    return {
        'messages': rows,
        'truncated': truncated,
        'limit': MEMBER_VISIBLE_LIMIT,
        'can_post': is_member,
        'slack_url': pod.slack_channel_url,
    }


def studio_chat(pod):
    """Context for the Studio pod Chat section: latest 200, hidden included."""
    total = PodMessage.objects.filter(pod=pod).count()
    hidden = PodMessage.objects.filter(pod=pod, hidden_at__isnull=False).count()
    latest = list(
        PodMessage.objects.filter(pod=pod)
        .select_related('author', 'hidden_by')
        .order_by('-created_at', '-pk')[:STUDIO_VISIBLE_LIMIT]
    )
    latest.reverse()
    return {
        'chat_total': total,
        'chat_hidden': hidden,
        'chat_truncated': total > STUDIO_VISIBLE_LIMIT,
        'chat_limit': STUDIO_VISIBLE_LIMIT,
        'chat_rows': [{
            'message': message,
            'author_name': display_name(message.author),
            'hidden_by_name': (
                (display_name(message.hidden_by) if message.hidden_by.first_name else message.hidden_by.email)
                if message.hidden_by_id else ''
            ),
        } for message in latest],
    }
