"""Studio enrollments management — issue #236.

Lists all course Enrollment rows with optional course filter, and lets
admins manually enroll/unenroll a user (creates rows with
``source='admin'``).
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
def enrollment_list(request):
    """List all enrollments, filterable by course.

    Query params:
        course: optional Course pk to filter on.
        status: 'active' (default) or 'all' to also show unenrolled rows.
    """
    course_id_raw = request.GET.get('course', '')
    status = request.GET.get('status', 'active')

    enrollments = (
        Enrollment.objects
        .select_related('user', 'course')
        .order_by('-enrolled_at')
    )

    course_id = None
    course_filter = None
    if course_id_raw:
        try:
            course_id = int(course_id_raw)
        except ValueError:
            course_id = None
        if course_id is not None:
            enrollments = enrollments.filter(course_id=course_id)
            course_filter = Course.objects.filter(pk=course_id).first()

    if status == 'active':
        enrollments = enrollments.filter(unenrolled_at__isnull=True)

    courses = Course.objects.order_by('title').only('id', 'title')

    return render(request, 'studio/enrollments/list.html', {
        'enrollments': enrollments,
        'courses': courses,
        'selected_course_id': course_id,
        'course_filter': course_filter,
        'status': status,
    })


@staff_required
@require_POST
def enrollment_create(request):
    """Manually enroll a user in a course (source='admin').

    Idempotent: if an active enrollment already exists, surface an info
    message and don't create a duplicate.
    """
    email = request.POST.get('email', '').strip()
    course_id_raw = request.POST.get('course_id', '').strip()

    if not email or not course_id_raw:
        messages.error(request, 'Email and course are required.')
        return redirect('studio_enrollment_list')

    try:
        course_id = int(course_id_raw)
    except ValueError:
        messages.error(request, 'Invalid course.')
        return redirect('studio_enrollment_list')

    course = get_object_or_404(Course, pk=course_id)
    try:
        user = User.objects.get(email=email)
    except User.DoesNotExist:
        messages.error(request, f'No user found with email "{email}".')
        return redirect('studio_enrollment_list')

    existing = Enrollment.objects.filter(
        user=user, course=course, unenrolled_at__isnull=True,
    ).first()
    if existing:
        messages.info(request, f'{email} is already enrolled in "{course.title}".')
        return redirect('studio_enrollment_list')

    Enrollment.objects.create(user=user, course=course, source=SOURCE_ADMIN)
    messages.success(request, f'Enrolled {email} in "{course.title}".')
    return redirect('studio_enrollment_list')


@staff_required
@require_POST
def enrollment_unenroll(request, enrollment_id):
    """Soft-delete (unenroll) an enrollment row."""
    enrollment = get_object_or_404(Enrollment, pk=enrollment_id)
    if enrollment.unenrolled_at is None:
        enrollment.unenrolled_at = timezone.now()
        enrollment.save(update_fields=['unenrolled_at'])
        messages.success(
            request,
            f'Unenrolled {enrollment.user.email} from "{enrollment.course.title}".',
        )
    return redirect('studio_enrollment_list')
