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
``/studio/crm/<id>/#onboarding``. Onboarding completion creates that
CRMRecord when one does not exist; if CRM create/find fails, the fan-out
falls back to the Studio user-detail page (``/studio/users/<pk>/``).
"""

import logging

from django.core.mail import EmailMultiAlternatives
from django.db import IntegrityError
from django.urls import reverse
from django.utils.html import escape

from content.access import LEVEL_TO_TIER_NAME, get_user_level
from crm.models import CRMRecord
from integrations.config import get_config, is_enabled, site_base_url
from notifications.models import Notification

# Reuse the exact active-staff query the plan-request flow uses so the two
# fan-outs can never drift (issue #882: "do not hardcode emails").
from plans.views.sprints import _member_display_name, _staff_users

logger = logging.getLogger(__name__)


def ensure_onboarding_crm_record(member):
    """Return ``member``'s CRM record, creating a minimal one when absent.

    Onboarding auto-create is intentionally minimal: the member answers
    remain canonical on ``questionnaires.Response`` and curated CRM fields
    stay untouched. Existing archived records are reused as-is.
    """
    try:
        record, _created = CRMRecord.objects.get_or_create(
            user=member,
            defaults={'status': 'active'},
        )
        return record
    except IntegrityError:
        # A concurrent retry may have inserted the one allowed row between
        # the get and create. Re-read the invariant row instead of surfacing
        # the race to the member-facing submission.
        return CRMRecord.objects.get(user=member)


def _crm_onboarding_path(record):
    """Studio CRM onboarding-section path for ``record``."""
    path = reverse('studio_crm_detail', kwargs={'crm_id': record.pk})
    return f'{path}#onboarding'


def _member_studio_user_url(member):
    """Absolute Studio user-detail URL (``/studio/users/<pk>/``) for ``member``."""
    path = reverse('studio_user_detail', kwargs={'user_id': member.pk})
    return f'{site_base_url()}{path}'


def _member_target_url(member):
    """Absolute URL the staff notification should open for ``member``.

    The member's CRM record when CRM create/find succeeds
    (``/studio/crm/<id>/#onboarding``), otherwise the Studio user-detail
    page (``/studio/users/<pk>/``). Never routes staff to the Django admin.
    """
    try:
        record = ensure_onboarding_crm_record(member)
    except Exception:
        logger.exception(
            'Failed to auto-create/find CRM record for onboarding submission '
            'by %s',
            getattr(member, 'email', member),
        )
        return _member_studio_user_url(member)
    return f'{site_base_url()}{_crm_onboarding_path(record)}'


def _member_tier_name(member):
    """Best-effort public tier name for the notification body."""
    try:
        level = get_user_level(member)
    except Exception:
        return ''
    return LEVEL_TO_TIER_NAME.get(level, '')


def _event_phrase(updated):
    """The staff-facing verb phrase: first completion vs a later edit."""
    if updated:
        return 'updated their onboarding answers'
    return 'just completed onboarding'


def _post_onboarding_to_slack(*, member, target_url, updated=False):
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
        logger.warning(
            'Onboarding-submitted Slack post skipped: no team-requests '
            'channel configured for the active Slack environment; '
            'falling back to staff email.',
        )
        return False
    bot_token = get_config('SLACK_BOT_TOKEN')
    if not bot_token:
        logger.warning(
            'Onboarding-submitted Slack post skipped: SLACK_BOT_TOKEN is '
            'not configured; falling back to staff email.',
        )
        return False

    import requests  # noqa: PLC0415 -- network dep, kept off module top.

    member_name = _member_display_name(member)
    phrase = _event_phrase(updated)
    text_fallback = f'{member_name} {phrase}'
    blocks = [
        {
            'type': 'section',
            'text': {
                'type': 'mrkdwn',
                'text': (
                    f'<{target_url}|{member_name}> (`{member.email}`) '
                    f'{phrase}.'
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
                        'text': 'Open onboarding in CRM',
                    },
                    'url': target_url,
                    'action_id': 'open_onboarding_crm',
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


def _onboarding_answers(member):
    """Return ``[(prompt, display)]`` for the member's answered questions."""
    from questionnaires.onboarding import (
        flatten_response_answers,
        get_onboarding_response,
    )

    try:
        response = get_onboarding_response(member)
        if response is None:
            return []
        rows = flatten_response_answers(response)
    except Exception:
        logger.exception('Failed to load onboarding answers for email')
        return []
    return [(r['prompt'], r['display']) for r in rows if r['answered']]


def _attribution_lines(member):
    """Best-effort ``[(label, value)]`` for stored signup attribution."""
    try:
        attribution = member.attribution
    except Exception:
        return []
    source = attribution.first_touch_referrer_source or ''
    if source == 'direct':
        source = ''
    pairs = [
        ('Heard about us via', source),
        ('Referrer host', attribution.first_touch_referrer_host),
        ('UTM source', attribution.first_touch_utm_source),
        ('UTM medium', attribution.first_touch_utm_medium),
        ('UTM campaign', attribution.first_touch_utm_campaign),
        ('UTM content', attribution.first_touch_utm_content),
        ('UTM term', attribution.first_touch_utm_term),
    ]
    return [(label, value) for label, value in pairs if value]


def _member_detail_rows(member, target_url, *, updated=False):
    """Ordered ``[(label, value)]`` member facts; empty values are dropped."""
    from payments.models import Membership

    tier_name = _member_tier_name(member)
    billing = ''
    try:
        membership = Membership.for_user(member)
    except Exception:
        membership = None
    if membership is not None:
        if membership.subscription_id:
            billing = 'Stripe subscription'
            if membership.billing_period_end:
                billing += (
                    f' (renews/ends {membership.billing_period_end:%Y-%m-%d})'
                )
        elif membership.stripe_customer_id:
            billing = 'Stripe customer, no active subscription'
    date_joined = getattr(member, 'date_joined', None)
    rows = [
        ('Name', _member_display_name(member)),
        ('Email', member.email),
        ('Plan', tier_name),
        ('Billing', billing),
        ('Signed up', f'{date_joined:%Y-%m-%d %H:%M} UTC' if date_joined else ''),
        ('Email verified', 'yes' if member.email_verified else 'no'),
        ('Timezone', getattr(member, 'preferred_timezone', '') or ''),
    ]
    rows += _attribution_lines(member)
    rows.append(('Open onboarding in CRM', target_url))
    studio_url = _member_studio_user_url(member)
    if studio_url != target_url:
        rows.append(('Studio user page', studio_url))
    return [(label, value) for label, value in rows if value]


def build_onboarding_email(member, target_url, *, updated=False):
    """Return ``(subject, text_body, html_body)`` for the staff email."""
    verb = 'Onboarding answers updated' if updated else 'Onboarding completed'
    tier_name = _member_tier_name(member)
    subject = f'{verb}: {_member_display_name(member)} <{member.email}>'
    if tier_name:
        subject += f' [{tier_name}]'
    details = _member_detail_rows(member, target_url, updated=updated)
    answers = _onboarding_answers(member)
    intro = (
        f'{_member_display_name(member)} ({member.email}) '
        f'{_event_phrase(updated)}.'
    )

    text = [intro, '', 'Member', '------']
    text += [f'{label}: {value}' for label, value in details]
    if answers:
        text += ['', 'Onboarding answers', '------------------']
        for prompt, display in answers:
            text += [f'{prompt}', f'  {display}', '']
    text_body = '\n'.join(text).rstrip() + '\n'

    def _cell(label, value):
        if value.startswith('http'):
            value_html = f'<a href="{escape(value)}">{escape(value)}</a>'
        else:
            value_html = escape(value)
        return (
            f'<tr><td style="padding:2px 12px 2px 0;color:#555">'
            f'{escape(label)}</td><td>{value_html}</td></tr>'
        )

    html = [
        f'<p>{escape(intro)}</p>',
        '<h3>Member</h3>',
        '<table>' + ''.join(_cell(a, b) for a, b in details) + '</table>',
    ]
    if answers:
        html.append('<h3>Onboarding answers</h3>')
        for prompt, display in answers:
            html.append(
                f'<p style="margin:0 0 10px"><strong>{escape(prompt)}</strong>'
                f'<br>{escape(display).replace(chr(10), "<br>")}</p>'
            )
    html.append(
        f'<p><a href="{escape(target_url)}">Open onboarding in CRM</a></p>'
    )
    return subject, text_body, '\n'.join(html)


def _email_staff_about_onboarding(*, member, target_url, updated=False):
    """Email every active staff user that a member completed onboarding."""
    recipients = list(_staff_users().values_list('email', flat=True))
    if not recipients:
        return 0
    subject, body, html_body = build_onboarding_email(
        member, target_url, updated=updated,
    )
    from_email = get_config('RELAY_TRANSACTIONAL_FROM_EMAIL', 'community@aishippinglabs.com')
    message = EmailMultiAlternatives(
        subject=subject,
        body=body,
        from_email=from_email,
        to=recipients,
    )
    message.attach_alternative(html_body, 'text/html')
    message.send(fail_silently=False)
    return len(recipients)


def _create_staff_onboarding_notifications(*, member, target_url, updated=False):
    """Create one ``onboarding_submitted`` Notification per active staff user."""
    member_name = _member_display_name(member)
    title = (
        f'Onboarding answers updated by {member_name}'
        if updated
        else f'Onboarding completed by {member_name}'
    )
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


def notify_staff_onboarding_submitted(member, *, updated=False):
    """Notify active staff that ``member`` just completed onboarding.

    ``updated=True`` is a member editing an already-submitted onboarding;
    the same channels fire with "updated their onboarding answers" copy so
    staff know the answers behind a plan changed.

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
            member=member, target_url=target_url, updated=updated,
        )
        if not posted_to_slack:
            _email_staff_about_onboarding(
                member=member, target_url=target_url, updated=updated,
            )
        _create_staff_onboarding_notifications(
            member=member, target_url=target_url, updated=updated,
        )
    except Exception:
        # The member's submission is the source of truth; never surface a
        # notification failure to the member.
        logger.exception(
            'Failed to notify staff about onboarding submission for %s',
            getattr(member, 'email', member),
        )
