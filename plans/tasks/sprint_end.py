"""Sprint-end recap delivery task for shared member plans."""

import logging

from django.db import IntegrityError, transaction
from django.db.models import Exists, OuterRef
from django.urls import reverse
from django.utils import timezone

from content.access import get_user_level
from email_app.services.email_service import EmailService
from integrations.config import is_enabled, site_base_url
from notifications.models import Notification
from plans.models import (
    SPRINT_END_DELIVERY_STATUS_EMAIL_FAILED,
    SPRINT_END_DELIVERY_STATUS_SENT,
    Plan,
    Sprint,
    SprintEndDeliveryLog,
    SprintEnrollment,
    SprintFeedbackRequest,
)
from plans.services import annotate_plan_progress, distribute_sprint_feedback
from questionnaires.models import Response

logger = logging.getLogger(__name__)

AUTO_DISTRIBUTE_FEEDBACK_KEY = 'SPRINT_END_AUTO_DISTRIBUTE_FEEDBACK_ENABLED'
EMAIL_TEMPLATE = 'sprint_end_recap'


def send_sprint_end_recaps(today=None):
    """Send one sprint-end recap bell/email per eligible sprint member."""
    if today is None:
        today = timezone.localdate()

    if is_enabled(AUTO_DISTRIBUTE_FEEDBACK_KEY):
        _distribute_due_feedback(today=today)

    summary = {
        'eligible_count': 0,
        'sent_count': 0,
        'email_failed_count': 0,
        'skipped_count': 0,
    }

    for plan in _eligible_plans(today=today):
        summary['eligible_count'] += 1
        result = _deliver_plan_recap(plan, today=today)
        summary[f'{result}_count'] += 1

    return summary


def build_sprint_end_next_action(*, ended_sprint, member):
    """Return the member's most useful next sprint CTA, or ``None``."""
    end_date = ended_sprint.end_date
    if end_date is None:
        return None

    user_level = get_user_level(member)
    next_sprint = (
        Sprint.objects
        .filter(
            status='active',
            start_date__gte=end_date,
            min_tier_level__lte=user_level,
        )
        .exclude(pk=ended_sprint.pk)
        .order_by('start_date', 'pk')
        .first()
    )
    if next_sprint is None:
        return None

    next_plan = Plan.objects.filter(
        sprint=next_sprint,
        member=member,
    ).first()
    if next_plan is not None:
        return {
            'kind': 'carry_over',
            'next_sprint': next_sprint,
            'plan': next_plan,
            'url': reverse(
                'my_plan_detail',
                kwargs={
                    'sprint_slug': next_sprint.slug,
                    'plan_id': next_plan.pk,
                },
            ),
            'label': 'Carry over unfinished work',
            'description': (
                'Open your next plan and carry over unfinished work from '
                f'{ended_sprint.name}.'
            ),
        }

    enrolled = SprintEnrollment.objects.filter(
        sprint=next_sprint,
        user=member,
    ).exists()
    if enrolled:
        return {
            'kind': 'prepare_plan',
            'next_sprint': next_sprint,
            'plan': None,
            'url': reverse(
                'cohort_board',
                kwargs={'sprint_slug': next_sprint.slug},
            ),
            'label': 'Prepare my next plan',
            'description': (
                f'You are enrolled in {next_sprint.name}. Open the cohort '
                'board to continue the plan-preparation flow.'
            ),
        }

    return {
        'kind': 'join_next',
        'next_sprint': next_sprint,
        'plan': None,
        'url': reverse(
            'sprint_detail',
            kwargs={'sprint_slug': next_sprint.slug},
        ),
        'label': 'Join the next sprint',
        'description': (
            f'{next_sprint.name} is open to your tier. Join when you are '
            'ready for the next cohort window.'
        ),
    }


def get_member_sprint_feedback_response(*, sprint, member):
    """Return the latest distributed feedback response for this member."""
    questionnaire_ids = list(
        SprintFeedbackRequest.objects
        .filter(sprint=sprint, distributed_at__isnull=False)
        .order_by('-created_at')
        .values_list('questionnaire_id', flat=True)
    )
    if not questionnaire_ids:
        return None
    return (
        Response.objects
        .filter(questionnaire_id__in=questionnaire_ids, respondent=member)
        .order_by('-created_at')
        .first()
    )


def _distribute_due_feedback(*, today):
    feedback_requests = (
        SprintFeedbackRequest.objects
        .filter(
            distributed_at__isnull=True,
            sprint__status__in=['active', 'completed'],
        )
        .select_related('sprint', 'questionnaire')
        .order_by('sprint__start_date', 'pk')
    )
    for feedback_request in feedback_requests:
        if not feedback_request.sprint.has_ended(today=today):
            continue
        distribute_sprint_feedback(feedback_request)


def _eligible_plans(*, today):
    enrollment_exists = SprintEnrollment.objects.filter(
        sprint_id=OuterRef('sprint_id'),
        user_id=OuterRef('member_id'),
    )
    qs = (
        annotate_plan_progress(
            Plan.objects.filter(
                shared_at__isnull=False,
                member__is_active=True,
                sprint__status__in=['active', 'completed'],
            )
        )
        .select_related('member', 'sprint')
        .annotate(has_enrollment=Exists(enrollment_exists))
        .filter(has_enrollment=True)
        .order_by('sprint__start_date', 'pk')
    )
    return [
        plan for plan in qs
        if plan.sprint.has_ended(today=today)
    ]


def _deliver_plan_recap(plan, *, today):
    log, created = _claim_delivery(plan)
    if not created:
        return 'skipped'

    feedback_response = get_member_sprint_feedback_response(
        sprint=plan.sprint,
        member=plan.member,
    )
    next_action = build_sprint_end_next_action(
        ended_sprint=plan.sprint,
        member=plan.member,
    )
    notification_url = _notification_url(plan, feedback_response)
    notification = Notification.objects.create(
        user=plan.member,
        title=f'Sprint recap: {plan.sprint.name}',
        body=_recap_sentence(
            done=plan.progress_done,
            total=plan.progress_total,
        ),
        url=notification_url,
        notification_type='sprint_recap',
    )

    email_log = None
    status = SPRINT_END_DELIVERY_STATUS_SENT
    last_error = ''
    try:
        email_log = EmailService().send(
            plan.member,
            EMAIL_TEMPLATE,
            _email_context(
                plan=plan,
                feedback_response=feedback_response,
                next_action=next_action,
            ),
        )
        if email_log is None:
            raise RuntimeError('sprint_end_recap email was not logged')
    except Exception as exc:
        logger.exception(
            'Failed to send sprint_end_recap email to %s for plan %s',
            plan.member.email,
            plan.pk,
        )
        status = SPRINT_END_DELIVERY_STATUS_EMAIL_FAILED
        last_error = str(exc)[:2000]

    next_sprint = next_action['next_sprint'] if next_action else None
    sent_at = timezone.now()
    SprintEndDeliveryLog.objects.filter(pk=log.pk).update(
        plan=plan,
        notification=notification,
        email_log=email_log,
        feedback_response=feedback_response,
        next_sprint=next_sprint,
        status=status,
        sent_at=sent_at,
        last_error=last_error,
        updated_at=sent_at,
    )
    return 'sent' if status == SPRINT_END_DELIVERY_STATUS_SENT else 'email_failed'


def _claim_delivery(plan):
    try:
        with transaction.atomic():
            return SprintEndDeliveryLog.objects.get_or_create(
                sprint=plan.sprint,
                member=plan.member,
                defaults={
                    'plan': plan,
                    'status': SPRINT_END_DELIVERY_STATUS_SENT,
                    'last_error': '',
                },
            )
    except IntegrityError:
        return SprintEndDeliveryLog.objects.get(
            sprint=plan.sprint,
            member=plan.member,
        ), False


def _notification_url(plan, feedback_response):
    if feedback_response is not None and feedback_response.status != 'submitted':
        return reverse(
            'sprint_feedback_fill',
            kwargs={
                'sprint_slug': plan.sprint.slug,
                'response_id': feedback_response.pk,
            },
        )
    return reverse(
        'my_plan_detail',
        kwargs={'sprint_slug': plan.sprint.slug, 'plan_id': plan.pk},
    )


def _absolute(path):
    return f'{site_base_url()}{path}'


def _feedback_context(feedback_response, sprint):
    if feedback_response is None:
        return {
            'has_feedback': False,
            'feedback_url': '',
            'feedback_cta_label': '',
            'feedback_copy': '',
        }

    path = reverse(
        'sprint_feedback_fill',
        kwargs={
            'sprint_slug': sprint.slug,
            'response_id': feedback_response.pk,
        },
    )
    submitted = feedback_response.status == 'submitted'
    return {
        'has_feedback': True,
        'feedback_url': _absolute(path),
        'feedback_cta_label': (
            'View your feedback' if submitted else 'Share sprint feedback'
        ),
        'feedback_copy': (
            'Your feedback is already submitted. You can reopen it to view '
            'your answers.'
            if submitted
            else 'A short feedback form is ready when you have a minute.'
        ),
    }


def _email_context(*, plan, feedback_response, next_action):
    plan_path = reverse(
        'my_plan_detail',
        kwargs={'sprint_slug': plan.sprint.slug, 'plan_id': plan.pk},
    )
    context = {
        'sprint_name': plan.sprint.name,
        'completed_count': plan.progress_done,
        'total_count': plan.progress_total,
        'progress_sentence': _recap_sentence(
            done=plan.progress_done,
            total=plan.progress_total,
        ),
        'plan_url': _absolute(plan_path),
        'has_next_action': next_action is not None,
        'next_action_url': '',
        'next_action_label': '',
        'next_action_copy': '',
    }
    context.update(_feedback_context(feedback_response, plan.sprint))

    if next_action is not None:
        context.update({
            'next_action_url': _absolute(next_action['url']),
            'next_action_label': next_action['label'],
            'next_action_copy': next_action['description'],
        })
    return context


def _recap_sentence(*, done, total):
    if total == 0:
        return 'Your sprint plan had no checkpoints yet, so there was no checkpoint progress to count.'
    checkpoint_word = 'checkpoint' if total == 1 else 'checkpoints'
    return f'You completed {done} of {total} {checkpoint_word}.'
