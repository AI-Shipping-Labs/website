"""Focused taxonomy and catalog facet coverage for issue #1244."""

from html.parser import HTMLParser

from django.test import SimpleTestCase, TestCase, tag
from django.utils import timezone

from content.models import Workshop
from content.workshop_facets import (
    EXCLUDED_TAGS,
    FACET_EXCLUDED,
    FACET_TECHNOLOGY,
    FACET_TOPIC,
    TECHNOLOGY_TAGS,
    facet_for_tag,
)
from tests.fixtures import TierSetupMixin

CATALOG_URL = "/workshops/catalog"
VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "source", "track", "wbr",
}


class _ElementParser(HTMLParser):
    """Collect elements with text and facet-container ancestry."""

    def __init__(self):
        super().__init__()
        self.elements = []
        self.stack = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        element = {
            "tag": tag,
            "attrs": attributes,
            "text": "",
            "facet_ancestors": [
                item["attrs"].get("data-testid")
                for item in self.stack
                if item["attrs"].get("data-testid", "").startswith(
                    "workshop-facet-"
                )
            ],
        }
        self.elements.append(element)
        if tag not in VOID_TAGS:
            self.stack.append(element)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index]["tag"] == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        for element in self.stack:
            element["text"] += data


def _parse(response):
    parser = _ElementParser()
    parser.feed(response.content.decode())
    return parser


def _make_workshop(*, slug, title, tags, pages=0, core_tools=None,
                   status="published"):
    return Workshop.objects.create(
        slug=slug,
        title=title,
        status=status,
        date=timezone.localdate(),
        landing_required_level=0,
        pages_required_level=pages,
        recording_required_level=pages,
        tags=tags,
        core_tools=core_tools or [],
    )


class WorkshopFacetTaxonomyTest(SimpleTestCase):
    def test_initial_taxonomy_sets_and_default_are_explicit(self):
        self.assertEqual(
            TECHNOLOGY_TAGS,
            {
                "django", "python", "fastapi", "react", "mcp",
                "temporal", "elasticsearch", "vllm", "runpod", "gpu",
                "claude-code", "ci-cd",
            },
        )
        self.assertEqual(
            EXCLUDED_TAGS,
            {
                "comparison", "personal-brand", "linkedin", "writing",
                "portfolio", "job-search", "project-selection", "cv",
            },
        )
        self.assertTrue(TECHNOLOGY_TAGS.isdisjoint(EXCLUDED_TAGS))
        self.assertEqual(facet_for_tag("django"), FACET_TECHNOLOGY)
        self.assertEqual(facet_for_tag("personal-brand"), FACET_EXCLUDED)
        self.assertEqual(facet_for_tag("new-theme"), FACET_TOPIC)
        self.assertEqual(facet_for_tag("Django"), FACET_TOPIC)


class WorkshopCatalogFacetTest(TierSetupMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.free_combo = _make_workshop(
            slug="free-rag-django",
            title="Free RAG Django",
            tags=["rag", "django", "personal-brand"],
        )
        cls.free_rag = _make_workshop(
            slug="free-rag-only",
            title="Free RAG Only",
            tags=["rag"],
        )
        cls.paid_combo = _make_workshop(
            slug="paid-rag-agents",
            title="Paid RAG Agents",
            tags=["ai-agents", "rag", "django", "python"],
            pages=10,
        )
        cls.career = _make_workshop(
            slug="career-brand",
            title="Career Brand",
            tags=["career", "personal-brand"],
        )
        cls.authored_python = _make_workshop(
            slug="authored-python",
            title="Authored Python Tool",
            tags=["search"],
            core_tools=["Python"],
        )
        cls.draft = _make_workshop(
            slug="draft-react",
            title="Draft React Workshop",
            tags=["draft-topic", "react"],
            core_tools=["Hidden Tool"],
            status="draft",
        )

    def test_catalog_renders_separate_labeled_facets_from_published_data(self):
        response = self.client.get(CATALOG_URL)
        parser = _parse(response)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [option["label"] for option in response.context["topic_options"]],
            ["ai-agents", "career", "rag", "search"],
        )
        self.assertEqual(
            [
                option["label"]
                for option in response.context["technology_options"]
            ],
            ["django", "Python"],
        )

        containers = {
            element["attrs"].get("data-testid"): element
            for element in parser.elements
            if element["attrs"].get("data-testid", "").startswith(
                "workshop-facet-"
            )
        }
        self.assertEqual(
            set(containers),
            {"workshop-facet-topic", "workshop-facet-technology"},
        )
        self.assertIn("Topics", containers["workshop-facet-topic"]["text"])
        self.assertIn(
            "Technologies",
            containers["workshop-facet-technology"]["text"],
        )

        facet_chips = [
            element for element in parser.elements
            if element["tag"] == "a" and element["attrs"].get("data-facet")
        ]
        self.assertGreater(len(facet_chips), 0)
        for chip in facet_chips:
            facet = chip["attrs"]["data-facet"]
            self.assertEqual(
                chip["facet_ancestors"],
                [f"workshop-facet-{facet}"],
            )

        rag = next(chip for chip in facet_chips if chip["text"].strip() == "rag")
        django = next(
            chip for chip in facet_chips if chip["text"].strip() == "django"
        )
        self.assertEqual(rag["attrs"]["data-facet"], "topic")
        self.assertEqual(django["attrs"]["data-facet"], "technology")
        self.assertFalse(
            any(chip["text"].strip() == "personal-brand" for chip in facet_chips)
        )
        self.assertFalse(
            any(chip["text"].strip() in {"draft-topic", "react", "Hidden Tool"}
                for chip in facet_chips)
        )

        body = response.content.decode()
        for retired_testid in (
            "workshop-topic-browser",
            "workshop-topic-options",
            "workshop-tool-filters",
        ):
            self.assertNotIn(retired_testid, body)

    def test_access_and_tag_facets_compose_with_and_semantics(self):
        free_rag = self.client.get(f"{CATALOG_URL}?access=free&tag=rag")
        self.assertContains(free_rag, "Free RAG Django")
        self.assertContains(free_rag, "Free RAG Only")
        self.assertNotContains(free_rag, "Paid RAG Agents")

        narrowed = self.client.get(
            f"{CATALOG_URL}?access=free&tag=rag&tag=django"
        )
        self.assertContains(narrowed, "Free RAG Django")
        self.assertNotContains(narrowed, "Free RAG Only")
        self.assertNotContains(narrowed, "Paid RAG Agents")
        parser = _parse(narrowed)
        active = {
            element["text"].strip()
            for element in parser.elements
            if element["tag"] == "a"
            and element["attrs"].get("data-facet")
            and element["attrs"].get("aria-current") == "page"
        }
        self.assertEqual(active, {"rag", "django"})
        self.assertEqual(
            narrowed.context["selected_topic_summary"],
            "Workshops about rag",
        )
        self.assertEqual(
            narrowed.context["selected_filter_summary"],
            "Workshops matching selected filters",
        )
        active_tags = {
            element["text"].strip(): element["attrs"].get("aria-label")
            for element in parser.elements
            if element["attrs"].get("data-testid") == "workshop-active-tag"
        }
        self.assertEqual(active_tags, {
            "rag": "Remove rag topic filter",
            "django": "Remove django technology filter",
        })
        free_option = next(
            element for element in parser.elements
            if element["attrs"].get("data-testid")
            == "workshop-access-filter-free"
        )
        self.assertEqual(free_option["attrs"].get("aria-current"), "page")

    def test_core_tool_wins_case_insensitive_collision_with_technology_tag(self):
        response = self.client.get(CATALOG_URL)
        python_options = [
            option for option in response.context["technology_options"]
            if option["label"].casefold() == "python"
        ]
        self.assertEqual(len(python_options), 1)
        self.assertEqual(python_options[0]["source"], "tool")
        self.assertEqual(python_options[0]["url"], f"{CATALOG_URL}?tool=Python")

        parser = _parse(response)
        python_chip = next(
            element for element in parser.elements
            if element["attrs"].get("data-testid")
            == "workshop-technology-option-python"
        )
        self.assertEqual(python_chip["attrs"].get("data-tool"), "Python")
        self.assertNotIn("data-topic", python_chip["attrs"])

        deep_link = self.client.get(f"{CATALOG_URL}?tag=python")
        self.assertContains(deep_link, "Paid RAG Agents")
        self.assertNotContains(deep_link, "Authored Python Tool")
        self.assertContains(deep_link, 'data-testid="workshop-active-tag"')

    def test_excluded_deep_link_remains_filterable_and_removable(self):
        response = self.client.get(f"{CATALOG_URL}?tag=personal-brand")
        self.assertContains(response, "Free RAG Django")
        self.assertContains(response, "Career Brand")
        self.assertNotContains(response, "Free RAG Only")
        self.assertContains(response, 'data-testid="workshop-active-tag"')
        parser = _parse(response)
        self.assertFalse(
            any(
                element["attrs"].get("data-facet")
                and element["text"].strip() == "personal-brand"
                for element in parser.elements
            )
        )
        active_tag = next(
            element for element in parser.elements
            if element["attrs"].get("data-testid") == "workshop-active-tag"
        )
        self.assertEqual(active_tag["attrs"].get("href"), CATALOG_URL)
        self.assertEqual(
            active_tag["attrs"].get("aria-label"),
            "Remove personal-brand filter",
        )
        self.assertEqual(response.context["selected_topic_summary"], "")
        self.assertEqual(
            response.context["selected_filter_summary"],
            "Workshops matching selected filters",
        )
        self.assertNotContains(response, 'data-testid="workshop-topic-summary"')

    def test_technology_only_empty_state_uses_neutral_copy_and_names(self):
        _make_workshop(
            slug="react-only",
            title="React Only",
            tags=["react"],
        )
        response = self.client.get(f"{CATALOG_URL}?tag=django&tag=react")
        parser = _parse(response)

        self.assertEqual(response.context["selected_topic_summary"], "")
        self.assertEqual(
            response.context["selected_filter_summary"],
            "Workshops matching selected filters",
        )
        self.assertNotContains(response, 'data-testid="workshop-topic-summary"')
        self.assertContains(response, "No workshops found")
        self.assertContains(response, "No workshops match the selected filters.")
        active_tags = {
            element["text"].strip(): element["attrs"].get("aria-label")
            for element in parser.elements
            if element["attrs"].get("data-testid") == "workshop-active-tag"
        }
        self.assertEqual(active_tags, {
            "django": "Remove django technology filter",
            "react": "Remove react technology filter",
        })

    def test_each_empty_facet_group_is_omitted(self):
        Workshop.objects.filter(status="published").update(
            tags=["career"], core_tools=[]
        )
        topics_only = self.client.get(CATALOG_URL)
        self.assertContains(topics_only, 'data-testid="workshop-facet-topic"')
        self.assertNotContains(
            topics_only, 'data-testid="workshop-facet-technology"'
        )

        Workshop.objects.filter(status="published").update(
            tags=["django"], core_tools=[]
        )
        technology_only = self.client.get(CATALOG_URL)
        self.assertNotContains(
            technology_only, 'data-testid="workshop-facet-topic"'
        )
        self.assertContains(
            technology_only, 'data-testid="workshop-facet-technology"'
        )

    def test_landing_preview_keeps_filters_hidden(self):
        response = self.client.get("/workshops")
        self.assertNotContains(response, 'data-testid="workshop-facet-topic"')
        self.assertNotContains(
            response, 'data-testid="workshop-facet-technology"'
        )
        self.assertNotContains(response, 'data-testid="workshop-access-filters"')
        self.assertContains(response, 'data-testid="workshops-preview"')

    @tag("visual_regression")
    def test_access_row_markup_and_classes_remain_unchanged(self):
        response = self.client.get(CATALOG_URL)
        parser = _parse(response)
        access_options = [
            element for element in parser.elements
            if element["attrs"].get("data-testid", "").startswith(
                "workshop-access-filter-"
            )
        ]
        self.assertEqual(
            [element["text"].strip() for element in access_options],
            ["All", "Free", "Paid"],
        )
        self.assertEqual(
            [element["attrs"]["data-testid"] for element in access_options],
            [
                "workshop-access-filter-all",
                "workshop-access-filter-free",
                "workshop-access-filter-paid",
            ],
        )
        base = (
            "inline-flex min-h-[44px] items-center justify-center rounded-full "
            "px-4 py-2 text-sm font-medium transition-colors "
            "focus-visible:outline-none focus-visible:ring-2 "
            "focus-visible:ring-ring focus-visible:ring-offset-2 "
        )
        self.assertEqual(
            access_options[0]["attrs"]["class"],
            base + "bg-accent text-accent-foreground",
        )
        unselected = (
            base
            + "bg-secondary text-muted-foreground hover:bg-secondary/80 "
            "hover:text-foreground"
        )
        self.assertEqual(access_options[1]["attrs"]["class"], unselected)
        self.assertEqual(access_options[2]["attrs"]["class"], unselected)
