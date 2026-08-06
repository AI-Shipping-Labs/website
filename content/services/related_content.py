"""Deterministic related-content recommendations for public detail pages."""

from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from django.utils.html import strip_tags
from django.utils.text import Truncator

from content.access import LEVEL_OPEN, get_required_tier_label
from content.templatetags.teaser_tags import strip_markdown
from content.utils.tags import normalize_tags

DEFAULT_RELATED_LIMIT = 3
RELATED_TITLE = 'Related content'
FALLBACK_TITLE = 'More from AI Shipping Labs'
DESCRIPTION_CHARS = 180


@dataclass(frozen=True)
class RelatedContentCard:
    """Presentation-only metadata for a related-content card."""

    content_type: str
    content_type_label: str
    icon: str
    title: str
    description: str
    url: str
    date_label: str
    tags: tuple[str, ...]
    required_level: int
    tier_label: str
    is_gated: bool


@dataclass(frozen=True)
class RelatedContentRail:
    """A rendered recommendation set and its heading."""

    title: str
    items: tuple[RelatedContentCard, ...]
    is_fallback: bool


@dataclass(frozen=True)
class _Candidate:
    card: RelatedContentCard
    all_tags: frozenset[str]
    sort_value: int
    title_key: str
    model_key: str
    pk: int


def build_related_content_rail(
    current: Any,
    *,
    limit: int = DEFAULT_RELATED_LIMIT,
) -> RelatedContentRail:
    """Return up to ``limit`` deterministic recommendations for ``current``.

    Matching recommendations share at least one normalized tag with the current
    object and sort by shared-tag count, newest public date, then title. If the
    current object is untagged or no tag matches exist, the rail falls back to
    the newest published internal content pages.
    """
    candidates = list(_iter_candidates(current))
    if limit <= 0 or not candidates:
        return RelatedContentRail(
            title=FALLBACK_TITLE,
            items=(),
            is_fallback=True,
        )

    current_tags = frozenset(normalize_tags(getattr(current, 'tags', [])))
    matching: list[tuple[int, _Candidate]] = []
    if current_tags:
        for candidate in candidates:
            shared_count = len(current_tags & candidate.all_tags)
            if shared_count:
                matching.append((shared_count, candidate))

    if matching:
        ordered = sorted(
            matching,
            key=lambda item: (
                -item[0],
                -item[1].sort_value,
                item[1].title_key,
                item[1].model_key,
                item[1].pk,
            ),
        )
        return RelatedContentRail(
            title=RELATED_TITLE,
            items=tuple(candidate.card for _, candidate in ordered[:limit]),
            is_fallback=False,
        )

    fallback = sorted(
        candidates,
        key=lambda candidate: (
            -candidate.sort_value,
            candidate.title_key,
            candidate.model_key,
            candidate.pk,
        ),
    )
    return RelatedContentRail(
        title=FALLBACK_TITLE,
        items=tuple(candidate.card for candidate in fallback[:limit]),
        is_fallback=True,
    )


def _iter_candidates(current: Any):
    from content.models import Article, Course, Project, Tutorial, Workshop
    from events.models import Event
    from events.models.event import HIDDEN_FROM_PUBLIC_STATUSES

    definitions = (
        (
            Article.objects.filter(published=True, page_type='blog'),
            'article',
            'Article',
            'file-text',
        ),
        (
            Tutorial.objects.filter(published=True),
            'tutorial',
            'Tutorial',
            'book-open',
        ),
        (
            Project.objects.filter(published=True, status='published'),
            'project',
            'Project',
            'rocket',
        ),
        (
            Workshop.objects.filter(status='published'),
            'workshop',
            'Workshop',
            'graduation-cap',
        ),
        (
            Course.objects.filter(status='published'),
            'course',
            'Course',
            'book-marked',
        ),
        (
            Event.objects.filter(published=True)
            .exclude(status__in=HIDDEN_FROM_PUBLIC_STATUSES),
            'event',
            'Event',
            'calendar',
        ),
    )

    current_model_key = _model_key(current)
    current_pk = getattr(current, 'pk', None)

    collected: list[tuple[Any, str, str, str]] = []
    for queryset, content_type, content_type_label, icon in definitions:
        for obj in queryset:
            if (
                current_pk is not None
                and _model_key(obj) == current_model_key
                and obj.pk == current_pk
            ):
                continue
            collected.append((obj, content_type, content_type_label, icon))

    dropped = _dropped_pair_keys(collected, current, current_model_key, current_pk)

    for obj, content_type, content_type_label, icon in collected:
        if (_model_key(obj), obj.pk) in dropped:
            continue
        card = _build_card(obj, content_type, content_type_label, icon)
        if not card.url:
            continue
        yield _Candidate(
            card=card,
            all_tags=frozenset(normalize_tags(getattr(obj, 'tags', []))),
            sort_value=_sort_value(_public_sort_date(obj)),
            title_key=card.title.casefold(),
            model_key=_model_key(obj),
            pk=obj.pk,
        )


def _dropped_pair_keys(
    collected: list[tuple[Any, str, str, str]],
    current: Any,
    current_model_key: str,
    current_pk: Any,
) -> set[tuple[str, int]]:
    """Return ``(model_key, pk)`` keys to drop so linked event/workshop pairs
    never occupy two slots in the same rail.

    A genuine pair is an ``Event`` and the ``Workshop`` whose ``event_id`` points
    at it. The Workshop always wins and the Event is dropped, regardless of the
    event's upcoming/past/null-start state (reporter directive on #1359: the
    durable workshop artifact takes precedence unconditionally). The current
    page's own linked counterpart is always dropped too, since it is a duplicate
    of the thing being viewed.
    """
    events_by_pk = {
        obj.pk: obj for obj, content_type, *_ in collected if content_type == 'event'
    }
    dropped: set[tuple[str, int]] = set()

    for obj, content_type, *_ in collected:
        if content_type != 'workshop':
            continue
        event_id = getattr(obj, 'event_id', None)
        if event_id is None:
            continue
        linked_event = events_by_pk.get(event_id)
        if linked_event is None:
            continue
        dropped.add((_model_key(linked_event), linked_event.pk))

    # Drop the current page's own linked counterpart (the other half of its pair).
    if current_model_key == 'content.workshop':
        linked_event_id = getattr(current, 'event_id', None)
        if linked_event_id is not None:
            dropped.add(('events.event', linked_event_id))
    elif current_model_key == 'events.event' and current_pk is not None:
        for obj, content_type, *_ in collected:
            if (
                content_type == 'workshop'
                and getattr(obj, 'event_id', None) == current_pk
            ):
                dropped.add((_model_key(obj), obj.pk))

    return dropped


def _build_card(
    obj: Any,
    content_type: str,
    content_type_label: str,
    icon: str,
) -> RelatedContentCard:
    required_level = _required_level(obj)
    return RelatedContentCard(
        content_type=content_type,
        content_type_label=content_type_label,
        icon=icon,
        title=str(getattr(obj, 'title', '') or '').strip(),
        description=_description(obj),
        url=obj.get_absolute_url() if hasattr(obj, 'get_absolute_url') else '',
        date_label=_date_label(obj),
        tags=tuple(normalize_tags(getattr(obj, 'tags', []))[:2]),
        required_level=required_level,
        tier_label=get_required_tier_label(required_level),
        is_gated=required_level > LEVEL_OPEN,
    )


def _model_key(obj: Any) -> str:
    return obj._meta.label_lower


def _required_level(obj: Any) -> int:
    if hasattr(obj, 'pages_required_level'):
        return getattr(obj, 'pages_required_level') or LEVEL_OPEN
    return getattr(obj, 'required_level', LEVEL_OPEN) or LEVEL_OPEN


def _description(obj: Any) -> str:
    html_description = getattr(obj, 'description_html', '')
    if html_description:
        text = html.unescape(strip_tags(html_description))
    else:
        raw_description = getattr(obj, 'description', '')
        text = strip_markdown(raw_description)

    text = ' '.join(str(text).split())
    return Truncator(text).chars(DESCRIPTION_CHARS)


def _public_sort_date(obj: Any):
    if hasattr(obj, 'date'):
        return getattr(obj, 'date')
    if hasattr(obj, 'start_datetime'):
        return getattr(obj, 'start_datetime')
    published_at = getattr(obj, 'published_at', None)
    if published_at is not None:
        return published_at
    return getattr(obj, 'created_at', None)


def _date_label(obj: Any) -> str:
    # Every card renders one non-zero-padded day form (``July 8, 2026``). The
    # models' own ``formatted_date()`` helpers disagree on padding
    # (``Event.formatted_date()`` is non-padded, but Article/Tutorial/Project use
    # zero-padded ``%B %d, %Y``), so the rail resolves the underlying date itself
    # rather than delegating, keeping one consistent format across card types.
    value = getattr(obj, 'date', None)
    if value is None:
        value = getattr(obj, 'start_datetime', None)
    if value is None:
        value = getattr(obj, 'published_at', None)

    if isinstance(value, (date, datetime)):
        return f'{value.strftime("%B")} {value.day}, {value.year}'
    return ''


def _sort_value(value: Any) -> int:
    if isinstance(value, datetime):
        return (
            value.toordinal() * 86_400
            + value.hour * 3_600
            + value.minute * 60
            + value.second
        )
    if isinstance(value, date):
        return value.toordinal() * 86_400
    return 0
