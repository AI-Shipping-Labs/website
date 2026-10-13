"""Pod meeting notes and transcripts (issue #1922).

Shared by the member pages, Studio and the staff API.

- Who adds: current pod members only (staff who are not members read but do
  not add). The author may remove their own artifact; staff delete in Studio.
- Text only: pasted text, or a ``.txt`` / ``.md`` / ``.vtt`` file read as
  UTF-8 into the same body. When both are given the pasted text wins and the
  file is ignored. Size cap ``PODS_ARTIFACT_MAX_KB`` (UTF-8 bytes) for both.
- An artifact may name one meeting of the same pod that already happened:
  ``held``, or ``scheduled`` with a past start. Saving notes for a past
  ``scheduled`` meeting marks it ``held`` in the same transaction, through
  ``record_outcome`` (same fields as ``Mark as held``). Removing an artifact
  never changes a meeting.
- ``PODS_ARTIFACTS_ENABLED`` switches the member side off (the caller 404s);
  Studio and the staff API keep working.
- No markdown anywhere: the body is stored trimmed and rendered escaped.
"""

import os
from zoneinfo import ZoneInfo

from django.db import transaction
from django.utils import timezone

from pods.models import (
    ARTIFACT_KIND_CHOICES,
    ARTIFACT_KIND_NOTES,
    ARTIFACT_SOURCE_PASTED,
    ARTIFACT_SOURCE_UPLOADED,
    ARTIFACT_TITLE_MAX_LENGTH,
    MEETING_STATUS_HELD,
    MEETING_STATUS_SCHEDULED,
    PodArtifact,
    PodMembership,
)
from pods.services import config as pods_config
from pods.services import meetings as mtg
from pods.services.activity import activity_for_pod
from pods.services.meeting_rules import meeting_end, meeting_state
from pods.services.membership import PodError, pod_url
from pods.services.people import display_name

ALLOWED_EXTENSIONS = ('.txt', '.md', '.vtt')
UPLOAD_ACCEPT = ','.join(ALLOWED_EXTENSIONS)
KIND_LABELS = dict(ARTIFACT_KIND_CHOICES)

TIP = 'Tip: record the call and add the transcript or notes here afterwards.'
SECTION_SUBTITLE = 'Pod members and admins can read these.'
PRIVACY_LINE = 'Pod members and admins can read this. Remove anything private before uploading.'
NO_MEETING_LABEL = 'Not tied to a meeting'
EMPTY_TITLE = 'No notes yet'
EMPTY_BODY = 'After a meeting, add the transcript or your notes so the pod can look back at what you agreed.'

MSG_EMPTY = 'Paste the notes or choose a file.'
MSG_BAD_EXTENSION = 'Upload a .txt, .md or .vtt file.'
MSG_NOT_TEXT = 'This file is not plain text. Paste the text instead.'
MSG_NO_TITLE = 'Add a title.'
MSG_TITLE_TOO_LONG = f'Titles can be up to {ARTIFACT_TITLE_MAX_LENGTH} characters.'
MSG_BAD_MEETING = 'Choose a meeting that has already happened.'
MSG_NOT_A_MEMBER = 'Only pod members can add notes.'
MSG_NOT_AUTHOR = 'Only the person who added these notes can remove them.'
MSG_SAVED = 'Notes saved.'
MSG_SAVED_HELD = 'Notes saved and meeting marked as held.'
MSG_REMOVED = 'Notes removed.'
MSG_DELETED = 'Notes deleted.'

CODE_NOT_A_MEMBER = 'not_a_member'
CODE_NOT_AUTHOR = 'not_author'
CODE_INVALID = 'validation_error'


def msg_too_big(max_kb):
    return f'Notes can be up to {max_kb} KB. Trim the text or split it into two.'


class ArtifactError(Exception):
    """A rule violation. ``errors`` maps form fields to member-facing messages."""

    def __init__(self, errors, code=CODE_INVALID):
        self.errors = dict(errors)
        self.code = code
        self.message = next(iter(self.errors.values()), '')
        super().__init__(self.message)


def artifacts_enabled():
    return pods_config.artifacts_enabled()


def max_bytes():
    return pods_config.artifact_max_kb() * 1024


def is_member(pod, user):
    if not getattr(user, 'is_authenticated', False):
        return False
    return PodMembership.objects.filter(pod=pod, user=user).exists()


# --- Meetings ------------------------------------------------------------------

def meeting_has_happened(meeting, now):
    """``held``, or ``scheduled`` with a start in the past."""
    if meeting.status == MEETING_STATUS_HELD:
        return True
    return meeting.status == MEETING_STATUS_SCHEDULED and meeting.starts_at <= now


def compact_date(value, zone_name):
    """``Oct 14`` in ``zone_name`` (``member_compact_date`` vocabulary)."""
    local = value.astimezone(ZoneInfo(zone_name or 'UTC'))
    return f'{local:%b} {local.day}'


def meeting_label(number, starts_at, zone_name):
    """``Meeting 2 - Oct 14`` (the meeting choice label and the title prefill)."""
    prefix = f'Meeting {number}' if number else 'Meeting'
    return f'{prefix} - {compact_date(starts_at, zone_name)}'


def meeting_choices(pod, state, zone_name):
    """``[(meeting, number, label)]`` for this pod's meetings that already happened."""
    return [
        (meeting, state.numbers.get(meeting.pk), meeting_label(state.numbers.get(meeting.pk), meeting.starts_at, zone_name))
        for meeting in state.meetings
        if meeting_has_happened(meeting, state.now)
    ]


def eligible_meeting(pod, raw, state):
    """The meeting of ``pod`` that ``raw`` names, if it already happened, else ``None``."""
    raw = str(raw or '').strip()
    if not raw.isascii() or not raw.isdigit() or len(raw) > 18:
        return None
    meeting = next((m for m in state.meetings if m.pk == int(raw)), None)
    if meeting is None or not meeting_has_happened(meeting, state.now):
        return None
    return meeting


def default_title(meeting, number, zone_name, now):
    """``Meeting 2 - Oct 14`` for a meeting, else ``Notes - <today>``."""
    if meeting is not None:
        return meeting_label(number, meeting.starts_at, zone_name)
    return f'Notes - {compact_date(now, zone_name)}'


# --- Validation ------------------------------------------------------------------

def clean_title(raw):
    title = (raw or '').strip()
    if not title:
        return None, MSG_NO_TITLE
    if len(title) > ARTIFACT_TITLE_MAX_LENGTH:
        return None, MSG_TITLE_TOO_LONG
    return title, ''


def clean_kind(raw):
    raw = (raw or '').strip()
    return raw if raw in KIND_LABELS else ARTIFACT_KIND_NOTES


def read_upload(upload, limit):
    """``(text, error)`` for an uploaded file: extension, size, then UTF-8."""
    name = os.path.basename(getattr(upload, 'name', '') or '')
    if os.path.splitext(name)[1].lower() not in ALLOWED_EXTENSIONS:
        return None, MSG_BAD_EXTENSION
    if (upload.size or 0) > limit:
        return None, msg_too_big(limit // 1024)
    raw = upload.read(limit + 1)
    if len(raw) > limit:
        return None, msg_too_big(limit // 1024)
    try:
        # ``utf-8-sig`` drops a byte-order mark some editors write.
        return raw.decode('utf-8-sig'), ''
    except UnicodeDecodeError:
        return None, MSG_NOT_TEXT


def clean_body(text, upload, limit):
    """``(body, source, filename, error)``. Pasted text wins over a file."""
    pasted = (text or '').strip()
    if pasted:
        if len(pasted.encode('utf-8')) > limit:
            return None, None, '', msg_too_big(limit // 1024)
        return pasted, ARTIFACT_SOURCE_PASTED, '', ''
    if upload is None:
        return None, None, '', MSG_EMPTY
    content, error = read_upload(upload, limit)
    if error:
        return None, None, '', error
    content = content.strip()
    if not content:
        return None, None, '', MSG_EMPTY
    if len(content.encode('utf-8')) > limit:
        return None, None, '', msg_too_big(limit // 1024)
    filename = os.path.basename(upload.name)[:255]
    return content, ARTIFACT_SOURCE_UPLOADED, filename, ''


# --- Writes ------------------------------------------------------------------------

def create_artifact(pod, actor, data, upload=None, *, now=None):
    """Create an artifact by a current member. Returns ``(artifact, marked_held)``.

    ``data`` holds the raw form values ``meeting``, ``kind``, ``title`` and
    ``text``. Every field is checked and all errors raised together.
    """
    now = now or timezone.now()
    if not is_member(pod, actor):
        raise ArtifactError({'form': MSG_NOT_A_MEMBER}, code=CODE_NOT_A_MEMBER)
    errors = {}
    state = meeting_state(pod, now)
    meeting = None
    raw_meeting = str(data.get('meeting') or '').strip()
    if raw_meeting:
        meeting = eligible_meeting(pod, raw_meeting, state)
        if meeting is None:
            errors['meeting'] = MSG_BAD_MEETING
    title, title_error = clean_title(data.get('title'))
    if title_error:
        errors['title'] = title_error
    body, source, filename, body_error = clean_body(data.get('text'), upload, max_bytes())
    if body_error:
        errors['body'] = body_error
    if errors:
        raise ArtifactError(errors)
    try:
        with transaction.atomic():
            artifact, marked_held = _save(pod, actor, meeting, data, title, body, source, filename, now)
    except PodError as exc:
        # The meeting changed between the form check and the lock.
        raise ArtifactError({'meeting': MSG_BAD_MEETING}) from exc
    return artifact, marked_held


def _save(pod, actor, meeting, data, title, body, source, filename, now):
    marked_held = False
    if meeting is not None and meeting.status == MEETING_STATUS_SCHEDULED:
        # Locks the pod and re-checks the meeting under the lock; sets the
        # same fields as ``Mark as held``.
        mtg.record_outcome(meeting, actor, mtg.OUTCOME_HELD, now=now)
        marked_held = True
    artifact = PodArtifact.objects.create(
        pod=pod,
        meeting=meeting,
        kind=clean_kind(data.get('kind')),
        title=title,
        body=body,
        source=source,
        original_filename=filename,
        added_by=actor,
    )
    return artifact, marked_held


def remove_own_artifact(artifact, actor):
    """Hard delete by the author. Never touches the meeting."""
    if not getattr(actor, 'is_authenticated', False) or artifact.added_by_id != actor.pk:
        raise ArtifactError({'form': MSG_NOT_AUTHOR}, code=CODE_NOT_AUTHOR)
    artifact.delete()


def staff_delete(artifact):
    """Hard delete from Studio. Never touches the meeting."""
    artifact.delete()


# --- Presentation ----------------------------------------------------------------

def _url(pod, name, **kwargs):
    """A per-pod notes route under the pod's course or sprint (issue #1921)."""
    return activity_for_pod(pod).action_url(pod, name, **kwargs)


def notes_anchor_url(pod):
    return f'{pod_url(pod)}#notes'


def new_artifact_url(pod, meeting_id=None):
    url = _url(pod, 'pod_artifact_new')
    return f'{url}?meeting={meeting_id}' if meeting_id else url


def artifact_url(pod, artifact):
    return _url(pod, 'pod_artifact_detail', artifact_id=artifact.pk)


def delete_url(pod, artifact):
    return _url(pod, 'pod_artifact_delete', artifact_id=artifact.pk)


def pod_artifacts(pod):
    """Every artifact of ``pod``, newest first, with its author (one query)."""
    return list(PodArtifact.objects.filter(pod=pod).select_related('added_by').order_by('-created_at', '-pk'))


def meta_line(artifact, numbers, viewer, zone_name):
    """``Meeting 2 · Added by Anna K. on Oct 14`` (``(you)`` for the viewer's own).

    No meeting part when the artifact names no meeting or the meeting no
    longer has a number. Display names only, never email.
    """
    who = display_name(artifact.added_by)
    if artifact.added_by_id and viewer is not None and artifact.added_by_id == viewer.pk:
        who = f'{who} (you)'
    added = f'Added by {who} on {compact_date(artifact.created_at, zone_name)}'
    number = numbers.get(artifact.meeting_id) if artifact.meeting_id else None
    return f'Meeting {number} · {added}' if number else added


def member_section(pod, viewer, artifacts, *, numbers, is_member, viewer_zone):
    """Context for the pod page ``Notes and transcripts`` section."""
    return {
        'rows': [{
            'artifact': artifact,
            'kind_label': artifact.get_kind_display(),
            'url': artifact_url(pod, artifact),
            'meta': meta_line(artifact, numbers, viewer, viewer_zone),
        } for artifact in artifacts],
        'can_add': is_member,
        'new_url': new_artifact_url(pod),
        'subtitle': SECTION_SUBTITLE,
        'empty_title': EMPTY_TITLE,
        'empty_body': EMPTY_BODY,
    }


def meeting_lines(pod, artifacts):
    """``{meeting_id: [{'label': 'Notes', 'title': ..., 'url': ...}]}``, newest first."""
    lines = {}
    for artifact in artifacts:
        if artifact.meeting_id:
            lines.setdefault(artifact.meeting_id, []).append({
                'label': artifact.get_kind_display(),
                'title': artifact.title,
                'url': artifact_url(pod, artifact),
            })
    return lines


def add_notes_state(meeting, *, is_member, now):
    """``''``, ``'primary'`` or ``'secondary'`` for a meeting row's ``Add notes``.

    After the scheduled end of a ``scheduled`` meeting it is the row's
    primary action; on a ``held`` meeting it is secondary. Between start and
    end the row is unchanged, and cancelled meetings and proposals never
    offer it. Only pod members add notes.
    """
    if not is_member:
        return ''
    if meeting.status == MEETING_STATUS_HELD:
        return 'secondary'
    if meeting.status == MEETING_STATUS_SCHEDULED and meeting_end(meeting) <= now:
        return 'primary'
    return ''


def tip_meeting_id(state):
    """The next upcoming ``scheduled`` meeting that has not started, or ``None``."""
    upcoming = [m for m in state.meetings if m.status == MEETING_STATUS_SCHEDULED and m.starts_at > state.now]
    return upcoming[0].pk if upcoming else None


def size_label(size_bytes):
    """``1.2 KB`` (never below ``0.1 KB``)."""
    return f'{max(size_bytes / 1024, 0.1):.1f} KB'


def studio_rows(pod, numbers):
    """Rows for the Studio ``Notes and transcripts`` table."""
    return [{
        'artifact': artifact,
        'kind_label': artifact.get_kind_display(),
        'meeting_label': f'Meeting {numbers[artifact.meeting_id]}' if numbers.get(artifact.meeting_id) else '',
        'added_by_name': display_name(artifact.added_by) if artifact.added_by_id else '',
        'added_by_email': artifact.added_by.email if artifact.added_by_id else '',
        'size': size_label(artifact.size_bytes),
        'view_url': artifact_url(pod, artifact),
    } for artifact in pod_artifacts(pod)]


def meeting_numbers(pod, now=None):
    return meeting_state(pod, now).numbers
