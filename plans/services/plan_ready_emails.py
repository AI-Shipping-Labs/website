"""Plan-ready email services for sprint plans (issues #1055, #1093, #1455).

Three layers live here:

- ``send_plan_ready_emails`` -- the sprint-wide bulk action (#1055).
- ``send_plan_ready_email_for_plan`` -- the idempotent one-plan default
  delivery used by plan creation (#1093).
- ``run_plan_ready_action`` -- the #1455 outcome layer wrapping the
  one-plan delivery with an explicit, side-effect-free preview and a
  stable status vocabulary shared by Studio, the API, the ``asl`` CLI,
  and the markdown importer.
"""

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

# Provider details belong in the durable internal log and server logs only.
# Keep every public surface on the same stable, retry-oriented message.
PLAN_READY_EMAIL_PUBLIC_ERROR = (
    'Plan-ready delivery failed; retry the same action.'
)


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
        'error': PLAN_READY_EMAIL_PUBLIC_ERROR if failed else '',
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
        # The notification row is created before the provider call. Keep it
        # in the same transaction so a failed attempt cannot leave a bell
        # behind that a later retry would duplicate.
        with transaction.atomic():
            delivery = NotificationService.create_plan_shared_delivery(
                plan,
                swallow_email_errors=False,
            )
            if delivery.email_log is None:
                raise RuntimeError('plan_shared email was not logged')
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
            failed_row['last_error'] = PLAN_READY_EMAIL_PUBLIC_ERROR
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
            with transaction.atomic():
                delivery = NotificationService.create_plan_shared_delivery(
                    plan,
                    swallow_email_errors=False,
                )
                if delivery.email_log is None:
                    raise RuntimeError('plan_shared email was not logged')
        except Exception as exc:
            logger.exception(
                'Failed to send bulk plan-ready email to %s for plan %s',
                plan.member.email,
                plan.pk,
            )
            _mark_plan_send_failed(log, exc)
            failed = _plan_identity(plan)
            failed['last_error'] = PLAN_READY_EMAIL_PUBLIC_ERROR
            summary['failed'].append(failed)
            summary['failed_count'] += 1
            continue

        sent_at = _mark_plan_send_sent(plan, log, delivery)
        sent = _plan_identity(plan)
        sent['sent_at'] = sent_at.isoformat()
        summary['sent'].append(sent)
        summary['sent_count'] += 1

    return summary


PLAN_READY_ACTION_ELIGIBLE = 'eligible'
PLAN_READY_ACTION_SENT = 'sent'
PLAN_READY_ACTION_ALREADY_SENT = 'already_sent'
PLAN_READY_ACTION_ALREADY_SHARED = 'already_shared'
PLAN_READY_ACTION_FAILED_RETRYABLE = 'failed_retryable'
PLAN_READY_ACTION_IN_PROGRESS = 'in_progress'

PLAN_READY_ACTION_STATUSES = (
    PLAN_READY_ACTION_ELIGIBLE,
    PLAN_READY_ACTION_SENT,
    PLAN_READY_ACTION_ALREADY_SENT,
    PLAN_READY_ACTION_ALREADY_SHARED,
    PLAN_READY_ACTION_FAILED_RETRYABLE,
    PLAN_READY_ACTION_IN_PROGRESS,
)


def _ready_action_outcome(status, *, dry_run, sent_at=None, error=''):
    """Build the stable one-plan outcome payload.

    Every boolean is derived from ``status`` so the flags can never
    disagree with the reported status.
    """
    return {
        'dry_run': dry_run,
        'status': status,
        'eligible': status == PLAN_READY_ACTION_ELIGIBLE,
        # A preview never asks the delivery layer for anything; a live
        # call always does, even when the state short-circuits it.
        'requested': not dry_run,
        'sent': status == PLAN_READY_ACTION_SENT,
        'skipped_already_sent': status == PLAN_READY_ACTION_ALREADY_SENT,
        'skipped_already_shared': status == PLAN_READY_ACTION_ALREADY_SHARED,
        'failed': status == PLAN_READY_ACTION_FAILED_RETRYABLE,
        'retryable': status == PLAN_READY_ACTION_FAILED_RETRYABLE,
        'sent_at': sent_at.isoformat() if sent_at else None,
        'error': (
            PLAN_READY_EMAIL_PUBLIC_ERROR
            if status == PLAN_READY_ACTION_FAILED_RETRYABLE
            else ''
        ),
    }


def _classify_plan_ready_state(plan, log):
    """Read-only readiness classification for one plan."""
    if log is not None and log.status == PLAN_READY_EMAIL_STATUS_SENT:
        return PLAN_READY_ACTION_ALREADY_SENT
    if log is not None and log.status == PLAN_READY_EMAIL_STATUS_SENDING:
        return PLAN_READY_ACTION_IN_PROGRESS
    if plan.shared_at is not None:
        # Shared through the legacy/explicit re-share path (#732) without a
        # successful ready log. Never auto-deliver again -- an intentional
        # second notification is the confirmed Studio Re-share action.
        return PLAN_READY_ACTION_ALREADY_SHARED
    return PLAN_READY_ACTION_ELIGIBLE


def _ready_action_envelope(plan, outcome):
    return {
        'plan_id': plan.pk,
        'member_id': plan.member_id,
        'member_email': plan.member.email,
        'sprint_slug': plan.sprint.slug,
        'shared_at': plan.shared_at.isoformat() if plan.shared_at else None,
        'ready_email': outcome,
    }


def run_plan_ready_action(plan, *, actor, dry_run=False):
    """Default plan-ready delivery for exactly one plan (issue #1455).

    This is the single contract behind the Studio ``Share with member``
    action, ``POST /api/plans/<id>/send-ready-email``, ``asl plans
    send-ready``, and the markdown importer. It is idempotent: a plan
    that already completed a successful default send, or that was shared
    through the explicit #732 re-share path, is reported rather than
    notified again. It never forces a re-send, never unshares, and never
    touches ``visibility`` or sibling plans.

    ``dry_run=True`` performs no ``Plan``, ``PlanReadyEmailLog``,
    ``Notification``, or ``EmailLog`` write and no provider call.
    """
    log = PlanReadyEmailLog.objects.filter(plan=plan).first()
    status = _classify_plan_ready_state(plan, log)

    if dry_run or status != PLAN_READY_ACTION_ELIGIBLE:
        sent_at = log.sent_at if (
            log is not None and status == PLAN_READY_ACTION_ALREADY_SENT
        ) else None
        return _ready_action_envelope(
            plan,
            _ready_action_outcome(status, dry_run=dry_run, sent_at=sent_at),
        )

    result = send_plan_ready_email_for_plan(plan, actor=actor)
    plan.refresh_from_db()
    log = PlanReadyEmailLog.objects.filter(plan=plan).first()

    if result['sent']:
        outcome = _ready_action_outcome(
            PLAN_READY_ACTION_SENT,
            dry_run=False,
            sent_at=log.sent_at if log else None,
        )
    elif result['failed']:
        outcome = _ready_action_outcome(
            PLAN_READY_ACTION_FAILED_RETRYABLE,
            dry_run=False,
            error=result['error'],
        )
    else:
        # The durable guard was claimed between classification and send:
        # either another request completed it, or one is mid-flight.
        concurrent = _classify_plan_ready_state(plan, log)
        outcome = _ready_action_outcome(
            concurrent
            if concurrent != PLAN_READY_ACTION_ELIGIBLE
            else PLAN_READY_ACTION_IN_PROGRESS,
            dry_run=False,
            sent_at=log.sent_at if (
                log is not None
                and concurrent == PLAN_READY_ACTION_ALREADY_SENT
            ) else None,
        )
    return _ready_action_envelope(plan, outcome)


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
