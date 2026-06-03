"""Studio views for managing internal-only personas (issue #801).

Personas classify engaged members into a small set of archetypes (Alex,
Priya, Sam, Taylor) so staff can pick the right onboarding questionnaire
and sprint plan. Personas are NEVER rendered to members -- every view
here is staff-only behind ``@staff_required``.

The CRUD pattern mirrors ``studio/views/sprints.py`` exactly:
``_parse_*`` helpers returning ``(value, error)``, a ``_render_form`` /
form-data helper pair, POST-validate-redirect with HTTP 400 re-render on
error, ``messages.success`` on success.

Every Studio surface that shows a persona shows ``name`` and
``archetype`` together -- the archetype is required context, not
optional, so staff can tell the personas apart.
"""

from django.contrib import messages
from django.db import transaction
from django.db.models import Count
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.text import slugify

from questionnaires.models import Persona, Questionnaire
from studio.decorators import staff_required
from studio.views.questionnaires import _parse_reorder_payload

# Only onboarding-purpose questionnaires are offered in the
# default-questionnaire dropdown. Imported value, not a hardcoded literal
# scattered across the codebase (#800 owns the vocabulary).
_ONBOARDING_PURPOSE = 'onboarding'


def _onboarding_questionnaires():
    """Questionnaires offered in the default-questionnaire dropdown."""
    return Questionnaire.objects.filter(
        purpose=_ONBOARDING_PURPOSE,
    ).order_by('title')


def _parse_order(raw):
    """Parse the ``order`` form field. Returns ``(value, error)``."""
    if raw in (None, ''):
        return 0, ''
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None, 'Order must be a whole number.'
    if value < 0:
        return None, 'Order must be zero or positive.'
    return value, ''


def _parse_default_questionnaire(raw):
    """Parse ``default_questionnaire``. Returns ``(Questionnaire|None, error)``.

    Empty / missing -> ``(None, '')`` (no default). A non-integer,
    unknown id, or non-onboarding questionnaire -> ``(None, error)`` so
    the caller re-renders with HTTP 400 and nothing is written.
    """
    if raw in (None, ''):
        return None, ''
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None, 'Selected default questionnaire does not exist.'
    questionnaire = _onboarding_questionnaires().filter(pk=value).first()
    if questionnaire is None:
        return None, (
            'Selected default questionnaire does not exist or is not an '
            'onboarding questionnaire.'
        )
    return questionnaire, ''


def _render_form(request, *, persona, form_action, form_data, error='', status=200):
    context = {
        'persona': persona,
        'form_action': form_action,
        'form_data': form_data,
        'questionnaire_choices': _onboarding_questionnaires(),
        'error': error,
        'primary_label': (
            'Save changes' if form_action == 'edit' else 'Create persona'
        ),
    }
    return render(request, 'studio/personas/form.html', context, status=status)


def _form_data_from_post(request):
    return {
        'name': (request.POST.get('name') or '').strip(),
        'archetype': (request.POST.get('archetype') or '').strip(),
        'slug': (request.POST.get('slug') or '').strip(),
        'description': (request.POST.get('description') or '').strip(),
        'default_questionnaire': (
            request.POST.get('default_questionnaire') or ''
        ).strip(),
        'is_active': request.POST.get('is_active') == 'on',
        'order': (request.POST.get('order') or '').strip(),
    }


def _form_data_from_persona(persona):
    return {
        'name': persona.name,
        'archetype': persona.archetype,
        'slug': persona.slug,
        'description': persona.description,
        'default_questionnaire': (
            str(persona.default_questionnaire_id)
            if persona.default_questionnaire_id else ''
        ),
        'is_active': persona.is_active,
        'order': str(persona.order),
    }


@staff_required
def persona_list(request):
    """Table of personas: name + archetype, active flag, default questionnaire."""
    personas = list(
        Persona.objects
        .select_related('default_questionnaire')
        .annotate(
            plan_count=Count('plans', distinct=True),
            crm_count=Count('crm_records', distinct=True),
        )
        .order_by('order', 'name')
    )
    return render(request, 'studio/personas/list.html', {
        'personas': personas,
    })


@staff_required
def persona_create(request):
    """Form to create a persona."""
    if request.method != 'POST':
        return _render_form(
            request,
            persona=None,
            form_action='create',
            form_data={
                'name': '',
                'archetype': '',
                'slug': '',
                'description': '',
                'default_questionnaire': '',
                'is_active': True,
                'order': '0',
            },
        )

    form_data = _form_data_from_post(request)
    name = form_data['name']
    archetype = form_data['archetype']
    raw_slug = form_data['slug']
    order, order_error = _parse_order(form_data['order'])
    default_questionnaire, dq_error = _parse_default_questionnaire(
        form_data['default_questionnaire']
    )

    if not name:
        return _render_form(
            request, persona=None, form_action='create',
            form_data=form_data, error='Name is required.', status=400,
        )
    if not archetype:
        return _render_form(
            request, persona=None, form_action='create',
            form_data=form_data, error='Archetype is required.', status=400,
        )

    slug = raw_slug or slugify(name)
    if not slug:
        return _render_form(
            request, persona=None, form_action='create',
            form_data=form_data,
            error='Slug could not be derived from name.', status=400,
        )

    if order_error:
        return _render_form(
            request, persona=None, form_action='create',
            form_data=form_data, error=order_error, status=400,
        )
    if dq_error:
        return _render_form(
            request, persona=None, form_action='create',
            form_data=form_data, error=dq_error, status=400,
        )

    if Persona.objects.filter(slug=slug).exists():
        return _render_form(
            request, persona=None, form_action='create',
            form_data=form_data,
            error=f'A persona with slug "{slug}" already exists. '
                  'Pick a different slug.',
            status=400,
        )

    persona = Persona.objects.create(
        name=name,
        archetype=archetype,
        slug=slug,
        description=form_data['description'],
        default_questionnaire=default_questionnaire,
        is_active=form_data['is_active'],
        order=order,
    )
    messages.success(request, f'Persona "{persona.name}" created.')
    return redirect('studio_persona_detail', persona_id=persona.pk)


@staff_required
def persona_detail(request, persona_id):
    """Persona metadata: name + archetype, description, default questionnaire."""
    persona = get_object_or_404(
        Persona.objects.select_related('default_questionnaire'),
        pk=persona_id,
    )
    return render(request, 'studio/personas/detail.html', {
        'persona': persona,
    })


@staff_required
def persona_edit(request, persona_id):
    """Edit all persona fields, including the default onboarding questionnaire."""
    persona = get_object_or_404(Persona, pk=persona_id)

    if request.method != 'POST':
        return _render_form(
            request,
            persona=persona,
            form_action='edit',
            form_data=_form_data_from_persona(persona),
        )

    form_data = _form_data_from_post(request)
    name = form_data['name']
    archetype = form_data['archetype']
    raw_slug = form_data['slug']
    order, order_error = _parse_order(form_data['order'])
    default_questionnaire, dq_error = _parse_default_questionnaire(
        form_data['default_questionnaire']
    )

    if not name:
        return _render_form(
            request, persona=persona, form_action='edit',
            form_data=form_data, error='Name is required.', status=400,
        )
    if not archetype:
        return _render_form(
            request, persona=persona, form_action='edit',
            form_data=form_data, error='Archetype is required.', status=400,
        )

    slug = raw_slug or slugify(name)
    if not slug:
        return _render_form(
            request, persona=persona, form_action='edit',
            form_data=form_data,
            error='Slug could not be derived from name.', status=400,
        )

    if order_error:
        return _render_form(
            request, persona=persona, form_action='edit',
            form_data=form_data, error=order_error, status=400,
        )
    if dq_error:
        return _render_form(
            request, persona=persona, form_action='edit',
            form_data=form_data, error=dq_error, status=400,
        )

    if Persona.objects.filter(slug=slug).exclude(pk=persona.pk).exists():
        return _render_form(
            request, persona=persona, form_action='edit',
            form_data=form_data,
            error=f'A different persona already uses slug "{slug}".',
            status=400,
        )

    persona.name = name
    persona.archetype = archetype
    persona.slug = slug
    persona.description = form_data['description']
    persona.default_questionnaire = default_questionnaire
    persona.is_active = form_data['is_active']
    persona.order = order
    persona.save()

    messages.success(request, f'Persona "{persona.name}" updated.')
    return redirect('studio_persona_detail', persona_id=persona.pk)


@staff_required
def persona_reorder(request):
    """Reorder personas (JSON API endpoint).

    Body: ``[{"id": <persona_pk>, "order": <int>}, ...]``. Personas have no
    parent, so each submitted id must simply exist as a ``Persona`` -- an
    unknown id is rejected with 400 and zero writes. The updates run inside a
    single ``transaction.atomic()`` so a bad payload never leaves a partial
    write. Mirrors the question / option reorder contract.
    """
    items, error_response = _parse_reorder_payload(request)
    if error_response is not None:
        return error_response

    submitted_ids = [pk for pk, _order in items]
    valid_count = Persona.objects.filter(pk__in=submitted_ids).count()
    if valid_count != len(set(submitted_ids)) or len(submitted_ids) != len(set(submitted_ids)):
        return JsonResponse(
            {'error': 'One or more ids are not valid personas.'},
            status=400,
        )

    with transaction.atomic():
        for pk, order in items:
            Persona.objects.filter(pk=pk).update(order=order)

    return JsonResponse({'status': 'ok'})
