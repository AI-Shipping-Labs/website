"""Studio views for course management and access management."""

import json

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from content.models import (
    Course,
    CourseAccess,
    CourseInstructor,
    Enrollment,
    Instructor,
    Module,
    Unit,
)
from content.services.course_instructors import (
    CourseInstructorError,
    add_course_instructor,
    remove_course_instructor,
    reorder_course_instructors,
)
from studio.decorators import staff_required
from studio.services.banner_panel import banner_panel_context
from studio.utils import get_github_edit_url, is_synced, studio_pagination_context
from studio.views.form_helpers import (
    parse_comma_separated_tags,
    reject_synced_content_post,
)
from studio.views.notifications import notification_action_context

User = get_user_model()

@staff_required
def course_list(request):
    """List all courses with status badges."""
    status_filter = request.GET.get('status', '')
    search = request.GET.get('q', '')

    courses = Course.objects.all()
    if status_filter:
        courses = courses.filter(status=status_filter)
    if search:
        courses = courses.filter(title__icontains=search)
    pager = studio_pagination_context(request, courses)

    return render(request, 'studio/courses/list.html', {
        'courses': pager['page'].object_list,
        'status_filter': status_filter,
        'search': search,
        **pager,
    })


@staff_required
def course_edit(request, course_id):
    """Edit an existing course with nested module/unit editors (read-only for synced items)."""
    course = get_object_or_404(Course, pk=course_id)
    synced = is_synced(course)

    if request.method == 'POST':
        if synced:
            return reject_synced_content_post()

        course.title = request.POST.get('title', '').strip()
        course.slug = request.POST.get('slug', '').strip() or slugify(course.title)
        course.description = request.POST.get('description', '')
        course.cover_image_url = request.POST.get('cover_image_url', '')
        course.status = request.POST.get('status', 'draft')
        course.required_level = int(request.POST.get('required_level', 0))
        course.discussion_url = request.POST.get('discussion_url', '')
        # Issue #1658: courses sold outside the membership plans. Only
        # local-only courses reach this branch (synced courses are
        # rejected above); invalid POST values fall back to 'tier'
        # rather than writing garbage into the DB column.
        access_mode_raw = request.POST.get('access_mode', 'tier')
        course.access_mode = access_mode_raw if access_mode_raw in ('tier', 'entitlement') else 'tier'
        course.enroll_url = request.POST.get('enroll_url', '')
        course.program_label = request.POST.get('program_label', '')
        course.tags = parse_comma_separated_tags(request.POST.get('tags', ''))
        individual_price_raw = request.POST.get('individual_price_eur', '').strip()
        if individual_price_raw:
            from decimal import Decimal, InvalidOperation
            try:
                course.individual_price_eur = Decimal(individual_price_raw)
            except InvalidOperation:
                pass
        else:
            course.individual_price_eur = None
        # Peer review fields
        course.peer_review_enabled = request.POST.get('peer_review_enabled') == 'on'
        peer_review_count_raw = request.POST.get('peer_review_count', '3').strip()
        try:
            course.peer_review_count = int(peer_review_count_raw)
        except (ValueError, TypeError):
            course.peer_review_count = 3
        peer_review_deadline_raw = request.POST.get('peer_review_deadline_days', '7').strip()
        try:
            course.peer_review_deadline_days = int(peer_review_deadline_raw)
        except (ValueError, TypeError):
            course.peer_review_deadline_days = 7
        course.peer_review_criteria = request.POST.get('peer_review_criteria', '')
        course.save()
        return redirect('studio_course_edit', course_id=course.pk)

    # Issue #1674: top-level modules only, tree-prefetched (children +
    # each leaf's units) — the tree template renders submodules
    # recursively from ``module.children.all()``.
    modules = list(course.get_syllabus())
    # Reparent target options (issue #1674): top-level modules only — a
    # submodule cannot itself become a parent (depth cap), so offering it
    # as a target would just bounce back as a Module.clean() error.
    top_level_modules_for_reparent = list(
        course.modules.filter(parent__isnull=True).order_by('sort_order', 'id'),
    )
    total_unit_count = Unit.objects.filter(module__course=course).count()

    access_count = CourseAccess.objects.filter(course=course).count()
    active_enrollment_count = Enrollment.objects.filter(
        course=course, unenrolled_at__isnull=True,
    ).count()
    cohort_count = course.aisl_cohorts.count()
    course_instructor_rows = list(
        CourseInstructor.objects.filter(course=course)
        .select_related('instructor')
        .order_by('position', 'pk')
    )
    attached_ids = [row.instructor_id for row in course_instructor_rows]
    available_instructors = Instructor.objects.exclude(pk__in=attached_ids).order_by(
        'name', 'instructor_id',
    )

    return render(request, 'studio/courses/form.html', {
        'course': course,
        'modules': modules,
        'top_level_modules_for_reparent': top_level_modules_for_reparent,
        'total_unit_count': total_unit_count,
        'form_action': 'edit',
        'is_synced': synced,
        'github_edit_url': get_github_edit_url(course),
        'notify_url': reverse('studio_course_notify', kwargs={'course_id': course.pk}),
        'announce_url': reverse('studio_course_announce_slack', kwargs={'course_id': course.pk}),
        'access_count': access_count,
        'active_enrollment_count': active_enrollment_count,
        'cohort_count': cohort_count,
        'course_instructor_rows': course_instructor_rows,
        'available_instructors': available_instructors,
        **notification_action_context('course', course),
        # Issues #788/#931: banner / social-image panel.
        **banner_panel_context(
            content_type='course',
            record=course,
            regenerate_url_name='studio_course_regenerate_banner',
            upload_url_name='studio_course_upload_banner',
            remove_url_name='studio_course_remove_banner',
            url_kwarg='course_id',
        ),
    })


def _course_instructors_redirect(course):
    return redirect(
        f"{reverse('studio_course_edit', kwargs={'course_id': course.pk})}#instructors"
    )


def _source_owned_response(course):
    if course.source_repo:
        return HttpResponse(
            'Course instructors are owned by course.yaml. Edit them in GitHub.',
            status=409,
        )
    return None


@staff_required
@require_POST
def course_instructor_add(request, course_id):
    course = get_object_or_404(Course, pk=course_id)
    if response := _source_owned_response(course):
        return response
    instructor_id = request.POST.get('instructor_id', '').strip()
    raw_position = request.POST.get('position', '').strip()
    try:
        position = int(raw_position)
    except (TypeError, ValueError):
        position = -1
    try:
        add_course_instructor(course, instructor_id, position)
    except CourseInstructorError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, 'Instructor added and order normalized.')
    return _course_instructors_redirect(course)


@staff_required
@require_POST
def course_instructor_remove(request, course_id, association_id):
    course = get_object_or_404(Course, pk=course_id)
    if response := _source_owned_response(course):
        return response
    try:
        remove_course_instructor(course, association_id)
    except CourseInstructorError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, 'Instructor removed and order normalized.')
    return _course_instructors_redirect(course)


@staff_required
@require_POST
def course_instructor_reorder(request, course_id):
    course = get_object_or_404(Course, pk=course_id)
    if response := _source_owned_response(course):
        return response
    raw_ids = request.POST.getlist('association_id')
    raw_positions = request.POST.getlist('position')
    try:
        association_ids = [int(value) for value in raw_ids]
        positions = [int(value) for value in raw_positions]
    except (TypeError, ValueError):
        association_ids = []
        positions = []
    try:
        reorder_course_instructors(course, association_ids, positions)
    except CourseInstructorError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, 'Instructor order saved.')
    return _course_instructors_redirect(course)


def _parse_available_after_days(raw):
    raw = (raw or '').strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


@staff_required
def module_create(request, course_id):
    """Create a module (or, with ``parent_id``, a submodule) for a course.

    Issue #1674: ``parent_id`` (optional POST field) creates a submodule
    under that module instead of a top-level module. ``Module.clean()``'s
    mixed-content/depth-cap/same-course invariants are enforced via
    ``full_clean()`` — a rejection surfaces as a Studio message naming
    the violation, never a silent no-op or a 500.
    """
    course = get_object_or_404(Course, pk=course_id)

    if is_synced(course):
        return HttpResponseForbidden(
            'This content is managed in GitHub. Edit it there.'
        )

    if request.method == 'POST':
        title = request.POST.get('title', '').strip()
        parent_id_raw = request.POST.get('parent_id', '').strip()
        parent = None
        if parent_id_raw:
            parent = get_object_or_404(Module, pk=parent_id_raw, course=course)
        siblings = course.modules.filter(parent=parent)
        max_order = siblings.order_by('-sort_order').values_list(
            'sort_order', flat=True,
        ).first() or 0
        module = Module(
            course=course,
            parent=parent,
            title=title,
            slug=slugify(title),
            sort_order=max_order + 1,
            is_bonus=request.POST.get('is_bonus') == 'on',
        )
        try:
            module.full_clean()
        except ValidationError as exc:
            messages.error(request, '; '.join(exc.messages))
        else:
            module.save()
            messages.success(request, f'"{module.title}" created.')
    return redirect('studio_course_edit', course_id=course.pk)


@staff_required
@require_POST
def module_edit(request, module_id):
    """Update a module's ``is_bonus``/``available_after_days`` (local courses only)."""
    module = get_object_or_404(Module, pk=module_id)
    course = module.course
    if is_synced(course):
        return HttpResponseForbidden(
            'This content is managed in GitHub. Edit it there.'
        )
    module.is_bonus = request.POST.get('is_bonus') == 'on'
    module.available_after_days = _parse_available_after_days(
        request.POST.get('available_after_days', ''),
    )
    try:
        module.full_clean()
    except ValidationError as exc:
        messages.error(request, '; '.join(exc.messages))
    else:
        module.save()
        messages.success(request, f'"{module.title}" updated.')
    return redirect('studio_course_edit', course_id=course.pk)


@staff_required
@require_POST
def module_reparent(request, module_id):
    """Move a module under a new parent (or to top level) (issue #1674).

    Server-side validation runs the same ``Module.clean()`` invariants
    (depth cap, same-course, mixed-content) as sync/create and returns a
    Studio message banner naming the specific violation on rejection —
    never a raw 500 or silent no-op.
    """
    module = get_object_or_404(Module, pk=module_id)
    course = module.course
    if is_synced(course):
        return HttpResponseForbidden(
            'This content is managed in GitHub. Edit it there.'
        )

    parent_id_raw = request.POST.get('parent_id', '').strip()
    new_parent = None
    if parent_id_raw:
        new_parent = get_object_or_404(Module, pk=parent_id_raw, course=course)

    original_parent_id = module.parent_id
    module.parent = new_parent
    try:
        module.full_clean()
    except ValidationError as exc:
        messages.error(request, '; '.join(exc.messages))
    else:
        if module.parent_id != original_parent_id:
            module.save(update_fields=['parent'])
            messages.success(request, f'"{module.title}" reparented.')
    return redirect('studio_course_edit', course_id=course.pk)


@staff_required
def unit_create(request, module_id):
    """Create a unit within a module."""
    module = get_object_or_404(Module, pk=module_id)

    if is_synced(module.course):
        return HttpResponseForbidden(
            'This content is managed in GitHub. Edit it there.'
        )

    if request.method == 'POST':
        title = request.POST.get('title', '').strip()
        max_order = module.units.order_by('-sort_order').values_list(
            'sort_order', flat=True,
        ).first() or 0
        unit = Unit(
            module=module,
            title=title,
            slug=slugify(title),
            sort_order=max_order + 1,
            is_bonus=request.POST.get('is_bonus') == 'on',
        )
        try:
            unit.full_clean()
        except ValidationError as exc:
            messages.error(request, '; '.join(exc.messages))
        else:
            unit.save()
            messages.success(request, f'"{unit.title}" created.')
    return redirect('studio_course_edit', course_id=module.course.pk)


@staff_required
def unit_edit(request, unit_id):
    """Edit a unit (read-only for synced courses)."""
    unit = get_object_or_404(Unit, pk=unit_id)
    course = unit.module.course
    synced = is_synced(course)

    if request.method == 'POST':
        if synced:
            return HttpResponseForbidden(
                'This content is managed in GitHub. Edit it there.'
            )

        unit.title = request.POST.get('title', '').strip()
        unit.video_url = request.POST.get('video_url', '')
        unit.body = request.POST.get('body', '')
        unit.homework = request.POST.get('homework', '')
        unit.is_preview = request.POST.get('is_preview') == 'on'
        unit.is_bonus = request.POST.get('is_bonus') == 'on'
        kind_raw = request.POST.get('kind', 'lesson').strip().lower()
        unit.kind = kind_raw if kind_raw in ('lesson', 'homework', 'event') else 'lesson'
        session_position_raw = request.POST.get('session_position', '').strip()
        unit.session_position = int(session_position_raw) if session_position_raw.isdigit() else None
        try:
            unit.full_clean()
        except ValidationError as exc:
            messages.error(request, '; '.join(exc.messages))
            unit.refresh_from_db()
        else:
            unit.save()
            return redirect('studio_course_edit', course_id=course.pk)

    return render(request, 'studio/courses/unit_form.html', {
        'unit': unit,
        'course': course,
        'is_synced': synced,
        'github_edit_url': get_github_edit_url(course),
    })


@staff_required
def module_reorder(request, course_id):
    """Reorder modules for a course (JSON API endpoint).

    Issue #1674: ``parent_id`` is an optional top-level key in the JSON
    body scoping the reorder to that parent's submodules; the payload
    stays a bare list of ``{id, sort_order}`` under an ``items`` key when
    ``parent_id`` is present, or (unchanged, backward-compatible) a bare
    list directly when it is not — existing callers/tests that POST a
    bare list keep working exactly as before, scoped to top-level modules.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    course = get_object_or_404(Course, pk=course_id)
    if isinstance(data, dict):
        parent_id = data.get('parent_id')
        items = data.get('items', [])
    else:
        parent_id = None
        items = data
    try:
        requested_ids = [int(item['id']) for item in items]
        positions = [int(item['sort_order']) for item in items]
    except (KeyError, TypeError, ValueError):
        return JsonResponse({'error': 'Invalid reorder payload'}, status=400)

    modules = Module.objects.filter(
        course=course, parent_id=parent_id, pk__in=requested_ids,
    )
    if modules.count() != len(set(requested_ids)):
        return JsonResponse({'error': 'Invalid module for this course'}, status=400)

    with transaction.atomic():
        for module_id, position in zip(requested_ids, positions, strict=True):
            Module.objects.filter(
                course=course, parent_id=parent_id, pk=module_id,
            ).update(sort_order=position)

    return JsonResponse({'status': 'ok'})


@staff_required
def unit_reorder(request, module_id):
    """Reorder units within a single module (JSON API endpoint, issue #1674).

    Same shape as ``module_reorder``'s bare-list payload — there was
    previously no unit-reorder endpoint at all; units could only be
    created, not reordered, in Studio.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    module = get_object_or_404(Module, pk=module_id)
    if is_synced(module.course):
        return HttpResponseForbidden(
            'This content is managed in GitHub. Edit it there.'
        )

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    try:
        requested_ids = [int(item['id']) for item in data]
        positions = [int(item['sort_order']) for item in data]
    except (KeyError, TypeError, ValueError):
        return JsonResponse({'error': 'Invalid reorder payload'}, status=400)

    units = Unit.objects.filter(module=module, pk__in=requested_ids)
    if units.count() != len(set(requested_ids)):
        return JsonResponse({'error': 'Invalid unit for this module'}, status=400)

    with transaction.atomic():
        for unit_id, position in zip(requested_ids, positions, strict=True):
            Unit.objects.filter(module=module, pk=unit_id).update(
                sort_order=position,
            )

    return JsonResponse({'status': 'ok'})


@staff_required
@require_POST
def course_create_stripe_product(request, course_id):
    """Deprecated: Studio no longer creates one-off Stripe course products."""
    get_object_or_404(Course, pk=course_id)
    return JsonResponse({
        'error': 'Studio Stripe product creation is deprecated. Use membership Payment Links.',
    }, status=410)


@staff_required
def course_access_list(request, course_id):
    """List all users with individual access to a course."""
    course = get_object_or_404(Course, pk=course_id)
    access_records = (
        CourseAccess.objects
        .filter(course=course)
        .select_related('user', 'granted_by')
        .order_by('-created_at')
    )

    return render(request, 'studio/courses/access_list.html', {
        'course': course,
        'access_records': access_records,
    })


@staff_required
@require_POST
def course_access_grant(request, course_id):
    """Grant a user access to a course.

    Accepts either a ``user_id`` (selected via the autocomplete) or an
    ``email`` (the keyboard-fast path). When both are provided, ``user_id``
    wins so the autocomplete selection is authoritative.
    """
    course = get_object_or_404(Course, pk=course_id)
    user_id_raw = request.POST.get('user_id', '').strip()
    email = request.POST.get('email', '').strip()

    user = None
    if user_id_raw:
        try:
            user = User.objects.get(pk=int(user_id_raw))
        except (ValueError, User.DoesNotExist):
            messages.error(request, 'Selected user no longer exists.')
            return redirect('studio_course_access_list', course_id=course.pk)
    elif email:
        try:
            user = User.objects.get(email__iexact=email)
        except User.DoesNotExist:
            messages.error(request, f'No user found with email "{email}".')
            return redirect('studio_course_access_list', course_id=course.pk)
    else:
        messages.error(request, 'Please provide an email address.')
        return redirect('studio_course_access_list', course_id=course.pk)

    # Check if the user already has access
    existing = CourseAccess.objects.filter(user=user, course=course).first()
    if existing:
        messages.info(
            request,
            f'{user.email} already has {existing.access_type} access to this course.',
        )
        return redirect('studio_course_access_list', course_id=course.pk)

    CourseAccess.objects.create(
        user=user,
        course=course,
        access_type='granted',
        granted_by=request.user,
    )
    messages.success(request, f'Access granted to {user.email}.')
    return redirect('studio_course_access_list', course_id=course.pk)


@staff_required
@require_POST
def course_access_revoke(request, course_id, access_id):
    """Revoke granted access for a user. Only granted access can be revoked."""
    course = get_object_or_404(Course, pk=course_id)
    access = get_object_or_404(CourseAccess, pk=access_id, course=course)

    if access.access_type != 'granted':
        messages.error(
            request,
            'Only granted access can be revoked from Studio. '
            'Purchased access cannot be revoked here.',
        )
        return redirect('studio_course_access_list', course_id=course.pk)

    email = access.user.email
    access.delete()
    messages.success(request, f'Access revoked for {email}.')
    return redirect('studio_course_access_list', course_id=course.pk)


@staff_required
def course_user_search(request, course_id):
    """Staff-only JSON endpoint that searches users for the access autocomplete.

    Accepts a ``q`` query parameter. Searches by email substring (case-insensitive)
    and exact numeric user ID. Returns at most 10 results with only the limited
    identity fields needed for selection (``id``, ``email``, ``name``). The
    course must exist (404 otherwise) so the URL can be tied to the access
    management page even though the search itself is course-agnostic.
    """
    get_object_or_404(Course, pk=course_id)
    query = request.GET.get('q', '').strip()
    if not query:
        return JsonResponse({'results': []})

    qs = User.objects.filter(email__icontains=query)
    if query.isdigit():
        qs = User.objects.filter(Q(email__icontains=query) | Q(pk=int(query)))

    qs = qs.order_by('email')[:10]
    results = [
        {
            'id': u.pk,
            'email': u.email,
            'name': (u.get_full_name() or '').strip(),
        }
        for u in qs
    ]
    return JsonResponse({'results': results})
