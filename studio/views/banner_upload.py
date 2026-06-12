"""Studio "Upload custom banner" + "Remove custom banner" endpoints (#931).

One upload view and one remove view per supported content type. Each is
``@staff_required + @require_POST`` and mirrors the structure of
:mod:`studio.views.banner_regenerate`.

Upload: validates the ``banner_image`` file, stores it on the content CDN
bucket under ``custom-banners/<type>/<id>-<uuid>.<ext>``, persists the CDN
URL to the record's ``custom_banner_url`` via ``.update()`` (no ``save()``
side-effects, consistent with the render task), best-effort deletes the
previously uploaded object, flashes success, and redirects back to the edit
page. On validation/config failure it flashes a specific error and makes no
DB change.

Remove: clears ``custom_banner_url`` and best-effort deletes the object so
the operator falls back to the generated banner.

All S3 work lives in
:mod:`integrations.services.banner_generator.custom_upload`; these views are
thin HTTP wrappers.
"""

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_POST

from content.models import Article, Course, Download, Project, Workshop
from events.models import Event, EventSeries
from integrations.services.banner_generator.content_models import model_for
from integrations.services.banner_generator.custom_upload import (
    CustomBannerUploadError,
    is_upload_enabled,
    safe_delete_custom_banner,
    upload_custom_banner,
)
from studio.decorators import staff_required

DISABLED_MESSAGE = (
    'Custom banner upload is not configured. Add the content CDN base and '
    'S3 bucket under Studio > Settings > Content Tools first.'
)
UPLOAD_SUCCESS_MESSAGE = (
    'Custom banner uploaded. It now overrides the generated banner.'
)
REMOVE_SUCCESS_MESSAGE = (
    'Custom banner removed. Showing the generated banner.'
)


def _do_upload(request, content_type, record):
    """Validate + store the uploaded banner and persist its URL.

    Flashes a config warning and no-ops when uploads are disabled; flashes
    the validation/storage error on :class:`CustomBannerUploadError`;
    otherwise persists ``custom_banner_url``, deletes the previous object,
    and flashes success. Returns nothing — the per-type view redirects.
    """
    if not is_upload_enabled():
        messages.warning(request, DISABLED_MESSAGE)
        return

    upload = request.FILES.get('banner_image')
    try:
        url = upload_custom_banner(content_type, record.pk, upload)
    except CustomBannerUploadError as exc:
        messages.error(request, exc.message)
        return

    previous = getattr(record, 'custom_banner_url', '') or ''
    model = model_for(content_type)
    model.objects.filter(pk=record.pk).update(custom_banner_url=url)
    if previous and previous != url:
        safe_delete_custom_banner(content_type, previous)
    messages.success(request, UPLOAD_SUCCESS_MESSAGE)


def _do_remove(request, content_type, record):
    """Clear ``custom_banner_url`` and best-effort delete the object."""
    previous = getattr(record, 'custom_banner_url', '') or ''
    if previous:
        model = model_for(content_type)
        model.objects.filter(pk=record.pk).update(custom_banner_url='')
        safe_delete_custom_banner(content_type, previous)
    messages.success(request, REMOVE_SUCCESS_MESSAGE)


# --------------------------------------------------------------------------
# Article
# --------------------------------------------------------------------------


@staff_required
@require_POST
def studio_article_upload_banner(request, article_id):
    """Upload a custom banner for an article."""
    article = get_object_or_404(Article, pk=article_id)
    _do_upload(request, 'article', article)
    return redirect('studio_article_edit', article_id=article.pk)


@staff_required
@require_POST
def studio_article_remove_banner(request, article_id):
    """Remove the custom banner for an article."""
    article = get_object_or_404(Article, pk=article_id)
    _do_remove(request, 'article', article)
    return redirect('studio_article_edit', article_id=article.pk)


# --------------------------------------------------------------------------
# Course
# --------------------------------------------------------------------------


@staff_required
@require_POST
def studio_course_upload_banner(request, course_id):
    """Upload a custom banner for a course."""
    course = get_object_or_404(Course, pk=course_id)
    _do_upload(request, 'course', course)
    return redirect('studio_course_edit', course_id=course.pk)


@staff_required
@require_POST
def studio_course_remove_banner(request, course_id):
    """Remove the custom banner for a course."""
    course = get_object_or_404(Course, pk=course_id)
    _do_remove(request, 'course', course)
    return redirect('studio_course_edit', course_id=course.pk)


# --------------------------------------------------------------------------
# Project
# --------------------------------------------------------------------------


@staff_required
@require_POST
def studio_project_upload_banner(request, project_id):
    """Upload a custom banner for a project."""
    project = get_object_or_404(Project, pk=project_id)
    _do_upload(request, 'project', project)
    return redirect('studio_project_review', project_id=project.pk)


@staff_required
@require_POST
def studio_project_remove_banner(request, project_id):
    """Remove the custom banner for a project."""
    project = get_object_or_404(Project, pk=project_id)
    _do_remove(request, 'project', project)
    return redirect('studio_project_review', project_id=project.pk)


# --------------------------------------------------------------------------
# Download
# --------------------------------------------------------------------------


@staff_required
@require_POST
def studio_download_upload_banner(request, download_id):
    """Upload a custom banner for a download."""
    download = get_object_or_404(Download, pk=download_id)
    _do_upload(request, 'download', download)
    return redirect('studio_download_edit', download_id=download.pk)


@staff_required
@require_POST
def studio_download_remove_banner(request, download_id):
    """Remove the custom banner for a download."""
    download = get_object_or_404(Download, pk=download_id)
    _do_remove(request, 'download', download)
    return redirect('studio_download_edit', download_id=download.pk)


# --------------------------------------------------------------------------
# Workshop
# --------------------------------------------------------------------------


@staff_required
@require_POST
def studio_workshop_upload_banner(request, workshop_id):
    """Upload a custom banner for a workshop."""
    workshop = get_object_or_404(Workshop, pk=workshop_id)
    _do_upload(request, 'workshop', workshop)
    return redirect('studio_workshop_edit', workshop_id=workshop.pk)


@staff_required
@require_POST
def studio_workshop_remove_banner(request, workshop_id):
    """Remove the custom banner for a workshop."""
    workshop = get_object_or_404(Workshop, pk=workshop_id)
    _do_remove(request, 'workshop', workshop)
    return redirect('studio_workshop_edit', workshop_id=workshop.pk)


# --------------------------------------------------------------------------
# Event
# --------------------------------------------------------------------------


@staff_required
@require_POST
def studio_event_upload_banner(request, event_id):
    """Upload a custom banner for a Studio event."""
    event = get_object_or_404(Event, pk=event_id)
    _do_upload(request, 'event', event)
    return redirect('studio_event_edit', event_id=event.pk)


@staff_required
@require_POST
def studio_event_remove_banner(request, event_id):
    """Remove the custom banner for a Studio event."""
    event = get_object_or_404(Event, pk=event_id)
    _do_remove(request, 'event', event)
    return redirect('studio_event_edit', event_id=event.pk)


# --------------------------------------------------------------------------
# Event series
# --------------------------------------------------------------------------


@staff_required
@require_POST
def studio_event_series_upload_banner(request, series_id):
    """Upload a custom banner for an event series."""
    series = get_object_or_404(EventSeries, pk=series_id)
    _do_upload(request, 'event_series', series)
    return redirect('studio_event_series_detail', series_id=series.pk)


@staff_required
@require_POST
def studio_event_series_remove_banner(request, series_id):
    """Remove the custom banner for an event series."""
    series = get_object_or_404(EventSeries, pk=series_id)
    _do_remove(request, 'event_series', series)
    return redirect('studio_event_series_detail', series_id=series.pk)
