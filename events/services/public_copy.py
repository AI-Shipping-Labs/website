"""Public-copy cleanup for event and series descriptions.

Operator notes are sometimes stored in the description field so staff can
see them in Studio. The course reader already refuses to show them. The
event page and the series page use the same rule.
"""

from __future__ import annotations

import re

from django.utils.html import strip_tags

_INTERNAL_DESCRIPTION_NOTE = re.compile(
    r'\s*(?:hidden series|internal note|operator note|staff note|'
    r'registration operations|registration setup)\s*:[^.!?]*(?:[.!?]|$)',
    re.IGNORECASE,
)


def strip_internal_description_notes(value: str) -> str:
    """Drop operator-note sentences from rendered or plain description copy.

    Returns an empty string when nothing a member should read remains,
    including a paragraph wrapper left behind by the removed sentence.
    """
    if not value:
        return ''
    cleaned = _INTERNAL_DESCRIPTION_NOTE.sub('', value).strip()
    if not strip_tags(cleaned).strip():
        return ''
    return cleaned
