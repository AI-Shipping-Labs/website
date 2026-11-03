"""AISL audit adapter for the shared questionnaire response workflows."""

import json

from community_base.questionnaires import response_workflows as _shared_workflows
from django.contrib.auth import get_user_model

from community.models import CommunityAuditLog
from questionnaires.models import Response

VALID_RESPONSE_STATUSES = _shared_workflows.VALID_RESPONSE_STATUSES
VALID_REVIEW_FILTERS = _shared_workflows.VALID_REVIEW_FILTERS
VALID_PURPOSES = _shared_workflows.VALID_PURPOSES
ResponseNotSubmitted = _shared_workflows.ResponseNotSubmitted


def response_queryset(*, include_answers=False):
    """Keep the CRM one-to-one row selected for AISL Studio queue views."""
    return _shared_workflows.response_queryset(
        include_answers=include_answers,
    ).select_related("respondent__crm_record")


def filter_response_queryset(queryset, **filters):
    return _shared_workflows.filter_response_queryset(queryset, **filters)


def compact_response_queryset(**filters):
    return _shared_workflows.compact_response_queryset(**filters).select_related(
        "respondent__crm_record",
    )


def transition_response_review(**kwargs):
    return _shared_workflows.transition_response_review(**kwargs)


def write_studio_audit_event(
    *,
    event,
    actor_ref,
    subject_ref,
    questionnaire_ref,
    previous_state,
    new_state,
    **metadata,
):
    """Translate the shared audit event into AISL's durable audit row."""
    actions = {
        "questionnaires.response.reviewed": "questionnaire_response_reviewed",
        "questionnaires.response.reopened": "questionnaire_response_reopened",
    }
    action = actions.get(event)
    if action is None:
        return

    response = Response.objects.select_related("respondent").filter(pk=subject_ref).first()
    if response is None:
        return
    actor_email = get_user_model().objects.filter(pk=actor_ref).values_list("email", flat=True).first()
    CommunityAuditLog.objects.create(
        user=response.respondent,
        action=action,
        details=json.dumps(
            {
                "response_id": response.pk,
                "questionnaire_id": response.questionnaire_id,
                "previous_review_state": previous_state,
                "new_review_state": new_state,
                "actor": actor_email or str(actor_ref),
            },
            sort_keys=True,
        ),
    )
