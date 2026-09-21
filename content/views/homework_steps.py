"""Learner-only draft API for the shared homework reader."""

import uuid

from community_base.homework_steps.models import HomeworkDraft
from community_base.homework_steps.services import DraftConflict, save_answer
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_POST

from content.models import Unit
from content.models.homework import Homework
from content.services.homework_step_reader import (
    AISLHomeworkAdapter,
    build_assignment,
)


@require_POST
def save_homework_step_answer(request, homework_id, question_id):
    """Update exactly one owned answer at the caller's draft revision."""
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Authentication required'}, status=401)
    homework = get_object_or_404(
        Homework.objects.select_related('cohort', 'cohort__course'),
        pk=homework_id, stepper_enabled=True,
    )
    unit = get_object_or_404(
        Unit.objects.select_related('module__course'),
        content_id=homework.content_id,
        module__course=homework.cohort.course,
        module__course__status='published',
        kind='homework',
    )
    adapter = AISLHomeworkAdapter(homework, unit)
    # Access must be checked before constructing or seeding a draft.
    preliminary = adapter.eligibility(request, None)
    if not preliminary.read or not preliminary.write:
        return JsonResponse(
            {'error': preliminary.reason or 'Homework is unavailable'}, status=403,
        )
    assignment = build_assignment(homework, unit, request.user)
    question = next(
        (item for item in assignment.questions if item.key == question_id), None,
    )
    if question is None:
        return JsonResponse({'error': 'Question does not belong to this homework'}, status=404)
    token = request.POST.get('draft_token', '')
    try:
        token = uuid.UUID(token)
    except (TypeError, ValueError, AttributeError):
        return JsonResponse({'error': 'Draft changed. Reload and try again.'}, status=409)
    if not HomeworkDraft.objects.filter(
        user=request.user, assignment_key=assignment.key, token=token,
    ).exists():
        return JsonResponse({'error': 'Draft changed. Reload and try again.'}, status=409)
    try:
        revision = int(request.POST.get('revision', ''))
        if revision < 0:
            raise ValueError
    except ValueError:
        return JsonResponse({'error': 'Invalid revision'}, status=400)
    answer = (
        request.POST.getlist('answer') if question.type == 'checkbox'
        else request.POST.get('answer', '')
    )
    try:
        draft = save_answer(
            request.user, assignment,
            question_key=question.key, answer=answer, revision=revision,
            token=token,
        )
    except DraftConflict:
        return JsonResponse({'error': 'Draft changed. Reload and try again.'}, status=409)
    except ValidationError as exc:
        return JsonResponse({'error': ' '.join(exc.messages)}, status=400)
    return JsonResponse({'revision': draft.revision, 'saved': True})
