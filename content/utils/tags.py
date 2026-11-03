"""Tag normalization utilities for content models.

Tags are stored as string[] (JSONField) on content items. This module provides
normalization: lowercase, replace spaces with hyphens, strip special characters.
"""

import re

from django.http import HttpResponse

CATALOG_MAX_TAGS = 2
CATALOG_MAX_TAG_LENGTH = 100
CATALOG_MAX_RAW_TAG_PARAMS = 10


def collect_tag_names(content_configs):
    """Return tag occurrences using one tags-only query per configuration.

    ``content_configs`` contains ``(model, filters)`` pairs.  Callers own the
    content-type and publication policy; this helper owns only the projected
    data access so the public tags page and tag sitemap cannot drift back to
    loading complete content rows.
    """
    tag_names = []
    for model_class, filters in content_configs:
        tag_lists = (
            model_class.objects
            .filter(**filters)
            .values_list('tags', flat=True)
        )
        for tags in tag_lists:
            tag_names.extend(tags or [])
    return tag_names


def normalize_tag(tag):
    """Normalize a single tag string.

    Rules:
    - Lowercase
    - Replace spaces with hyphens
    - Strip all characters except letters, digits, hyphens
    - Collapse multiple hyphens into one
    - Strip leading/trailing hyphens

    Examples:
        "Machine Learning" -> "machine-learning"
        "AI & ML" -> "ai-ml"
        "Python 3.12" -> "python-312"
        "  hello  world  " -> "hello-world"
    """
    if not tag or not isinstance(tag, str):
        return ''
    # Lowercase and strip whitespace
    tag = tag.strip().lower()
    # Replace spaces and underscores with hyphens
    tag = tag.replace(' ', '-').replace('_', '-')
    # Remove all characters except letters, digits, hyphens
    tag = re.sub(r'[^a-z0-9-]', '', tag)
    # Collapse multiple hyphens
    tag = re.sub(r'-{2,}', '-', tag)
    # Strip leading/trailing hyphens
    tag = tag.strip('-')
    return tag


def normalize_tags(tags):
    """Normalize a list of tag strings.

    Removes duplicates and empty tags after normalization.
    Preserves order (first occurrence wins for duplicates).
    """
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


def _has_invalid_raw_tag(raw_tag):
    """Return True if a raw tag query param exceeds length or has control chars."""
    if len(raw_tag) > CATALOG_MAX_TAG_LENGTH:
        return True
    for ch in raw_tag:
        code = ord(ch)
        if code < 32 or code == 127:
            return True
    return False


def parse_bounded_selected_tags(raw_tags):
    """Return ``(tags, is_valid)`` for a catalog ``?tag=`` parameter list."""
    if len(raw_tags) > CATALOG_MAX_RAW_TAG_PARAMS:
        return [], False
    tags = []
    seen = set()
    for raw in raw_tags:
        if _has_invalid_raw_tag(raw):
            return [], False
        cleaned = raw.strip()
        if cleaned and cleaned not in seen:
            tags.append(cleaned)
            seen.add(cleaned)
    if len(tags) > CATALOG_MAX_TAGS:
        return [], False
    return tags, True


def invalid_tag_filter_response():
    """Return an uncacheable 400 response for out-of-bounds tag filter queries."""
    response = HttpResponse(
        'Invalid tag filter.',
        status=400,
        content_type='text/plain; charset=utf-8',
    )
    response['Cache-Control'] = 'no-store'
    response['X-Robots-Tag'] = 'noindex, nofollow'
    return response


def resolve_bounded_selected_tags(request):
    """Extract bounded selected tags from ``request.GET`` or return a 400 response."""
    tags, is_valid = parse_bounded_selected_tags(request.GET.getlist('tag'))
    if not is_valid:
        return [], invalid_tag_filter_response()
    return tags, None


def filter_items_by_tags(items, selected_tags):
    """Return items whose ``tags`` list contains every tag in ``selected_tags``."""
    if not selected_tags:
        return list(items)
    required = set(selected_tags)
    matched = []
    for item in items:
        item_tags = set(item.tags or [])
        if required.issubset(item_tags):
            matched.append(item)
    return matched
