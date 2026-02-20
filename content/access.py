"""
Access control utilities for content gating.

Provides the core access check pattern: user.tier.level >= content.required_level.
Anonymous users are treated as level 0 (free tier).
"""

# Visibility level constants matching tier levels in payments.Tier
LEVEL_OPEN = 0
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

# Map from required_level to the tier name shown in CTAs
LEVEL_TO_TIER_NAME = {
    LEVEL_OPEN: 'Free',
    LEVEL_BASIC: 'Basic',
    LEVEL_MAIN: 'Main',
    LEVEL_PREMIUM: 'Premium',
}


def get_user_level(user):
    """Return the access level for a user.

    Anonymous users and users without a tier are treated as level 0.
    """
    if user is None or not user.is_authenticated:
        return 0
    if user.tier_id is None:
        return 0
    return user.tier.level


def can_access(user, content):
    """Check whether a user can access a content object.

    Args:
        user: The request user (may be AnonymousUser).
        content: Any model instance with a ``required_level`` attribute.

    Returns:
        True if the user's tier level is >= the content's required_level.
    """
    if content.required_level == 0:
        return True
    return get_user_level(user) >= content.required_level


def get_required_tier_name(required_level):
    """Return the human-readable tier name for a required_level value."""
    return LEVEL_TO_TIER_NAME.get(required_level, 'Premium')


def get_teaser_text(content, max_chars=200):
    """Extract a teaser from a content object.

    For models with ``description``, uses that.
    For models with ``content_markdown`` or ``content_html``, extracts the
    first ``max_chars`` characters from the markdown (preferred) or
    strips HTML from content_html.

    Returns a plain text string.
    """
    # Prefer description if available and non-empty
    description = getattr(content, 'description', '')
    if description:
        return description[:max_chars]

    # Fall back to markdown content
    markdown = getattr(content, 'content_markdown', '')
    if markdown:
        return markdown[:max_chars]

    return ''


def build_gating_context(user, content, content_type='article'):
    """Build template context for gated content display.

    Args:
        user: The request user.
        content: The content model instance.
        content_type: A string like 'article', 'recording', 'project', etc.
            Used to build the CTA message.

    Returns:
        A dict with gating information to merge into the template context.
        If the user has access, ``is_gated`` will be False and other keys
        will be absent.
    """
    has_access = can_access(user, content)

    if has_access:
        return {'is_gated': False}

    tier_name = get_required_tier_name(content.required_level)
    teaser = get_teaser_text(content)

    # Build CTA message based on content type
    action_verbs = {
        'article': 'read this article',
        'recording': 'watch this recording',
        'project': 'view this project',
        'tutorial': 'read this tutorial',
        'curated_link': 'access this resource',
        'download': 'download this resource',
        'event': 'join this event',
    }
    action = action_verbs.get(content_type, 'access this content')
    cta_message = f'Upgrade to {tier_name} to {action}'

    return {
        'is_gated': True,
        'teaser': teaser,
        'cta_message': cta_message,
        'required_tier_name': tier_name,
        'pricing_url': '/pricing',
    }
