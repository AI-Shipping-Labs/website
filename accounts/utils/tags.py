"""Contact-tag normalization and mutation helpers (issue #354).

Contact tags live on ``User.tags`` as a list of normalized strings. They are
SEPARATE from the content-tag namespace (articles, downloads, etc.). They use
the same slug rules plus a private source namespace such as ``stripe:active``
or ``course:data-engineering-zoomcamp``.

The ``add_tag`` / ``remove_tag`` helpers wrap normalization plus persistence so
view code never has to touch the JSON list directly. The ``rename_tag`` /
``delete_tag`` helpers (issue #694) operate across every user that carries the
tag in a single transaction so operators can clean up the global tag namespace
without iterating one user at a time.
"""

import re

from django.contrib.auth import get_user_model
from django.db import transaction


def normalize_tag(tag):
    """Normalize a single operator contact tag."""
    if not tag or not isinstance(tag, str):
        return ''
    tag = tag.strip().lower()
    tag = tag.replace(' ', '-').replace('_', '-')
    tag = re.sub(r'[^a-z0-9:-]', '', tag)
    tag = re.sub(r'-{2,}', '-', tag)
    tag = tag.strip('-')
    return tag


def normalize_tags(tags):
    """Normalize contact tags, removing duplicates and empty values."""
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

__all__ = [
    'normalize_tag',
    'normalize_tags',
    'add_tag',
    'remove_tag',
    'rename_tag',
    'delete_tag',
    'list_all_tags',
    'count_users_with_tag',
    'user_ids_with_exact_tag',
]


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


def list_all_tags():
    """Return the sorted, deduped union of every contact tag across users.

    Powers the user-list tag picker (issue #694) and the user-detail
    ``<datalist>``. Reads only the ``tags`` column to avoid materializing
    full User rows and normalizes defensively in case any rows pre-date the
    normalization helper.
    """
    User = get_user_model()
    seen = set()
    for tag_list in User.objects.values_list('tags', flat=True):
        if not tag_list:
            continue
        for tag in normalize_tags(tag_list):
            seen.add(tag)
    return sorted(seen)


def count_users_with_tag(name):
    """Return the number of users currently carrying ``name``.

    Used by the user-detail delete-tag-everywhere confirm copy
    ("This removes it from {N} users."). Normalizes the input so callers
    can pass the raw chip text.
    """
    normalized = normalize_tag(name)
    if not normalized:
        return 0
    User = get_user_model()
    count = 0
    for tag_list in User.objects.values_list('tags', flat=True):
        if isinstance(tag_list, list) and normalized in tag_list:
            count += 1
    return count


def user_ids_with_exact_tag(name):
    """Return user IDs carrying the normalized tag as an exact list item."""
    normalized = normalize_tag(name)
    if not normalized:
        return []
    User = get_user_model()
    return [
        user_id
        for user_id, tags in User.objects.values_list('id', 'tags').iterator()
        if isinstance(tags, list) and normalized in tags
    ]


def rename_tag(old, new):
    """Rename a tag across every user that carries it (issue #694).

    Behaviour:

    - Both arguments are normalized via ``normalize_tag``.
    - If ``new`` normalizes to the empty string, raises ``ValueError``.
    - If ``old`` normalizes to the empty string, returns
      ``{"affected": 0, "old": "", "new": <normalized>}`` (nothing to rename).
    - If ``old == new`` after normalization, no-op:
      ``{"affected": 0, "old": <normalized>, "new": <normalized>}``.
    - Otherwise, every user that has ``old`` in ``tags`` is updated:
      ``old`` is replaced with ``new``, deduping so a user that already
      carried ``new`` does not end up with the same slug twice. Each row
      is persisted with ``update_fields=['tags']``.

    The whole set of writes is wrapped in a single ``transaction.atomic()``
    so the global namespace stays consistent on failure.
    """
    new_normalized = normalize_tag(new)
    if not new_normalized:
        raise ValueError('New tag name cannot be empty.')

    old_normalized = normalize_tag(old)
    if not old_normalized:
        return {'affected': 0, 'old': '', 'new': new_normalized}

    if old_normalized == new_normalized:
        return {
            'affected': 0,
            'old': old_normalized,
            'new': new_normalized,
        }

    User = get_user_model()
    affected = 0
    with transaction.atomic():
        # Only iterate users that actually carry the old tag. ``tags`` is a
        # JSONField list, so we filter in Python to stay portable across
        # sqlite / postgres without leaning on ``contains`` lookups that
        # don't work the same way everywhere.
        for user in User.objects.exclude(tags=[]).only('id', 'tags'):
            current = list(user.tags or [])
            if old_normalized not in current:
                continue
            new_list = []
            seen = set()
            for tag in current:
                if tag == old_normalized:
                    candidate = new_normalized
                else:
                    candidate = tag
                if candidate in seen:
                    continue
                seen.add(candidate)
                new_list.append(candidate)
            user.tags = new_list
            user.save(update_fields=['tags'])
            affected += 1

    return {
        'affected': affected,
        'old': old_normalized,
        'new': new_normalized,
    }


def delete_tag(name):
    """Delete a tag from every user that carries it (issue #694).

    Returns ``{"affected": <int>, "name": <normalized>}``. Wrapped in a
    single ``transaction.atomic()`` so the global namespace stays
    consistent on failure. An empty / unknown ``name`` is a no-op.
    """
    normalized = normalize_tag(name)
    if not normalized:
        return {'affected': 0, 'name': ''}

    User = get_user_model()
    affected = 0
    with transaction.atomic():
        for user in User.objects.exclude(tags=[]).only('id', 'tags'):
            current = list(user.tags or [])
            if normalized not in current:
                continue
            current.remove(normalized)
            user.tags = current
            user.save(update_fields=['tags'])
            affected += 1

    return {'affected': affected, 'name': normalized}
