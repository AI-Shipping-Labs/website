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
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_POST

from content.models import Article, Course, Download, Project, Workshop
from events.models import Event
from integrations.services.banner_generator import is_enabled
from integrations.services.banner_generator.dispatch import enqueue_force
from studio.decorators import staff_required

SUCCESS_MESSAGE = (
    'Banner regeneration queued. Refresh in a few seconds to see the new image.'
)
DISABLED_MESSAGE = (
    'Banner generator is not configured. Add the function URL and bearer '
    'token under Studio > Settings > Content Tools first.'
)


def _trigger(request, content_type, record):
    """Enqueue a forced banner render and flash the result.

    Centralises the message-bus call so each per-type view stays a
    one-liner. ``record`` is the already-fetched model instance, used
    only for its primary key.
    """
    if not is_enabled():
        messages.warning(request, DISABLED_MESSAGE)
        return
    enqueue_force(content_type, record.pk)
    messages.success(request, SUCCESS_MESSAGE)


@staff_required
@require_POST
def studio_article_regenerate_banner(request, article_id):
    """Force-enqueue a banner render for an article."""
    article = get_object_or_404(Article, pk=article_id)
    _trigger(request, 'article', article)
    return redirect('studio_article_edit', article_id=article.pk)


@staff_required
@require_POST
def studio_course_regenerate_banner(request, course_id):
    """Force-enqueue a banner render for a course."""
    course = get_object_or_404(Course, pk=course_id)
    _trigger(request, 'course', course)
    return redirect('studio_course_edit', course_id=course.pk)


@staff_required
@require_POST
def studio_project_regenerate_banner(request, project_id):
    """Force-enqueue a banner render for a project."""
    project = get_object_or_404(Project, pk=project_id)
    _trigger(request, 'project', project)
    return redirect('studio_project_review', project_id=project.pk)


@staff_required
@require_POST
def studio_download_regenerate_banner(request, download_id):
    """Force-enqueue a banner render for a download."""
    download = get_object_or_404(Download, pk=download_id)
    _trigger(request, 'download', download)
    return redirect('studio_download_edit', download_id=download.pk)


@staff_required
@require_POST
def studio_workshop_regenerate_banner(request, workshop_id):
    """Force-enqueue a banner render for a workshop."""
    workshop = get_object_or_404(Workshop, pk=workshop_id)
    _trigger(request, 'workshop', workshop)
    return redirect('studio_workshop_edit', workshop_id=workshop.pk)


@staff_required
@require_POST
def studio_event_regenerate_banner(request, event_id):
    """Force-enqueue a banner render for a Studio event (issue #895)."""
    event = get_object_or_404(Event, pk=event_id)
    _trigger(request, 'event', event)
    return redirect('studio_event_edit', event_id=event.pk)
