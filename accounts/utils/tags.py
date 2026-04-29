"""Contact-tag normalization and mutation helpers (issue #354).

Contact tags live on ``User.tags`` as a list of normalized strings. They are
SEPARATE from the content-tag namespace (articles, downloads, etc.) but share
the same slug shape. To keep one source of truth for the slug rules we delegate
normalization to ``content/utils/tags.py``.

The ``add_tag`` / ``remove_tag`` helpers wrap normalization plus persistence so
view code never has to touch the JSON list directly.
"""

from content.utils.tags import normalize_tag as normalize_content_tag

__all__ = ['normalize_tag', 'normalize_tags', 'add_tag', 'remove_tag']


def normalize_tag(tag):
    """Normalize a contact tag.

    Most contact tags use the public content-tag slug rules. Course import tags
    additionally preserve the private ``course:<slug>`` namespace without
    changing public content tag semantics.
    """
    if not tag or not isinstance(tag, str):
        return ''
    raw = tag.strip()
    prefix = 'course:'
    if raw.lower().startswith(prefix):
        course_slug = normalize_content_tag(raw[len(prefix):])
        if course_slug:
            return f'{prefix}{course_slug}'
        return ''
    return normalize_content_tag(raw)


def normalize_tags(tags):
    """Normalize a list of contact tags, preserving first occurrence order."""
    if not tags or not isinstance(tags, list):
        return []
    seen = set()
    result = []
    for tag in tags:
        normalized = normalize_tag(tag)
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def add_tag(user, raw):
    """Add a tag to ``user.tags``, normalizing the input.

    Idempotent: adding an existing tag is a no-op. Returns the normalized tag
    string (or empty string if the input normalized to nothing -- the caller
    can treat that as "rejected, please show a flash"). Persists with
    ``update_fields=['tags']`` to avoid touching unrelated columns.
    """
    normalized = normalize_tag(raw)
    if not normalized:
        return ''
    current = list(user.tags or [])
    if normalized in current:
        return normalized
    current.append(normalized)
    user.tags = current
    user.save(update_fields=['tags'])
    return normalized


def remove_tag(user, raw):
    """Remove a tag from ``user.tags``.

    Idempotent: removing a tag the user does not have is a no-op. Returns the
    normalized tag string regardless of whether it was actually removed.
    """
    normalized = normalize_tag(raw)
    if not normalized:
        return ''
    current = list(user.tags or [])
    if normalized not in current:
        return normalized
    current.remove(normalized)
    user.tags = current
    user.save(update_fields=['tags'])
    return normalized
