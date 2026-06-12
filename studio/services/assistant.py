"""Studio AI assistant service (issue #872, Phase 1).

Turns a natural-language staff request into a PROPOSED CRM action over a
deliberately small, hand-curated surface: exactly two tools,
``add_member_note`` and ``update_member_profile``. The model is asked to
either choose ONE tool and produce its arguments, or to decline when
nothing fits.

This module is Django-independent, mirroring
:mod:`plans.services.next_sprint_draft`: it imports neither ``django.db``
models, ``request`` objects, nor the ``studio`` / ``crm`` / ``plans``
apps. The Studio view is the thin wrapper that resolves the target member
via the ORM and executes the confirmed action. An import-isolation test
enforces the seam.

Structured output uses the #799 pattern: each tool's Pydantic argument
model doubles as the tool ``input_schema`` via ``model_json_schema()``,
the model is allowed to call one of the two tools (or none), and the
validated ``result.tool_input`` is the structured proposal. The model is
called exactly once per propose; it is NEVER re-invoked at execute time.
"""

from typing import Optional

from pydantic import BaseModel, Field, ValidationError

from integrations.services import llm
from integrations.services.llm import LLMError

# Versioned as a module-level constant so future eval harnesses can diff
# prompt revisions across stored runs (mirrors next_sprint_draft).
SYSTEM_PROMPT = (
    'You are a careful operations assistant embedded in the staff Studio '
    'of an AI engineering community platform. A staff member describes, in '
    'plain language, a change they want to make to a member record.\n\n'
    'You can perform EXACTLY TWO actions, exposed as tools:\n'
    '1. add_member_note — attach an internal note to a member, identified '
    'by their email.\n'
    '2. update_member_profile — update one or more whitelisted CRM fields '
    '(status, persona, summary, next_steps) on a member, identified by '
    'their email.\n\n'
    'Rules you must follow:\n'
    '- Choose the single tool that matches the request and fill in only '
    'the fields the request actually implies. Do not invent values.\n'
    '- For update_member_profile, only set the fields the staff member '
    'asked to change; leave the rest unset.\n'
    '- Always include the member email exactly as given.\n'
    '- If the request does not clearly map to one of these two actions '
    '(for example it asks to delete data, send an email, create an event, '
    'or anything else), DO NOT call a tool. Instead reply with a short '
    'sentence explaining you can only add member notes or update member '
    'profiles right now.\n'
    'Everything you propose is reviewed and explicitly confirmed by the '
    'staff member before it runs. Nothing you output is executed directly.'
)

# Tool names. Kept as constants so the view, the audit log, and tests all
# refer to the same identifiers.
TOOL_ADD_NOTE = 'add_member_note'
TOOL_UPDATE_PROFILE = 'update_member_profile'

# Whitelisted note kinds/visibilities. Duplicated here (not imported from
# plans.models) to keep this module ORM-free; the view validates against
# the canonical model choices a second time at execute, so this stays a
# convenience hint for the model, not the authority.
NOTE_KINDS = (
    'persona', 'background', 'intake', 'meeting', 'recommendation',
    'action_item', 'source', 'general',
)
NOTE_VISIBILITIES = ('internal', 'external')

# Explicit whitelist of profile fields the model may set. ANY other key in
# the proposed payload is a hard rejection (see ``validate_profile_keys``).
PROFILE_FIELD_WHITELIST = ('status', 'persona', 'summary', 'next_steps')
PROFILE_STATUS_CHOICES = ('active', 'archived')


class AddMemberNoteArgs(BaseModel):
    """Arguments for the ``add_member_note`` tool."""

    member_email: str = Field(
        description='Email address of the member the note is about.',
    )
    body: str = Field(
        description='The note text to record on the member.',
    )
    kind: str = Field(
        default='general',
        description=(
            'Note kind. One of: '
            + ', '.join(NOTE_KINDS)
            + '. Defaults to "general".'
        ),
    )
    visibility: str = Field(
        default='internal',
        description=(
            'Note visibility: "internal" (staff-only) or "external" '
            '(shareable with the member). Defaults to "internal".'
        ),
    )


class UpdateMemberProfileArgs(BaseModel):
    """Arguments for the ``update_member_profile`` tool.

    Every CRM field is Optional so the model sets ONLY what the request
    implies; unset fields are excluded from the executed payload. The
    field set here is the whitelist — Pydantic ignores unknown keys by
    default, but the view ALSO re-validates the raw proposed keys against
    :data:`PROFILE_FIELD_WHITELIST` so a non-whitelisted key is a visible
    rejection rather than a silent drop.
    """

    member_email: str = Field(
        description='Email address of the member to update.',
    )
    status: Optional[str] = Field(
        default=None,
        description='CRM status: "active" or "archived".',
    )
    persona: Optional[str] = Field(
        default=None,
        description='Free-text persona label (max 120 chars).',
    )
    summary: Optional[str] = Field(
        default=None,
        description='Short staff summary of who this member is.',
    )
    next_steps: Optional[str] = Field(
        default=None,
        description='What is next for this member.',
    )


# Fields on the profile args that are NOT writable CRM fields (they
# identify the target, not the change).
_PROFILE_NON_FIELD_KEYS = frozenset({'member_email'})


class AssistantUnavailable(LLMError):
    """Raised when a proposal is requested but the LLM service is disabled.

    Subclasses :class:`LLMError` so callers catching the generic LLM
    failure also catch this, while a caller that wants to render the
    distinct "not configured" state can branch on the type.
    """


class WhitelistViolation(Exception):
    """Raised when a proposed profile payload names a non-whitelisted key.

    Carries the offending keys so the view can show a clear message and
    refuse the write — the payload is never silently trimmed.
    """

    def __init__(self, offending_keys):
        self.offending_keys = list(offending_keys)
        super().__init__(
            'Proposed update touches non-whitelisted field(s): '
            + ', '.join(self.offending_keys)
        )


class AssistantProposal(BaseModel):
    """A single proposed action (or a decline), ready for staff review.

    ``tool_name`` is one of the two tool constants when the model chose an
    action; it is ``None`` for a decline. ``payload`` is the validated,
    whitelist-checked argument dict that will be replayed verbatim at
    execute time — the model is not consulted again. ``message`` carries
    the model's decline / clarification text when no tool was chosen.
    """

    tool_name: Optional[str] = None
    payload: dict = Field(default_factory=dict)
    message: str = ''

    @property
    def is_action(self):
        return self.tool_name is not None


def _build_tools():
    """Return the two tool specs from the Pydantic argument schemas."""
    return [
        {
            'name': TOOL_ADD_NOTE,
            'description': (
                'Attach an internal note to a member identified by email.'
            ),
            'input_schema': AddMemberNoteArgs.model_json_schema(),
        },
        {
            'name': TOOL_UPDATE_PROFILE,
            'description': (
                'Update one or more whitelisted CRM profile fields '
                '(status, persona, summary, next_steps) on a member '
                'identified by email.'
            ),
            'input_schema': UpdateMemberProfileArgs.model_json_schema(),
        },
    ]


def validate_profile_keys(raw_payload):
    """Reject a profile payload that names any non-whitelisted field.

    Inspects the RAW proposed keys (everything except the target
    ``member_email``) and raises :class:`WhitelistViolation` if any falls
    outside :data:`PROFILE_FIELD_WHITELIST`. Enforced at BOTH propose and
    execute so a non-whitelisted key is never silently dropped.
    """
    offending = [
        key for key in raw_payload
        if key not in _PROFILE_NON_FIELD_KEYS
        and key not in PROFILE_FIELD_WHITELIST
    ]
    if offending:
        raise WhitelistViolation(offending)


def _normalize_note_payload(raw):
    """Validate + normalise an ``add_member_note`` payload.

    Returns a clean dict with defaulted kind/visibility. Invalid enum
    values fall back to the safe defaults rather than failing the whole
    proposal — the staff member still reviews the result before it runs.
    """
    args = AddMemberNoteArgs.model_validate(raw)
    kind = args.kind if args.kind in NOTE_KINDS else 'general'
    visibility = (
        args.visibility if args.visibility in NOTE_VISIBILITIES else 'internal'
    )
    return {
        'member_email': args.member_email.strip(),
        'body': args.body,
        'kind': kind,
        'visibility': visibility,
    }


def _normalize_profile_payload(raw):
    """Validate a profile payload and return only the set, allowed fields.

    Raises :class:`WhitelistViolation` BEFORE validation if the raw
    payload names a non-whitelisted key. After validation, only the fields
    the model actually set (non-None) are kept, plus ``member_email``.
    """
    validate_profile_keys(raw)
    args = UpdateMemberProfileArgs.model_validate(raw)
    payload = {'member_email': args.member_email.strip()}
    if args.status is not None:
        payload['status'] = args.status
    if args.persona is not None:
        payload['persona'] = args.persona
    if args.summary is not None:
        payload['summary'] = args.summary
    if args.next_steps is not None:
        payload['next_steps'] = args.next_steps
    return payload


def propose_action(request_text):
    """Ask the model to propose ONE CRM action (or decline) for the text.

    The model is called EXACTLY ONCE here. The returned
    :class:`AssistantProposal` carries the validated, whitelist-checked
    payload that the view replays verbatim at execute time — the model is
    never consulted again.

    Args:
        request_text: The staff member's natural-language request.

    Returns:
        AssistantProposal: an action (``tool_name`` + ``payload``) or a
        decline (``tool_name=None`` + ``message``).

    Raises:
        AssistantUnavailable: when ``llm.is_enabled()`` is False;
            ``llm.complete`` is never called.
        WhitelistViolation: when a proposed profile payload names a
            non-whitelisted field.
        LLMError: when the LLM call fails or a chosen tool's arguments
            cannot be validated.
    """
    if not llm.is_enabled():
        raise AssistantUnavailable(
            'The assistant is not configured (no LLM provider).'
        )

    tools = _build_tools()
    messages = [{'role': 'user', 'content': request_text}]

    # ``tool_choice`` left to {'type': 'auto'} so the model MAY decline
    # (no tool) when the request maps to neither action.
    result = llm.complete(
        messages,
        system=SYSTEM_PROMPT,
        tools=tools,
        tool_choice={'type': 'auto'},
    )

    if result.tool_name is None or result.tool_input is None:
        decline = (result.text or '').strip() or (
            'I can only add member notes or update member profiles right '
            'now.'
        )
        return AssistantProposal(tool_name=None, message=decline)

    if result.tool_name == TOOL_ADD_NOTE:
        try:
            payload = _normalize_note_payload(result.tool_input)
        except ValidationError as exc:
            raise LLMError(f'Invalid note proposal: {exc}') from None
        return AssistantProposal(tool_name=TOOL_ADD_NOTE, payload=payload)

    if result.tool_name == TOOL_UPDATE_PROFILE:
        try:
            payload = _normalize_profile_payload(result.tool_input)
        except ValidationError as exc:
            raise LLMError(f'Invalid profile proposal: {exc}') from None
        return AssistantProposal(
            tool_name=TOOL_UPDATE_PROFILE, payload=payload,
        )

    # The model named a tool we did not register — treat as a decline.
    return AssistantProposal(
        tool_name=None,
        message=(
            'I can only add member notes or update member profiles right '
            'now.'
        ),
    )
