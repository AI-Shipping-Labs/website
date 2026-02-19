import json

from django.http import JsonResponse
from django.shortcuts import render, get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_POST

from content.access import (
    can_access, get_required_tier_name, get_user_level, LEVEL_TO_TIER_NAME,
)
from content.models import Course, Module, Unit, UserCourseProgress


def courses_list(request):
    """Course catalog page: grid of all published courses."""
    courses = Course.objects.filter(status='published')

    context = {
        'courses': courses,
    }
    return render(request, 'content/courses_list.html', context)


def course_detail(request, slug):
    """Course detail page: always visible for SEO.

    Shows title, description, instructor bio, full syllabus, tags,
    discussion link. Access-dependent elements:
    - Authorized user: clickable unit links, progress bar
    - Unauthorized user: unit titles (not clickable), CTA
    - Free course + unauthenticated: CTA to sign up
    """
    course = get_object_or_404(Course, slug=slug, status='published')
    user = request.user

    has_access = can_access(user, course)
    modules = course.get_syllabus()
    total = course.total_units()
    completed = course.completed_units(user)

    # Build set of completed unit IDs for template
    completed_unit_ids = set()
    if user.is_authenticated:
        completed_unit_ids = set(
            UserCourseProgress.objects.filter(
                user=user,
                unit__module__course=course,
                completed_at__isnull=False,
            ).values_list('unit_id', flat=True)
        )

    # Determine CTA
    cta_message = ''
    cta_url = ''
    if not has_access:
        tier_name = get_required_tier_name(course.required_level)
        # Find yearly price for the tier if available
        from payments.models import Tier
        try:
            tier = Tier.objects.get(level=course.required_level)
            price_str = f'{tier.price_eur_year}/year' if tier.price_eur_year else ''
            if price_str:
                cta_message = f'Unlock with {tier_name} \u2014 \u20ac{price_str}'
            else:
                cta_message = f'Unlock with {tier_name}'
        except Tier.DoesNotExist:
            cta_message = f'Unlock with {tier_name}'
        cta_url = '/pricing'
    elif course.is_free and not user.is_authenticated:
        cta_message = 'Sign up free to start this course'
        cta_url = '/accounts/signup'

    # Progress percentage
    progress_pct = 0
    if total > 0 and has_access:
        progress_pct = int((completed / total) * 100)

    context = {
        'course': course,
        'modules': modules,
        'has_access': has_access,
        'total_units': total,
        'completed_units': completed,
        'completed_unit_ids': completed_unit_ids,
        'progress_pct': progress_pct,
        'cta_message': cta_message,
        'cta_url': cta_url,
        'is_free_course': course.is_free,
        'user_authenticated': user.is_authenticated,
    }
    return render(request, 'content/course_detail.html', context)


# --- API endpoints ---


def api_courses_list(request):
    """GET /api/courses - list all published courses with is_locked flag."""
    courses = Course.objects.filter(status='published')
    user = request.user

    data = []
    for course in courses:
        is_locked = not can_access(user, course)
        data.append({
            'id': course.pk,
            'slug': course.slug,
            'title': course.title,
            'description': course.description[:200] if course.description else '',
            'cover_image_url': course.cover_image_url,
            'instructor_name': course.instructor_name,
            'tags': course.tags,
            'is_free': course.is_free,
            'required_level': course.required_level,
            'is_locked': is_locked,
        })

    return JsonResponse({'courses': data})


def api_course_detail(request, slug):
    """GET /api/courses/{slug} - detail + syllabus + progress."""
    course = get_object_or_404(Course, slug=slug, status='published')
    user = request.user

    has_access = can_access(user, course)
    modules = course.get_syllabus()
    total = course.total_units()
    completed = course.completed_units(user)

    # Build syllabus
    syllabus = []
    for module in modules:
        units_data = []
        for unit in module.units.all().order_by('sort_order'):
            unit_info = {
                'id': unit.pk,
                'title': unit.title,
                'sort_order': unit.sort_order,
                'is_preview': unit.is_preview,
            }
            units_data.append(unit_info)
        syllabus.append({
            'id': module.pk,
            'title': module.title,
            'sort_order': module.sort_order,
            'units': units_data,
        })

    data = {
        'id': course.pk,
        'slug': course.slug,
        'title': course.title,
        'description': course.description,
        'cover_image_url': course.cover_image_url,
        'instructor_name': course.instructor_name,
        'instructor_bio': course.instructor_bio,
        'tags': course.tags,
        'is_free': course.is_free,
        'required_level': course.required_level,
        'discussion_url': course.discussion_url,
        'is_locked': not has_access,
        'syllabus': syllabus,
    }

    # Include progress for authenticated users
    if user.is_authenticated:
        data['progress'] = {
            'completed': completed,
            'total': total,
        }

    return JsonResponse(data)


# --- Unit page view ---


def _get_unit_or_404(slug, module_sort, unit_sort):
    """Resolve a unit from course slug, module sort_order, unit sort_order."""
    course = get_object_or_404(Course, slug=slug, status='published')
    module = get_object_or_404(Module, course=course, sort_order=module_sort)
    unit = get_object_or_404(Unit, module=module, sort_order=unit_sort)
    return course, module, unit


def _get_all_units_ordered(course):
    """Return all units in the course ordered by module sort_order, then unit sort_order."""
    return list(
        Unit.objects.filter(module__course=course)
        .select_related('module')
        .order_by('module__sort_order', 'sort_order')
    )


def _get_next_unit(course, current_unit):
    """Find the next unit in sort order (across module boundaries)."""
    all_units = _get_all_units_ordered(course)
    for i, u in enumerate(all_units):
        if u.pk == current_unit.pk and i + 1 < len(all_units):
            return all_units[i + 1]
    return None


def course_unit_detail(request, slug, module_sort, unit_sort):
    """Unit page: gated by tier level, except for preview units.

    Shows video player, lesson text, homework, sidebar navigation,
    mark-complete toggle, and next-unit button.
    """
    course, module, unit = _get_unit_or_404(slug, module_sort, unit_sort)
    user = request.user

    # Access check: preview units are open to all; otherwise check tier
    has_access = unit.is_preview or can_access(user, course)

    if not has_access:
        tier_name = get_required_tier_name(course.required_level)
        context = {
            'course': course,
            'unit': unit,
            'is_gated': True,
            'required_tier_name': tier_name,
            'cta_message': f'Upgrade to {tier_name} to access this lesson',
            'pricing_url': '/pricing',
        }
        return render(request, 'content/course_unit_detail.html', context, status=403)

    # Build sidebar navigation data
    modules = course.get_syllabus()

    # Completed unit IDs for sidebar checkmarks
    completed_unit_ids = set()
    is_completed = False
    if user.is_authenticated:
        completed_unit_ids = set(
            UserCourseProgress.objects.filter(
                user=user,
                unit__module__course=course,
                completed_at__isnull=False,
            ).values_list('unit_id', flat=True)
        )
        is_completed = unit.pk in completed_unit_ids

    # Next unit
    next_unit = _get_next_unit(course, unit)

    context = {
        'course': course,
        'module': module,
        'unit': unit,
        'modules': modules,
        'is_gated': False,
        'has_access': True,
        'completed_unit_ids': completed_unit_ids,
        'is_completed': is_completed,
        'next_unit': next_unit,
        'user_authenticated': user.is_authenticated,
    }
    return render(request, 'content/course_unit_detail.html', context)


# --- Unit API endpoints ---


def api_course_unit_detail(request, slug, unit_id):
    """GET /api/courses/{slug}/units/{unit_id} - full unit content if authorized."""
    course = get_object_or_404(Course, slug=slug, status='published')
    unit = get_object_or_404(Unit, pk=unit_id, module__course=course)
    user = request.user

    # Access check: preview units open to all
    has_access = unit.is_preview or can_access(user, course)

    if not has_access:
        tier_name = get_required_tier_name(course.required_level)
        return JsonResponse(
            {'error': 'Access denied', 'required_tier_name': tier_name},
            status=403,
        )

    data = {
        'id': unit.pk,
        'title': unit.title,
        'sort_order': unit.sort_order,
        'video_url': unit.video_url,
        'body': unit.body,
        'body_html': unit.body_html,
        'homework': unit.homework,
        'homework_html': unit.homework_html,
        'timestamps': unit.timestamps,
        'is_preview': unit.is_preview,
        'module': {
            'id': unit.module.pk,
            'title': unit.module.title,
            'sort_order': unit.module.sort_order,
        },
    }

    # Include completion status for authenticated users
    if user.is_authenticated:
        is_completed = UserCourseProgress.objects.filter(
            user=user, unit=unit, completed_at__isnull=False,
        ).exists()
        data['is_completed'] = is_completed

    return JsonResponse(data)


@require_POST
def api_course_unit_complete(request, slug, unit_id):
    """POST /api/courses/{slug}/units/{unit_id}/complete - toggle completion."""
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Authentication required'}, status=401)

    course = get_object_or_404(Course, slug=slug, status='published')
    unit = get_object_or_404(Unit, pk=unit_id, module__course=course)
    user = request.user

    # Must have access to mark complete
    has_access = unit.is_preview or can_access(user, course)
    if not has_access:
        return JsonResponse({'error': 'Access denied'}, status=403)

    # Toggle: if progress exists with completed_at, unset it; otherwise create/set it
    progress, created = UserCourseProgress.objects.get_or_create(
        user=user, unit=unit,
    )

    if created or progress.completed_at is None:
        # Mark as completed
        progress.completed_at = timezone.now()
        progress.save()
        return JsonResponse({'completed': True})
    else:
        # Uncomplete - delete the record
        progress.delete()
        return JsonResponse({'completed': False})
