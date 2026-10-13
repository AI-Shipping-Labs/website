"""Member pod notes and transcripts (issue #1922).

Same access rules as the pod page: the course must be in
``PODS_COURSE_SLUGS``, users outside the cohort get 404, archived pods are
404 for members. On top of that every URL is 404 while
``PODS_ARTIFACTS_ENABLED`` is off, and for anyone who is neither a pod member
nor staff (requesters, waitlisted and former members included). Staff who
are not members read but cannot add (403). Only the author removes (403 for
everyone else). An artifact of another pod is a 404.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from pods.models import ARTIFACT_KIND_CHOICES, ARTIFACT_KIND_NOTES, PodArtifact
from pods.services import artifacts as artifact_svc
from pods.services import meetings as mtg
from pods.services import membership as svc
from pods.services.meeting_rules import meeting_state
from pods.services.tab import course_header_context
from pods.views.member import LOGIN_URL, _enabled_course, _pod_for_viewer

DRAFT_MAX_CHARS = 2 * 1024 * 1024 + 1


def _artifact_pod(request, slug, pod_id):
    """``(course, pod, is_member)`` for a member or staff, else 404."""
    course = _enabled_course(slug)
    pod = _pod_for_viewer(request, course, pod_id)
    if not artifact_svc.artifacts_enabled():
        raise Http404('Pod notes are not available')
    member = svc.is_member(pod, request.user)
    if not member and not request.user.is_staff:
        raise Http404('Pod not found')
    return course, pod, member


def _forbidden_for_staff():
    return HttpResponseForbidden(artifact_svc.MSG_NOT_A_MEMBER)


@login_required(login_url=LOGIN_URL)
def artifact_new(request, slug, pod_id):
    """GET/POST ``.../pods/<id>/notes/new`` (optional ``?meeting=<id>``)."""
    course, pod, member = _artifact_pod(request, slug, pod_id)
    if not member:
        return _forbidden_for_staff()
    now = timezone.now()
    zone_name = mtg.zone_for(request.user)
    state = meeting_state(pod, now)
    choices = artifact_svc.meeting_choices(pod, state, zone_name)
    errors = {}
    if request.method == 'POST':
        form = {
            'meeting': (request.POST.get('meeting') or '').strip(),
            'kind': artifact_svc.clean_kind(request.POST.get('kind')),
            'title': request.POST.get('title', ''),
            'text': request.POST.get('text', ''),
        }
        try:
            _artifact, marked_held = artifact_svc.create_artifact(
                pod, request.user, form, request.FILES.get('file'), now=now,
            )
        except artifact_svc.ArtifactError as exc:
            if exc.code == artifact_svc.CODE_NOT_A_MEMBER:
                return _forbidden_for_staff()
            errors = exc.errors
            # Keep the typed values; a file is never kept.
            form['text'] = form['text'][:DRAFT_MAX_CHARS]
        else:
            messages.success(request, artifact_svc.MSG_SAVED_HELD if marked_held else artifact_svc.MSG_SAVED)
            return redirect(artifact_svc.notes_anchor_url(pod))
    else:
        meeting = artifact_svc.eligible_meeting(pod, request.GET.get('meeting'), state)
        form = {
            'meeting': str(meeting.pk) if meeting is not None else '',
            'kind': ARTIFACT_KIND_NOTES,
            'title': artifact_svc.default_title(
                meeting, state.numbers.get(meeting.pk) if meeting is not None else None, zone_name, now,
            ),
            'text': '',
        }
    context = course_header_context(course, request.user, pod.cohort)
    context.update({
        'pod': pod,
        'form': form,
        'errors': errors,
        'meeting_options': [
            {'value': str(meeting.pk), 'label': label} for meeting, _number, label in reversed(choices)
        ],
        'no_meeting_label': artifact_svc.NO_MEETING_LABEL,
        'kind_choices': ARTIFACT_KIND_CHOICES,
        'upload_accept': artifact_svc.UPLOAD_ACCEPT,
        'max_kb': artifact_svc.max_bytes() // 1024,
        'privacy_line': artifact_svc.PRIVACY_LINE,
        'cancel_url': artifact_svc.notes_anchor_url(pod),
    })
    return render(request, 'pods/artifact_form.html', context, status=400 if errors else 200)


def _artifact(pod, artifact_id):
    return get_object_or_404(PodArtifact.objects.select_related('added_by'), pk=artifact_id, pod=pod)


@login_required(login_url=LOGIN_URL)
def artifact_detail(request, slug, pod_id, artifact_id):
    """GET ``.../pods/<id>/notes/<artifact_id>`` -- read one artifact."""
    course, pod, _member = _artifact_pod(request, slug, pod_id)
    artifact = _artifact(pod, artifact_id)
    zone_name = mtg.zone_for(request.user)
    numbers = meeting_state(pod).numbers
    context = course_header_context(course, request.user, pod.cohort)
    context.update({
        'pod': pod,
        'artifact': artifact,
        'kind_label': artifact.get_kind_display(),
        'meta': artifact_svc.meta_line(artifact, numbers, request.user, zone_name),
        'back_url': artifact_svc.notes_anchor_url(pod),
        'can_remove': artifact.added_by_id == request.user.pk,
        'delete_url': artifact_svc.delete_url(pod, artifact),
    })
    return render(request, 'pods/artifact_detail.html', context)


@require_POST
@login_required(login_url=LOGIN_URL)
def artifact_delete(request, slug, pod_id, artifact_id):
    """POST ``.../pods/<id>/notes/<artifact_id>/delete`` -- the author removes it."""
    _course, pod, _member = _artifact_pod(request, slug, pod_id)
    artifact = _artifact(pod, artifact_id)
    try:
        artifact_svc.remove_own_artifact(artifact, request.user)
    except artifact_svc.ArtifactError as exc:
        return HttpResponseForbidden(exc.message)
    messages.success(request, artifact_svc.MSG_REMOVED)
    return redirect(artifact_svc.notes_anchor_url(pod))
