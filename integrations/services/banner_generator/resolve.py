"""Effective banner / social-image resolution (issue #931).

Every banner-bearing content record can hold up to three image URLs that
all map to the same on-page banner == OG/Twitter social image:

==========================  ==========================  ============
Field                       Owner                       Sync-clobbered?
==========================  ==========================  ============
``cover_image_url``         content repo frontmatter    yes (every sync)
``custom_banner_url``       Studio custom upload (#931) no
``auto_banner_url``         banner-generator pipeline   no
==========================  ==========================  ============

The precedence is fixed: frontmatter cover wins, then the operator's
Studio custom upload, then the generated banner. This module is the
single source of truth for that ordering so the public ``og:image``
(``seo_tags._get_image_url``), every model ``display_image_url`` accessor,
and the Studio preview panel all agree.

The custom upload sits *below* the frontmatter cover on purpose: for
git-managed content the frontmatter remains the operator's source of
truth, and a Studio upload is a sync-safe override that beats the
generated banner. For Studio-owned types (``event`` with
``origin == studio``, ``event_series``) there is no frontmatter cover, so
the custom upload is effectively the top override.
"""

# Resolution order. Each entry is a record attribute; the first non-empty
# value wins. Kept as a module constant so tests and callers reference the
# exact same precedence.
BANNER_FIELD_ORDER = (
    'cover_image_url',
    'custom_banner_url',
    'auto_banner_url',
)

# Maps the winning field name to a human-facing source label used by the
# Studio preview badge.
BANNER_SOURCE_LABELS = {
    'cover_image_url': 'Frontmatter cover',
    'custom_banner_url': 'Custom upload',
    'auto_banner_url': 'Generated',
}


def effective_banner_url(record):
    """Return the highest-precedence non-empty banner URL for ``record``.

    Resolves ``cover_image_url`` -> ``custom_banner_url`` ->
    ``auto_banner_url`` and returns ``''`` when none is set. Uses
    ``getattr`` with a default so it is safe on records that do not define
    every field (e.g. ``EventSeries`` has no ``cover_image_url``).
    """
    for attr in BANNER_FIELD_ORDER:
        url = getattr(record, attr, '') or ''
        if url:
            return url
    return ''


def banner_source(record):
    """Return the source label for the effective banner, or ``''``.

    Mirrors :func:`effective_banner_url` but returns the human-facing
    label ("Frontmatter cover", "Custom upload", or "Generated") for the
    field that won, so the Studio panel can render a source badge. Returns
    ``''`` when the record has no banner at all.
    """
    for attr in BANNER_FIELD_ORDER:
        url = getattr(record, attr, '') or ''
        if url:
            return BANNER_SOURCE_LABELS[attr]
    return ''
