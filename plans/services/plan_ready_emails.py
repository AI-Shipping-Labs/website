"""Bulk plan-ready email service for sprint plans (issue #1055)."""

import logging

from django.db import IntegrityError, transaction
from django.utils import timezone

from notifications.services.notification_service import NotificationService
from plans.models import (
    PLAN_READY_EMAIL_STATUS_FAILED,
    PLAN_READY_EMAIL_STATUS_SENDING,
    PLAN_READY_EMAIL_STATUS_SENT,
    Plan,
    PlanReadyEmailLog,
)

logger = logging.getLogger(__name__)


def _plan_identity(plan):
    member = plan.member
    display_name = (member.get_full_name() or '').strip()
    return {
        'plan_id': plan.pk,
        'member_id': member.pk,
        'member_email': member.email,
        'member_name': display_name,
        'sprint_slug': plan.sprint.slug,
        'shared_at': plan.shared_at.isoformat() if plan.shared_at else None,
    }


def _log_for(plan, logs_by_plan_id):
    return logs_by_plan_id.get(plan.pk)


def _empty_summary(sprint, *, dry_run):
    return {
        'dry_run': dry_run,
        'sprint': {
            'id': sprint.pk,
            'slug': sprint.slug,
            'name': sprint.name,
        },
        'total_plans': 0,
        'eligible_count': 0,
        'already_sent_count': 0,
        'failed_previous_attempts_count': 0,
        'sent_count': 0,
        'skipped_already_sent_count': 0,
        'failed_count': 0,
        'eligible': [],
        'sent': [],
        'skipped_already_sent': [],
        'failed': [],
        'failed_previous_attempts': [],
    }


def preview_plan_ready_emails(sprint):
    """Return the bulk ready-email audience without side effects."""
    return send_plan_ready_emails(sprint=sprint, actor=None, dry_run=True)


def _ready_email_result(*, requested, sent=False, skipped=False, failed=False, error=''):
    return {
        'requested': requested,
        'sent': sent,
        'skipped_already_sent': skipped,
        'failed': failed,
        'error': error,
    }


def send_plan_ready_email_for_plan(plan, *, actor):
    """Send the default plan-ready email for one newly prepared plan.

    This is the individual-create counterpart to ``send_plan_ready_emails``:
    it uses ``PlanReadyEmailLog`` as the durable per-plan guard, sends through
    the same ``plan_shared`` delivery path, stamps ``Plan.shared_at`` only on
    success, and lets failed rows remain eligible for the bulk retry action.
    """
    log, should_send = _claim_plan_for_send(plan, actor=actor)
    if not should_send:
        return _ready_email_result(requested=True, skipped=True)

    try:
        delivery = NotificationService.create_plan_shared_delivery(plan)
        if delivery.email_log is None:
            raise RuntimeError(
                delivery.email_error or 'plan_shared email was not logged',
            )
    except Exception as exc:
        logger.exception(
            'Failed to send individual plan-ready email to %s for plan %s',
            plan.member.email,
            plan.pk,
        )
        _mark_plan_send_failed(log, exc)
        return _ready_email_result(
            requested=True,
            failed=True,
            error=str(exc),
        )

    _mark_plan_send_sent(plan, log, delivery)
    return _ready_email_result(requested=True, sent=True)


def send_plan_ready_emails(*, sprint, actor, dry_run=False):
    """Send or preview plan-ready emails for one sprint.

    Eligibility is deliberately operator-driven: every plan in the sprint is
    eligible unless this bulk action has already recorded a successful send.
    Failed previous attempts are eligible again and counted separately for
    operator visibility.
    """
    plans = list(
        Plan.objects
        .filter(sprint=sprint)
        .select_related('member', 'sprint')
        .order_by('created_at', 'pk')
    )
    logs = PlanReadyEmailLog.objects.filter(
        plan_id__in=[plan.pk for plan in plans],
    )
    logs_by_plan_id = {log.plan_id: log for log in logs}

    summary = _empty_summary(sprint, dry_run=dry_run)
    summary['total_plans'] = len(plans)

    for plan in plans:
        row = _plan_identity(plan)
        log = _log_for(plan, logs_by_plan_id)
        if log and log.status == PLAN_READY_EMAIL_STATUS_SENT:
            row['sent_at'] = log.sent_at.isoformat() if log.sent_at else None
            summary['skipped_already_sent'].append(row)
            summary['already_sent_count'] += 1
            if not dry_run:
                summary['skipped_already_sent_count'] += 1
            continue
        if log and log.status == PLAN_READY_EMAIL_STATUS_SENDING:
            row['status'] = PLAN_READY_EMAIL_STATUS_SENDING
            summary['skipped_already_sent'].append(row)
            if not dry_run:
                summary['skipped_already_sent_count'] += 1
            continue

        summary['eligible'].append(row)
        summary['eligible_count'] += 1
        if log and log.status == PLAN_READY_EMAIL_STATUS_FAILED:
            failed_row = dict(row)
            failed_row['last_error'] = log.last_error
            summary['failed_previous_attempts'].append(failed_row)
            summary['failed_previous_attempts_count'] += 1

    if dry_run:
        summary['skipped_already_sent_count'] = len(
            summary['skipped_already_sent'],
        )
        return summary

    for row in list(summary['eligible']):
        plan = next(plan for plan in plans if plan.pk == row['plan_id'])
        log, should_send = _claim_plan_for_send(plan, actor=actor)
        if not should_send:
            skipped = _plan_identity(plan)
            skipped['sent_at'] = log.sent_at.isoformat() if log.sent_at else None
            summary['skipped_already_sent'].append(skipped)
            summary['skipped_already_sent_count'] += 1
            continue

        try:
            delivery = NotificationService.create_plan_shared_delivery(plan)
            if delivery.email_log is None:
                raise RuntimeError(
                    delivery.email_error or 'plan_shared email was not logged',
                )
        except Exception as exc:
            logger.exception(
                'Failed to send bulk plan-ready email to %s for plan %s',
                plan.member.email,
                plan.pk,
            )
            _mark_plan_send_failed(log, exc)
            failed = _plan_identity(plan)
            failed['last_error'] = str(exc)
            summary['failed'].append(failed)
            summary['failed_count'] += 1
            continue

        sent_at = _mark_plan_send_sent(plan, log, delivery)
        sent = _plan_identity(plan)
        sent['sent_at'] = sent_at.isoformat()
        summary['sent'].append(sent)
        summary['sent_count'] += 1

    return summary


def _claim_plan_for_send(plan, *, actor):
    """Create/update the durable send guard for one plan."""
    try:
        with transaction.atomic():
            log, created = PlanReadyEmailLog.objects.select_for_update().get_or_create(
                plan=plan,
                defaults={
                    'sprint': plan.sprint,
                    'member': plan.member,
                    'triggered_by': actor,
                    'status': PLAN_READY_EMAIL_STATUS_SENDING,
                    'last_error': '',
                },
            )
            if not created and log.status in (
                PLAN_READY_EMAIL_STATUS_SENT,
                PLAN_READY_EMAIL_STATUS_SENDING,
            ):
                return log, False
            if not created:
                log.sprint = plan.sprint
                log.member = plan.member
                log.triggered_by = actor
                log.status = PLAN_READY_EMAIL_STATUS_SENDING
                log.last_error = ''
                log.save(update_fields=[
                    'sprint', 'member', 'triggered_by', 'status',
                    'last_error', 'updated_at',
                ])
            return log, True
    except IntegrityError:
        log = PlanReadyEmailLog.objects.get(plan=plan)
        return log, False


def _mark_plan_send_failed(log, exc):
    now = timezone.now()
    PlanReadyEmailLog.objects.filter(pk=log.pk).update(
        status=PLAN_READY_EMAIL_STATUS_FAILED,
        last_error=str(exc)[:2000],
        updated_at=now,
    )


def _mark_plan_send_sent(plan, log, delivery):
    sent_at = timezone.now()
    Plan.objects.filter(pk=plan.pk, shared_at__isnull=True).update(
        shared_at=sent_at,
        updated_at=sent_at,
    )
    PlanReadyEmailLog.objects.filter(pk=log.pk).update(
        status=PLAN_READY_EMAIL_STATUS_SENT,
        notification=delivery.notification,
        email_log=delivery.email_log,
        sent_at=sent_at,
        last_error='',
        updated_at=sent_at,
    )
    return sent_at
