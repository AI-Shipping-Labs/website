"""Short-lived lookup sets used to reject unknown public listing facets."""

from django.core.cache import cache

from content.models import Article, Course, CuratedLink, Download, Project, Workshop
from content.topics import BLOG_TOPICS, WORKSHOP_TOPICS
from content.utils.tags import collect_tag_names
from events.models import Event
from events.models.event import PUBLIC_EVENT_STATUSES

FACET_VALUE_CACHE_TIMEOUT = 300
_FACET_CACHE_PREFIX = "anon-page:v1:facets"


def _listing_tag_sources(path):
    """Return published model filters that supply tags for a listing."""
    sources = {
        "/blog": ((Article, {"published": True, "page_type": "blog"}),),
        "/courses": ((Course, {
            "status": "published",
            "aisl_extension__access_mode": "tier",
        }),),
        "/downloads": ((Download, {"published": True}),),
        "/events": ((Event, {"status__in": PUBLIC_EVENT_STATUSES}),),
        "/projects": ((Project, {"published": True}),),
        "/resources": ((CuratedLink, {"published": True}),),
        "/workshops/catalog": ((Workshop, {"status": "published"}),),
    }
    return sources.get(path, ())


def _public_tag_sources():
    """Return published content types represented on the public tag index."""
    return (
        (Article, {"published": True}),
        (Project, {"published": True}),
        (Course, {"status": "published"}),
        (Download, {"published": True}),
        (Event, {"status__in": PUBLIC_EVENT_STATUSES}),
    )


def _collect_tags(sources):
    """Collect distinct string tags with narrow values-list queries."""
    tags = set()
    for tag in collect_tag_names(sources):
        cleaned = str(tag).strip()
        if cleaned:
            tags.add(cleaned)
    return tuple(sorted(tags))


def _facet_cache_key(path, facet):
    return f"{_FACET_CACHE_PREFIX}:{path}:{facet}"


def _topic_values(path):
    """Return curated topics represented by currently published tags."""
    taxonomy = {
        "/blog": BLOG_TOPICS,
        "/workshops/catalog": WORKSHOP_TOPICS,
    }.get(path)
    if taxonomy is None:
        return ()
    available_tags = {tag.lower() for tag in known_public_tags(path)}
    matches = []
    for slug, definition in taxonomy.items():
        topic_tags = {tag.lower() for tag in definition["tags"]}
        if available_tags.intersection(topic_tags):
            matches.append(slug)
    return tuple(matches)


def _difficulty_values():
    """Return difficulty filters currently available on published projects."""
    values = set(
        Project.objects.filter(published=True)
        .values_list("difficulty", flat=True)
    )
    if "" in values:
        values.add("unspecified")
    values.discard("")
    return tuple(sorted(values))


def _load_facet_values(path, facet):
    """Load one bounded public facet value set for the five-minute cache."""
    if facet == "tag":
        return known_public_tags(path)
    if facet == "topic":
        return _topic_values(path)
    if facet == "difficulty" and path == "/projects":
        return _difficulty_values()
    return ()


def known_public_tags(path=None):
    """Return published public tags, optionally scoped to one listing route."""
    normalized_path = path.rstrip("/") if path else None
    cache_path = normalized_path or "all-public"
    key = _facet_cache_key(cache_path, "tag")
    values = cache.get(key)
    if values is None:
        sources = (
            _listing_tag_sources(normalized_path)
            if normalized_path else _public_tag_sources()
        )
        values = _collect_tags(sources)
        cache.set(key, values, FACET_VALUE_CACHE_TIMEOUT)
    return frozenset(values)


def known_facet_values(path, facet):
    """Read a route-specific facet set, computing it at most every five minutes."""
    key = _facet_cache_key(path, facet)
    values = cache.get(key)
    if values is None:
        values = _load_facet_values(path, facet)
        cache.set(key, values, FACET_VALUE_CACHE_TIMEOUT)
    return frozenset(values)
