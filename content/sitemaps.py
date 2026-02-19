"""
Django sitemaps for all public content.

Includes:
- Published articles with required_level=0
- All published courses (always public)
- All events (upcoming/completed)
- Published recordings with required_level=0
- Published projects with required_level=0
- Published tutorials with required_level=0
- Tag pages (index + individual tags)
- Static pages (home, about, blog listing, etc.)
"""

from django.contrib.sitemaps import Sitemap
from django.urls import reverse

from content.models import Article, Course, Recording, Project, Tutorial, Download
from events.models import Event


class ArticleSitemap(Sitemap):
    """Sitemap for published, open (non-gated) articles."""
    changefreq = 'weekly'
    priority = 0.8

    def items(self):
        return Article.objects.filter(
            published=True,
            required_level=0,
        ).order_by('-date')

    def lastmod(self, obj):
        return obj.updated_at

    def location(self, obj):
        return obj.get_absolute_url()


class CourseSitemap(Sitemap):
    """Sitemap for published courses (always public catalog)."""
    changefreq = 'weekly'
    priority = 0.9

    def items(self):
        return Course.objects.filter(
            status='published',
        ).order_by('-created_at')

    def lastmod(self, obj):
        return obj.updated_at

    def location(self, obj):
        return obj.get_absolute_url()


class EventSitemap(Sitemap):
    """Sitemap for all non-draft events."""
    changefreq = 'daily'
    priority = 0.7

    def items(self):
        return Event.objects.exclude(
            status='draft',
        ).order_by('-start_datetime')

    def lastmod(self, obj):
        return obj.updated_at

    def location(self, obj):
        return obj.get_absolute_url()


class RecordingSitemap(Sitemap):
    """Sitemap for published, open recordings."""
    changefreq = 'weekly'
    priority = 0.7

    def items(self):
        return Recording.objects.filter(
            published=True,
            required_level=0,
        ).order_by('-date')

    def lastmod(self, obj):
        return obj.updated_at

    def location(self, obj):
        return obj.get_absolute_url()


class ProjectSitemap(Sitemap):
    """Sitemap for published, open projects."""
    changefreq = 'weekly'
    priority = 0.6

    def items(self):
        return Project.objects.filter(
            published=True,
            required_level=0,
        ).order_by('-date')

    def lastmod(self, obj):
        return obj.updated_at

    def location(self, obj):
        return obj.get_absolute_url()


class TutorialSitemap(Sitemap):
    """Sitemap for published, open tutorials."""
    changefreq = 'weekly'
    priority = 0.7

    def items(self):
        return Tutorial.objects.filter(
            published=True,
            required_level=0,
        ).order_by('-date')

    def lastmod(self, obj):
        return obj.updated_at

    def location(self, obj):
        return obj.get_absolute_url()


class StaticViewSitemap(Sitemap):
    """Sitemap for static pages."""
    changefreq = 'monthly'
    priority = 0.5

    def items(self):
        return [
            'home',
            'about',
            'blog_list',
            'recordings_list',
            'projects_list',
            'courses_list',
            'events_list',
            'downloads_list',
            'tutorials_list',
            'collection_list',
            'tags_index',
        ]

    def location(self, item):
        return reverse(item)


def _collect_all_tags():
    """Collect all unique tags from all published content types.

    Returns a sorted list of unique tag strings.
    """
    tag_set = set()
    # Content types with their published filters
    content_configs = [
        (Article, {'published': True}),
        (Recording, {'published': True}),
        (Project, {'published': True}),
        (Tutorial, {'published': True}),
        (Course, {'status': 'published'}),
        (Download, {'published': True}),
        (Event, {}),
    ]
    for model_class, filters in content_configs:
        for obj in model_class.objects.filter(**filters):
            if obj.tags:
                tag_set.update(obj.tags)
    return sorted(tag_set)


class TagSitemap(Sitemap):
    """Sitemap for individual tag pages (/tags/{tag})."""
    changefreq = 'weekly'
    priority = 0.4

    def items(self):
        return _collect_all_tags()

    def location(self, item):
        return reverse('tags_detail', kwargs={'tag': item})


# Collected sitemaps dict for use in urls.py
sitemaps = {
    'articles': ArticleSitemap,
    'courses': CourseSitemap,
    'events': EventSitemap,
    'recordings': RecordingSitemap,
    'projects': ProjectSitemap,
    'tutorials': TutorialSitemap,
    'tags': TagSitemap,
    'static': StaticViewSitemap,
}
