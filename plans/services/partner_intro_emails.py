"""Preview and send sprint accountability partner intro emails."""

import logging

from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone

from accounts.utils.display import display_name
from community.services.slack_links import build_slack_profile_url
from email_app.services.email_service import EmailService
from integrations.config import get_config, site_base_url
from plans.models import (
    PARTNER_INTRO_EMAIL_STATUS_FAILED,
    PARTNER_INTRO_EMAIL_STATUS_SENDING,
    PARTNER_INTRO_EMAIL_STATUS_SENT,
    Plan,
    SprintAccountabilityPartner,
    SprintEnrollment,
    SprintPartnerIntroEmailLog,
)

logger = logging.getLogger(__name__)

TEMPLATE_NAME = 'sprint_partner_intro'


def preview_partner_intro_emails(sprint):
    """Return the partner-intro readiness summary without side effects."""
    return send_partner_intro_emails(sprint=sprint, actor=None, dry_run=True)


def send_partner_intro_emails(*, sprint, actor, dry_run=False):
    """Preview or send partner intro emails for one sprint."""
    data = _audience_data(sprint)
    summary = _empty_summary(sprint, dry_run=dry_run)
    summary['total_enrolled'] = len(data['enrolled_members'])

    _add_global_blockers(summary, sprint)
    rows = _build_rows(sprint, data)

    for row in rows:
        summary['rows'].append(row)
        status = row['status']
        if status == 'already_sent':
            summary['already_sent'].append(row)
            summary['already_sent_count'] += 1
            if not dry_run:
                summary['skipped_already_sent_count'] += 1
        elif status == 'sending':
            summary['already_sent'].append(row)
            if not dry_run:
                summary['skipped_already_sent_count'] += 1
        elif status == 'missing_plan':
            summary['missing_plan'].append(row)
            summary['missing_plan_count'] += 1
        elif status == 'missing_partner':
            summary['missing_partner'].append(row)
            summary['missing_partner_count'] += 1
        elif status == 'candidate':
            summary['candidate'].append(row)

        for warning in row['missing_slack_links']:
            summary['missing_slack_links'].append(warning)
            summary['missing_slack_link_count'] += 1
        for invalid_partner in row['invalid_partners']:
            summary['invalid_partners'].append({
                'member_id': row['member_id'],
                'member_email': row['member_email'],
                'partner_id': invalid_partner['id'],
                'partner_email': invalid_partner['email'],
                'partner_name': invalid_partner['name'],
            })
            summary['invalid_partner_count'] += 1

    if summary['total_enrolled'] < 2:
        summary['blockers'].append({
            'code': 'too_few_enrollments',
            'message': 'At least two enrolled members are required.',
        })
    if summary['missing_plan_count']:
        summary['blockers'].append({
            'code': 'missing_plans',
            'message': 'Every enrolled member needs a sprint plan.',
        })
    if summary['missing_partner_count']:
        summary['blockers'].append({
            'code': 'missing_partners',
            'message': (
                'Every enrolled member needs at least one accountability '
                'partner who is also enrolled.'
            ),
        })

    summary['send_ready'] = not summary['blockers']
    if summary['send_ready']:
        for row in summary['candidate']:
            row['status'] = 'eligible'
            summary['eligible'].append(row)
        summary['eligible_count'] = len(summary['eligible'])
    else:
        summary['eligible_count'] = 0

    if dry_run:
        summary['skipped_already_sent_count'] = len(summary['already_sent'])
        return summary

    if not summary['send_ready']:
        return summary

    for row in list(summary['eligible']):
        member = data['members_by_id'][row['member_id']]
        log, should_send = _claim_member_for_send(
            sprint=sprint,
            member=member,
            actor=actor,
            partner_snapshot=row['partners'],
        )
        if not should_send:
            skipped = _row_identity(member)
            skipped['status'] = log.status
            skipped['sent_at'] = log.sent_at.isoformat() if log.sent_at else None
            summary['skipped_already_sent'].append(skipped)
            summary['skipped_already_sent_count'] += 1
            continue

        try:
            email_log = EmailService().send(
                member,
                TEMPLATE_NAME,
                _email_context(sprint=sprint, member=member, row=row),
            )
            if email_log is None:
                raise RuntimeError('sprint_partner_intro email was not logged')
        except Exception as exc:
            logger.exception(
                'Failed to send partner intro email to %s for sprint %s',
                member.email,
                sprint.pk,
            )
            _mark_send_failed(log, exc)
            failed = dict(row)
            failed['last_error'] = str(exc)
            summary['failed'].append(failed)
            summary['failed_count'] += 1
            continue

        sent_at = _mark_send_sent(log, email_log)
        sent = dict(row)
        sent['sent_at'] = sent_at.isoformat()
        summary['sent'].append(sent)
        summary['sent_count'] += 1

    return summary


def _empty_summary(sprint, *, dry_run):
    return {
        'dry_run': dry_run,
        'send_ready': False,
        'sprint': {
            'id': sprint.pk,
            'slug': sprint.slug,
            'name': sprint.name,
            'status': sprint.status,
        },
        'total_enrolled': 0,
        'eligible_count': 0,
        'already_sent_count': 0,
        'missing_plan_count': 0,
        'missing_partner_count': 0,
        'missing_slack_link_count': 0,
        'invalid_partner_count': 0,
        'sent_count': 0,
        'skipped_already_sent_count': 0,
        'failed_count': 0,
        'blockers': [],
        'rows': [],
        'eligible': [],
        'candidate': [],
        'already_sent': [],
        'missing_plan': [],
        'missing_partner': [],
        'missing_slack_links': [],
        'invalid_partners': [],
        'sent': [],
        'skipped_already_sent': [],
        'failed': [],
    }


def _add_global_blockers(summary, sprint):
    if sprint.status != 'active':
        summary['blockers'].append({
            'code': 'inactive_sprint',
            'message': 'Partner intro emails are only available for active sprints.',
        })


def _audience_data(sprint):
    enrollments = list(
        SprintEnrollment.objects
        .filter(sprint=sprint)
        .select_related('user')
        .order_by('enrolled_at', 'pk')
    )
    enrolled_members = [enrollment.user for enrollment in enrollments]
    enrolled_ids = {member.pk for member in enrolled_members}
    plans_by_member_id = {
        plan.member_id: plan
        for plan in Plan.objects.filter(sprint=sprint, member_id__in=enrolled_ids)
    }
    logs_by_member_id = {
        log.member_id: log
        for log in SprintPartnerIntroEmailLog.objects.filter(
            sprint=sprint,
            member_id__in=enrolled_ids,
        )
    }
    assignments = (
        SprintAccountabilityPartner.objects
        .filter(sprint=sprint, member_id__in=enrolled_ids)
        .select_related('partner')
        .order_by('partner__email', 'partner_id')
    )
    partners_by_member_id = {}
    invalid_partners_by_member_id = {}
    for assignment in assignments:
        if assignment.partner_id in enrolled_ids:
            partners_by_member_id.setdefault(assignment.member_id, []).append(
                assignment.partner,
            )
        else:
            invalid_partners_by_member_id.setdefault(
                assignment.member_id,
                [],
            ).append(assignment.partner)

    return {
        'enrolled_members': enrolled_members,
        'members_by_id': {member.pk: member for member in enrolled_members},
        'plans_by_member_id': plans_by_member_id,
        'logs_by_member_id': logs_by_member_id,
        'partners_by_member_id': partners_by_member_id,
        'invalid_partners_by_member_id': invalid_partners_by_member_id,
    }


def _build_rows(sprint, data):
    rows = []
    slack_team_id = (get_config('SLACK_TEAM_ID', '') or '').strip()
    for member in data['enrolled_members']:
        row = _row_identity(member)
        row['partners'] = [
            _partner_identity(partner, slack_team_id=slack_team_id)
            for partner in data['partners_by_member_id'].get(member.pk, [])
        ]
        row['invalid_partners'] = [
            _partner_identity(partner, slack_team_id=slack_team_id)
            for partner in data['invalid_partners_by_member_id'].get(member.pk, [])
        ]
        row['missing_slack_links'] = [
            {
                'member_id': member.pk,
                'member_email': member.email,
                'partner_id': partner['id'],
                'partner_email': partner['email'],
                'partner_name': partner['name'],
                'partner_slack_identity': partner['slack_identity'],
                'reason': 'missing_slack_profile_url',
            }
            for partner in row['partners']
            if not partner['slack_profile_url']
        ]

        log = data['logs_by_member_id'].get(member.pk)
        if log and log.status == PARTNER_INTRO_EMAIL_STATUS_SENT:
            row['status'] = 'already_sent'
            row['sent_at'] = log.sent_at.isoformat() if log.sent_at else None
        elif log and log.status == PARTNER_INTRO_EMAIL_STATUS_SENDING:
            row['status'] = 'sending'
        elif member.pk not in data['plans_by_member_id']:
            row['status'] = 'missing_plan'
        elif not row['partners']:
            row['status'] = 'missing_partner'
        else:
            row['status'] = 'candidate'
            if log and log.status == PARTNER_INTRO_EMAIL_STATUS_FAILED:
                row['previous_failed'] = True
                row['last_error'] = log.last_error
        rows.append(row)
    return rows


def _row_identity(member):
    return {
        'member_id': member.pk,
        'member_email': member.email,
        'member_name': display_name(member),
    }


def _partner_identity(partner, *, slack_team_id):
    slack_user_id = (getattr(partner, 'slack_user_id', '') or '').strip()
    slack_identity = _slack_identity(partner, slack_user_id)
    return {
        'id': partner.pk,
        'name': display_name(partner),
        'email': partner.email,
        'slack_user_id': slack_user_id,
        'slack_identity': slack_identity,
        'slack_profile_url': build_slack_profile_url(slack_user_id, slack_team_id),
    }


def _slack_identity(user, slack_user_id):
    slack_metadata = (getattr(user, 'import_metadata', None) or {}).get('slack') or {}
    for key in (
        'display_name',
        'display_name_normalized',
        'real_name',
        'real_name_normalized',
        'name',
    ):
        value = (slack_metadata.get(key) or '').strip()
        if value:
            return value
    return slack_user_id


def _email_context(*, sprint, member, row):
    board_path = reverse('cohort_board', kwargs={'sprint_slug': sprint.slug})
    return {
        'sprint_name': sprint.name,
        'sprint_slug': sprint.slug,
        'member_name': display_name(member),
        'partner_count': len(row['partners']),
        'partners': row['partners'],
        'board_url': f'{site_base_url()}{board_path}',
    }


def _claim_member_for_send(*, sprint, member, actor, partner_snapshot):
    try:
        with transaction.atomic():
            log, created = (
                SprintPartnerIntroEmailLog.objects.select_for_update()
                .get_or_create(
                    sprint=sprint,
                    member=member,
                    defaults={
                        'triggered_by': actor,
                        'status': PARTNER_INTRO_EMAIL_STATUS_SENDING,
                        'last_error': '',
                        'partner_snapshot': partner_snapshot,
                    },
                )
            )
            if not created and log.status in (
                PARTNER_INTRO_EMAIL_STATUS_SENT,
                PARTNER_INTRO_EMAIL_STATUS_SENDING,
            ):
                return log, False
            if not created:
                log.triggered_by = actor
                log.status = PARTNER_INTRO_EMAIL_STATUS_SENDING
                log.last_error = ''
                log.partner_snapshot = partner_snapshot
                log.save(update_fields=[
                    'triggered_by', 'status', 'last_error',
                    'partner_snapshot', 'updated_at',
                ])
            return log, True
    except IntegrityError:
        log = SprintPartnerIntroEmailLog.objects.get(sprint=sprint, member=member)
        return log, False


def _mark_send_failed(log, exc):
    now = timezone.now()
    SprintPartnerIntroEmailLog.objects.filter(pk=log.pk).update(
        status=PARTNER_INTRO_EMAIL_STATUS_FAILED,
        last_error=str(exc)[:2000],
        updated_at=now,
    )


def _mark_send_sent(log, email_log):
    sent_at = timezone.now()
    SprintPartnerIntroEmailLog.objects.filter(pk=log.pk).update(
        status=PARTNER_INTRO_EMAIL_STATUS_SENT,
        email_log=email_log,
        sent_at=sent_at,
        last_error='',
        updated_at=sent_at,
    )
    return sent_at
