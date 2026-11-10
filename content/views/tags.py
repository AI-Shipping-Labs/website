"""Views for /tags and /tags/{tag} pages."""

import datetime as dt
from collections import Counter

from django.db import connections
from django.http import Http404
from django.shortcuts import render

from content.caching import cache_anonymous_page, known_public_tags
from content.models import Article, Course, Download, Project
from content.utils.tags import collect_tag_names
from events.models import Event
from events.models.event import PUBLIC_EVENT_STATUSES

CONTENT_TYPES = [
    {
        'model': Article,
        'filter': {'published': True},
        'date_field': 'date',
        'only_fields': ('id', 'tags', 'title', 'description', 'slug', 'date'),
        'type_label': 'Article',
        'type_color': 'bg-blue-500/15 text-blue-800 dark:text-blue-400',
        'url_func': lambda obj: f'/blog/{obj.slug}',
    },
    {
        'model': Project,
        'filter': {'published': True},
        'date_field': 'date',
        'only_fields': ('id', 'tags', 'title', 'description', 'slug', 'date'),
        'type_label': 'Project',
        'type_color': 'bg-green-500/15 text-green-800 dark:text-green-400',
        'url_func': lambda obj: f'/projects/{obj.slug}',
    },
    {
        'model': Course,
        'filter': {'status': 'published'},
        'date_field': 'created_at',
        'only_fields': (
            'id', 'tags', 'title', 'description', 'slug', 'created_at',
        ),
        'type_label': 'Course',
        'type_color': 'bg-orange-500/15 text-orange-800 dark:text-orange-400',
        'url_func': lambda obj: f'/courses/{obj.slug}',
    },
    {
        'model': Download,
        'filter': {'published': True},
        'date_field': 'created_at',
        'only_fields': (
            'id', 'tags', 'title', 'description', 'slug', 'created_at',
        ),
        'type_label': 'Download',
        'type_color': 'bg-red-500/15 text-red-800 dark:text-red-400',
        'url_func': lambda obj: f'/downloads/{obj.slug}',
    },
    {
        'model': Event,
        'filter': {'status__in': PUBLIC_EVENT_STATUSES},
        'date_field': 'start_datetime',
        'only_fields': (
            'id', 'tags', 'title', 'description', 'slug', 'start_datetime',
        ),
        'type_label': 'Event',
        'type_color': 'bg-yellow-500/15 text-yellow-800 dark:text-yellow-400',
        'url_func': lambda obj: obj.get_absolute_url(),
    },
]


def _collect_all_tags():
    """Return public tag counts sorted by descending count."""
    tag_counter = Counter(collect_tag_names(
        (ct['model'], ct['filter']) for ct in CONTENT_TYPES
    ))
    return tag_counter.most_common()


@cache_anonymous_page()
def tags_index(request):
    """GET /tags - Show all tags with content count per tag."""
    tag_counts = _collect_all_tags()
    context = {'tag_counts': tag_counts, 'total_tags': len(tag_counts)}
    return render(request, 'content/tags_index.html', context)


def _tagged_queryset(content_type, tag):
    """Filter published rows by JSON tag in SQL where the backend supports it."""
    queryset = content_type['model'].objects.filter(**content_type['filter'])
    if connections[queryset.db].vendor == 'postgresql':
        queryset = queryset.filter(tags__contains=[tag])
    return queryset.only(*content_type['only_fields'])


def _tagged_items(content_type, tag):
    """Yield matching content rows, retaining SQLite JSONField behavior."""
    queryset = _tagged_queryset(content_type, tag)
    if connections[queryset.db].vendor == 'postgresql':
        yield from queryset
        return
    yield from (obj for obj in queryset if tag in (obj.tags or []))


def _result_for(content_type, obj):
    """Build one template result from a tagged model instance."""
    return {
        'title': obj.title,
        'description': getattr(obj, 'description', ''),
        'url': content_type['url_func'](obj),
        'date': getattr(obj, content_type['date_field']),
        'type_label': content_type['type_label'],
        'type_color': content_type['type_color'],
        'tags': obj.tags,
    }


def _tag_result_sort_key(result):
    """Normalize dates and datetimes to a comparable UTC datetime."""
    value = result['date']
    if isinstance(value, dt.datetime):
        return value
    if isinstance(value, dt.date):
        return dt.datetime.combine(value, dt.time.min, tzinfo=dt.timezone.utc)
    return dt.datetime.min.replace(tzinfo=dt.timezone.utc)


@cache_anonymous_page()
def tags_detail(request, tag):
    """GET /tags/{tag} - Show public content with that tag."""
    if tag not in known_public_tags():
        raise Http404

    results = [
        _result_for(content_type, obj)
        for content_type in CONTENT_TYPES
        for obj in _tagged_items(content_type, tag)
    ]
    results.sort(key=_tag_result_sort_key, reverse=True)
    context = {'tag': tag, 'results': results, 'result_count': len(results)}
    return render(request, 'content/tags_detail.html', context)
