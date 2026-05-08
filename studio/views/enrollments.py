"""Studio enrollments management — issue #236, refactored for #293.

Each Studio course now exposes its enrollments under
``/studio/courses/<course_id>/enrollments/`` instead of a top-level tab. The
queryset, status filter, and admin-source manual-enroll behaviour are
unchanged; only the surface (URL + scoping) moves.
"""

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from content.models import Course, Enrollment
from content.models.enrollment import SOURCE_ADMIN
from studio.decorators import staff_required

User = get_user_model()


@staff_required
def enrollment_list(request, course_id):
    """List enrollments for a single course.

    The course is taken from the URL path (``course_id``) — the previous
    ``?course=`` query-param branch is gone. The ``status`` filter still
    accepts ``active`` (default) and ``all``.
    """
    course = get_object_or_404(Course, pk=course_id)
    status = request.GET.get('status', 'active')

    enrollments = (
        Enrollment.objects
        .filter(course=course)
        .select_related('user', 'course')
        .order_by('-enrolled_at')
    )

    if status == 'active':
        enrollments = enrollments.filter(unenrolled_at__isnull=True)

    return render(request, 'studio/courses/enrollments_list.html', {
        'course': course,
        'enrollments': enrollments,
        'status': status,
    })


@staff_required
@require_POST
def enrollment_create(request, course_id):
    """Manually enroll a user in this course (source='admin').

    The course is taken from the URL path. Either ``user_id`` (autocomplete
    selection) or ``email`` (keyboard-fast path) comes from POST.
    ``user_id`` wins when both are present. Idempotent: if an active
    enrollment already exists, surface an info message and don't create a
    duplicate.
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
            return redirect('studio_course_enrollment_list', course_id=course.pk)
    elif email:
        try:
            user = User.objects.get(email__iexact=email)
        except User.DoesNotExist:
            messages.error(request, f'No user found with email "{email}".')
            return redirect('studio_course_enrollment_list', course_id=course.pk)
    else:
        messages.error(request, 'Email is required.')
        return redirect('studio_course_enrollment_list', course_id=course.pk)

    existing = Enrollment.objects.filter(
        user=user, course=course, unenrolled_at__isnull=True,
    ).first()
    if existing:
        messages.info(request, f'{user.email} is already enrolled in "{course.title}".')
        return redirect('studio_course_enrollment_list', course_id=course.pk)

    Enrollment.objects.create(user=user, course=course, source=SOURCE_ADMIN)
    messages.success(request, f'Enrolled {user.email} in "{course.title}".')
    return redirect('studio_course_enrollment_list', course_id=course.pk)


@staff_required
@require_POST
def enrollment_unenroll(request, course_id, enrollment_id):
    """Soft-delete (unenroll) an enrollment row.

    Cross-course safety: the enrollment must belong to the course in the URL.
    A mismatched ``enrollment_id`` returns 404 instead of unenrolling someone
    on the wrong course.
    """
    enrollment = get_object_or_404(
        Enrollment, pk=enrollment_id, course_id=course_id,
    )
    if enrollment.unenrolled_at is None:
        enrollment.unenrolled_at = timezone.now()
        enrollment.save(update_fields=['unenrolled_at'])
        messages.success(
            request,
            f'Unenrolled {enrollment.user.email} from "{enrollment.course.title}".',
        )
    return redirect('studio_course_enrollment_list', course_id=course_id)
