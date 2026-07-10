"""Sprint cadence notifications and delivery-log helpers."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.db.models import Prefetch
from django.urls import reverse
from django.utils import timezone

from email_app.services.email_service import EmailService
from integrations.config import site_base_url
from notifications.models import Notification
from plans.models import (
    SPRINT_CADENCE_KIND_SLACK_PROGRESS,
    SPRINT_CADENCE_KIND_WEEK_NOTE_PROMPT,
    SPRINT_CADENCE_KIND_WEEK_START,
    SPRINT_CADENCE_STATUS_EMAIL_FAILED,
    SPRINT_CADENCE_STATUS_SENT,
    SPRINT_CADENCE_STATUS_SKIPPED,
    Plan,
    SprintCadenceDeliveryLog,
    Week,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CadenceSummary:
    week_start_created: int = 0
    week_note_prompt_created: int = 0
    emails_sent: int = 0
    emails_failed: int = 0

    def as_dict(self):
        return {
            'week_start_created': self.week_start_created,
            'week_note_prompt_created': self.week_note_prompt_created,
            'emails_sent': self.emails_sent,
            'emails_failed': self.emails_failed,
        }


def _member_plan_path(plan, week=None, *, progress_event=None):
    path = reverse(
        'my_plan_detail',
        kwargs={'sprint_slug': plan.sprint.slug, 'plan_id': plan.pk},
    )
    if progress_event is not None:
        path = f'{path}?progress_event={progress_event.pk}#slack-progress'
    elif week is not None:
        path = f'{path}#week-{week.pk}'
    return path


def _member_plan_url(plan, week=None):
    return f'{site_base_url()}{_member_plan_path(plan, week)}'


def _email_allowed(user):
    if not user.is_active:
        return False
    if not user.email_verified:
        return False
    if user.unsubscribed:
        return False
    return user.email_preferences.get('sprint_cadence_emails', True) is not False


def _ordered_weeks(plan):
    return list(plan.weeks.all())


def _week_offsets(weeks):
    positions = [week.position for week in weeks]
    if len(set(positions)) == len(positions) and all(p >= 0 for p in positions):
        return {week.pk: week.position for week in weeks}
    return {week.pk: index for index, week in enumerate(weeks)}


def _week_start_date(plan, week, offsets):
    offset = offsets.get(week.pk, max(week.week_number - 1, 0))
    return plan.sprint.start_date + timedelta(days=offset * 7)


def _week_end_date(plan, week, offsets):
    start = _week_start_date(plan, week, offsets)
    end = start + timedelta(days=6)
    sprint_last_day = plan.sprint.end_date - timedelta(days=1)
    return min(end, sprint_last_day)


def _week_theme(week):
    return (week.theme or '').strip() or 'your sprint focus'


def _unfinished_checkpoint_count(week):
    return week.checkpoints.filter(done_at__isnull=True).count()


def _previous_week_needs_note(weeks, current_week):
    try:
        index = [week.pk for week in weeks].index(current_week.pk)
    except ValueError:
        return None
    if index <= 0:
        return None
    previous = weeks[index - 1]
    if previous.notes.exists():
        return None
    return previous


def _create_log_once(*, kind, plan, week=None, progress_event=None,
                     source_message_ts=''):
    try:
        with transaction.atomic():
            return SprintCadenceDeliveryLog.objects.create(
                kind=kind,
                plan=plan,
                member=plan.member,
                week=week,
                progress_event=progress_event,
                source_message_ts=source_message_ts or '',
                status=SPRINT_CADENCE_STATUS_SKIPPED,
            )
    except IntegrityError:
        return None


def _finalize_log(log, *, notification, email_log=None, status=None,
                  last_error=''):
    log.notification = notification
    log.email_log = email_log
    log.status = status or SPRINT_CADENCE_STATUS_SENT
    log.last_error = last_error
    log.sent_at = timezone.now()
    log.save(update_fields=[
        'notification', 'email_log', 'status', 'last_error', 'sent_at',
        'updated_at',
    ])


def _send_cadence_email(log, *, template_name, context):
    if not _email_allowed(log.member):
        return None, ''
    try:
        email_log = EmailService().send(log.member, template_name, context)
    except Exception as exc:  # noqa: BLE001 - log and continue per issue.
        logger.warning(
            'Sprint cadence email failed for log %s: %s', log.pk, exc,
            exc_info=True,
        )
        return None, str(exc)
    return email_log, ''


def _deliver_week_start(plan, week, weeks):
    log = _create_log_once(
        kind=SPRINT_CADENCE_KIND_WEEK_START,
        plan=plan,
        week=week,
    )
    if log is None:
        return None

    unfinished = _unfinished_checkpoint_count(week)
    previous = _previous_week_needs_note(weeks, week)
    theme = _week_theme(week)
    note_prompt = ''
    if previous is not None:
        note_prompt = f" Write your Week {previous.week_number} note when you can."

    title = f'Week {week.week_number} is ready: {theme}'
    body = (
        f'Week {week.week_number} has {unfinished} unfinished '
        f'checkpoint{"" if unfinished == 1 else "s"}.{note_prompt}'
    )
    notification = Notification.objects.create(
        user=plan.member,
        title=title,
        body=body,
        url=_member_plan_path(plan, week),
        notification_type='sprint_week_start',
    )
    context = {
        'sprint_name': plan.sprint.name,
        'week_number': week.week_number,
        'week_theme': theme,
        'unfinished_count': unfinished,
        'unfinished_label': (
            'unfinished checkpoint' if unfinished == 1
            else 'unfinished checkpoints'
        ),
        'previous_week_number': previous.week_number if previous else '',
        'needs_previous_week_note': previous is not None,
        'plan_url': _member_plan_url(plan, week),
    }
    email_log, error = _send_cadence_email(
        log,
        template_name='sprint_week_start',
        context=context,
    )
    status = (
        SPRINT_CADENCE_STATUS_EMAIL_FAILED
        if error else SPRINT_CADENCE_STATUS_SENT
    )
    _finalize_log(
        log,
        notification=notification,
        email_log=email_log,
        status=status,
        last_error=error,
    )
    return log


def _deliver_week_note_prompt(plan, week):
    if week.notes.exists():
        return None
    log = _create_log_once(
        kind=SPRINT_CADENCE_KIND_WEEK_NOTE_PROMPT,
        plan=plan,
        week=week,
    )
    if log is None:
        return None

    title = f'Write your Week {week.week_number} sprint note'
    body = (
        f'Capture how Week {week.week_number} went while it is still fresh.'
    )
    notification = Notification.objects.create(
        user=plan.member,
        title=title,
        body=body,
        url=_member_plan_path(plan, week),
        notification_type='week_note_prompt',
    )
    context = {
        'sprint_name': plan.sprint.name,
        'week_number': week.week_number,
        'week_theme': _week_theme(week),
        'plan_url': _member_plan_url(plan, week),
    }
    email_log, error = _send_cadence_email(
        log,
        template_name='sprint_week_note_prompt',
        context=context,
    )
    status = (
        SPRINT_CADENCE_STATUS_EMAIL_FAILED
        if error else SPRINT_CADENCE_STATUS_SENT
    )
    _finalize_log(
        log,
        notification=notification,
        email_log=email_log,
        status=status,
        last_error=error,
    )
    return log


def _eligible_plans(today):
    return (
        Plan.objects
        .filter(
            member__is_active=True,
            shared_at__isnull=False,
            sprint__status='active',
            sprint__start_date__lte=today,
        )
        .select_related('member', 'sprint')
        .prefetch_related(
            Prefetch(
                'weeks',
                queryset=Week.objects.order_by(
                    'position', 'week_number',
                ).prefetch_related('notes'),
            ),
        )
    )


def send_sprint_cadence_notifications(*, today=None):
    """Send due week-start and week-note prompt notifications.

    ``today`` is injectable for tests. Production callers omit it and use the
    current local date.
    """
    if today is None:
        today = timezone.localdate()

    summary = CadenceSummary()
    for plan in _eligible_plans(today):
        if plan.sprint.end_date is None or today >= plan.sprint.end_date:
            continue
        weeks = _ordered_weeks(plan)
        offsets = _week_offsets(weeks)
        for week in weeks:
            if _week_start_date(plan, week, offsets) == today:
                log = _deliver_week_start(plan, week, weeks)
                if log is not None:
                    summary = CadenceSummary(
                        week_start_created=summary.week_start_created + 1,
                        week_note_prompt_created=summary.week_note_prompt_created,
                        emails_sent=summary.emails_sent + int(log.email_log_id is not None),
                        emails_failed=(
                            summary.emails_failed
                            + int(log.status == SPRINT_CADENCE_STATUS_EMAIL_FAILED)
                        ),
                    )
            if _week_end_date(plan, week, offsets) == today:
                log = _deliver_week_note_prompt(plan, week)
                if log is not None:
                    summary = CadenceSummary(
                        week_start_created=summary.week_start_created,
                        week_note_prompt_created=(
                            summary.week_note_prompt_created + 1
                        ),
                        emails_sent=summary.emails_sent + int(log.email_log_id is not None),
                        emails_failed=(
                            summary.emails_failed
                            + int(log.status == SPRINT_CADENCE_STATUS_EMAIL_FAILED)
                        ),
                    )
    return summary.as_dict()


def _change_labels(changes):
    labels = []
    for change in changes:
        label = (change.item_description or '').strip()
        if label:
            labels.append(label)
    return labels


def create_slack_progress_delivery(event, changes):
    """Create the member-facing notification for newly applied Slack changes."""
    plan = event.plan
    if plan is None or plan.member_id is None:
        return None
    if plan.sprint is None or plan.sprint.status != 'active':
        return None
    if plan.shared_at is None:
        return None

    changes = list(changes)
    if not changes:
        return None

    log = _create_log_once(
        kind=SPRINT_CADENCE_KIND_SLACK_PROGRESS,
        plan=plan,
        progress_event=event,
        source_message_ts=event.source_message_ts,
    )
    if log is None:
        return None

    count = len(changes)
    labels = _change_labels(changes)
    if labels:
        body = 'Updated: ' + '; '.join(labels[:5])
        if len(labels) > 5:
            body += f'; and {len(labels) - 5} more'
    else:
        body = 'Open your plan to review the updates.'
    notification = Notification.objects.create(
        user=plan.member,
        title=(
            f'We marked {count} item{"" if count == 1 else "s"} '
            'done from your Slack update.'
        ),
        body=body,
        url=_member_plan_path(plan, progress_event=event),
        notification_type='slack_progress',
    )
    _finalize_log(log, notification=notification)
    return log


def unresolved_slack_progress_event_for_plan(plan, event_id):
    if not event_id:
        return None
    if not str(event_id).isdigit():
        return None
    from crm.models import IngestedProgressEvent

    event = (
        IngestedProgressEvent.objects
        .filter(pk=int(event_id), plan=plan)
        .prefetch_related('changes')
        .first()
    )
    if event is None or not event.changes.exists():
        return None
    return event
