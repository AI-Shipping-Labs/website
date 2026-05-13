from collections import Counter
from urllib.parse import urlencode

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_POST

from content.access import (
    LEVEL_MAIN,
    build_gating_context,
    can_access,
    get_required_tier_name,
    get_user_level,
)
from content.models import (
    Cohort,
    CohortEnrollment,
    Course,
    Module,
    Unit,
    UserCourseProgress,
)
from content.services import completion as completion_service
from content.services import course_units as course_unit_service
from content.services.enrollment import (
    ensure_enrollment,
    is_enrolled,
)
from content.services.enrollment import (
    unenroll as unenroll_user,
)
from content.views.pages import _filter_by_tags, _get_selected_tags


def courses_list(request):
    """Course catalog page: grid of all published courses."""
    courses = Course.objects.filter(status='published')
    selected_tags = _get_selected_tags(request)

    # Collect all tags from published courses for the tag filter UI
    all_tags = set()
    for course in courses:
        if course.tags:
            all_tags.update(course.tags)
    all_tags = sorted(all_tags)

    # Filter by tags if provided (AND logic)
    courses = _filter_by_tags(courses, selected_tags)

    # Set of course IDs the user is currently enrolled in — drives the
    # "Enrolled" badge in the template (issue #236). Single query.
    enrolled_course_ids: set[int] = set()
    if request.user.is_authenticated:
        from content.models import Enrollment
        enrolled_course_ids = set(
            Enrollment.objects
            .filter(user=request.user, unenrolled_at__isnull=True)
            .values_list('course_id', flat=True)
        )

    context = {
        'courses': courses,
        'all_tags': all_tags,
        'selected_tags': selected_tags,
        'current_tag': selected_tags[0] if len(selected_tags) == 1 else '',
        'base_path': '/courses',
        'enrolled_course_ids': enrolled_course_ids,
    }
    return render(request, 'content/courses_list.html', context)


@ensure_csrf_cookie
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

    # Build set of completed unit IDs and per-module completion counts for
    # the template. Anonymous users get empty containers so the template
    # branches always fall through to the plain "X lessons" rendering.
    #
    # Single query: pull (unit_id, module_id) pairs for completed progress
    # rows in this course and derive both lookups in Python — keeps the
    # query count constant regardless of module count (issue #282 N+1
    # guard).
    completed_unit_ids: set[int] = set()
    completed_count_by_module: dict[int, int] = {}
    if user.is_authenticated:
        progress_rows = UserCourseProgress.objects.filter(
            user=user,
            unit__module__course=course,
            completed_at__isnull=False,
        ).values_list('unit_id', 'unit__module_id')
        module_id_counts: Counter = Counter()
        for unit_id, module_id in progress_rows:
            completed_unit_ids.add(unit_id)
            module_id_counts[module_id] += 1
        completed_count_by_module = dict(module_id_counts)

    gating = build_gating_context(user, course, 'course')

    # Determine CTA
    cta_message = ''
    cta_url = ''
    buy_individual = False
    buy_individual_price = None
    course_url = course.get_absolute_url()
    if not has_access:
        if gating.get('gated_reason') == 'unverified_email':
            cta_message = ''
        else:
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
            # Show individual purchase button if price is set
            if course.individual_price_eur is not None:
                buy_individual = True
                buy_individual_price = course.individual_price_eur
    elif course.is_free and not user.is_authenticated:
        cta_message = 'Sign up free to start this course'
        cta_url = f'/accounts/signup/?{urlencode({"next": course_url})}'

    # Progress percentage
    progress_pct = 0
    if total > 0 and has_access:
        progress_pct = int((completed / total) * 100)

    # Active cohorts
    active_cohorts = course.cohorts.filter(is_active=True).order_by('start_date')
    user_enrolled_cohort_ids = set()
    if user.is_authenticated:
        user_enrolled_cohort_ids = set(
            CohortEnrollment.objects.filter(
                user=user,
                cohort__course=course,
                cohort__is_active=True,
            ).values_list('cohort_id', flat=True)
        )

    # Discussion button: visible only on paid courses for Main+ tier users with community access.
    show_discussion = (
        bool(course.discussion_url)
        and course.required_level >= LEVEL_MAIN
        and get_user_level(user) >= LEVEL_MAIN
    )

    # Enrollment state (issue #236). Drives the Enroll / Continue buttons.
    user_is_enrolled = is_enrolled(user, course)
    next_unit_for_user = None
    if user_is_enrolled:
        next_unit_for_user = course.get_next_unit_for(user)

    context = {
        'course': course,
        'modules': modules,
        'has_access': has_access,
        'total_units': total,
        'completed_units': completed,
        'completed_unit_ids': completed_unit_ids,
        'completed_count_by_module': completed_count_by_module,
        'progress_pct': progress_pct,
        'cta_message': cta_message,
        'cta_url': cta_url,
        'is_free_course': course.is_free,
        'user_authenticated': user.is_authenticated,
        'active_cohorts': active_cohorts,
        'user_enrolled_cohort_ids': user_enrolled_cohort_ids,
        'buy_individual': buy_individual,
        'buy_individual_price': buy_individual_price,
        'testimonials': course.testimonials,
        'show_discussion': show_discussion,
        'user_is_enrolled': user_is_enrolled,
        'next_unit_for_user': next_unit_for_user,
    }
    if gating.get('gated_reason') == 'unverified_email':
        context.update(gating)
    elif gating.get('gated_reason'):
        context['gated_reason'] = gating['gated_reason']
    return render(request, 'content/course_detail.html', context)


# --- Enrollment endpoints (issue #236) ---


@require_POST
@login_required(login_url='/accounts/login/')
def enroll_course(request, slug):
    """POST /courses/{slug}/enroll — create an active Enrollment.

    Idempotent: if already enrolled, just redirect.

    Behaviour:
    - Requires login (decorator handles the redirect).
    - Tier-gated courses without access: redirect back to the detail page
      with the existing CTA — we don't create an enrollment we couldn't
      honour. Free courses are always enrollable.
    - On success, redirect to the next unfinished unit (or first unit if
      none completed yet); fall back to the course page if the course has
      no units.
    """
    course = get_object_or_404(Course, slug=slug, status='published')
    user = request.user

    # Don't enroll users who can't actually access the course content.
    # The course detail page surfaces the upgrade CTA in that case.
    if not can_access(user, course):
        return redirect(course.get_absolute_url())

    ensure_enrollment(user, course)

    next_unit = course.get_next_unit_for(user)
    if next_unit is not None:
        return redirect(next_unit.get_absolute_url())
    # No units yet — bounce back to the course page so the user sees the
    # "Enrolled" state.
    return redirect(course.get_absolute_url())


@require_POST
@login_required(login_url='/accounts/login/')
def unenroll_course(request, slug):
    """POST /courses/{slug}/unenroll — soft-delete the active enrollment."""
    course = get_object_or_404(Course, slug=slug, status='published')
    unenroll_user(request.user, course)
    return redirect(course.get_absolute_url())


# --- API endpoints ---


def api_courses_list(request):
    """GET /api/courses - list all published courses with is_locked flag."""
    courses = Course.objects.filter(status='published')
    user = request.user

    data = []
    for course in courses:
        is_locked = not can_access(user, course)
        primary = course.primary_instructor
        data.append({
            'id': course.pk,
            'slug': course.slug,
            'title': course.title,
            'description': course.description[:200] if course.description else '',
            'cover_image_url': course.cover_image_url,
            'instructor_name': primary.name if primary else '',
            'instructors': [
                {'id': i.instructor_id, 'name': i.name}
                for i in course.ordered_instructors
            ],
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

    # Build syllabus. ``modules`` comes from ``Course.get_syllabus()`` which
    # prefetches units already ordered by ``sort_order``; iterating
    # ``module.units.all()`` reads from the prefetch cache. Adding an extra
    # ``.order_by()`` here would force a fresh SELECT per module (N+1) — see
    # issue #287.
    syllabus = []
    for module in modules:
        units_data = []
        for unit in module.units.all():
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

    # Single query: ordered_instructors fetches the full M2M; primary is
    # the first row. Avoids the additional .first() query primary_instructor
    # would issue on top of ordered_instructors. Issue #287 / #423.
    ordered_instructors = course.ordered_instructors
    primary = ordered_instructors[0] if ordered_instructors else None
    data = {
        'id': course.pk,
        'slug': course.slug,
        'title': course.title,
        'description': course.description,
        'cover_image_url': course.cover_image_url,
        'instructor_name': primary.name if primary else '',
        'instructor_bio': primary.bio if primary else '',
        'instructors': [
            {
                'id': i.instructor_id,
                'name': i.name,
                'bio': i.bio,
                'photo_url': i.photo_url,
            }
            for i in ordered_instructors
        ],
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


def _get_unit_or_404(course_slug, module_slug, unit_slug):
    """Resolve a unit from course slug, module slug, unit slug."""
    course = get_object_or_404(Course, slug=course_slug, status='published')
    module = get_object_or_404(Module, course=course, slug=module_slug)
    unit = get_object_or_404(Unit, module=module, slug=unit_slug)
    return course, module, unit


def module_overview(request, course_slug, module_slug):
    """Module overview page: renders ``Module.overview_html`` + lesson list.

    Issue #222: the module README is now the module overview rather than a
    sibling Unit.

    Access mirrors the course detail page: the page is always reachable for
    SEO; gated content shows the upgrade CTA. Unit links in the lesson
    list are clickable for users with access; the unit detail view itself
    handles the per-lesson gating / teaser.
    """
    course = get_object_or_404(Course, slug=course_slug, status='published')
    module = get_object_or_404(Module, course=course, slug=module_slug)
    user = request.user

    has_access = can_access(user, course)
    # ``Unit.Meta.ordering = ['sort_order']`` already guarantees ordering;
    # an explicit ``.order_by()`` would be redundant. Issue #287.
    units = list(module.units.all())

    completed_unit_ids: set[int] = set()
    if user.is_authenticated:
        completed_unit_ids = set(
            UserCourseProgress.objects.filter(
                user=user,
                unit__module=module,
                completed_at__isnull=False,
            ).values_list('unit_id', flat=True)
        )

    cta_message = ''
    cta_url = ''
    if not has_access:
        tier_name = get_required_tier_name(course.required_level)
        cta_message = f'Upgrade to {tier_name} to access this module'
        cta_url = '/pricing'

    context = {
        'course': course,
        'module': module,
        'units': units,
        'has_access': has_access,
        'user_authenticated': user.is_authenticated,
        'completed_unit_ids': completed_unit_ids,
        'cta_message': cta_message,
        'cta_url': cta_url,
    }
    return render(request, 'content/module_overview.html', context)


def _get_all_units_ordered(course):
    """Return all units in course reading order."""
    return course_unit_service.get_all_units_ordered(course)


def _get_next_unit(course, current_unit):
    """Find the next unit in course reading order."""
    return course_unit_service.get_next_unit(course, current_unit)


def _get_prev_unit(course, current_unit):
    """Find the previous unit in course reading order."""
    return course_unit_service.get_prev_unit(course, current_unit)


def course_unit_detail(request, course_slug, module_slug, unit_slug):
    """Unit page: gated by tier level, except for preview units.

    Shows video player, lesson text, homework, sidebar navigation,
    mark-complete toggle, and next-unit button.
    """
    course, module, unit = _get_unit_or_404(course_slug, module_slug, unit_slug)
    user = request.user

    access_decision = course_unit_service.decide_course_unit_access(user, unit)
    if not access_decision.has_access:
        context = course_unit_service.build_gated_course_unit_context(
            user, course, module, unit, access_decision,
        )
        return render(
            request,
            'content/course_unit_detail.html',
            context,
            status=access_decision.status_code,
        )

    drip_decision = course_unit_service.decide_course_unit_drip_lock(user, unit)
    if drip_decision.is_locked:
        context = course_unit_service.build_drip_locked_course_unit_context(
            course, module, unit, drip_decision,
        )
        return render(request, 'content/course_unit_detail.html', context, status=403)

    context = course_unit_service.build_course_unit_navigation_context(
        user, course, module, unit,
    )
    return render(request, 'content/course_unit_detail.html', context)


# --- Unit API endpoints ---


def api_course_unit_detail(request, slug, unit_id):
    """GET /api/courses/{slug}/units/{unit_id} - full unit content if authorized."""
    course = get_object_or_404(Course, slug=slug, status='published')
    unit = get_object_or_404(Unit, pk=unit_id, module__course=course)
    user = request.user

    # Access check (issue #465): preview units open to all; otherwise
    # delegate to ``can_access(user, unit)`` which resolves the unit's
    # effective_required_level (unit override > course default > course
    # required_level) and honours CourseAccess on the parent course.
    if unit.is_preview:
        has_access = True
    else:
        has_access = can_access(user, unit)

    if not has_access:
        if not user.is_authenticated:
            return JsonResponse({'error': 'Authentication required'}, status=401)
        tier_name = get_required_tier_name(unit.effective_required_level)
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
    """POST /api/courses/{slug}/units/{unit_id}/complete - toggle completion.

    Issue #365 — toggle is now routed through
    :mod:`content.services.completion` so course units and workshop
    pages share the same primitives. Behaviour is unchanged: 401 for
    anonymous, 403 without access, ``{"completed": true|false}``
    response, and auto-enrollment on first completion (handled inside
    the service).
    """
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Authentication required'}, status=401)

    course = get_object_or_404(Course, slug=slug, status='published')
    unit = get_object_or_404(Unit, pk=unit_id, module__course=course)
    user = request.user

    # Must have access to mark complete (issue #465: delegate to the
    # unit so the effective level / per-unit access wins; mirrors the
    # detail-view check).
    has_access = unit.is_preview or can_access(user, unit)
    if not has_access:
        return JsonResponse({'error': 'Access denied'}, status=403)

    # Toggle: if completed, uncomplete; otherwise mark complete.
    if completion_service.is_completed(user, unit):
        completion_service.unmark_completed(user, unit)
        return JsonResponse({'completed': False})

    completion_service.mark_completed(user, unit)
    return JsonResponse({'completed': True})


# --- Cohort enrollment endpoints ---


@require_POST
def api_cohort_enroll(request, slug, cohort_id):
    """POST /api/courses/{slug}/cohorts/{cohort_id}/enroll - enroll in a cohort."""
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Authentication required'}, status=401)

    course = get_object_or_404(Course, slug=slug, status='published')
    cohort = get_object_or_404(Cohort, pk=cohort_id, course=course, is_active=True)
    user = request.user

    # Must have required tier to enroll
    if not can_access(user, course):
        tier_name = get_required_tier_name(course.required_level)
        return JsonResponse(
            {'error': f'{tier_name} membership required to enroll'},
            status=403,
        )

    # Check capacity
    if cohort.is_full:
        return JsonResponse(
            {'error': 'Cohort is full'},
            status=409,
        )

    # Check if already enrolled
    if CohortEnrollment.objects.filter(cohort=cohort, user=user).exists():
        return JsonResponse(
            {'error': 'Already enrolled in this cohort'},
            status=409,
        )

    CohortEnrollment.objects.create(cohort=cohort, user=user)
    return JsonResponse({'enrolled': True, 'cohort_id': cohort.pk})


@require_POST
def api_cohort_unenroll(request, slug, cohort_id):
    """POST /api/courses/{slug}/cohorts/{cohort_id}/unenroll - leave a cohort."""
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Authentication required'}, status=401)

    course = get_object_or_404(Course, slug=slug, status='published')
    cohort = get_object_or_404(Cohort, pk=cohort_id, course=course)
    user = request.user

    enrollment = CohortEnrollment.objects.filter(cohort=cohort, user=user).first()
    if not enrollment:
        return JsonResponse(
            {'error': 'Not enrolled in this cohort'},
            status=404,
        )

    enrollment.delete()
    return JsonResponse({'enrolled': False, 'cohort_id': cohort.pk})


# --- Individual course purchase ---


@require_POST
def api_course_purchase(request, slug):
    """POST /api/courses/{slug}/purchase - create a Stripe checkout for one-time course purchase."""
    from integrations.config import get_config, is_enabled
    if not is_enabled('STRIPE_CHECKOUT_ENABLED'):
        return JsonResponse({
            'error': 'Checkout is disabled. Use payment links.',
            'portal_url': get_config('STRIPE_CUSTOMER_PORTAL_URL', ''),
        }, status=410)

    if not request.user.is_authenticated:
        course = get_object_or_404(Course, slug=slug, status='published')
        next_url = course.get_absolute_url()
        return JsonResponse({
            'error': 'Authentication required',
            'login_url': f'/accounts/login/?{urlencode({"next": next_url})}',
        }, status=401)

    course = get_object_or_404(Course, slug=slug, status='published')

    # Check if already has access
    if can_access(request.user, course):
        return JsonResponse({'error': 'You already have access to this course'}, status=400)

    # Course must have individual pricing configured
    if not course.individual_price_eur:
        return JsonResponse({'error': 'This course is not available for individual purchase'}, status=400)

    if not course.stripe_price_id:
        return JsonResponse({'error': 'Stripe pricing not configured for this course'}, status=400)

    from payments.services import _get_stripe_client

    user = request.user
    success_url = request.build_absolute_uri(f'/courses/{course.slug}?purchase=success')
    cancel_url = request.build_absolute_uri(f'/courses/{course.slug}?purchase=cancelled')

    try:
        client = _get_stripe_client()

        session_params = {
            'mode': 'payment',
            'line_items': [{'price': course.stripe_price_id, 'quantity': 1}],
            'success_url': success_url,
            'cancel_url': cancel_url,
            'client_reference_id': str(user.pk),
            'customer_email': user.email,
            'metadata': {
                'user_id': str(user.pk),
                'course_id': str(course.pk),
            },
        }

        # If user already has a Stripe customer ID, use it instead of email
        if user.stripe_customer_id:
            session_params.pop('customer_email')
            session_params['customer'] = user.stripe_customer_id

        session = client.checkout.sessions.create(params=session_params)
        return JsonResponse({'checkout_url': session.url})
    except Exception:
        import logging
        logging.getLogger(__name__).exception(
            'Failed to create course purchase checkout for course %s', course.slug
        )
        return JsonResponse({'error': 'Failed to create checkout session'}, status=500)
