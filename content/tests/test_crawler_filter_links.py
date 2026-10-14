from datetime import date
from html.parser import HTMLParser
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

from allauth.socialaccount.models import SocialApp
from django.contrib.sites.models import Site
from django.template.loader import render_to_string
from django.test import TestCase, tag

from content.models import Project


class _AnchorParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.anchors = []
        self.filter_empty_anchors = []
        self._element_stack = []

    def handle_starttag(self, tag_name, attrs):
        attrs = dict(attrs)
        inside_filter_empty = any(item[1] for item in self._element_stack)
        if tag_name == "a":
            self.anchors.append(attrs)
            if inside_filter_empty:
                self.filter_empty_anchors.append(attrs)
        is_filter_empty = attrs.get("data-empty-kind") == "filter"
        self._element_stack.append(
            (tag_name, inside_filter_empty or is_filter_empty),
        )

    def handle_startendtag(self, tag_name, attrs):
        if tag_name == "a":
            attrs = dict(attrs)
            self.anchors.append(attrs)
            if any(item[1] for item in self._element_stack):
                self.filter_empty_anchors.append(attrs)

    def handle_endtag(self, tag_name):
        for index in range(len(self._element_stack) - 1, -1, -1):
            if self._element_stack[index][0] == tag_name:
                del self._element_stack[index:]
                break


def _anchors(html):
    parser = _AnchorParser()
    parser.feed(html)
    return parser.anchors


def _has_nofollow(anchor):
    return "nofollow" in anchor.get("rel", "").split()


def _find(anchors, *, testid=None, href=None):
    matches = anchors
    if testid is not None:
        matches = [a for a in matches if a.get("data-testid") == testid]
    if href is not None:
        matches = [a for a in matches if a.get("href") == href]
    return matches


@tag("core")
class CrawlerAuthLinkTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        site = Site.objects.get_current()
        for provider, name in (
            ("google", "Google"),
            ("github", "GitHub"),
            ("slack", "Slack"),
        ):
            app = SocialApp.objects.create(
                provider=provider,
                name=name,
                client_id=f"{provider}-crawler-client",
                secret=f"{provider}-crawler-secret",
            )
            app.sites.add(site)

    def test_header_desktop_and_mobile_auth_links_are_nofollow(self):
        response = self.client.get(
            "/projects",
            {"difficulty": "intermediate", "tag": ["gemini", "agents"]},
        )
        anchors = _anchors(response.content.decode())

        for testid, expected_count in (
            ("header-join-free-link", 1),
            ("mobile-header-join-free-link", 1),
            ("header-sign-in-link", 2),
        ):
            matches = _find(anchors, testid=testid)
            self.assertEqual(len(matches), expected_count)
            self.assertTrue(all(_has_nofollow(anchor) for anchor in matches))

        expected_next = "/projects?difficulty=intermediate&tag=gemini&tag=agents"
        for anchor in _find(anchors, testid="header-sign-in-link"):
            self.assertEqual(parse_qs(urlparse(anchor["href"]).query)["next"], [expected_next])

    def test_every_social_login_link_is_nofollow_and_keeps_next(self):
        next_url = "/projects?difficulty=intermediate&tag=gemini&tag=agents"
        response = self.client.get("/accounts/login/", {"next": next_url})
        anchors = _anchors(response.content.decode())

        for provider in ("google", "github", "slack"):
            matches = _find(anchors, testid=f"oauth-{provider}-action")
            self.assertEqual(len(matches), 1)
            self.assertTrue(_has_nofollow(matches[0]))
            self.assertEqual(
                parse_qs(urlparse(matches[0]["href"]).query)["next"],
                [next_url],
            )


@tag("core")
class CrawlerFilterLinkTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.project = Project.objects.create(
            title="Crawler Link Project",
            slug="crawler-link-project",
            description="A project used to exercise filter links.",
            date=date(2026, 10, 15),
            author="AI Shipping Labs",
            difficulty="intermediate",
            tags=["gemini", "agents"],
            source_code_url="https://github.com/example/crawler-link-project",
            published=True,
        )

    def test_project_combination_filters_and_card_tags_are_nofollow(self):
        response = self.client.get(
            "/projects",
            {"difficulty": "intermediate", "tag": "gemini"},
        )
        anchors = _anchors(response.content.decode())

        filter_links = [
            anchor
            for anchor in anchors
            if anchor.get("data-testid", "").startswith("project-difficulty-")
            or anchor.get("data-testid", "").startswith("project-tag-")
            or anchor.get("data-testid") == "project-card-tag-link"
        ]
        self.assertGreaterEqual(len(filter_links), 5)
        self.assertTrue(all(_has_nofollow(anchor) for anchor in filter_links))
        self.assertTrue(
            any(
                "difficulty=intermediate" in anchor.get("href", "")
                and "tag=" in anchor.get("href", "")
                for anchor in filter_links
            )
        )

        project_links = _find(anchors, href=self.project.get_absolute_url())
        self.assertEqual(len(project_links), 1)
        self.assertFalse(_has_nofollow(project_links[0]))

    def test_detail_filter_tags_are_nofollow_but_content_link_is_followable(self):
        response = self.client.get(self.project.get_absolute_url())
        anchors = _anchors(response.content.decode())

        for tag_name in self.project.tags:
            matches = _find(anchors, href=f"/projects?tag={tag_name}")
            self.assertEqual(len(matches), 1)
            self.assertTrue(_has_nofollow(matches[0]))

        source = _find(anchors, href=self.project.source_code_url)
        self.assertEqual(len(source), 1)
        self.assertEqual(set(source[0]["rel"].split()), {"noopener", "noreferrer"})
        self.assertFalse(_has_nofollow(source[0]))

    def test_topic_and_tag_catalog_controls_render_nofollow(self):
        topic = SimpleNamespace(slug="agents", label="Agents")
        rendered = {
            "blog": render_to_string(
                "content/blog_list.html",
                {
                    "topic_filters": [topic],
                    "selected_topic": "agents",
                    "selected_tags": ["missing"],
                    "articles": [],
                },
            ),
            "workshops": render_to_string(
                "content/_workshops_catalog.html",
                {
                    "show_catalog_filters": True,
                    "topic_filters": [topic],
                    "selected_topic": "agents",
                    "selected_tags": ["missing"],
                    "base_path": "/workshops/catalog",
                    "workshops": [],
                    "has_published_workshops": True,
                    "has_active_filters": True,
                    "clear_filters_url": "/workshops/catalog",
                },
            ),
            "resources": render_to_string(
                "content/collection_list.html",
                {
                    "all_tags": ["agents"],
                    "selected_tags": ["missing"],
                    "base_path": "/resources",
                    "grouped_categories": [],
                },
            ),
            "downloads": render_to_string(
                "content/downloads_list.html",
                {
                    "all_tags": ["agents"],
                    "selected_tags": ["missing"],
                    "base_path": "/downloads",
                    "downloads": [],
                },
            ),
        }

        expected_hrefs = {
            "blog": "/blog?topic=agents",
            "workshops": "/workshops/catalog?topic=agents",
            "resources": "/resources?tag=missing&tag=agents",
            "downloads": "/downloads?tag=missing&tag=agents",
        }
        for surface, html in rendered.items():
            matches = _find(_anchors(html), href=expected_hrefs[surface])
            self.assertEqual(len(matches), 1, surface)
            self.assertTrue(_has_nofollow(matches[0]), surface)

        clear_hrefs = {
            "blog": "/blog",
            "workshops": "/workshops/catalog",
            "resources": "/resources",
            "downloads": "/downloads",
        }
        for surface, html in rendered.items():
            parser = _AnchorParser()
            parser.feed(html)
            matches = _find(
                parser.filter_empty_anchors,
                href=clear_hrefs[surface],
            )
            self.assertEqual(len(matches), 1, surface)
            self.assertTrue(_has_nofollow(matches[0]), surface)

    def test_project_and_event_empty_filter_recovery_links_are_nofollow(self):
        cases = (
            ("/projects?tag=missing", "/projects"),
            ("/events?filter=past&tag=missing", "/events?filter=past"),
        )
        for request_url, clear_href in cases:
            response = self.client.get(request_url)
            parser = _AnchorParser()
            parser.feed(response.content.decode())
            matches = _find(parser.filter_empty_anchors, href=clear_href)
            self.assertEqual(len(matches), 1, request_url)
            self.assertTrue(_has_nofollow(matches[0]), request_url)

        event_tabs = _find(
            parser.anchors,
            testid="events-filter-upcoming",
        )
        self.assertEqual(len(event_tabs), 1)
        self.assertTrue(_has_nofollow(event_tabs[0]))

    def test_event_tag_filter_partial_renders_nofollow(self):
        html = render_to_string(
            "events/_upcoming_event_card_tags.html",
            {
                "event": SimpleNamespace(tags=["agents"]),
                "filter_mode": "past",
            },
        )
        matches = _find(_anchors(html), href="/events?filter=past&tag=agents")
        self.assertEqual(len(matches), 1)
        self.assertTrue(_has_nofollow(matches[0]))
