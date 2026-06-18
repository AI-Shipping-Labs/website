"""Assemble a member's CRM profile context for plan generation (issue #883).

The plan-create flow (``studio_plan_create``) pre-fills the form for a
specific member via ``?user=<pk>``. This service gathers everything staff
need to start a plan from the member's stated goals/background instead of a
blank form:

- the member's submitted/draft onboarding answers (reusing the #871
  ``flatten_response_answers`` helper -- no duplicated answer-type branching),
- the ``persona`` / ``persona_ref`` / ``summary`` / ``next_steps`` snapshot
  fields from their :class:`crm.models.CRMRecord` (when one exists),
- their most recent internal :class:`plans.models.InterviewNote` rows.

It is read-only: it never mutates onboarding data, notes, or the CRM record.
The assembled context also exposes a ``copy_text`` block so staff can paste
the profile into an LLM prompt or the plan body (manual-assist interpretation
of the issue's open question -- no automatic generation in this phase).
"""

from crm.services.activity_context import (
    PROFILE_ACTIVITY_LIMIT,
    build_activity_context,
)
from plans.models import InterviewNote
from questionnaires.onboarding import (
    flatten_response_answers,
    get_onboarding_response,
)

# How many recent internal notes to surface in the profile panel. The panel
# is a quick-reference, not the full note history (that lives on the CRM
# detail page), so we cap it.
RECENT_INTERNAL_NOTE_LIMIT = 5


def _persona_label(record):
    """Best human-readable persona label for a CRM record.

    Prefers the structured ``persona_ref`` (issue #801) display label when
    set, falling back to the free-text ``persona`` field which remains the
    source of truth. Returns ``''`` when neither is set.
    """
    if record is None:
        return ''
    if record.persona_ref_id is not None and record.persona_ref is not None:
        return record.persona_ref.display_label
    return (record.persona or '').strip()


def build_member_profile_context(member):
    """Return the read-only profile context for ``member``.

    Always returns a dict with every key populated so the template never
    has to guard against missing keys. A member with no onboarding response
    and/or no CRM record gets the available subset plus explicit empty
    markers (``has_onboarding`` / ``has_crm_record`` / ``has_notes`` flags),
    never a broken or blank panel.

    Keys:

    - ``member``: the user.
    - ``has_onboarding``: ``True`` when an onboarding response exists.
    - ``onboarding_submitted``: ``True`` when that response is submitted.
    - ``onboarding_answers``: the flattened Q&A list (``[]`` when none).
    - ``has_crm_record``: ``True`` when a ``CRMRecord`` exists.
    - ``persona``: best persona label (``''`` when none).
    - ``summary`` / ``next_steps``: CRM snapshot fields (``''`` when none).
    - ``has_notes``: ``True`` when recent internal notes exist.
    - ``recent_notes``: up to ``RECENT_INTERNAL_NOTE_LIMIT`` internal notes.
    - ``has_recent_activity``: ``True`` when recent activity rows exist.
    - ``recent_activity``: up to ``PROFILE_ACTIVITY_LIMIT`` serialized rows.
    - ``recent_activity_total`` / ``recent_activity_has_more``: prompt
      provenance for the capped recent-activity slice.
    - ``copy_text``: a plain-text rendering of the whole profile.
    """
    onboarding_response = get_onboarding_response(member)
    if onboarding_response is not None:
        onboarding_answers = flatten_response_answers(onboarding_response)
        onboarding_submitted = onboarding_response.status == 'submitted'
    else:
        onboarding_answers = []
        onboarding_submitted = False

    # ``crm_record`` is a OneToOne reverse accessor; it raises
    # ``CRMRecord.DoesNotExist`` when the member is not tracked.
    record = getattr(member, 'crm_record', None)

    recent_notes = list(
        InterviewNote.objects
        .filter(member=member)
        .internal()
        .select_related('created_by')
        .order_by('-created_at')[:RECENT_INTERNAL_NOTE_LIMIT]
    )
    activity_context = build_activity_context(
        member,
        limit=PROFILE_ACTIVITY_LIMIT,
    )

    context = {
        'member': member,
        'has_onboarding': onboarding_response is not None,
        'onboarding_submitted': onboarding_submitted,
        'onboarding_answers': onboarding_answers,
        'has_crm_record': record is not None,
        'persona': _persona_label(record),
        'summary': (record.summary or '').strip() if record else '',
        'next_steps': (record.next_steps or '').strip() if record else '',
        'has_notes': bool(recent_notes),
        'recent_notes': recent_notes,
        'has_recent_activity': bool(activity_context['activities']),
        'recent_activity': activity_context['activities'],
        'recent_activity_total': activity_context['activity_total'],
        'recent_activity_limit': activity_context['activity_limit'],
        'recent_activity_has_more': activity_context['activity_has_more'],
    }
    context['copy_text'] = _render_copy_text(context)
    return context


def _render_copy_text(context):
    """Render the profile context as a plain-text block for copy/paste.

    Empty sections are omitted entirely so the pasted prompt stays tight.
    Returns ``''`` when there is nothing to copy (no onboarding, no CRM
    fields, no notes) so the template can hide the copy affordance.
    """
    member = context['member']
    name = (member.get_full_name() or '').strip() or member.email
    lines = [f'Member: {name} ({member.email})']

    persona = context['persona']
    if persona:
        lines.append(f'Persona: {persona}')

    if context['summary']:
        lines.append('')
        lines.append('Summary:')
        lines.append(context['summary'])

    if context['next_steps']:
        lines.append('')
        lines.append('Next steps:')
        lines.append(context['next_steps'])

    answered = [
        row for row in context['onboarding_answers'] if row['answered']
    ]
    if answered:
        lines.append('')
        lines.append('Onboarding answers:')
        for row in answered:
            lines.append(f'- {row["prompt"]}: {row["display"]}')

    if context['recent_notes']:
        lines.append('')
        lines.append('Recent internal notes:')
        for note in context['recent_notes']:
            body = (note.body or '').strip()
            if body:
                lines.append(f'- {body}')

    if context['recent_activity']:
        lines.append('')
        lines.append('Recent activity:')
        for activity in context['recent_activity']:
            occurred = activity['occurred_at'].date().isoformat()
            category = activity['category_label']
            type_label = activity['type_label']
            label = activity['label']
            lines.append(f'- {occurred} [{category}] {type_label}: {label}')
        if context['recent_activity_has_more']:
            lines.append(
                f'- Showing {context["recent_activity_limit"]} '
                f'of {context["recent_activity_total"]} events'
            )

    # Only the header line means nothing substantive to copy.
    if len(lines) <= 1:
        return ''
    return '\n'.join(lines)
