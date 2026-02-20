"""Tag normalization utilities for content models.

Tags are stored as string[] (JSONField) on content items. This module provides
normalization: lowercase, replace spaces with hyphens, strip special characters.
"""

import re


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
