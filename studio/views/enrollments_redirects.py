"""Redirect shims from the old ``/studio/enrollments/`` URLs (issue #293).

Enrollments moved under ``/studio/courses/<course_id>/enrollments/``. These
shims keep any existing bookmarks / external links / in-flight POSTs working:

- The list URL uses 301 (permanent) so browsers cache the redirect.
- The create / unenroll POST URLs use 307 (temporary) so the request method
  and body are preserved by the user-agent (RFC 7231 §6.4.7). 301 may be
  silently downgraded to GET by some clients.
"""

from urllib.parse import urlencode

from django.http import HttpResponsePermanentRedirect, HttpResponseRedirect
from django.shortcuts import redirect
from django.urls import reverse

from content.models import Course, Enrollment


class HttpResponseTemporaryRedirect(HttpResponseRedirect):
    """RFC 7231 §6.4.7: 307 preserves method and body across the redirect."""

    status_code = 307


def _course_list_redirect():
    """Permanent fallback when no usable course can be inferred."""
    return HttpResponsePermanentRedirect(reverse('studio_course_list'))


def enrollment_list_redirect(request):
    """``GET /studio/enrollments/`` -> course-scoped page or course list.

    - ``?course=<id>`` resolving to a real course: 301 to that course's
      enrollments page, preserving ``?status=`` if present.
    - Missing or invalid ``course``: 301 to ``/studio/courses/`` so the
      operator can pick one (the global "all enrollments" view is gone).
    """
    course_id_raw = request.GET.get('course', '').strip()
    if not course_id_raw:
        return _course_list_redirect()

    try:
        course_id = int(course_id_raw)
    except ValueError:
        return _course_list_redirect()

    if not Course.objects.filter(pk=course_id).exists():
        return _course_list_redirect()

    target = reverse('studio_course_enrollment_list', kwargs={'course_id': course_id})
    status = request.GET.get('status', '').strip()
    if status:
        target = f'{target}?{urlencode({"status": status})}'
    return HttpResponsePermanentRedirect(target)


def enrollment_create_redirect(request):
    """``GET/POST /studio/enrollments/create`` -> course-scoped create.

    Reads ``course_id`` from POST (or GET, for paranoia). If valid, 307s to
    the course-scoped create URL so the original POST body is replayed
    against the new endpoint. Without a usable course id, falls back to
    ``/studio/courses/`` (302).
    """
    course_id_raw = (
        request.POST.get('course_id', '')
        or request.GET.get('course_id', '')
    ).strip()
    if not course_id_raw:
        return redirect('studio_course_list')

    try:
        course_id = int(course_id_raw)
    except ValueError:
        return redirect('studio_course_list')

    if not Course.objects.filter(pk=course_id).exists():
        return redirect('studio_course_list')

    target = reverse(
        'studio_course_enrollment_create', kwargs={'course_id': course_id},
    )
    return HttpResponseTemporaryRedirect(target)


def enrollment_unenroll_redirect(request, enrollment_id):
    """``POST /studio/enrollments/<id>/unenroll`` -> course-scoped unenroll.

    Looks up the enrollment to discover its course id, then 307s to
    ``/studio/courses/<course_id>/enrollments/<enrollment_id>/unenroll``
    so the POST body and method are preserved.
    """
    enrollment = Enrollment.objects.filter(pk=enrollment_id).first()
    if enrollment is None:
        return redirect('studio_course_list')

    target = reverse(
        'studio_course_enrollment_unenroll',
        kwargs={'course_id': enrollment.course_id, 'enrollment_id': enrollment.pk},
    )
    return HttpResponseTemporaryRedirect(target)
