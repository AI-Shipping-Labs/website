"""Staff notification when a member completes onboarding (issue #882).

Mirrors the plan-request fan-out (``plans.views.sprints``): when a member
finishes onboarding -- via the #802 form OR the #804 AI chat -- the team
is notified the same three ways:

- Slack: best-effort Block Kit post to the team-requests channel when
  configured. Returns ``False`` (so the caller falls back to email) on a
  network failure, when Slack is disabled, or when no channel/token is set.
- Email fallback: ONLY when the Slack post did not happen, email every
  active staff user.
- In-app: ALWAYS create one ``Notification`` (type ``onboarding_submitted``)
  per active staff user.

The single entry point is :func:`notify_staff_onboarding_submitted`, called
from both onboarding submission code paths so there is exactly one
notification site. Every channel is best-effort and wrapped so a failure
NEVER breaks the member's submission (the member's action is the source of
truth, mirroring the plan-request / payment precedent).

The in-app notification's ``url`` deep-links to the member's CRM record at
``/studio/crm/<id>/`` when a :class:`~crm.models.CRMRecord` exists, else to
the Django admin user-change page. A CRMRecord is NOT auto-created here --
tracking is an explicit staff action (issue #560).
"""

import logging

from django.core.mail import send_mail
from django.urls import reverse

from content.access import LEVEL_TO_TIER_NAME, get_user_level
from crm.models import CRMRecord
from integrations.config import get_config, is_enabled, site_base_url
from notifications.models import Notification

# Reuse the exact active-staff query the plan-request flow uses so the two
# fan-outs can never drift (issue #882: "do not hardcode emails").
from plans.views.sprints import _member_admin_url, _member_display_name, _staff_users

logger = logging.getLogger(__name__)


def _member_crm_path(member):
    """Studio CRM detail path for ``member`` when tracked, else ``None``."""
    record = CRMRecord.objects.filter(user=member).first()
    if record is None:
        return None
    return reverse('studio_crm_detail', kwargs={'crm_id': record.pk})


def _member_target_url(member):
    """Absolute URL the staff notification should open for ``member``.

    The member's CRM record when one exists (``/studio/crm/<id>/``),
    otherwise the Django admin user-change page (mirrors the plan-request
    fallback). Never auto-creates a CRMRecord.
    """
    crm_path = _member_crm_path(member)
    if crm_path is not None:
        return f'{site_base_url()}{crm_path}'
    return _member_admin_url(member)


def _member_tier_name(member):
    """Best-effort public tier name for the notification body."""
    try:
        level = get_user_level(member)
    except Exception:
        return ''
    return LEVEL_TO_TIER_NAME.get(level, '')


def _post_onboarding_to_slack(*, member, target_url):
    """Best-effort Block Kit post to the team-requests channel.

    Returns ``True`` when posted, ``False`` (so the caller emails instead)
    when Slack is disabled, no channel/token is configured, or the post
    fails.
    """
    from community.slack_config import get_slack_team_requests_channel_id

    if not is_enabled('SLACK_ENABLED'):
        return False
    channel_id = get_slack_team_requests_channel_id()
    if not channel_id:
        return False
    bot_token = get_config('SLACK_BOT_TOKEN')
    if not bot_token:
        return False

    import requests  # noqa: PLC0415 -- network dep, kept off module top.

    member_name = _member_display_name(member)
    text_fallback = f'{member_name} just completed onboarding'
    blocks = [
        {
            'type': 'section',
            'text': {
                'type': 'mrkdwn',
                'text': (
                    f'<{target_url}|{member_name}> (`{member.email}`) '
                    f'just completed onboarding.'
                ),
            },
        },
        {
            'type': 'actions',
            'elements': [
                {
                    'type': 'button',
                    'text': {
                        'type': 'plain_text',
                        'text': 'Open member in CRM',
                    },
                    'url': target_url,
                    'action_id': 'open_member_crm',
                },
            ],
        },
    ]

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
                'Onboarding-submitted Slack post failed: %s',
                data.get('error'),
            )
            return False
        return True
    except Exception:
        logger.exception('Failed to post onboarding notification to Slack')
        return False


def _email_staff_about_onboarding(*, member, target_url):
    """Email every active staff user that a member completed onboarding."""
    recipients = list(_staff_users().values_list('email', flat=True))
    if not recipients:
        return 0
    member_name = _member_display_name(member)
    subject = f'Onboarding completed: {member.email}'
    body = (
        f'{member_name} ({member.email}) just completed onboarding.\n\n'
        f'Open member: {target_url}\n'
    )
    from_email = get_config('SES_FROM_EMAIL', 'community@aishippinglabs.com')
    send_mail(
        subject=subject,
        message=body,
        from_email=from_email,
        recipient_list=recipients,
        fail_silently=True,
    )
    return len(recipients)


def _create_staff_onboarding_notifications(*, member, target_url):
    """Create one ``onboarding_submitted`` Notification per active staff user."""
    member_name = _member_display_name(member)
    title = f'Onboarding completed by {member_name}'
    tier_name = _member_tier_name(member)
    body = f'Tier: {tier_name}' if tier_name else ''
    notifications = [
        Notification(
            user=staff,
            title=title,
            body=body,
            url=target_url,
            notification_type='onboarding_submitted',
        )
        for staff in _staff_users()
    ]
    if notifications:
        Notification.objects.bulk_create(notifications)
    return len(notifications)


def notify_staff_onboarding_submitted(member):
    """Notify active staff that ``member`` just completed onboarding.

    The single notification site for both onboarding submission paths
    (#802 form and #804 AI chat). Mirrors the plan-request fan-out: a
    best-effort Slack post OR (when Slack did not post) a staff email,
    and ALWAYS one in-app ``Notification`` per active staff user.

    Best-effort throughout: any Slack/email/notification failure is logged
    and swallowed so it can never break the member's submission.
    """
    try:
        target_url = _member_target_url(member)
        posted_to_slack = _post_onboarding_to_slack(
            member=member, target_url=target_url,
        )
        if not posted_to_slack:
            _email_staff_about_onboarding(
                member=member, target_url=target_url,
            )
        _create_staff_onboarding_notifications(
            member=member, target_url=target_url,
        )
    except Exception:
        # The member's submission is the source of truth; never surface a
        # notification failure to the member.
        logger.exception(
            'Failed to notify staff about onboarding submission for %s',
            getattr(member, 'email', member),
        )
