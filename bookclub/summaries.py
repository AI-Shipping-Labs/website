"""Book Club summary helpers (issue #1368).

Organizer-authored summaries live directly on ``Book`` / ``Chapter`` (no
dedicated model). This module holds the thin, unit-testable seams the views,
Studio, and admin API all share:

- ``summary_paragraphs`` — split a plain-text summary into render-ready
  paragraphs (blank-line separated), matching the prototype's ``<p>`` loop.
  There is deliberately NO markdown engine.
- ``resolve_publish_state`` — the single publish/unpublish decision (idempotent
  timestamp set, unpublish clears, empty-publish rejected) shared by Studio and
  the API so the state machine cannot drift between surfaces.
- ``build_notes_digest`` / ``append_notes_digest`` — the pure server-side
  (no-AI) "pull all notes" aggregation that seeds a chapter's draft from every
  member's note.
"""

from __future__ import annotations

from django.utils import timezone

from accounts.utils.display import display_name
from bookclub.models import Note

# Separator between the existing draft and an appended notes digest. Kept simple
# and plain-text so the organizer can edit the draft down by hand.
NOTES_DIGEST_SEPARATOR = '----'


def summary_paragraphs(text):
    """Split a plain-text summary into paragraphs (blank-line separated).

    Returns a list of stripped, non-empty paragraphs. An empty / whitespace
    body yields an empty list so callers can treat "no paragraphs" as "nothing
    to render".
    """
    if not text:
        return []
    chunks = text.replace('\r\n', '\n').split('\n\n')
    return [chunk.strip() for chunk in chunks if chunk.strip()]


def summary_excerpt(text, *, limit=160):
    """First paragraph of a summary, truncated for a one-line index row."""
    paragraphs = summary_paragraphs(text)
    if not paragraphs:
        return ''
    first = paragraphs[0]
    if len(first) <= limit:
        return first
    return first[: limit - 1].rstrip() + '…'


def resolve_publish_state(current_published_at, summary_text, publish):
    """Decide the new ``summary_published_at`` for a publish toggle.

    Returns ``(new_published_at, empty_publish_error)`` where
    ``empty_publish_error`` is ``True`` when the caller asked to publish an
    empty body (the caller rejects the write with a friendly message).

    - ``publish`` truthy + non-empty body: set the timestamp to now only when
      currently unpublished (idempotent — a re-save never bumps it).
    - ``publish`` truthy + empty body: reject (``empty_publish_error = True``).
    - ``publish`` falsy: clear the timestamp (unpublish).
    """
    if publish:
        if not (summary_text or '').strip():
            return current_published_at, True
        if current_published_at is None:
            return timezone.now(), False
        return current_published_at, False
    return None, False


def build_notes_digest(chapter):
    """Aggregate every member's note for ``chapter`` into a plain-text digest.

    Pure server-side aggregation — no AI, no external call. Returns
    ``(digest_text, note_count)``; ``(None, 0)`` when the chapter has no
    non-empty member notes. Diagrams (mermaid source) are not included. Ordered
    oldest-first so the digest reads in the order notes were written.
    """
    notes = list(
        Note.objects.filter(chapter=chapter)
        .select_related('user')
        .order_by('created_at')
    )
    lines = []
    for note in notes:
        body = (note.body or '').strip()
        if not body:
            continue
        lines.append(f'- {display_name(note.user)}: {body}')
    count = len(lines)
    if count == 0:
        return None, 0
    header = (
        f'Member notes — {count} note{"" if count == 1 else "s"}, '
        f'pulled {timezone.localdate().isoformat()}'
    )
    digest = header + '\n' + '\n'.join(lines)
    return digest, count


def append_notes_digest(chapter):
    """Append the member-notes digest to ``chapter.summary`` (non-destructive).

    Returns the number of notes pulled. Never overwrites existing draft text —
    the digest is appended under a ``----`` separator. Never touches
    ``summary_published_at``. A no-op (returns 0, writes nothing) when the
    chapter has no member notes.
    """
    digest, count = build_notes_digest(chapter)
    if count == 0:
        return 0
    existing = (chapter.summary or '').rstrip()
    if existing:
        chapter.summary = (
            f'{existing}\n\n{NOTES_DIGEST_SEPARATOR}\n\n{digest}'
        )
    else:
        chapter.summary = digest
    chapter.save(update_fields=['summary', 'updated_at'])
    return count
