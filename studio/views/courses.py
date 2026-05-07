"""Studio views for course management and access management."""

import json
import logging

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from content.models import Course, CourseAccess, Module, Unit
from studio.decorators import staff_required
from studio.utils import get_github_edit_url, is_synced
from studio.views.form_helpers import (
    parse_comma_separated_tags,
    reject_synced_content_post,
)

User = get_user_model()

logger = logging.getLogger(__name__)


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

    return render(request, 'studio/courses/list.html', {
        'courses': courses,
        'status_filter': status_filter,
        'search': search,
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

    modules = course.modules.prefetch_related('units').order_by('sort_order')

    return render(request, 'studio/courses/form.html', {
        'course': course,
        'modules': modules,
        'form_action': 'edit',
        'is_synced': synced,
        'github_edit_url': get_github_edit_url(course),
        'notify_url': reverse('studio_course_notify', kwargs={'course_id': course.pk}),
        'announce_url': reverse('studio_course_announce_slack', kwargs={'course_id': course.pk}),
        'create_stripe_product_url': reverse('studio_course_create_stripe_product', kwargs={'course_id': course.pk}),
    })


@staff_required
def module_create(request, course_id):
    """Create a module for a course (AJAX or form POST)."""
    course = get_object_or_404(Course, pk=course_id)

    if is_synced(course):
        return HttpResponseForbidden(
            'This content is managed in GitHub. Edit it there.'
        )

    if request.method == 'POST':
        title = request.POST.get('title', '').strip()
        max_order = course.modules.order_by('-sort_order').values_list(
            'sort_order', flat=True,
        ).first() or 0
        from django.utils.text import slugify
        Module.objects.create(
            course=course,
            title=title,
            slug=slugify(title),
            sort_order=max_order + 1,
        )
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
        from django.utils.text import slugify
        Unit.objects.create(
            module=module,
            title=title,
            slug=slugify(title),
            sort_order=max_order + 1,
        )
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
    """Reorder modules for a course (JSON API endpoint)."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    for item in data:
        Module.objects.filter(pk=item['id']).update(sort_order=item['sort_order'])

    return JsonResponse({'status': 'ok'})


@staff_required
@require_POST
def course_create_stripe_product(request, course_id):
    """Create a Stripe product and price for individual course purchase."""
    course = get_object_or_404(Course, pk=course_id)

    if course.stripe_product_id:
        return JsonResponse({'error': 'Course already has a Stripe product'}, status=400)

    if not course.individual_price_eur:
        return JsonResponse({'error': 'Set individual_price_eur before creating a Stripe product'}, status=400)

    try:
        from payments.services import _get_stripe_client

        client = _get_stripe_client()

        # Create Stripe product
        product = client.products.create(params={
            'name': course.title,
            'description': course.description[:500] if course.description else '',
            'metadata': {
                'course_id': str(course.pk),
                'course_slug': course.slug,
            },
        })

        # Create Stripe price (one-time, in EUR)
        price = client.prices.create(params={
            'product': product.id,
            'unit_amount': int(course.individual_price_eur * 100),
            'currency': 'eur',
        })

        course.stripe_product_id = product.id
        course.stripe_price_id = price.id
        course.save(update_fields=['stripe_product_id', 'stripe_price_id'])

        return JsonResponse({
            'product_id': product.id,
            'price_id': price.id,
        })
    except Exception as e:
        logger.exception('Failed to create Stripe product for course %s', course.pk)
        return JsonResponse({'error': str(e)}, status=500)


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
    """Grant a user access to a course by email."""
    course = get_object_or_404(Course, pk=course_id)
    email = request.POST.get('email', '').strip()

    if not email:
        messages.error(request, 'Please provide an email address.')
        return redirect('studio_course_access_list', course_id=course.pk)

    try:
        user = User.objects.get(email=email)
    except User.DoesNotExist:
        messages.error(request, f'No user found with email "{email}".')
        return redirect('studio_course_access_list', course_id=course.pk)

    # Check if the user already has access
    existing = CourseAccess.objects.filter(user=user, course=course).first()
    if existing:
        messages.info(
            request,
            f'{email} already has {existing.access_type} access to this course.',
        )
        return redirect('studio_course_access_list', course_id=course.pk)

    CourseAccess.objects.create(
        user=user,
        course=course,
        access_type='granted',
        granted_by=request.user,
    )
    messages.success(request, f'Access granted to {email}.')
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
