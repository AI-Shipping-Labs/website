"""Optional paginated incoming details on the existing staff course endpoint."""

from django.core.paginator import EmptyPage, Paginator
from django.db.models import Q, Value
from django.db.models.functions import Concat
from django.http import JsonResponse

from api.safety import error_response
from content.models import ProjectSubmission
from content.services.incoming_peer_reviews import incoming_breakdown

PAGE_SIZE = 20


def _submission_query(course, cohort, project, student):
    queryset = ProjectSubmission.objects.filter(course=course).select_related(
        'user', 'course', 'cohort', 'course_project', 'course_project__cohort',
    ).order_by('pk')
    if cohort is not None:
        queryset = queryset.filter(cohort=cohort)
    if project is not None:
        queryset = queryset.filter(course_project=project)
    if student:
        queryset = queryset.annotate(
            student_name=Concat('user__first_name', Value(' '), 'user__last_name'),
        ).filter(Q(user__email__icontains=student) | Q(student_name__icontains=student))
    return queryset


def _staff_submission(submission):
    data = incoming_breakdown(submission, staff=True)
    data['student'] = {
        'id': submission.user_id, 'email': submission.user.email,
        'name': submission.user.get_full_name(),
    }
    data['course'] = submission.course.slug
    data['project'] = None
    if submission.course_project is not None:
        data['project'] = {
            'id': submission.course_project_id,
            'slug': submission.course_project.slug,
            'title': submission.course_project.title,
        }
    data['status'] = submission.status
    return data


def _details_response(queryset, page_number, data):
    paginator = Paginator(queryset, PAGE_SIZE)
    try:
        page = paginator.page(page_number)
    except EmptyPage:
        return error_response('Page not found', 'page_not_found', status=404)
    data['submissions'] = [_staff_submission(submission) for submission in page]
    data['pagination'] = {
        'page': page.number, 'page_size': PAGE_SIZE,
        'total': paginator.count, 'total_pages': paginator.num_pages,
    }
    return JsonResponse(data)


def _add_incoming_details(request, course, cohort, project, data):
    """Keep existing counts-only clients unchanged; opt in with detail filters."""
    raw_id = (request.GET.get('submission') or '').strip()
    student = (request.GET.get('student') or '').strip()
    if not raw_id and not student and request.GET.get('details') != '1':
        return JsonResponse(data)
    try:
        page = int(request.GET.get('page', '1'))
        submission_id = None
        if raw_id:
            submission_id = int(raw_id)
        if page < 1 or (submission_id is not None and submission_id < 1):
            raise ValueError
    except ValueError:
        return error_response('submission and page must be positive integers', 'invalid_query_param', status=422)
    queryset = _submission_query(course, cohort, project, student)
    if submission_id is not None:
        queryset = queryset.filter(pk=submission_id)
        if not queryset.exists():
            return error_response('Submission not found in scope', 'unknown_submission', status=404)
    return _details_response(queryset, page, data)
