"""
Access control utilities for content gating.

Provides the core access check pattern: user.tier.level >= content.required_level.
Anonymous users are treated as level 0 (free tier).
"""

# Visibility level constants matching tier levels in payments.Tier
LEVEL_OPEN = 0
# LEVEL_REGISTERED is a content-side sentinel (issue #465). It does NOT
# correspond to a real Tier row — Tier rows stay at 0/10/20/30. It means
# "any authenticated user, regardless of tier" and is used on per-unit
# course gating to draw a sign-in wall (anonymous denied, free verified
# allowed). The constant sits between LEVEL_OPEN and LEVEL_BASIC so
# numeric ``>=`` comparisons keep working for paid tiers.
LEVEL_REGISTERED = 5
LEVEL_BASIC = 10
LEVEL_MAIN = 20
LEVEL_PREMIUM = 30

# Choices for the required_level field on content models
VISIBILITY_CHOICES = [
    (LEVEL_OPEN, 'Open (everyone)'),
    (LEVEL_BASIC, 'Basic and above'),
    (LEVEL_MAIN, 'Main and above'),
    (LEVEL_PREMIUM, 'Premium only'),
]

# Choices for unit-level access fields (issue #465). Adds the
# LEVEL_REGISTERED option so course authors can default a course to
# "free with sign-in" without paywalling the catalog. Course.required_level
# stays on VISIBILITY_CHOICES because the catalog/course detail tier copy
# still distinguishes Free / Basic / Main / Premium only.
UNIT_VISIBILITY_CHOICES = [
    (LEVEL_OPEN, 'Open (everyone)'),
    (LEVEL_REGISTERED, 'Registered users (any tier)'),
    (LEVEL_BASIC, 'Basic and above'),
    (LEVEL_MAIN, 'Main and above'),
    (LEVEL_PREMIUM, 'Premium only'),
]

# Map from required_level to the tier name shown in CTAs.
# LEVEL_REGISTERED maps to 'Free' so the CTA copy reuses the existing
# "Free" tier label; the call site distinguishes the registered-wall
# CTA ("Sign in to read") from the upgrade CTA via gated_reason.
LEVEL_TO_TIER_NAME = {
    LEVEL_OPEN: 'Free',
    LEVEL_REGISTERED: 'Free',
    LEVEL_BASIC: 'Basic',
    LEVEL_MAIN: 'Main',
    LEVEL_PREMIUM: 'Premium',
}

# Issue #481: public-facing badge / sentence labels. Replaces the
# previous ``Basic+`` / ``Main+`` / ``Premium+`` shorthand on public
# surfaces (course/workshop/event cards and detail pages, paywall
# cards). Premium is the highest tier so it does NOT take a
# ``+``/``or above`` suffix — there is no higher public tier to upgrade
# to. ``LEVEL_OPEN`` keeps the existing "Free" label; ``LEVEL_REGISTERED``
# makes it explicit that a free account is required.
LEVEL_TO_PUBLIC_LABEL = {
    LEVEL_OPEN: 'Free',
    LEVEL_REGISTERED: 'Free with sign-in',
    LEVEL_BASIC: 'Basic or above',
    LEVEL_MAIN: 'Main or above',
    LEVEL_PREMIUM: 'Premium',
}

# Public-facing access label keyed by the bare tier NAME (not level). Used
# by the gated-card partial to render the tier pill ("Basic or above
# required" / "Main or above required" / "Premium required") from the one
# ``required_tier_name`` value the view supplies, so the template never
# branches on tier name. Premium is terminal and takes no "or above".
NAME_TO_PUBLIC_LABEL = {
    'Free': 'Free',
    'Basic': 'Basic or above',
    'Main': 'Main or above',
    'Premium': 'Premium',
}

# Issue #1335: single source of the gated-banner verb/noun copy, keyed by
# content type. ``verb`` fills "Sign in to {verb}" / "Upgrade to {tier} to
# {verb}"; ``noun`` fills "This {noun} is free — you just need an account."
# Replaces the two duplicated ``action_verbs`` dicts that used to live
# inside ``build_gating_context`` and the hand-written strings scattered
# across the workshop / course-unit / poll call sites.
CONTENT_TYPE_COPY = {
    'article': ('read this article', 'article'),
    'tutorial': ('read this tutorial', 'tutorial'),
    'recording': ('watch this recording', 'recording'),
    'unit': ('read this lesson', 'lesson'),
    'project': ('view this project', 'project'),
    'download': ('download this resource', 'download'),
    'event': ('register for this event', 'event'),
    'course': ('access this course', 'course'),
    'curated_link': ('access this resource', 'resource'),
    'poll': ('vote in this poll', 'poll'),
}
DEFAULT_CONTENT_TYPE_COPY = ('access this content', 'content')

# Default upgrade-path description for the ``insufficient_tier`` branch.
# Individual surfaces may pass a more specific ``upgrade_description``
# (e.g. the project write-up copy) without re-inventing the CTA labels.
DEFAULT_UPGRADE_DESCRIPTION = (
    'Get full access to this content and more with a membership.'
)

# Sentinel to distinguish "caller didn't pass active_override" from "caller passed None"
_SENTINEL = object()


def get_user_level(user, active_override=_SENTINEL):
    """Return the access level for a user.

    Anonymous users and users without a tier are treated as level 0.
    Staff and superuser accounts always get maximum access (LEVEL_PREMIUM).

    If the user has an active TierOverride (is_active=True and not yet
    expired), returns ``max(user.tier.level, override.override_tier.level)``
    so the override only ever grants MORE access, never less.

    Args:
        user: The request user (may be AnonymousUser or None).
        active_override: Optional pre-fetched TierOverride (or None).
            When provided, skips the DB query for the override.
            Pass ``None`` explicitly to indicate "no override exists".
            Omit (or pass the default sentinel) to auto-query.
    """
    if user is None or not user.is_authenticated:
        return 0
    if user.is_staff or user.is_superuser:
        return LEVEL_PREMIUM

    base_level = 0
    if user.tier_id is not None:
        base_level = user.tier.level

    # Check for active tier override
    if active_override is _SENTINEL:
        # Caller did not provide an override — query the DB
        override_level = _get_override_level(user)
    elif active_override is not None:
        override_level = active_override.override_tier.level
    else:
        override_level = None

    if override_level is not None:
        return max(base_level, override_level)

    return base_level


def _get_override_level(user):
    """Return the override tier level if the user has an active, non-expired override.

    Returns None if no active override exists.
    """
    from django.utils import timezone

    from accounts.models import TierOverride

    override = (
        TierOverride.objects
        .filter(user=user, is_active=True, expires_at__gt=timezone.now())
        .select_related('override_tier')
        .order_by('-override_tier__level', '-expires_at')
        .first()
    )
    if override is not None:
        return override.override_tier.level
    return None


def get_active_override(user):
    """Return the active TierOverride for a user, or None.

    Convenience function for views that need the full override object
    (e.g. dashboard badge, account page).
    """
    if user is None or not user.is_authenticated:
        return None
    from django.utils import timezone

    from accounts.models import TierOverride

    return (
        TierOverride.objects
        .filter(user=user, is_active=True, expires_at__gt=timezone.now())
        .select_related('override_tier')
        .order_by('-override_tier__level', '-expires_at')
        .first()
    )


def _resolve_required_level(content):
    """Return the access level to gate ``content`` against.

    Issue #465: ``Unit`` exposes ``effective_required_level`` so per-unit
    overrides and course-level defaults can override ``required_level``.
    Falling back to ``content.required_level`` keeps every other content
    type untouched. ``effective_required_level`` may be ``None`` when both
    the unit override and the course default are unset — treat that as
    "use the underlying ``required_level`` field" so courses without the
    new fields keep working.
    """
    effective = getattr(content, 'effective_required_level', None)
    if effective is not None:
        return effective
    return getattr(content, 'required_level', LEVEL_OPEN)


def can_access(user, content):
    """Check whether a user can access a content object.

    Args:
        user: The request user (may be AnonymousUser).
        content: Any model instance with a ``required_level`` attribute.
            ``Unit`` instances additionally expose
            ``effective_required_level`` which resolves the unit override
            and course default introduced in issue #465.

    Returns:
        True if the user's tier level is >= the content's required level,
        or if the user has individual CourseAccess for a Course (or a
        unit belonging to a course they have CourseAccess for).
    """
    required = _resolve_required_level(content)

    if required == LEVEL_OPEN:
        if user is None or not user.is_authenticated:
            return True
        if get_user_level(user) >= LEVEL_BASIC:
            return True
        return bool(user.email_verified)

    if required == LEVEL_REGISTERED:
        # Issue #465: registration wall. Anonymous denied, any tier
        # allowed when their email is verified (Basic+ tier rows are
        # treated as already verified — same rule LEVEL_OPEN uses).
        if user is None or not user.is_authenticated:
            return False
        if get_user_level(user) >= LEVEL_BASIC:
            return True
        return bool(user.email_verified)

    if get_user_level(user) >= required:
        return True
    # Check individual course access (CourseAccess model). Both the
    # course itself and any unit belonging to that course should bypass
    # the per-unit / course-level gating once a user holds CourseAccess.
    if user is not None and user.is_authenticated:
        if _is_course(content):
            from content.models import CourseAccess
            return CourseAccess.objects.filter(
                user=user, course=content,
            ).exists()
        if _is_unit(content):
            from content.models import CourseAccess
            return CourseAccess.objects.filter(
                user=user, course=content.module.course,
            ).exists()
    return False


def get_gated_reason(user, content):
    """Return why access is denied, or an empty string when access is allowed."""
    if can_access(user, content):
        return ''
    required = _resolve_required_level(content)
    # Free authenticated users on free content (LEVEL_OPEN or
    # LEVEL_REGISTERED) are blocked by email verification, not by tier.
    if (
        required in (LEVEL_OPEN, LEVEL_REGISTERED)
        and user is not None
        and user.is_authenticated
        and get_user_level(user) < LEVEL_BASIC
        and not user.email_verified
    ):
        return 'unverified_email'
    if (
        required == LEVEL_REGISTERED
        and (user is None or not user.is_authenticated)
    ):
        return 'authentication_required'
    return 'insufficient_tier'


def build_verify_email_context(user):
    """Build shared context for the verify-email-required partial."""
    from django.urls import reverse

    return {
        'gated_reason': 'unverified_email',
        'verify_email_address': user.email,
        'verify_resend_url': reverse('account_resend_verification'),
        'verify_resend_label': 'Resend verification email',
        'pricing_url': '/pricing',
    }


def _is_course(content):
    """Check if a content object is a Course instance without importing at module level."""
    return content.__class__.__name__ == 'Course'


def _is_unit(content):
    """Check if a content object is a Unit instance without importing at module level."""
    return content.__class__.__name__ == 'Unit'


def get_required_tier_name(required_level):
    """Return the human-readable tier name for a required_level value."""
    return LEVEL_TO_TIER_NAME.get(required_level, 'Premium')


def get_public_label_for_tier_name(tier_name):
    """Return the public access label for a bare tier NAME.

    Issue #1335: the gated-card partial carries ``required_tier_name``
    (Basic / Main / Premium / Free) and renders "{label} required". This
    maps that name to its public label so the mapping stays in Python and
    the template does not branch on tier name.
    """
    return NAME_TO_PUBLIC_LABEL.get(tier_name, tier_name)


def get_content_type_copy(content_type):
    """Return the ``(verb, noun)`` gated-banner copy for a content type."""
    return CONTENT_TYPE_COPY.get(content_type, DEFAULT_CONTENT_TYPE_COPY)


def get_required_tier_label(required_level):
    """Return the public-facing access label for a ``required_level``.

    Issue #481: replaces ``Basic+`` / ``Main+`` / ``Premium+`` shorthand
    with copy that reads naturally in the UI ("Basic or above", "Main or
    above", "Premium"). Use this on every public-facing surface (cards,
    detail headers, gated paywall pills, event badges).

    The bare tier name (``Basic``, ``Main``, ``Premium``) is still
    available via :func:`get_required_tier_name` and remains the right
    choice when a sentence already provides the "or above" context (e.g.
    ``Upgrade to Basic to access this workshop``).
    """
    return LEVEL_TO_PUBLIC_LABEL.get(required_level, 'Premium')


def get_teaser_text(content, max_chars=200):
    """Extract a teaser from a content object.

    For models with ``description``, uses that.
    For models with ``content_markdown`` or ``content_html``, extracts the
    first ``max_chars`` characters from the markdown (preferred) or
    strips HTML from content_html.

    Returns a plain text string.
    """
    from content.utils.markdown import markdown_to_plain_text

    # Prefer description if available and non-empty
    description = getattr(content, 'description', '')
    if description:
        return markdown_to_plain_text(description)[:max_chars]

    # Fall back to markdown content
    markdown = getattr(content, 'content_markdown', '')
    if markdown:
        return markdown_to_plain_text(markdown)[:max_chars]

    return ''


def build_gated_access_copy(
    *,
    gated_reason,
    verb,
    noun,
    required_level,
    user=None,
    resource_url='',
    upgrade_description=None,
    show_signin_on_paid_guest=True,
    encode_next=True,
):
    """Return the canonical ``_gated_access_card.html`` copy for a gate.

    Issue #1335: this is the single source of gated-banner copy. It is
    keyed by ``gated_reason`` and by the content-type ``verb`` / ``noun``
    so every surface (article, tutorial, recording, unit, project,
    download, event, course, resource, poll) reads the same wording. Both
    the content-instance entry point (:func:`build_gating_context`) and
    the workshop per-level helpers call this so no call site hand-assembles
    headings or CTA labels.

    ``resource_url`` is the path the visitor should return to after
    authenticating; it feeds the ``?next=`` query string on the sign-in and
    sign-up URLs. Only the ``authentication_required`` and
    ``insufficient_tier`` reasons are handled here — ``unverified_email``
    renders ``content/_verify_email_required.html`` instead.
    """
    from urllib.parse import urlencode

    is_authenticated = bool(
        user is not None and getattr(user, 'is_authenticated', False)
    )
    # ``encode_next=True`` percent-encodes the path (workshop/course-unit
    # behavior). ``encode_next=False`` keeps slashes readable, matching the
    # article/project/poll surfaces that historically rendered an unencoded
    # ``?next=`` (Django's ``urlencode`` filter treats ``/`` as safe).
    if not resource_url:
        next_qs = ''
    elif encode_next:
        next_qs = urlencode({'next': resource_url})
    else:
        next_qs = f'next={resource_url}'
    login_url = f'/accounts/login/?{next_qs}' if next_qs else '/accounts/login/'
    signup_url = f'/accounts/signup/?{next_qs}' if next_qs else '/accounts/signup/'

    if gated_reason == 'authentication_required':
        # Free-with-sign-in content for an anonymous visitor. No tier pill,
        # no Pricing route — the primary CTA is Sign In and the companion
        # is Create a free account (workshop-recording wording, #465/#571).
        return {
            'gated_heading': f'Sign in to {verb}',
            'gated_description': (
                f'This {noun} is free — you just need an account. '
                'Sign in or create one in seconds.'
            ),
            'required_tier_name': '',
            'current_user_state': '',
            'gated_cta_url': login_url,
            'gated_cta_label': 'Sign In',
            'signup_cta_url': signup_url,
            'signup_cta_label': 'Create a free account',
            'signin_cta_url': '',
            'signin_cta_label': '',
        }

    # insufficient_tier: signed-in below tier, or anonymous on paid content.
    tier_name = get_required_tier_name(required_level)
    current_user_state = ''
    signup_cta_url = ''
    signup_cta_label = ''
    signin_cta_url = ''
    signin_cta_label = ''
    if is_authenticated:
        current_user_state = (
            f'Current access: {get_required_tier_name(get_user_level(user))} member'
        )
    else:
        # Anonymous on a paid wall: keep the upgrade path but offer a
        # no-cost account first and a sign-in link for existing members.
        signup_cta_url = signup_url
        signup_cta_label = 'Create a free account'
        if show_signin_on_paid_guest:
            signin_cta_url = login_url
            signin_cta_label = 'Already a member? Sign in'

    return {
        'gated_heading': f'Upgrade to {tier_name} to {verb}',
        'gated_description': upgrade_description or DEFAULT_UPGRADE_DESCRIPTION,
        'required_tier_name': tier_name,
        'current_user_state': current_user_state,
        'gated_cta_url': '/pricing',
        'gated_cta_label': 'View Pricing',
        'signup_cta_url': signup_cta_url,
        'signup_cta_label': signup_cta_label,
        'signin_cta_url': signin_cta_url,
        'signin_cta_label': signin_cta_label,
    }


def build_gating_context(
    user,
    content,
    content_type='article',
    *,
    resource_url=None,
    upgrade_description=None,
    gated_card_testid='gated-access-card',
    gated_icon='lock',
    gated_cta_testid='gated-pricing-link',
    show_signin_on_paid_guest=True,
):
    """Build template context for gated content display.

    Issue #1335: emits the full ``content/_gated_access_card.html`` variable
    set (headings, description, CTA labels/URLs, tier pill, current-access
    line, sign-up / sign-in companions) so every surface renders the one
    canonical partial with the one canonical copy. Per-surface differences
    (testid, icon, upgrade description) are arguments, not new copy.

    Args:
        user: The request user.
        content: The content model instance.
        content_type: 'article', 'tutorial', 'recording', 'project', etc.
            Selects the verb/noun copy from :data:`CONTENT_TYPE_COPY`.
        resource_url: Path to return to after auth (for ``?next=``). Falls
            back to ``content.get_absolute_url()`` when available.

    Returns:
        A dict to merge into the template context. When the user has access,
        ``is_gated`` is False and no other card keys are present.
    """
    gated_reason = get_gated_reason(user, content)

    if not gated_reason:
        return {'is_gated': False}

    if gated_reason == 'unverified_email':
        return {
            'is_gated': True,
            **build_verify_email_context(user),
        }

    if resource_url is None:
        get_url = getattr(content, 'get_absolute_url', None)
        resource_url = get_url() if callable(get_url) else ''

    verb, noun = get_content_type_copy(content_type)
    copy = build_gated_access_copy(
        gated_reason=gated_reason,
        verb=verb,
        noun=noun,
        required_level=_resolve_required_level(content),
        user=user,
        resource_url=resource_url,
        upgrade_description=upgrade_description,
        show_signin_on_paid_guest=show_signin_on_paid_guest,
        encode_next=False,
    )

    return {
        'is_gated': True,
        'gated_reason': gated_reason,
        'teaser': get_teaser_text(content),
        'gated_card_testid': gated_card_testid,
        'gated_icon': gated_icon,
        'gated_cta_testid': gated_cta_testid,
        'pricing_url': '/pricing',
        **copy,
    }
