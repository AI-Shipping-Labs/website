import json

from django.http import JsonResponse
from django.shortcuts import render, get_object_or_404
from django.views.decorators.http import require_POST

from content.access import (
    can_access, get_required_tier_name, get_user_level, LEVEL_TO_TIER_NAME,
)
from content.models import Course, Unit, UserCourseProgress


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
