"""Studio views for course CRUD and access management."""

import json
import logging

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.http import JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from content.models import Course, Module, Unit, CourseAccess
from studio.decorators import staff_required

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
def course_create(request):
    """Create a new course."""
    if request.method == 'POST':
        title = request.POST.get('title', '').strip()
        slug = request.POST.get('slug', '').strip() or slugify(title)
        description = request.POST.get('description', '')
        cover_image_url = request.POST.get('cover_image_url', '')
        instructor_name = request.POST.get('instructor_name', '')
        instructor_bio = request.POST.get('instructor_bio', '')
        status = request.POST.get('status', 'draft')
        is_free = request.POST.get('is_free') == 'on'
        required_level = int(request.POST.get('required_level', 0))
        discussion_url = request.POST.get('discussion_url', '')
        tags_raw = request.POST.get('tags', '')
        tags = [t.strip() for t in tags_raw.split(',') if t.strip()] if tags_raw else []
        individual_price_raw = request.POST.get('individual_price_eur', '').strip()
        individual_price_eur = None
        if individual_price_raw:
            from decimal import Decimal, InvalidOperation
            try:
                individual_price_eur = Decimal(individual_price_raw)
            except InvalidOperation:
                pass

        course = Course.objects.create(
            title=title,
            slug=slug,
            description=description,
            cover_image_url=cover_image_url,
            instructor_name=instructor_name,
            instructor_bio=instructor_bio,
            status=status,
            is_free=is_free,
            required_level=required_level,
            discussion_url=discussion_url,
            tags=tags,
            individual_price_eur=individual_price_eur,
        )
        return redirect('studio_course_edit', course_id=course.pk)

    return render(request, 'studio/courses/form.html', {
        'course': None,
        'form_action': 'create',
    })


@staff_required
def course_edit(request, course_id):
    """Edit an existing course with nested module/unit editors."""
    course = get_object_or_404(Course, pk=course_id)

    if request.method == 'POST':
        course.title = request.POST.get('title', '').strip()
        course.slug = request.POST.get('slug', '').strip() or slugify(course.title)
        course.description = request.POST.get('description', '')
        course.cover_image_url = request.POST.get('cover_image_url', '')
        course.instructor_name = request.POST.get('instructor_name', '')
        course.instructor_bio = request.POST.get('instructor_bio', '')
        course.status = request.POST.get('status', 'draft')
        course.is_free = request.POST.get('is_free') == 'on'
        course.required_level = int(request.POST.get('required_level', 0))
        course.discussion_url = request.POST.get('discussion_url', '')
        tags_raw = request.POST.get('tags', '')
        course.tags = [t.strip() for t in tags_raw.split(',') if t.strip()] if tags_raw else []
        individual_price_raw = request.POST.get('individual_price_eur', '').strip()
        if individual_price_raw:
            from decimal import Decimal, InvalidOperation
            try:
                course.individual_price_eur = Decimal(individual_price_raw)
            except InvalidOperation:
                pass
        else:
            course.individual_price_eur = None
        course.save()
        return redirect('studio_course_edit', course_id=course.pk)

    modules = course.modules.prefetch_related('units').order_by('sort_order')

    return render(request, 'studio/courses/form.html', {
        'course': course,
        'modules': modules,
        'form_action': 'edit',
        'notify_url': reverse('studio_course_notify', kwargs={'course_id': course.pk}),
        'announce_url': reverse('studio_course_announce_slack', kwargs={'course_id': course.pk}),
        'create_stripe_product_url': reverse('studio_course_create_stripe_product', kwargs={'course_id': course.pk}),
    })


@staff_required
def module_create(request, course_id):
    """Create a module for a course (AJAX or form POST)."""
    course = get_object_or_404(Course, pk=course_id)

    if request.method == 'POST':
        title = request.POST.get('title', '').strip()
        max_order = course.modules.order_by('-sort_order').values_list(
            'sort_order', flat=True,
        ).first() or 0
        Module.objects.create(
            course=course,
            title=title,
            sort_order=max_order + 1,
        )
    return redirect('studio_course_edit', course_id=course.pk)


@staff_required
def unit_create(request, module_id):
    """Create a unit within a module."""
    module = get_object_or_404(Module, pk=module_id)

    if request.method == 'POST':
        title = request.POST.get('title', '').strip()
        max_order = module.units.order_by('-sort_order').values_list(
            'sort_order', flat=True,
        ).first() or 0
        Unit.objects.create(
            module=module,
            title=title,
            sort_order=max_order + 1,
        )
    return redirect('studio_course_edit', course_id=module.course.pk)


@staff_required
def unit_edit(request, unit_id):
    """Edit a unit."""
    unit = get_object_or_404(Unit, pk=unit_id)

    if request.method == 'POST':
        unit.title = request.POST.get('title', '').strip()
        unit.video_url = request.POST.get('video_url', '')
        unit.body = request.POST.get('body', '')
        unit.homework = request.POST.get('homework', '')
        unit.is_preview = request.POST.get('is_preview') == 'on'
        unit.save()
        return redirect('studio_course_edit', course_id=unit.module.course.pk)

    return render(request, 'studio/courses/unit_form.html', {
        'unit': unit,
        'course': unit.module.course,
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
