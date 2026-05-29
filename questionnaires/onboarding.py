"""Form-first member onboarding helpers (issue #802).

The onboarding flow lives in the ``accounts`` app (its views), but the
domain logic that maps the member's self-identification selection to a
target onboarding ``Questionnaire`` — and the derived "has this member
onboarded?" state — belongs with the questionnaire models, so it is kept
here next to the shared fill-in services.

Design notes:
- Onboarding completion is NOT a model field. It is derived from the
  existence of a submitted ``purpose='onboarding'`` ``Response`` for the
  member, so no migration is needed.
- The member-facing self-identification options are ARCHETYPE
  descriptions only. The internal persona name (Alex / Priya / Sam /
  Taylor) MUST never reach the member, so each option carries the
  persona ``id`` as an opaque value and is labeled with the archetype.
"""

from questionnaires.models import Persona, Questionnaire, Response

# Slug of the persona-agnostic onboarding questionnaire seeded by #801.
GENERIC_ONBOARDING_SLUG = 'onboarding-general'

# Opaque self-ID values for the two persona-agnostic options. They are
# not persona ids, so they never collide with a ``Persona.pk`` value.
SELF_ID_NONE = 'none'
SELF_ID_MULTIPLE = 'multiple'
_GENERIC_VALUES = frozenset({SELF_ID_NONE, SELF_ID_MULTIPLE})


def has_completed_onboarding(user):
    """True when ``user`` has a submitted onboarding ``Response``."""
    if not user.is_authenticated:
        return False
    return Response.objects.filter(
        respondent=user,
        questionnaire__purpose='onboarding',
        status='submitted',
    ).exists()


def get_onboarding_response(user):
    """Return the member's onboarding ``Response`` (draft or submitted).

    There is at most one because the self-ID step is asked only once;
    switching persona mid-flow is a staff action. Returns ``None`` when
    the member has not started onboarding.
    """
    return (
        Response.objects
        .filter(respondent=user, questionnaire__purpose='onboarding')
        .select_related('questionnaire')
        .order_by('created_at')
        .first()
    )


def get_generic_onboarding_questionnaire():
    """Return the seeded generic onboarding questionnaire, or ``None``."""
    return (
        Questionnaire.objects
        .filter(slug=GENERIC_ONBOARDING_SLUG, purpose='onboarding')
        .first()
    )


def self_identification_options():
    """Build the member-facing self-identification option list.

    One option per active ``Persona`` that has a ``default_questionnaire``,
    plus the two persona-agnostic options. Each persona option's ``value``
    is the persona pk (opaque) and its ``label`` is the persona archetype
    — the internal persona ``name`` is deliberately excluded.
    """
    options = []
    personas = (
        Persona.objects
        .filter(is_active=True, default_questionnaire__isnull=False)
        .order_by('order', 'name')
    )
    for persona in personas:
        options.append({
            'value': str(persona.pk),
            'label': persona.archetype,
            'help_text': persona.description,
        })
    options.append({
        'value': SELF_ID_NONE,
        'label': 'None of these / not sure',
        'help_text': '',
    })
    options.append({
        'value': SELF_ID_MULTIPLE,
        'label': 'More than one / both',
        'help_text': '',
    })
    return options


def resolve_target_questionnaire(selection):
    """Map a self-ID selection to its target onboarding ``Questionnaire``.

    - ``none`` / ``multiple`` -> the generic onboarding questionnaire.
    - a persona pk -> that persona's ``default_questionnaire``, falling
      back to the generic questionnaire when the persona has none (data
      gap) or the selection does not match an active persona.

    Returns ``None`` when no onboarding questionnaire is available at all
    (the caller shows a friendly "not ready yet" message rather than 500).
    """
    generic = get_generic_onboarding_questionnaire()
    if selection in _GENERIC_VALUES:
        return generic

    if selection and selection.isdigit():
        persona = (
            Persona.objects
            .filter(pk=int(selection), is_active=True)
            .select_related('default_questionnaire')
            .first()
        )
        if persona is not None and persona.default_questionnaire is not None:
            return persona.default_questionnaire

    # Unknown selection or persona without a questionnaire: fall back.
    return generic
