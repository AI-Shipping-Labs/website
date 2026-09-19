"""
Django sitemaps for all public content.

Includes:
- All published articles (open and gated)
- All published courses (always public)
- All events (upcoming/completed, including recordings) at /events/<id>/<slug>
- Published projects with required_level=0
- Published tutorials with required_level=0
- Tag pages (index + individual tags)
- Static pages (home, about, blog listing, etc.)
"""

from community_base.knowledge_base.models import (
    SECTION_DOCS,
    SECTION_WIKI,
    KnowledgeBasePage,
)
from django.contrib.sitemaps import Sitemap
from django.urls import reverse

from content.models import (
    Article,
    Course,
    Download,
    MarketingPage,
    Project,
    Tutorial,
    Workshop,
    WorkshopPage,
)
from content.utils.tags import collect_tag_names
from events.models import Event


class ArticleSitemap(Sitemap):
    """Sitemap for all published articles, open and gated."""
    changefreq = 'weekly'
    priority = 0.8

    def items(self):
        return Article.objects.filter(
            published=True,
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


class WorkshopSitemap(Sitemap):
    """Sitemap for published workshop landing pages."""
    changefreq = 'weekly'
    priority = 0.8

    def items(self):
        return Workshop.objects.filter(
            status='published',
        ).order_by('-date')

    def lastmod(self, obj):
        return obj.updated_at

    def location(self, obj):
        return obj.get_absolute_url()


class WorkshopPageSitemap(Sitemap):
    """Sitemap for tutorial pages of published workshops."""
    changefreq = 'weekly'
    priority = 0.6

    def items(self):
        return WorkshopPage.objects.filter(
            workshop__status='published',
        ).select_related('workshop').order_by('workshop_id', 'sort_order')

    def lastmod(self, obj):
        return obj.updated_at

    def location(self, obj):
        return obj.get_absolute_url()


class MarketingPageSitemap(Sitemap):
    """Sitemap for published standalone marketing pages."""
    changefreq = 'monthly'
    priority = 0.6

    def items(self):
        return MarketingPage.objects.filter(
            status='published',
            show_in_sitemap=True,
        ).order_by('public_path')

    def lastmod(self, obj):
        return obj.updated_at

    def location(self, obj):
        return obj.get_absolute_url()


class KnowledgeBaseSitemap(Sitemap):
    """Sitemap for one knowledge base section's published pages (#1685)."""
    changefreq = 'weekly'
    priority = 0.6
    section = ''

    def items(self):
        return KnowledgeBasePage.objects.filter(
            section=self.section,
            status='published',
        ).order_by('slug')

    def lastmod(self, obj):
        return obj.updated_at

    def location(self, obj):
        return obj.get_absolute_url()


class WikiPageSitemap(KnowledgeBaseSitemap):
    section = SECTION_WIKI


class DocsPageSitemap(KnowledgeBaseSitemap):
    section = SECTION_DOCS


class StaticViewSitemap(Sitemap):
    """Sitemap for static pages."""
    changefreq = 'monthly'
    priority = 0.5

    def items(self):
        return [
            'home',
            'about',
            'blog_list',
            'projects_list',
            'courses_list',
            'events_list',
            'downloads_list',
            'tutorials_list',
            'workshops_list',
            'collection_list',
            'tags_index',
            # Issue #1712: the company-interviews list is open; the gated
            # /interview/companies/<slug> detail pages stay out (open-only
            # sitemap convention; #1723 owns the gated-URL policy).
            'company_interviews_list',
            'terms',
            'privacy',
            'impressum',
        ]

    def location(self, item):
        return reverse(item)


TAG_CONTENT_CONFIGS = (
    (Article, {'published': True}),
    (Project, {'published': True}),
    (Tutorial, {'published': True}),
    (Course, {'status': 'published'}),
    (Download, {'published': True}),
    (Event, {}),
)


def _collect_all_tags():
    """Collect all unique tags from all published content types.

    Returns a sorted list of unique tag strings.
    """
    return sorted(set(collect_tag_names(TAG_CONTENT_CONFIGS)))


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
    'projects': ProjectSitemap,
    'tutorials': TutorialSitemap,
    'workshops': WorkshopSitemap,
    'workshop_pages': WorkshopPageSitemap,
    'marketing_pages': MarketingPageSitemap,
    'wiki_pages': WikiPageSitemap,
    'docs_pages': DocsPageSitemap,
    'tags': TagSitemap,
    'static': StaticViewSitemap,
}
