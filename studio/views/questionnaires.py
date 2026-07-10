"""Studio views for authoring questionnaires and viewing responses (issue #800).

Mirrors the established Studio CRUD pattern from ``studio/views/sprints.py``:
``_parse_*`` helpers returning ``(value, error)``, a ``_render_form``
helper, ``_form_data_from_post`` / ``_form_data_from_<obj>`` helpers,
POST-validate-redirect with HTTP 400 re-render on error, ``messages.success``
on success, ``@staff_required`` on every view, URL naming
``studio_questionnaire_<action>``.

All views are staff-only. Anonymous users are redirected to the login
page; authenticated non-staff users get a 403. See ``studio/decorators.py``.

Response views are read-only here -- staff view what was collected.
Authoring / submitting member-facing responses is #802 / #803.
"""

import json

from django.contrib import messages
from django.db import transaction
from django.db.models import Count, Q
from django.http import HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.text import slugify

from questionnaires.models import (
    PURPOSE_CHOICES,
    QUESTION_TYPE_CHOICES,
    Question,
    Questionnaire,
    QuestionOption,
    ResponseQuestion,
    ResponseQuestionOption,
)
from studio.decorators import staff_required
from studio.utils import studio_pagination_context


def _parse_reorder_payload(request):
    """Parse + validate a reorder request body into ``(items, error_response)``.

    ``items`` is a list of ``(pk, order)`` int tuples on success, ``None`` on
    error (with a ready-to-return ``JsonResponse`` in the second slot). Mirrors
    the ``module_reorder`` contract: 405 on non-POST, 400 on unparseable JSON,
    400 on any malformed item (missing ``id``/``order``, non-int, negative
    ``order``). No writes happen here; the caller validates parent scope and
    applies updates inside a transaction.
    """
    if request.method != 'POST':
        return None, JsonResponse({'error': 'POST required'}, status=405)

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return None, JsonResponse({'error': 'Invalid JSON'}, status=400)

    if not isinstance(data, list):
        return None, JsonResponse(
            {'error': 'Payload must be a list of {id, order} items.'},
            status=400,
        )

    items = []
    for entry in data:
        if not isinstance(entry, dict) or 'id' not in entry or 'order' not in entry:
            return None, JsonResponse(
                {'error': 'Each item needs an id and an order.'}, status=400,
            )
        raw_id = entry['id']
        raw_order = entry['order']
        # Reject bools (a subclass of int) and non-ints outright.
        if isinstance(raw_id, bool) or isinstance(raw_order, bool):
            return None, JsonResponse(
                {'error': 'id and order must be integers.'}, status=400,
            )
        if not isinstance(raw_id, int) or not isinstance(raw_order, int):
            return None, JsonResponse(
                {'error': 'id and order must be integers.'}, status=400,
            )
        if raw_order < 0:
            return None, JsonResponse(
                {'error': 'order must be zero or positive.'}, status=400,
            )
        items.append((raw_id, raw_order))

    return items, None

_VALID_PURPOSES = {value for value, _label in PURPOSE_CHOICES}
_VALID_QUESTION_TYPES = {value for value, _label in QUESTION_TYPE_CHOICES}
_CHOICE_TYPES = {'single_choice', 'multiple_choice'}


# ---------------------------------------------------------------------------
# Questionnaire metadata CRUD
# ---------------------------------------------------------------------------


def _normalize_purpose(raw):
    """Coerce the raw purpose to a valid choice; default to general."""
    if raw in _VALID_PURPOSES:
        return raw
    return 'general'


def _render_form(request, *, questionnaire, form_action, form_data, error='', status=200):
    context = {
        'questionnaire': questionnaire,
        'form_action': form_action,
        'form_data': form_data,
        'purpose_choices': PURPOSE_CHOICES,
        'error': error,
        'primary_label': (
            'Save changes' if form_action == 'edit' else 'Create questionnaire'
        ),
    }
    return render(
        request, 'studio/questionnaires/form.html', context, status=status,
    )


def _form_data_from_post(request):
    return {
        'title': (request.POST.get('title') or '').strip(),
        'slug': (request.POST.get('slug') or '').strip(),
        'purpose': (request.POST.get('purpose') or '').strip(),
        'description': (request.POST.get('description') or '').strip(),
        'is_active': request.POST.get('is_active') == 'on',
    }


def _form_data_from_questionnaire(questionnaire):
    return {
        'title': questionnaire.title,
        'slug': questionnaire.slug,
        'purpose': questionnaire.purpose,
        'description': questionnaire.description,
        'is_active': questionnaire.is_active,
    }


@staff_required
def questionnaire_list(request):
    """Table of questionnaires with purpose, question + response counts."""
    search = (request.GET.get('q') or '').strip()
    questionnaires = (
        Questionnaire.objects
        .annotate(
            num_questions=Count('questions', distinct=True),
            num_responses=Count('responses', distinct=True),
        )
        .order_by('-created_at')
    )
    if search:
        questionnaires = questionnaires.filter(
            Q(title__icontains=search)
            | Q(slug__icontains=search)
            | Q(purpose__icontains=search)
            | Q(description__icontains=search)
        )
    pager = studio_pagination_context(request, questionnaires)
    return render(request, 'studio/questionnaires/list.html', {
        'questionnaires': pager['page'].object_list,
        'search': search,
        **pager,
    })


@staff_required
def questionnaire_create(request):
    """Form to create a questionnaire."""
    if request.method != 'POST':
        return _render_form(
            request,
            questionnaire=None,
            form_action='create',
            form_data={
                'title': '',
                'slug': '',
                'purpose': 'general',
                'description': '',
                'is_active': True,
            },
        )

    form_data = _form_data_from_post(request)
    title = form_data['title']
    raw_slug = form_data['slug']
    purpose = _normalize_purpose(form_data['purpose'])

    if not title:
        return _render_form(
            request, questionnaire=None, form_action='create',
            form_data=form_data, error='Title is required.', status=400,
        )

    slug = raw_slug or slugify(title)
    if not slug:
        return _render_form(
            request, questionnaire=None, form_action='create',
            form_data=form_data,
            error='Slug could not be derived from title.', status=400,
        )

    if Questionnaire.objects.filter(slug=slug).exists():
        return _render_form(
            request, questionnaire=None, form_action='create',
            form_data=form_data,
            error=f'A questionnaire with slug "{slug}" already exists. '
                  'Pick a different slug.',
            status=400,
        )

    questionnaire = Questionnaire.objects.create(
        title=title,
        slug=slug,
        purpose=purpose,
        description=form_data['description'],
        is_active=form_data['is_active'],
    )
    messages.success(request, f'Questionnaire "{questionnaire.title}" created.')
    return redirect('studio_questionnaire_detail', questionnaire_id=questionnaire.pk)


@staff_required
def questionnaire_detail(request, questionnaire_id):
    """Metadata + ordered base-question list + link to responses."""
    questionnaire = get_object_or_404(Questionnaire, pk=questionnaire_id)
    questions = list(
        questionnaire.questions.prefetch_related('options').all()
    )
    return render(request, 'studio/questionnaires/detail.html', {
        'questionnaire': questionnaire,
        'questions': questions,
        'response_count': questionnaire.response_count,
    })


@staff_required
def questionnaire_edit(request, questionnaire_id):
    """Edit questionnaire metadata."""
    questionnaire = get_object_or_404(Questionnaire, pk=questionnaire_id)

    if request.method != 'POST':
        return _render_form(
            request,
            questionnaire=questionnaire,
            form_action='edit',
            form_data=_form_data_from_questionnaire(questionnaire),
        )

    form_data = _form_data_from_post(request)
    title = form_data['title']
    raw_slug = form_data['slug']
    purpose = _normalize_purpose(form_data['purpose'])

    if not title:
        return _render_form(
            request, questionnaire=questionnaire, form_action='edit',
            form_data=form_data, error='Title is required.', status=400,
        )

    slug = raw_slug or slugify(title)
    if not slug:
        return _render_form(
            request, questionnaire=questionnaire, form_action='edit',
            form_data=form_data,
            error='Slug could not be derived from title.', status=400,
        )

    if Questionnaire.objects.filter(slug=slug).exclude(pk=questionnaire.pk).exists():
        return _render_form(
            request, questionnaire=questionnaire, form_action='edit',
            form_data=form_data,
            error=f'A different questionnaire already uses slug "{slug}".',
            status=400,
        )

    questionnaire.title = title
    questionnaire.slug = slug
    questionnaire.purpose = purpose
    questionnaire.description = form_data['description']
    questionnaire.is_active = form_data['is_active']
    questionnaire.save()

    messages.success(request, f'Questionnaire "{questionnaire.title}" updated.')
    return redirect('studio_questionnaire_detail', questionnaire_id=questionnaire.pk)


# ---------------------------------------------------------------------------
# Base-question CRUD
# ---------------------------------------------------------------------------


def _normalize_question_type(raw):
    """Return ``(value, error)`` for the raw question_type field."""
    if raw in _VALID_QUESTION_TYPES:
        return raw, ''
    return None, 'Pick a valid question type.'


def _parse_optional_int(raw, *, label):
    """Parse an optional integer field. ``(value|None, error)``."""
    if raw in (None, ''):
        return None, ''
    try:
        return int(raw), ''
    except (TypeError, ValueError):
        return None, f'{label} must be a whole number.'


_FREE_TEXT_SUFFIX = ' [free text]'


def _parse_options(raw):
    """Parse the one-per-line options textarea.

    Blank lines are skipped. Returns the ordered list of non-empty,
    trimmed option dicts. Append ``[free text]`` to a line to allow an
    attached member text field for that option.
    """
    if not raw:
        return []
    options = []
    for line in raw.splitlines():
        value = line.strip()
        if not value:
            continue
        allows_free_text = value.endswith(_FREE_TEXT_SUFFIX)
        if allows_free_text:
            value = value[:-len(_FREE_TEXT_SUFFIX)].strip()
        if value:
            options.append({
                'label': value,
                'allows_free_text': allows_free_text,
            })
    return options


def _format_options(options):
    lines = []
    for opt in options:
        suffix = _FREE_TEXT_SUFFIX if opt.allows_free_text else ''
        lines.append(f'{opt.label}{suffix}')
    return '\n'.join(lines)


def _render_question_form(
    request, *, questionnaire, question, form_action, form_data,
    error='', status=200,
):
    context = {
        'questionnaire': questionnaire,
        'question': question,
        'form_action': form_action,
        'form_data': form_data,
        'question_type_choices': QUESTION_TYPE_CHOICES,
        'choice_types': sorted(_CHOICE_TYPES),
        'error': error,
        'primary_label': (
            'Save changes' if form_action == 'edit' else 'Add question'
        ),
    }
    return render(
        request, 'studio/questionnaires/question_form.html', context,
        status=status,
    )


def _question_form_data_from_post(request):
    return {
        'question_type': (request.POST.get('question_type') or '').strip(),
        'prompt': (request.POST.get('prompt') or '').strip(),
        'help_text': (request.POST.get('help_text') or '').strip(),
        'is_required': request.POST.get('is_required') == 'on',
        'order': (request.POST.get('order') or '').strip(),
        'scale_min': (request.POST.get('scale_min') or '').strip(),
        'scale_max': (request.POST.get('scale_max') or '').strip(),
        'options': request.POST.get('options') or '',
    }


def _question_form_data_from_question(question):
    return {
        'question_type': question.question_type,
        'prompt': question.prompt,
        'help_text': question.help_text,
        'is_required': question.is_required,
        'order': str(question.order),
        'scale_min': '' if question.scale_min is None else str(question.scale_min),
        'scale_max': '' if question.scale_max is None else str(question.scale_max),
        'options': _format_options(question.options.all()),
    }


def _validate_question_post(form_data):
    """Validate parsed question form data. Returns ``(parsed, error)``.

    ``parsed`` is a dict ready to assign to a ``Question`` (plus the
    ``options`` list) or ``None`` when ``error`` is non-empty.
    """
    question_type, type_error = _normalize_question_type(form_data['question_type'])
    if type_error:
        return None, type_error

    prompt = form_data['prompt']
    if not prompt:
        return None, 'Prompt is required.'

    order, order_error = _parse_optional_int(form_data['order'], label='Order')
    if order_error:
        return None, order_error
    if order is None:
        order = 0

    scale_min, min_error = _parse_optional_int(
        form_data['scale_min'], label='Scale min',
    )
    if min_error:
        return None, min_error
    scale_max, max_error = _parse_optional_int(
        form_data['scale_max'], label='Scale max',
    )
    if max_error:
        return None, max_error

    options = _parse_options(form_data['options'])
    if question_type in _CHOICE_TYPES and not options:
        return None, 'Choice questions need at least one option (one per line).'

    return {
        'question_type': question_type,
        'prompt': prompt,
        'help_text': form_data['help_text'],
        'is_required': form_data['is_required'],
        'order': order,
        'scale_min': scale_min,
        'scale_max': scale_max,
        'options': options,
    }, ''


@staff_required
def question_create(request, questionnaire_id):
    """Add a base question to a questionnaire."""
    questionnaire = get_object_or_404(Questionnaire, pk=questionnaire_id)

    if request.method != 'POST':
        next_order = questionnaire.questions.count()
        return _render_question_form(
            request,
            questionnaire=questionnaire,
            question=None,
            form_action='create',
            form_data={
                'question_type': 'text',
                'prompt': '',
                'help_text': '',
                'is_required': False,
                'order': str(next_order),
                'scale_min': '',
                'scale_max': '',
                'options': '',
            },
        )

    form_data = _question_form_data_from_post(request)
    parsed, error = _validate_question_post(form_data)
    if error:
        return _render_question_form(
            request, questionnaire=questionnaire, question=None,
            form_action='create', form_data=form_data, error=error, status=400,
        )

    options = parsed.pop('options')
    question = Question.objects.create(questionnaire=questionnaire, **parsed)
    if question.is_choice_type:
        for index, option in enumerate(options):
            QuestionOption.objects.create(
                question=question,
                label=option['label'],
                allows_free_text=option['allows_free_text'],
                order=index,
            )
    messages.success(request, 'Question added.')
    return redirect('studio_questionnaire_detail', questionnaire_id=questionnaire.pk)


@staff_required
def question_edit(request, questionnaire_id, question_id):
    """Edit a base question and its options."""
    questionnaire = get_object_or_404(Questionnaire, pk=questionnaire_id)
    question = get_object_or_404(
        Question, pk=question_id, questionnaire=questionnaire,
    )

    if request.method != 'POST':
        return _render_question_form(
            request,
            questionnaire=questionnaire,
            question=question,
            form_action='edit',
            form_data=_question_form_data_from_question(question),
        )

    form_data = _question_form_data_from_post(request)
    parsed, error = _validate_question_post(form_data)
    if error:
        return _render_question_form(
            request, questionnaire=questionnaire, question=question,
            form_action='edit', form_data=form_data, error=error, status=400,
        )

    options = parsed.pop('options')
    for field, value in parsed.items():
        setattr(question, field, value)
    question.save()

    # Replace the option set wholesale (the textarea is the source of
    # truth). Non-choice questions end up with zero options.
    question.options.all().delete()
    if question.is_choice_type:
        for index, option in enumerate(options):
            QuestionOption.objects.create(
                question=question,
                label=option['label'],
                allows_free_text=option['allows_free_text'],
                order=index,
            )

    messages.success(request, 'Question updated.')
    return redirect('studio_questionnaire_detail', questionnaire_id=questionnaire.pk)


@staff_required
def question_delete(request, questionnaire_id, question_id):
    """POST-only delete of a base question."""
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])
    questionnaire = get_object_or_404(Questionnaire, pk=questionnaire_id)
    question = get_object_or_404(
        Question, pk=question_id, questionnaire=questionnaire,
    )
    question.delete()
    messages.success(request, 'Question deleted.')
    return redirect('studio_questionnaire_detail', questionnaire_id=questionnaire.pk)


@staff_required
def question_reorder(request, questionnaire_id):
    """Reorder base questions within a questionnaire (JSON API endpoint).

    Body: ``[{"id": <question_pk>, "order": <int>}, ...]``. Every submitted id
    must be a ``Question`` of THIS questionnaire -- a cross-parent or unknown id
    is rejected with 400 and zero writes. The updates run inside a single
    ``transaction.atomic()`` so a bad payload never leaves a partial write.

    Only base ``Question`` rows are touched; ``Response`` / ``ResponseQuestion``
    snapshots are never written here (the update is scoped by parent), so a
    member's historical response stays byte-identical across a reorder.
    """
    questionnaire = get_object_or_404(Questionnaire, pk=questionnaire_id)

    items, error_response = _parse_reorder_payload(request)
    if error_response is not None:
        return error_response

    submitted_ids = [pk for pk, _order in items]
    valid_count = questionnaire.questions.filter(pk__in=submitted_ids).count()
    if valid_count != len(set(submitted_ids)) or len(submitted_ids) != len(set(submitted_ids)):
        return JsonResponse(
            {'error': 'One or more ids are not questions of this questionnaire.'},
            status=400,
        )

    with transaction.atomic():
        for pk, order in items:
            Question.objects.filter(
                pk=pk, questionnaire=questionnaire,
            ).update(order=order)

    return JsonResponse({'status': 'ok'})


@staff_required
def question_option_reorder(request, questionnaire_id, question_id):
    """Reorder the options of a choice question (JSON API endpoint).

    Body: ``[{"id": <option_pk>, "order": <int>}, ...]``. The question is scoped
    to its questionnaire (404 otherwise). Every submitted id must be a
    ``QuestionOption`` of THIS question; a cross-parent / unknown id is rejected
    with 400 and zero writes. Updates run inside a single ``transaction.atomic()``.

    The wholesale options textarea author path in ``question_edit`` remains the
    source of truth for add/remove/relabel -- this endpoint only mutates the
    ``order`` field of existing rows.
    """
    questionnaire = get_object_or_404(Questionnaire, pk=questionnaire_id)
    question = get_object_or_404(
        Question, pk=question_id, questionnaire=questionnaire,
    )

    items, error_response = _parse_reorder_payload(request)
    if error_response is not None:
        return error_response

    submitted_ids = [pk for pk, _order in items]
    valid_count = question.options.filter(pk__in=submitted_ids).count()
    if valid_count != len(set(submitted_ids)) or len(submitted_ids) != len(set(submitted_ids)):
        return JsonResponse(
            {'error': 'One or more ids are not options of this question.'},
            status=400,
        )

    with transaction.atomic():
        for pk, order in items:
            QuestionOption.objects.filter(
                pk=pk, question=question,
            ).update(order=order)

    return JsonResponse({'status': 'ok'})


# ---------------------------------------------------------------------------
# Response viewing (read-only)
# ---------------------------------------------------------------------------


@staff_required
def questionnaire_responses(request, questionnaire_id):
    """List of responses: respondent, status, submitted date, answered count."""
    questionnaire = get_object_or_404(Questionnaire, pk=questionnaire_id)
    responses = list(
        questionnaire.responses
        .select_related('respondent')
        .annotate(answered_count=Count('answers', distinct=True))
        .order_by('-created_at')
    )
    return render(request, 'studio/questionnaires/responses.html', {
        'questionnaire': questionnaire,
        'responses': responses,
    })


@staff_required
def questionnaire_response_detail(request, questionnaire_id, response_id):
    """One respondent's full Q&A.

    Each ``ResponseQuestion`` prompt is paired with its ``Answer``
    (rendered via ``display_value``); unanswered questions are clearly
    marked as blank, and per-respondent custom questions (where
    ``source_question`` is null) are flagged.
    """
    questionnaire = get_object_or_404(Questionnaire, pk=questionnaire_id)
    response = get_object_or_404(
        questionnaire.responses.select_related('respondent'),
        pk=response_id,
    )

    # Map question_id -> Answer so the template renders blanks for
    # unanswered questions rather than omitting them.
    answers_by_question = {
        answer.question_id: answer
        for answer in response.answers.prefetch_related(
            'selected_options', 'option_texts',
        ).all()
    }
    rows = []
    for rq in response.response_questions.all():
        answer = answers_by_question.get(rq.pk)
        rows.append({
            'question': rq,
            'answer': answer,
            'is_answered': answer is not None and answer.display_value != '',
            'is_custom': rq.is_custom,
        })

    # Staff-only: the internal persona signal inferred by the AI
    # onboarding chat (#804), when this response came from that path. It
    # is never rendered on any member-facing surface.
    persona_signal = ''
    conversation = getattr(response, 'ai_conversation', None)
    if conversation is not None:
        persona_signal = conversation.persona_signal

    return render(request, 'studio/questionnaires/response_detail.html', {
        'questionnaire': questionnaire,
        'response': response,
        'rows': rows,
        'persona_signal': persona_signal,
    })


# ---------------------------------------------------------------------------
# Per-member response-question customization (issue #802)
#
# Staff customize ONE member's onboarding question set by editing that
# response's ``ResponseQuestion`` rows -- including adding a
# ``source_question=None`` one-off custom question. This never mutates
# the shared base ``Question`` rows or any other member's response.
# ---------------------------------------------------------------------------


def _get_response_for_questionnaire(questionnaire_id, response_id):
    """Fetch a response scoped to its questionnaire, or 404."""
    questionnaire = get_object_or_404(Questionnaire, pk=questionnaire_id)
    response = get_object_or_404(
        questionnaire.responses.select_related('respondent'),
        pk=response_id,
    )
    return questionnaire, response


def _render_response_question_form(
    request, *, questionnaire, response, response_question, form_action,
    form_data, error='', status=200,
):
    context = {
        'questionnaire': questionnaire,
        'response': response,
        'response_question': response_question,
        'form_action': form_action,
        'form_data': form_data,
        'question_type_choices': QUESTION_TYPE_CHOICES,
        'choice_types': sorted(_CHOICE_TYPES),
        'error': error,
        'primary_label': (
            'Save changes' if form_action == 'edit' else 'Add question'
        ),
    }
    return render(
        request, 'studio/questionnaires/response_question_form.html', context,
        status=status,
    )


def _response_question_form_data_from_rq(rq):
    return {
        'question_type': rq.question_type,
        'prompt': rq.prompt,
        'help_text': rq.help_text,
        'is_required': rq.is_required,
        'order': str(rq.order),
        'scale_min': '' if rq.scale_min is None else str(rq.scale_min),
        'scale_max': '' if rq.scale_max is None else str(rq.scale_max),
        'options': _format_options(rq.options.all()),
    }


def _sync_response_question_options(rq, options):
    """Update snapshot options while preserving same-label answer text."""
    existing_by_label = {opt.label: opt for opt in rq.options.all()}
    keep_ids = []
    for index, option in enumerate(options):
        existing = existing_by_label.get(option['label'])
        if existing is None:
            existing = ResponseQuestionOption.objects.create(
                response_question=rq,
                label=option['label'],
                allows_free_text=option['allows_free_text'],
                order=index,
            )
        else:
            existing.allows_free_text = option['allows_free_text']
            existing.order = index
            existing.save(update_fields=[
                'allows_free_text', 'order', 'updated_at',
            ])
        keep_ids.append(existing.pk)
    rq.options.exclude(pk__in=keep_ids).delete()


@staff_required
def response_question_create(request, questionnaire_id, response_id):
    """Add a one-off question to a single member's response.

    The new ``ResponseQuestion`` has ``source_question=None`` so it is a
    per-respondent custom question that exists only on this response.
    """
    questionnaire, response = _get_response_for_questionnaire(
        questionnaire_id, response_id,
    )

    if request.method != 'POST':
        next_order = response.response_questions.count()
        return _render_response_question_form(
            request,
            questionnaire=questionnaire,
            response=response,
            response_question=None,
            form_action='create',
            form_data={
                'question_type': 'text',
                'prompt': '',
                'help_text': '',
                'is_required': False,
                'order': str(next_order),
                'scale_min': '',
                'scale_max': '',
                'options': '',
            },
        )

    form_data = _question_form_data_from_post(request)
    parsed, error = _validate_question_post(form_data)
    if error:
        return _render_response_question_form(
            request, questionnaire=questionnaire, response=response,
            response_question=None, form_action='create',
            form_data=form_data, error=error, status=400,
        )

    options = parsed.pop('options')
    rq = ResponseQuestion.objects.create(
        response=response, source_question=None, **parsed,
    )
    if rq.is_choice_type:
        for index, option in enumerate(options):
            ResponseQuestionOption.objects.create(
                response_question=rq,
                label=option['label'],
                allows_free_text=option['allows_free_text'],
                order=index,
            )
    messages.success(request, 'Custom question added for this member.')
    return redirect(
        'studio_questionnaire_response_detail',
        questionnaire_id=questionnaire.pk, response_id=response.pk,
    )


@staff_required
def response_question_edit(request, questionnaire_id, response_id, rq_id):
    """Edit one member's ``ResponseQuestion`` (never the base question)."""
    questionnaire, response = _get_response_for_questionnaire(
        questionnaire_id, response_id,
    )
    rq = get_object_or_404(ResponseQuestion, pk=rq_id, response=response)

    if request.method != 'POST':
        return _render_response_question_form(
            request,
            questionnaire=questionnaire,
            response=response,
            response_question=rq,
            form_action='edit',
            form_data=_response_question_form_data_from_rq(rq),
        )

    form_data = _question_form_data_from_post(request)
    parsed, error = _validate_question_post(form_data)
    if error:
        return _render_response_question_form(
            request, questionnaire=questionnaire, response=response,
            response_question=rq, form_action='edit',
            form_data=form_data, error=error, status=400,
        )

    options = parsed.pop('options')
    for field, value in parsed.items():
        setattr(rq, field, value)
    rq.save()

    if rq.is_choice_type:
        _sync_response_question_options(rq, options)
    else:
        rq.options.all().delete()

    messages.success(request, 'Question updated for this member.')
    return redirect(
        'studio_questionnaire_response_detail',
        questionnaire_id=questionnaire.pk, response_id=response.pk,
    )


@staff_required
def response_question_delete(request, questionnaire_id, response_id, rq_id):
    """POST-only delete of one member's ``ResponseQuestion``.

    CASCADE removes any ``Answer`` for the removed question; the response
    stays valid. The template confirms before posting here.
    """
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])
    questionnaire, response = _get_response_for_questionnaire(
        questionnaire_id, response_id,
    )
    rq = get_object_or_404(ResponseQuestion, pk=rq_id, response=response)
    rq.delete()
    messages.success(request, 'Question removed from this member’s response.')
    return redirect(
        'studio_questionnaire_response_detail',
        questionnaire_id=questionnaire.pk, response_id=response.pk,
    )
