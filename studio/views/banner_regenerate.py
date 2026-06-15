"""Studio "Regenerate banner" POST endpoints (issue #788).

One view per supported content type. Each is ``@staff_required +
@require_POST``, force-enqueues a banner-generator task (bypassing the
``cover_image_url`` / title-hash short-circuits — the operator clicked
the button on purpose), flashes a success message, and redirects back
to the matching Studio edit page.

Failures inside the enqueued task are swallowed and logged by
:func:`integrations.services.banner_generator.tasks.render_banner_for_content`,
so the view simply flashes the queued-for-render confirmation and lets
the worker do its work.
"""

from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_POST

from content.models import Article, Course, Download, Project, Workshop
from events.models import Event
from integrations.services.banner_generator import is_enabled
from integrations.services.banner_generator.dispatch import enqueue_force
from studio.decorators import staff_required


def _wants_json(request):
    """Return True when the regenerate POST came from the in-place JS handler.

    The progressively-enhanced "Regenerate banner" form fetches the same URL
    with ``X-Requested-With: XMLHttpRequest`` (and an ``Accept: application/
    json`` header) so the JS gets a clean JSON envelope instead of a redirect.
    A plain no-JS form POST carries neither and falls back to the redirect +
    flash path (issue #995).
    """
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return True
    return "application/json" in request.headers.get("Accept", "")


SUCCESS_MESSAGE = (
    'Banner regeneration queued. Refresh in a few seconds to see the new image.'
)
DISABLED_MESSAGE = (
    'Banner generator is not configured. Add the function URL and bearer '
    'token under Studio > Settings > Content Tools first.'
)


def _trigger(request, content_type, record, redirect_response):
    """Enqueue a forced banner render and respond JSON or redirect+flash.

    Centralises the message-bus call so each per-type view stays a one-liner.
    ``record`` is the already-fetched model instance, used only for its
    primary key. The ``enqueue_force`` call happens exactly once per request.

    When the request is an AJAX/fetch call (the progressively-enhanced
    in-place loader, issue #995) the result is returned as a JSON envelope so
    the poller has a clean response; otherwise the no-JS form fallback gets the
    flash message + ``redirect_response``.
    """
    if not is_enabled():
        if _wants_json(request):
            return JsonResponse(
                {'status': 'disabled', 'error': DISABLED_MESSAGE},
                status=422,
            )
        messages.warning(request, DISABLED_MESSAGE)
        return redirect_response
    task_id = enqueue_force(content_type, record.pk)
    if _wants_json(request):
        return JsonResponse({'status': 'queued', 'task_id': task_id})
    messages.success(request, SUCCESS_MESSAGE)
    return redirect_response


@staff_required
@require_POST
def studio_article_regenerate_banner(request, article_id):
    """Force-enqueue a banner render for an article."""
    article = get_object_or_404(Article, pk=article_id)
    return _trigger(
        request, 'article', article,
        redirect('studio_article_edit', article_id=article.pk),
    )


@staff_required
@require_POST
def studio_course_regenerate_banner(request, course_id):
    """Force-enqueue a banner render for a course."""
    course = get_object_or_404(Course, pk=course_id)
    return _trigger(
        request, 'course', course,
        redirect('studio_course_edit', course_id=course.pk),
    )


@staff_required
@require_POST
def studio_project_regenerate_banner(request, project_id):
    """Force-enqueue a banner render for a project."""
    project = get_object_or_404(Project, pk=project_id)
    return _trigger(
        request, 'project', project,
        redirect('studio_project_review', project_id=project.pk),
    )


@staff_required
@require_POST
def studio_download_regenerate_banner(request, download_id):
    """Force-enqueue a banner render for a download."""
    download = get_object_or_404(Download, pk=download_id)
    return _trigger(
        request, 'download', download,
        redirect('studio_download_edit', download_id=download.pk),
    )


@staff_required
@require_POST
def studio_workshop_regenerate_banner(request, workshop_id):
    """Force-enqueue a banner render for a workshop."""
    workshop = get_object_or_404(Workshop, pk=workshop_id)
    return _trigger(
        request, 'workshop', workshop,
        redirect('studio_workshop_edit', workshop_id=workshop.pk),
    )


@staff_required
@require_POST
def studio_event_regenerate_banner(request, event_id):
    """Force-enqueue a banner render for a Studio event (issue #895)."""
    event = get_object_or_404(Event, pk=event_id)
    return _trigger(
        request, 'event', event,
        redirect('studio_event_edit', event_id=event.pk),
    )
