"""Access-controlled recording serving endpoint (issue #1134, Phase A).

Serves an event's recording from the private S3 recordings bucket. The
recordings bucket has full block-public-access on, so the raw
``event.recording_s3_url`` is not directly embeddable. This view enforces
the same tier gating the event/workshop surfaces use (``can_access``)
BEFORE issuing any URL, then ``302``-redirects to a freshly minted,
short-lived presigned S3 ``GetObject`` URL.

The presigned URL is NEVER rendered into HTML — the in-page ``<video>``
``<source>`` points at this stable serving endpoint (a path ending in
``.mp4`` so ``detect_video_source`` classifies it as ``self_hosted``), and
every request (including each ``Range`` request the browser re-issues while
seeking) re-checks access before redirecting to a fresh presigned URL.
"""

from django.contrib.auth.views import redirect_to_login
from django.http import Http404, HttpResponseForbidden, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_GET

from content.access import can_access
from events.models import Event
from integrations.config import get_config
from jobs.tasks.recordings_s3 import build_recording_presigned_url

DEFAULT_PRESIGNED_TTL_SECONDS = 900


def _resolve_ttl_seconds():
    """Read the presigned-URL TTL through the IntegrationSetting framework."""
    raw = get_config(
        'RECORDING_PRESIGNED_URL_TTL_SECONDS',
        DEFAULT_PRESIGNED_TTL_SECONDS,
    )
    try:
        ttl = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_PRESIGNED_TTL_SECONDS
    if ttl <= 0:
        return DEFAULT_PRESIGNED_TTL_SECONDS
    return ttl


@require_GET
def event_recording_stream(request, event_id, slug):
    """Redirect an authorized viewer to a presigned S3 URL for the recording.

    Behavior (mirrors ``content.views.api.download_file`` + the event-detail
    visibility rules):

    - Resolves the event by integer ``event_id``; the ``slug`` is cosmetic
      and 301s to the canonical form on a mismatch (same as ``event_detail``).
    - Draft / retired-duplicate events are hidden from non-staff (404).
    - ``404`` when ``event.recording_s3_url`` is empty (no asset to serve).
    - Enforces ``can_access(request.user, event)`` BEFORE minting any URL:
      an under-tier authenticated user gets ``403``; an anonymous user on a
      gated recording is redirected to login with ``?next=``. Neither denial
      path emits a presigned URL.
    - On success, records a deduped best-effort ``resource_view`` and
      ``302``-redirects to a short-lived presigned ``GetObject`` URL.
    """
    event = get_object_or_404(Event, pk=event_id)

    # Cosmetic-slug mismatch 301s to the canonical stream URL, mirroring
    # event_detail so a stale link still resolves.
    if slug != event.slug:
        return redirect(
            f'/events/{event.pk}/{event.slug}/recording.mp4',
            permanent=True,
        )

    # Draft events are not publicly visible (staff bypass), matching
    # event_detail.
    if event.status == 'draft' and not request.user.is_staff:
        raise Http404

    # A retired duplicate (cancelled AND unpublished) 404s for non-staff,
    # matching event_detail.
    if (
        event.status == 'cancelled'
        and not event.published
        and not request.user.is_staff
    ):
        raise Http404

    # No S3 recording asset — nothing to serve.
    if not event.recording_s3_url:
        raise Http404

    # Access control BEFORE issuing any presigned URL. A bug here leaks
    # paid content, so this is the hard gate.
    if not can_access(request.user, event):
        if not request.user.is_authenticated:
            # Anonymous on a gated recording: send to login with ?next=,
            # the event-detail convention. No presigned URL is issued.
            return redirect_to_login(request.get_full_path())
        # Authenticated but under-tier: 403, no presigned URL.
        return HttpResponseForbidden('Insufficient access level')

    # Authorized viewer. Record a deduped resource_view (best-effort;
    # never raises into the redirect) mirroring workshop_video/download_file.
    if request.user.is_authenticated:
        from analytics.activity import record_resource_view
        record_resource_view(
            request.user,
            object_type='recording',
            object_id=f'event:{event.pk}',
            title=event.title,
            target_url=event.get_absolute_url(),
        )

    presigned_url = build_recording_presigned_url(
        event.recording_s3_url,
        _resolve_ttl_seconds(),
    )
    return HttpResponseRedirect(presigned_url)
