from collections import Counter
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_POST

from accounts.oauth_context import get_oauth_provider_context
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
from content.models.peer_review import CourseProject
from content.services import completion as completion_service
from content.services import course_units as course_unit_service
from content.services.course_inline import inline_units_and_topics
from content.services.course_schedule import (
    build_deadline_context,
    schedule_timezone_name,
    select_display_cohort,
)
from content.services.enrollment import (
    ensure_enrollment,
    ensure_self_paced_cohort_enrollment,
    is_enrolled,
)
from content.services.enrollment import (
    unenroll as unenroll_user,
)
from content.services.homework_submissions import (
    parse_submission_post,
    save_submission,
)
from content.views.pages import _filter_by_tags, _get_selected_tags
from events.models import Event
from events.models.event import PUBLIC_EVENT_STATUSES
from events.services.display_time import build_event_time_display


def _build_live_session_entries(events_qs, user):
    """Return row-presentation data for the course page's live-sessions block.

    Issue #1660: mirrors ``plans.views.sprints._build_sprint_call_entries``
    ("can join now" gate) and ``bookclub.views`` ("read the recap" gate),
    but this block shows every occurrence — not just the next upcoming one.
    """
    entries = []
    for event in events_qs:
        is_past = event.is_past
        entries.append({
            'event': event,
            'time_display': build_event_time_display(event, user),
            'is_past': is_past,
            # Same "can join now" gate the event detail page's join
            # affordance uses (issue #1660 spec: `can_show_zoom_link` and
            # not-yet-ended).
            'can_join_now': not is_past and event.can_show_zoom_link(),
            'join_url': event.get_join_url(),
            'recap_url': event.get_recap_url() if event.has_recap else '',
        })
    return entries


def _course_grid_classes(count):
    """Return the canonical listing-grid class string.

    Issue #1719: previously special-cased 1- and 2-item counts with
    ``lg:mx-auto lg:max-w-*``, which centred the grid under a heading that
    starts at the left edge. A CSS grid item that doesn't span columns
    already starts at that same edge and doesn't stretch below the column
    count, so the unconditional
    class string is correct for any ``count`` — matching the documented
    listing-grid pattern (``_docs/design-system.md``) and the Projects
    grid (``templates/content/projects_list.html``).
    """
    del count
    return "grid gap-6 sm:grid-cols-2 lg:grid-cols-3"


def courses_list(request):
    """Course catalog page: grid of all published courses.

    Issue #1658: published courses split into two groups. Standard
    (``access_mode='tier'``) courses keep today's grid, tag-filter facet
    pool, and empty-state behaviour unchanged. Entitlement-mode courses
    (sold outside the membership plans, e.g. Maven) render in their own
    "External courses" section below — excluded from the tag-filter pool
    and never filtered out by the selected tag.
    """
    published = Course.objects.filter(status='published')
    standard_courses = published.filter(aisl_extension__access_mode='tier')
    entitlement_courses = list(
        published.filter(aisl_extension__access_mode='entitlement').order_by('-created_at')
    )
    selected_tags = _get_selected_tags(request)

    # Collect tags from standard (tier-gated) published courses only —
    # entitlement courses don't feed the filter facet pool.
    all_tags = set()
    for course in standard_courses:
        if course.tags:
            all_tags.update(course.tags)
    all_tags = sorted(all_tags)

    # Filter standard courses by tag if provided (AND logic). Entitlement
    # courses are never filtered — they always show in their own section.
    courses = list(_filter_by_tags(standard_courses, selected_tags))

    course_grid_classes = _course_grid_classes(len(courses))
    entitlement_course_grid_classes = _course_grid_classes(len(entitlement_courses))

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
        'course_grid_classes': course_grid_classes,
        'entitlement_courses': entitlement_courses,
        'entitlement_course_grid_classes': entitlement_course_grid_classes,
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
    if has_access:
        # Issue #1674: "first gains course access" — the other of the two
        # points the spec names for implicit self-paced cohort membership.
        ensure_self_paced_cohort_enrollment(user, course)
    modules = course.get_syllabus()
    total = course.total_units()
    completed = course.completed_units(user)

    # Derived week dates and deliverable deadlines use the same selected
    # schedule cohort. An enrolled learner sees only an owned cohort; a
    # validated query key can select a public preview without granting access.
    viewer_cohort, schedule_is_preview = select_display_cohort(
        course, user, request.GET.get('cohort', ''),
    )
    unit_deadlines, module_deadline_summaries = build_deadline_context(
        course, viewer_cohort,
    )
    module_week_ranges = {
        module_id: course_unit_service.format_week_range(*week_range)
        for module_id, week_range in course_unit_service.build_module_week_dates(
            modules, viewer_cohort,
        ).items()
    }

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
        elif gating.get('gated_reason') == 'entitlement_required':
            # Issue #1658: sold-separately course — CTA is "Enroll via
            # {program_label}" linking to the external enroll_url, not
            # the tier-pricing "Unlock with {tier}" / /membership CTA.
            cta_message = gating['gated_heading']
            cta_url = gating['gated_cta_url']
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
            cta_url = '/membership'
    elif course.is_free and not user.is_authenticated:
        cta_message = 'Sign up free to start this course'
        cta_url = f'/accounts/signup/?{urlencode({"next": course_url})}'

    # Progress percentage
    progress_pct = 0
    if total > 0 and has_access:
        progress_pct = int((completed / total) * 100)

    # Active cohorts
    # Issue #1674: the self-enroll block is dated-cohort-only — a
    # self-paced cohort is never shown as something to manually join,
    # membership in it is implicit.
    active_cohorts = course.aisl_cohorts.filter(
        is_active=True, mode='cohort',
    ).order_by('start_date')
    user_enrolled_cohort_ids = set()
    if user.is_authenticated:
        user_enrolled_cohort_ids = set(
            CohortEnrollment.objects.filter(
                user=user,
                cohort__course=course,
                cohort__is_active=True,
            ).values_list('cohort_id', flat=True)
        )

    course_projects = list(CourseProject.objects.filter(course=course).select_related('cohort', 'module'))
    has_configured_projects = bool(course_projects)
    if viewer_cohort is not None or request.GET.get('cohort'):
        course_projects = [
            project for project in course_projects
            if project.cohort_id is None
            or project.cohort_id == getattr(viewer_cohort, 'pk', None)
        ]
    preview_project_ids = set()
    if not user.is_staff:
        course_projects = [
            project for project in course_projects
            if (
                project.cohort_id is None
                or project.cohort_id in user_enrolled_cohort_ids
                or project.cohort_id == getattr(viewer_cohort, 'pk', None)
            )
        ]
        preview_project_ids = {
            project.pk for project in course_projects
            if (
                not user.is_authenticated
                or not has_access
                or (project.cohort_id and project.cohort_id not in user_enrolled_cohort_ids)
            )
        }
    projects_by_module = {}
    for project in course_projects:
        if project.module_id:
            projects_by_module.setdefault(project.module_id, []).append(project)

    # Live-sessions block (issue #1660): resolves every EventSeries linked
    # via a Cohort of this course, then renders occurrences only when the
    # viewer is entitled (enrolled in one of those cohorts, or staff) to at
    # least one such series. This is an entitlement gate, not a tier gate —
    # anonymous and non-entitled authenticated viewers see no block at all,
    # with no upsell CTA (#1658 owns the course-level entitlement CTA).
    live_session_entries = []
    if user.is_authenticated:
        cohorts_with_series = list(
            course.aisl_cohorts.exclude(event_series_id__isnull=True)
        )
        if cohorts_with_series:
            if user.is_staff:
                entitled_series_ids = {
                    cohort.event_series_id for cohort in cohorts_with_series
                }
            else:
                entitled_series_ids = set(
                    CohortEnrollment.objects.filter(
                        user=user,
                        cohort__course=course,
                        cohort__event_series_id__isnull=False,
                    ).values_list('cohort__event_series_id', flat=True)
                )
            if entitled_series_ids:
                # Issue #1660: same status exclusion as the sprint page fix
                # — draft/cancelled occurrences stay out of this block too.
                live_session_events = (
                    Event.objects.filter(
                        event_series_id__in=entitled_series_ids,
                        status__in=PUBLIC_EVENT_STATUSES,
                    )
                    .select_related('event_series')
                    .order_by('start_datetime')
                )
                live_session_entries = _build_live_session_entries(
                    live_session_events, user,
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
        'required_tier_name': (
            get_required_tier_name(course.required_level)
            if course.required_level > 0 else ''
        ),
        'is_free_course': course.is_free,
        'user_authenticated': user.is_authenticated,
        'active_cohorts': active_cohorts,
        'module_week_ranges': module_week_ranges,
        'schedule_cohort': viewer_cohort,
        'schedule_is_preview': schedule_is_preview,
        'schedule_timezone': schedule_timezone_name(course, user),
        'unit_deadlines': unit_deadlines,
        'module_deadline_summaries': module_deadline_summaries,
        'live_session_entries': live_session_entries,
        'user_enrolled_cohort_ids': user_enrolled_cohort_ids,
        'course_projects': course_projects,
        'projects_by_module': projects_by_module,
        'preview_project_ids': preview_project_ids,
        'has_configured_projects': has_configured_projects,
        'buy_individual': buy_individual,
        'buy_individual_price': buy_individual_price,
        'testimonials': course.testimonials,
        'show_discussion': show_discussion,
        'user_is_enrolled': user_is_enrolled,
        'next_unit_for_user': next_unit_for_user,
    }
    # Issue #652: free-anon course detail surfaces render the inline
    # register card instead of the legacy "Sign Up Free" button. Pass
    # the OAuth provider flags and the round-trip URL so the inline
    # card's _register_form / _oauth_providers / _legal_footer
    # partials render with the right context. The flags are cheap
    # (single SocialApp query) so we add them unconditionally.
    if course.is_free and not user.is_authenticated:
        context.update(get_oauth_provider_context())
        context['next_url'] = course_url
        # Issue #653: suppress the footer newsletter CTA on the free-anon
        # branch so the inline register card is the only signup form on
        # the page. Free registration already implies newsletter opt-in
        # (a disclosure line in the inline card makes this explicit), so
        # the footer block would create a competing form.
        context['hide_footer_newsletter'] = True
    if gating.get('gated_reason') == 'unverified_email':
        context.update(gating)
    elif gating.get('gated_reason'):
        context['gated_reason'] = gating['gated_reason']
    # Issue #1658: tells the gated-access card to render the "Sold
    # separately" pill and open the enroll CTA in a new tab.
    context['gated_entitlement'] = gating.get('gated_entitlement', False)
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

    was_already_enrolled = is_enrolled(user, course)
    ensure_enrollment(user, course)

    # GA4 conversion (issue #774): fire course_enroll on the *next*
    # page render via a one-shot session flag. site_context pops the
    # key so the event fires exactly once. Only set on a real new
    # enrollment — re-clicking Enroll on an already-active row is not
    # a fresh conversion.
    if not was_already_enrolled:
        request.session['gtag_event_pending'] = {
            'event': 'course_enroll',
            'params': {
                'course_slug': course.slug,
                'login_state': 'authenticated',
            },
        }

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
    # prefetches the full tree (top-level modules, their children, and
    # every leaf module's units) already ordered; iterating
    # ``module.children.all()``/``module.units.all()`` reads from the
    # prefetch cache. Adding an extra ``.order_by()`` here would force a
    # fresh SELECT per module (N+1) — see issue #287.
    #
    # AI Buildcamp presents selected one-page child modules as inline units
    # while preserving their source IDs and canonical unit URLs.
    syllabus = [_module_json(module, course.slug) for module in modules]

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


def _unit_json(unit):
    """Issue #1674: shared unit JSON shape for the public syllabus API.

    ``session_position`` is included only when ``kind == 'event'`` — the
    stored integer, NOT a resolved Event (resolution is viewer/cohort
    -specific and this is an unauthenticated-safe read endpoint).
    """
    data = {
        'id': unit.pk,
        'title': unit.title,
        'sort_order': unit.sort_order,
        'is_preview': unit.is_preview,
        'kind': unit.kind,
        'is_bonus': unit.is_bonus,
    }
    if unit.kind == 'event':
        data['session_position'] = unit.session_position
    return data


def _module_json(module, course_slug):
    """Issue #1674: shared module JSON shape for the public syllabus API.

    A leaf module carries ``units``. A parent normally carries ``modules``;
    AI Buildcamp also exposes selected one-page children as inline ``units``.
    """
    data = {
        'id': module.pk,
        'title': module.title,
        'sort_order': module.sort_order,
        'parent_id': module.parent_id,
        'is_bonus': module.is_bonus,
    }
    children = list(module.children.all())
    if children:
        inline_units, topics = inline_units_and_topics(children, course_slug)
        data['modules'] = [_module_json(child, course_slug) for child in topics]
        data['units'] = [_unit_json(unit) for unit in inline_units]
    else:
        data['units'] = [_unit_json(unit) for unit in module.units.all()]
    return data


def _resolve_top_level_module(course, module_slug):
    """Resolve a TOP-LEVEL module (``parent_id`` is ``None``) by slug.

    Issue #1674: scoping to ``parent__isnull=True`` is what keeps this
    lookup unambiguous — ``module_top_level_slug_unique_per_course``
    guarantees a top-level slug is unique within the course, unlike a
    submodule slug (unique only among its own siblings). A 404 here (not
    a raw ``MultipleObjectsReturned``) is the only possible failure mode.
    """
    return get_object_or_404(
        Module.objects.select_related('parent'), course=course,
        parent__isnull=True, slug=module_slug,
    )


def _render_module_overview(request, course, module):
    """Module overview page: renders ``Module.overview_html`` + lesson list.

    Issue #222: the module README is now the module overview rather than a
    sibling Unit. Issue #1674: also used for a submodule's own overview
    page — a submodule either lists its own units (leaf) or has no
    ``submodules`` at all (max depth two, so a submodule never has
    children of its own).

    Access mirrors the course detail page: the page is always reachable for
    SEO; gated content shows the upgrade CTA. Unit links in the lesson
    list are clickable for users with access; the unit detail view itself
    handles the per-lesson gating / teaser.
    """
    if (
        course.reader_navigation_scope == 'submodule'
        and not CourseProject.objects.filter(module=module).exists()
    ):
        child_ids = list(module.children.values_list('pk', flat=True))
        ordered_units = (
            list(Unit.objects.filter(module_id__in=child_ids).order_by(
                'module__sort_order', 'module__pk', 'sort_order', 'pk',
            ))
            if child_ids else list(module.units.order_by('sort_order', 'pk'))
        )
        first_unit = next(
            (unit for unit in ordered_units if unit.kind == 'lesson'),
            ordered_units[0] if ordered_units else None,
        )
        if first_unit is not None:
            return redirect(first_unit.get_absolute_url())

    user = request.user

    has_access = can_access(user, course)
    # ``Unit.Meta.ordering = ['sort_order']`` already guarantees ordering;
    # an explicit ``.order_by()`` would be redundant. Issue #287.
    units = list(module.units.all())
    # Issue #1674: a parent module holds submodules, not units directly —
    # ``submodules`` is empty for a leaf module (today's two-level shape).
    children = list(module.children.order_by('sort_order', 'id').prefetch_related('units'))
    inline_units, submodules = inline_units_and_topics(children, course.slug)
    units.extend(inline_units)
    course_projects = list(CourseProject.objects.filter(module=module).select_related('cohort'))
    viewer_cohort, _ = select_display_cohort(
        course, user, request.GET.get('cohort', ''),
    )
    if viewer_cohort is not None or request.GET.get('cohort'):
        course_projects = [
            project for project in course_projects
            if project.cohort_id is None
            or project.cohort_id == getattr(viewer_cohort, 'pk', None)
        ]
    preview_project_ids = set()
    if not user.is_staff:
        cohort_ids = set(CohortEnrollment.objects.filter(
            user=user, cohort__course=course,
        ).values_list('cohort_id', flat=True)) if user.is_authenticated else set()
        course_projects = [
            project for project in course_projects
            if (project.cohort_id is None or project.cohort_id in cohort_ids
                or project.cohort_id == getattr(viewer_cohort, 'pk', None))
        ]
        preview_project_ids = {
            project.pk for project in course_projects
            if (not user.is_authenticated or not has_access
                or (project.cohort_id and project.cohort_id not in cohort_ids))
        }

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
    gated_entitlement = False
    if not has_access:
        if course.access_mode == 'entitlement':
            # Issue #1673: mirror the course-detail pattern — no tier
            # unlocks an entitlement-mode course, so the module CTA must
            # not point at /membership either. See course_detail() above.
            gated_entitlement = True
            cta_message = (
                f'Enroll via {course.program_label} to access this module'
                if course.program_label
                else 'Enroll to access this module'
            )
            cta_url = course.enroll_url or ''
        else:
            tier_name = get_required_tier_name(course.required_level)
            cta_message = f'Upgrade to {tier_name} to access this module'
            cta_url = '/membership'

    context = {
        'course': course,
        'module': module,
        'units': units,
        'submodules': submodules,
        'display_children': children,
        'course_projects': course_projects,
        'preview_project_ids': preview_project_ids,
        'schedule_timezone': schedule_timezone_name(course, user),
        'has_access': has_access,
        'user_authenticated': user.is_authenticated,
        'completed_unit_ids': completed_unit_ids,
        'cta_message': cta_message,
        'cta_url': cta_url,
        'required_tier_name': (
            get_required_tier_name(course.required_level)
            if not has_access and not gated_entitlement else ''
        ),
        'gated_entitlement': gated_entitlement,
    }
    return render(request, 'content/module_overview.html', context)


def module_overview(request, course_slug, module_slug):
    """``/courses/<course_slug>/<module_slug>`` — TOP-LEVEL modules only
    (issue #1674). Unchanged two-segment shape and behaviour for every
    existing two-level course; a submodule's own overview page now lives
    at the three-segment ``course_unit_detail`` route below instead
    (``/courses/<course>/<parent>/<submodule>``)."""
    course = get_object_or_404(Course, slug=course_slug, status='published')
    module = _resolve_top_level_module(course, module_slug)
    return _render_module_overview(request, course, module)


def _render_course_unit_detail(request, course, module, unit):
    """Unit page: gated by tier level, except for preview units.

    Shows video player, lesson text, homework, sidebar navigation,
    mark-complete toggle, and next-unit button.
    """
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

    # Issue #1674: "first interacts with a unit" is one of the two points
    # the spec names for implicit self-paced cohort membership (the other
    # is course_detail below, "first gains course access"). Idempotent —
    # a no-op once a self-paced CohortEnrollment already exists, and a
    # no-op for courses with no mode='self_paced' Cohort at all.
    ensure_self_paced_cohort_enrollment(user, course)

    drip_decision = course_unit_service.decide_course_unit_drip_lock(user, unit)
    if drip_decision.is_locked:
        context = course_unit_service.build_drip_locked_course_unit_context(
            course, module, unit, drip_decision,
        )
        return render(request, 'content/course_unit_detail.html', context, status=403)

    if request.method == 'POST':
        return _handle_homework_submission_post(request, unit)

    # Record a `lesson_open` activity row for the CRM timeline (issue #853),
    # only for authenticated users who have access (this branch). Deduped:
    # re-opening the same unit within 30 minutes does not create a new row.
    # Defensive — never raises into the page render.
    from analytics.activity import record_lesson_open
    record_lesson_open(user, unit=unit)

    context = course_unit_service.build_course_unit_navigation_context(
        user, course, module, unit,
    )
    context.update(course_unit_service.build_homework_submission_context(user, unit))
    return render(request, 'content/course_unit_detail.html', context)


def course_unit_detail(request, course_slug, module_slug, unit_slug):
    """``/courses/<course_slug>/<module_slug>/<unit_slug>`` — dispatches
    between a leaf module's unit page (unchanged, every existing
    two-level course) and a parent module's SUBMODULE overview page
    (issue #1674's new shape,
    ``/courses/<course>/<parent>/<submodule>``).

    Deterministic, not a fallback guess: ``Module.clean()`` forbids mixed
    content, so a top-level module holds EITHER child submodules OR
    direct units, never both. If ``module_slug`` names a top-level module
    WITH children, ``unit_slug`` can only be one of its submodule slugs
    (that module has no direct units to be a unit's parent). If it has
    NO children, ``unit_slug`` can only be a unit slug under it — exactly
    today's two-level behaviour, byte-for-byte unchanged.
    """
    course = get_object_or_404(Course, slug=course_slug, status='published')
    top_module = _resolve_top_level_module(course, module_slug)

    if top_module.children.exists():
        submodule = get_object_or_404(
            Module.objects.select_related('parent'), course=course,
            parent=top_module, slug=unit_slug,
        )
        return _render_module_overview(request, course, submodule)

    unit = get_object_or_404(Unit, module=top_module, slug=unit_slug)
    return _render_course_unit_detail(request, course, top_module, unit)


def course_submodule_unit_detail(
    request, course_slug, parent_slug, module_slug, unit_slug,
):
    """``/courses/<course>/<parent>/<submodule>/<unit>`` (issue #1674):
    a unit's page when its module is a submodule. Entirely new URL
    territory — a two-level course never produces a four-segment path,
    so this never collides with any existing URL."""
    course = get_object_or_404(Course, slug=course_slug, status='published')
    parent_module = _resolve_top_level_module(course, parent_slug)
    submodule = get_object_or_404(
        Module.objects.select_related('parent'), course=course,
        parent=parent_module, slug=module_slug,
    )
    unit = get_object_or_404(Unit, module=submodule, slug=unit_slug)
    return _render_course_unit_detail(request, course, submodule, unit)


def _handle_homework_submission_post(request, unit):
    """Handle a homework submission POST on the unit detail page.

    Issue #1683 tranche 1. Reuses the same URL/view as the GET unit page —
    called from ``_render_course_unit_detail``, so it applies uniformly
    whether the unit's module is top-level or a submodule (issue #1674).
    The absolute requirement: a submitted answer is never lost and never
    silently rejected, so every branch below either saves the submission
    or shows a specific reason it wasn't saved and redirects back to the
    same unit page (never a generic error, never a silent no-op).
    """
    unit_url = unit.get_absolute_url()

    if not request.user.is_authenticated:
        return redirect(f'/accounts/login/?next={unit_url}')

    homework = course_unit_service.resolve_homework_for_unit(unit, request.user)
    if homework is None:
        return redirect(unit_url)

    if not homework.is_accepting_submissions:
        if homework.is_self_paced:
            messages.error(
                request,
                'This homework is closed; this answer was not saved.',
            )
        else:
            messages.error(
                request,
                'The deadline for this homework has passed; this answer was not saved.',
            )
        return redirect(unit_url)

    answers_by_question_id = parse_submission_post(request.POST, homework)
    homework_link = request.POST.get('homework_link', '').strip()
    save_submission(
        homework, request.user,
        homework_link=homework_link,
        answers_by_question_id=answers_by_question_id,
    )
    messages.success(
        request,
        'Your homework was submitted. You can update it anytime before the deadline.',
    )
    return redirect(unit_url)


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
        'kind': unit.kind,
        'is_bonus': unit.is_bonus,
        'module': {
            'id': unit.module.pk,
            'title': unit.module.title,
            'sort_order': unit.module.sort_order,
        },
    }
    if unit.kind == 'event':
        data['session_position'] = unit.session_position

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

    # Issue #768: marking a course unit complete is a real platform
    # action. Flip ``account_activated`` if not already set.
    # Idempotent — only the first completion writes the row.
    from accounts.utils.activation import mark_activated
    mark_activated(user)

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

    # Issue #1674: self-paced cohort membership is implicit, never a
    # student action via this endpoint.
    if cohort.mode == 'self_paced':
        return JsonResponse(
            {'error': 'This cohort is self-paced; membership is automatic.'},
            status=400,
        )

    # Issue #1658: entitlement-mode courses are never self-enroll — this
    # applies unconditionally, including to staff and users who already
    # hold CourseAccess. Cohort membership for these courses comes only
    # from staff admin (CohortEnrollmentInline) or the enrollment
    # integration (#1659), never this student-facing endpoint.
    if course.access_mode == 'entitlement':
        return JsonResponse(
            {
                'error': (
                    'This is an external course; enrollment is '
                    'managed by staff or the enrollment integration.'
                ),
            },
            status=403,
        )

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

    # Record a `course_enroll` activity row for the CRM timeline
    # (issue #853). Defensive — never raises into the enroll path.
    from analytics.activity import record_course_enroll
    record_course_enroll(user, course)

    return JsonResponse({'enrolled': True, 'cohort_id': cohort.pk})


@require_POST
def api_cohort_unenroll(request, slug, cohort_id):
    """POST /api/courses/{slug}/cohorts/{cohort_id}/unenroll - leave a cohort."""
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Authentication required'}, status=401)

    course = get_object_or_404(Course, slug=slug, status='published')
    cohort = get_object_or_404(Cohort, pk=cohort_id, course=course)
    user = request.user

    # Issue #1674: symmetric with api_cohort_enroll.
    if cohort.mode == 'self_paced':
        return JsonResponse(
            {'error': 'This cohort is self-paced; membership is automatic.'},
            status=400,
        )

    # Issue #1658: symmetry with api_cohort_enroll — a user who cannot
    # self-enroll must not be able to self-unenroll either.
    if course.access_mode == 'entitlement':
        return JsonResponse(
            {
                'error': (
                    'This is an external course; enrollment is '
                    'managed by staff or the enrollment integration.'
                ),
            },
            status=403,
        )

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
    """Deprecated: individual course checkout sessions are no longer created locally."""
    from integrations.config import get_config

    return JsonResponse({
        'error': 'Local course checkout is deprecated. Use membership payment links.',
        'portal_url': get_config('STRIPE_CUSTOMER_PORTAL_URL', ''),
    }, status=410)
