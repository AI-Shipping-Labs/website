"""Shared query and review workflows for questionnaire responses (#1289)."""

import json

from django.db import transaction
from django.db.models import Case, Count, DateTimeField, F, IntegerField, Q, When
from django.utils import timezone

from community.models import CommunityAuditLog
from questionnaires.models import Response

VALID_RESPONSE_STATUSES = ('submitted', 'draft', 'all')
VALID_REVIEW_FILTERS = ('awaiting', 'reviewed', 'all')
VALID_PURPOSES = ('onboarding', 'feedback', 'general', 'all')


class ResponseNotSubmitted(Exception):
    """Raised when an operator tries to review a draft response."""


def response_queryset(*, include_answers=False):
    """Bounded base queryset shared by Studio and staff-token APIs."""
    queryset = Response.objects.select_related(
        'questionnaire', 'respondent', 'respondent__crm_record', 'reviewed_by',
    )
    if include_answers:
        queryset = queryset.prefetch_related(
            'response_questions',
            'answers__selected_options',
            'answers__option_texts',
        )
    return queryset


def filter_response_queryset(
    queryset,
    *,
    status='submitted',
    review='awaiting',
    purpose='all',
    questionnaire=None,
    search='',
):
    """Apply the exact composable queue filters and deterministic ordering."""
    if status != 'all':
        queryset = queryset.filter(status=status)

    if review != 'all':
        if status == 'draft':
            return queryset.none()
        queryset = queryset.filter(status='submitted')
        if review == 'awaiting':
            queryset = queryset.filter(reviewed_at__isnull=True)
        else:
            queryset = queryset.filter(reviewed_at__isnull=False)

    if purpose != 'all':
        queryset = queryset.filter(questionnaire__purpose=purpose)
    if questionnaire is not None:
        queryset = queryset.filter(questionnaire_id=questionnaire)
    if search:
        queryset = queryset.filter(
            Q(respondent__email__icontains=search)
            | Q(respondent__first_name__icontains=search)
            | Q(respondent__last_name__icontains=search)
            | Q(questionnaire__title__icontains=search)
            | Q(questionnaire__slug__icontains=search)
        )

    return queryset.annotate(
        response_sort_group=Case(
            When(status='submitted', then=0),
            default=1,
            output_field=IntegerField(),
        ),
        response_sort_at=Case(
            When(status='submitted', then=F('submitted_at')),
            default=F('updated_at'),
            output_field=DateTimeField(),
        ),
    ).order_by(
        'response_sort_group',
        F('response_sort_at').desc(nulls_last=True),
        '-pk',
    )


def compact_response_queryset(**filters):
    """Queue rows with one aggregate for their answered count."""
    return filter_response_queryset(
        response_queryset().annotate(answered_count=Count('answers', distinct=True)),
        **filters,
    )


def transition_response_review(*, response_id, reviewed, actor, questionnaire_id=None):
    """Atomically review/reopen one response, preserving true no-op history."""
    with transaction.atomic():
        # Keep nullable outer joins out of the locking query: PostgreSQL does
        # not allow ``FOR UPDATE`` on the nullable side of an outer join.
        queryset = Response.objects.select_related(
            'questionnaire', 'respondent',
        ).select_for_update()
        if questionnaire_id is not None:
            queryset = queryset.filter(questionnaire_id=questionnaire_id)
        response = queryset.get(pk=response_id)

        previous_state = response.review_state
        changed = False
        if response.status != 'submitted':
            raise ResponseNotSubmitted
        if reviewed:
            if response.reviewed_at is None:
                response.reviewed_at = timezone.now()
                response.reviewed_by = actor
                response.save(update_fields=['reviewed_at', 'reviewed_by', 'updated_at'])
                changed = True
        elif response.reviewed_at is not None:
            response.reviewed_at = None
            response.reviewed_by = None
            response.save(update_fields=['reviewed_at', 'reviewed_by', 'updated_at'])
            changed = True

        if changed:
            new_state = response.review_state
            action = (
                'questionnaire_response_reviewed'
                if reviewed
                else 'questionnaire_response_reopened'
            )
            CommunityAuditLog.objects.create(
                user=response.respondent,
                action=action,
                details=json.dumps({
                    'response_id': response.pk,
                    'questionnaire_id': response.questionnaire_id,
                    'previous_review_state': previous_state,
                    'new_review_state': new_state,
                    'actor': actor.email,
                }, sort_keys=True),
            )

    return response, changed
