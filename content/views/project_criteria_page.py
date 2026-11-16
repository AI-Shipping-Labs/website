"""The course's Evaluation criteria page, generated from course.yaml."""

from django.http import Http404
from django.shortcuts import get_object_or_404, render

from content.models import Course
from content.services.project_criteria_page import criteria_page_context
from content.services.project_reader import project_reader_context
from content.views.peer_review import _require_course_access


def course_project_criteria(request, slug):
    """Criteria, intro/outro prose and the open-attempt pointer in the reader shell."""
    course = get_object_or_404(Course, slug=slug, status='published')
    guard = _require_course_access(request, course)
    if guard:
        return guard
    page = criteria_page_context(request.user, course)
    if page is None:
        raise Http404
    return render(request, 'content/peer_review/criteria_page.html', {
        **project_reader_context(
            request, course, page['criteria_project'], criteria_current=True,
        ),
        **page,
        'course': course,
    })
