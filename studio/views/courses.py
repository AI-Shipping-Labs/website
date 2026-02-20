"""Studio views for course CRUD."""

import json

from django.http import JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.utils.text import slugify

from content.models import Course, Module, Unit
from studio.decorators import staff_required


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
        course.save()
        return redirect('studio_course_edit', course_id=course.pk)

    modules = course.modules.prefetch_related('units').order_by('sort_order')

    return render(request, 'studio/courses/form.html', {
        'course': course,
        'modules': modules,
        'form_action': 'edit',
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
