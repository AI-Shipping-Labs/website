"""Views for /tags and /tags/{tag} pages.

/tags - Lists all tags with content counts, sorted by count descending.
/tags/{tag} - Lists all content with that tag across all content types.
"""

from collections import Counter

from django.shortcuts import render

from content.models import Article, Recording, Project, CuratedLink, Download, Course
from events.models import Event


# Content type configuration: (model_class, published_filter, date_field, type_label, url_func)
CONTENT_TYPES = [
    {
        'model': Article,
        'filter': {'published': True},
        'date_field': 'date',
        'type_label': 'Article',
        'type_color': 'bg-blue-500/20 text-blue-400',
        'url_func': lambda obj: f'/blog/{obj.slug}',
    },
    {
        'model': Recording,
        'filter': {'published': True},
        'date_field': 'date',
        'type_label': 'Recording',
        'type_color': 'bg-purple-500/20 text-purple-400',
        'url_func': lambda obj: f'/event-recordings/{obj.slug}',
    },
    {
        'model': Project,
        'filter': {'published': True},
        'date_field': 'date',
        'type_label': 'Project',
        'type_color': 'bg-green-500/20 text-green-400',
        'url_func': lambda obj: f'/projects/{obj.slug}',
    },
    {
        'model': Course,
        'filter': {'status': 'published'},
        'date_field': 'created_at',
        'type_label': 'Course',
        'type_color': 'bg-orange-500/20 text-orange-400',
        'url_func': lambda obj: f'/courses/{obj.slug}',
    },
    {
        'model': Download,
        'filter': {'published': True},
        'date_field': 'created_at',
        'type_label': 'Download',
        'type_color': 'bg-red-500/20 text-red-400',
        'url_func': lambda obj: f'/downloads/{obj.slug}',
    },
    {
        'model': Event,
        'filter': {},  # Events have status field, show upcoming/completed
        'date_field': 'start_datetime',
        'type_label': 'Event',
        'type_color': 'bg-yellow-500/20 text-yellow-400',
        'url_func': lambda obj: f'/events/{obj.slug}',
    },
]


def _get_all_published_items():
    """Return all published content items across all types."""
    items = []
    for ct in CONTENT_TYPES:
        queryset = ct['model'].objects.filter(**ct['filter'])
        for obj in queryset:
            if obj.tags:
                items.append((obj, ct))
    return items


def _collect_all_tags():
    """Collect all tags from all content types with counts.

    Returns a list of (tag, count) tuples sorted by count descending.
    """
    tag_counter = Counter()
    for ct in CONTENT_TYPES:
        queryset = ct['model'].objects.filter(**ct['filter'])
        for obj in queryset:
            if obj.tags:
                for tag in obj.tags:
                    tag_counter[tag] += 1
    return tag_counter.most_common()


def tags_index(request):
    """GET /tags - Show all tags with content count per tag."""
    tag_counts = _collect_all_tags()

    context = {
        'tag_counts': tag_counts,
        'total_tags': len(tag_counts),
    }
    return render(request, 'content/tags_index.html', context)


def tags_detail(request, tag):
    """GET /tags/{tag} - Show all content with that tag across all types."""
    # Collect all items with this tag
    results = []
    for ct in CONTENT_TYPES:
        queryset = ct['model'].objects.filter(**ct['filter'])
        for obj in queryset:
            if obj.tags and tag in obj.tags:
                date_val = getattr(obj, ct['date_field'])
                results.append({
                    'title': obj.title,
                    'description': getattr(obj, 'description', ''),
                    'url': ct['url_func'](obj),
                    'date': date_val,
                    'type_label': ct['type_label'],
                    'type_color': ct['type_color'],
                    'tags': obj.tags,
                })

    # Sort by date descending
    results.sort(key=lambda x: x['date'], reverse=True)

    context = {
        'tag': tag,
        'results': results,
        'result_count': len(results),
    }
    return render(request, 'content/tags_detail.html', context)
