"""Static guard for the design-system reading-order rules.

``_docs/design-system.md`` (Spacing and Layout) says a section title comes
first and its links or actions sit below it and wrap, rather than being pinned
opposite it, and that module progress and the next action stay in one vertical
reading order.  The shared owners are ``{% section_header %}`` and
``{% progress_block %}`` from ``content.templatetags.layout_components``.

This guard parses every non-Studio template under ``templates/`` into a
tolerant element tree and flags three hand-rolled patterns:

``heading_action_opposite``
    A ``justify-between`` container whose direct children include an
    ``h1``-``h3`` and a link or button (or a group of them).
``progress_cta_row``
    A row (``flex`` without ``flex-col``, or ``*:flex-row``) whose direct
    children hold a progress element and a primary ``button_classes`` CTA.
``handrolled_see_all_link``
    An ``h1``-``h3`` immediately followed by a "See all" / "View all" link,
    which is what ``{% section_header %}`` renders.

Documented exceptions handled structurally, each with a boundary test below:
list and table rows (``li``/``tr``/``td``/``th``/``summary`` containers),
dismissal controls (close/dismiss buttons in alert and dialog title rows),
headings wrapped inside a title block (callout and card-internal layouts), and
Studio templates, which follow their own stacked-header contract.  Anything
else goes in ``design_layout_allowlist.py`` with a reason, and that file can
only shrink.
"""

from __future__ import annotations

import re
import tempfile
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath

from django.conf import settings
from django.template import Context, Template
from django.test import SimpleTestCase, tag

from content.tests.design_layout_allowlist import DESIGN_LAYOUT_ALLOWLIST, DESIGN_LAYOUT_CEILING

EXCLUDED_PREFIXES = ("templates/studio/",)
SECTION_HEADER_OWNER = "templates/includes/_section_header.html"

VOID_TAGS = frozenset(
    {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
)
HEADING_TAGS = frozenset({"h1", "h2", "h3"})
ROW_CONTAINER_TAGS = frozenset({"li", "tr", "td", "th", "summary", "thead", "tbody"})
TEMPLATE_COMMENT_RE = re.compile(
    r"\{%\s*comment\b.*?%\}.*?\{%\s*endcomment\s*%\}|\{#.*?#\}|<!--.*?-->",
    re.DOTALL,
)
SEE_ALL_RE = re.compile(r"^\s*(see|view) all\b", re.IGNORECASE)
DISMISS_LABEL_RE = re.compile(r"^\s*(close|dismiss)\b", re.IGNORECASE)


@dataclass(eq=False)
class Element:
    tag: str
    attrs: dict[str, str]
    line: int
    children: list[Element] = field(default_factory=list)
    own_text: list[str] = field(default_factory=list)

    @property
    def classes(self) -> list[str]:
        return self.attrs.get("class", "").split()

    def walk(self):
        yield self
        for child in self.children:
            yield from child.walk()

    def text(self) -> str:
        return "".join(self.own_text) + "".join(child.text() for child in self.children)


class _TreeBuilder(HTMLParser):
    """Tolerant builder: unknown end tags are ignored, mismatches pop to the match."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Element("#root", {}, 0)
        self.stack = [self.root]

    def _element(self, tag, attrs):
        element = Element(tag, {name: value or "" for name, value in attrs}, self.getpos()[0])
        self.stack[-1].children.append(element)
        return element

    def handle_starttag(self, tag, attrs):
        element = self._element(tag, attrs)
        if tag not in VOID_TAGS:
            self.stack.append(element)

    def handle_startendtag(self, tag, attrs):
        self._element(tag, attrs)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return

    def handle_data(self, data):
        self.stack[-1].own_text.append(data)


def _blank(match: re.Match[str]) -> str:
    return re.sub(r"[^\n]", " ", match.group(0))


def parse_template(source: str) -> Element:
    builder = _TreeBuilder()
    builder.feed(TEMPLATE_COMMENT_RE.sub(_blank, source))
    builder.close()
    return builder.root


def _is_action(element: Element) -> bool:
    return element.tag in {"a", "button"} or "button_classes" in element.attrs.get("class", "")


def _is_dismissal(element: Element) -> bool:
    if DISMISS_LABEL_RE.match(element.attrs.get("aria-label", "")):
        return True
    if any("dismiss" in name for name in element.attrs):
        return True
    return ".close()" in element.attrs.get("onclick", "")


def _contains_heading(element: Element) -> bool:
    return any(node.tag in HEADING_TAGS for node in element.walk())


def _real_actions(element: Element) -> list[Element]:
    return [node for node in element.walk() if _is_action(node) and not _is_dismissal(node)]


def _is_primary_cta(element: Element) -> bool:
    classes = element.attrs.get("class", "")
    return "button_classes" in classes and re.search(r"button_classes\s+['\"]primary['\"]", classes) is not None


def _is_progress(element: Element) -> bool:
    if element.tag == "progress" or element.attrs.get("role") == "progressbar":
        return True
    if any(name.startswith("data-") and "progress" in name for name in element.attrs):
        return True
    if element.attrs.get("data-testid", "").endswith("progress-bar"):
        return True
    return "{% progress_block" in "".join(element.own_text)


def _is_row(element: Element) -> bool:
    classes = element.classes
    if any(token.endswith(":flex-row") for token in classes):
        return True
    return "flex" in classes and "flex-col" not in classes


def heading_action_opposite(root: Element) -> list[Element]:
    matches = []
    for container in root.walk():
        if container.tag in ROW_CONTAINER_TAGS:
            continue
        if not any(token.split(":")[-1] == "justify-between" for token in container.classes):
            continue
        headings = [child for child in container.children if child.tag in HEADING_TAGS]
        if not headings:
            continue
        action_children = [
            child
            for child in container.children
            if child.tag not in HEADING_TAGS and not _contains_heading(child) and _real_actions(child)
        ]
        if action_children:
            matches.append(container)
    return matches


def progress_cta_row(root: Element) -> list[Element]:
    matches = []
    for container in root.walk():
        if not _is_row(container):
            continue
        progress_children = [
            child for child in container.children if any(_is_progress(node) for node in child.walk())
        ]
        cta_children = [
            child
            for child in container.children
            if child not in progress_children and any(_is_primary_cta(node) for node in child.walk())
        ]
        if progress_children and cta_children:
            matches.append(container)
    return matches


def handrolled_see_all_link(root: Element) -> list[Element]:
    matches = []
    for parent in root.walk():
        children = parent.children
        for index, child in enumerate(children[:-1]):
            if child.tag not in HEADING_TAGS:
                continue
            following = children[index + 1]
            candidates = [following] if following.tag == "a" else []
            if following.tag in {"p", "div", "span"}:
                candidates = [node for node in following.walk() if node.tag == "a"][:1]
            if any(SEE_ALL_RE.match(candidate.text()) for candidate in candidates):
                matches.append(child)
    return matches


@dataclass(frozen=True)
class Rule:
    rule_id: str
    matcher: object
    excluded_paths: tuple[str, ...] = ()


RULES = (
    Rule("handrolled_see_all_link", handrolled_see_all_link, (SECTION_HEADER_OWNER,)),
    Rule("heading_action_opposite", heading_action_opposite),
    Rule("progress_cta_row", progress_cta_row),
)
RULE_BY_ID = {rule.rule_id: rule for rule in RULES}


def find_matches(rule: Rule, relative_path: str, source: str) -> list[int]:
    if relative_path.startswith(EXCLUDED_PREFIXES) or relative_path in rule.excluded_paths:
        return []
    return [element.line for element in rule.matcher(parse_template(source))]


def discover_templates(template_root: Path) -> tuple[Path, ...]:
    return tuple(sorted((path for path in template_root.rglob("*.html") if path.is_file()), key=lambda p: p.as_posix()))


def scan_templates(template_root: Path, base_dir: Path) -> dict[str, dict[str, list[int]]]:
    results: dict[str, dict[str, list[int]]] = {rule.rule_id: {} for rule in RULES}
    for path in discover_templates(template_root):
        relative_path = path.relative_to(base_dir).as_posix()
        source = path.read_text(encoding="utf-8")
        for rule in RULES:
            lines = find_matches(rule, relative_path, source)
            if lines:
                results[rule.rule_id][relative_path] = lines
    return results


@tag("core")
class DesignLayoutLintTest(SimpleTestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.base_dir = Path(settings.BASE_DIR)
        cls.template_root = cls.base_dir / "templates"
        cls.results = scan_templates(cls.template_root, cls.base_dir)

    def test_templates_do_not_exceed_the_allowlist(self):
        offenders = []
        for rule in RULES:
            for path, lines in sorted(self.results[rule.rule_id].items()):
                allowed = DESIGN_LAYOUT_ALLOWLIST.get(rule.rule_id, {}).get(path, (0, ""))[0]
                if len(lines) > allowed:
                    offenders.extend(f"{rule.rule_id} {path}:{line} (allowed={allowed})" for line in lines)
        self.assertEqual(
            offenders,
            [],
            "Hand-rolled layout that breaks _docs/design-system.md (Spacing and Layout):\n"
            + "\n".join(offenders)
            + "\nUse {% section_header %} (title, then the discovery link below) or "
            "{% progress_block %} (bar, count, then the action below) from "
            "layout_components, or stack the actions under the title and let them wrap. "
            "Do not add allowlist entries.",
        )

    def test_allowlist_is_exact_reasoned_and_shrink_only(self):
        problems = []
        self.assertEqual(sorted(DESIGN_LAYOUT_ALLOWLIST), sorted(RULE_BY_ID))
        discovered = {path.relative_to(self.base_dir).as_posix() for path in discover_templates(self.template_root)}
        for rule_id, entries in DESIGN_LAYOUT_ALLOWLIST.items():
            for path, (allowed, reason) in entries.items():
                pure = PurePosixPath(path)
                if not path.startswith("templates/") or ".." in pure.parts or pure.as_posix() != path:
                    problems.append(f"{rule_id}/{path}: path must be a repository-relative template path")
                if path not in discovered:
                    problems.append(f"{rule_id}/{path}: template is gone; delete the entry")
                if len(reason.strip()) < 20:
                    problems.append(f"{rule_id}/{path}: every entry needs a written reason")
                ceiling = DESIGN_LAYOUT_CEILING.get((rule_id, path))
                if ceiling is None or allowed > ceiling:
                    problems.append(f"{rule_id}/{path}: allowlist may only shrink (ceiling={ceiling}, allowed={allowed})")
                actual = len(self.results[rule_id].get(path, []))
                if actual < allowed:
                    fix = "delete the entry" if actual == 0 else f"lower the count to {actual}"
                    problems.append(f"{rule_id}/{path}: allowed={allowed}, actual={actual}; {fix}")
        self.assertEqual(problems, [])

    def test_discovery_includes_untracked_templates(self):
        local_tmp = self.base_dir / ".tmp"
        local_tmp.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="design-layout-lint-", dir=local_tmp) as directory:
            base = Path(directory)
            template = base / "templates" / "new" / "untracked.html"
            template.parent.mkdir(parents=True)
            template.write_text(PINNED_SEE_ALL_HEADER, encoding="utf-8")
            results = scan_templates(base / "templates", base)
        self.assertEqual(results["heading_action_opposite"], {"templates/new/untracked.html": [1]})


# The exact markup that pinned "See all live sessions" opposite "Next live
# session" on course Home before this guard existed.
PINNED_SEE_ALL_HEADER = (
    '<header class="mb-3 flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">\n'
    '  <h2 id="course-home-next-session-heading" class="text-lg font-semibold text-foreground">'
    "Next live session</h2>\n"
    "  <a href=\"{% url 'course_office_hours' slug=course.slug %}{{ cohort_query }}\" "
    'class="text-sm font-medium text-accent hover:underline">See all live sessions</a>\n'
    "</header>"
)

# The Current module card layout that pinned the primary button beside the bar.
PROGRESS_BESIDE_CTA = (
    '<div class="mt-4 flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">\n'
    '  <div class="min-w-0 flex-1"><div class="h-2.5 w-full rounded-full bg-muted" role="progressbar"></div>'
    "<p>1 of 4 done</p></div>\n"
    "  <a href=\"/x\" class=\"{% button_classes 'primary' size='md' %}\">Continue lesson</a>\n"
    "</div>"
)


@tag("core")
class DesignLayoutRuleBoundaryTest(SimpleTestCase):
    def _count(self, rule_id, source, path="templates/content/sample.html"):
        return len(find_matches(RULE_BY_ID[rule_id], path, source))

    def test_pinned_see_all_header_is_flagged_by_both_header_rules(self):
        self.assertEqual(self._count("heading_action_opposite", PINNED_SEE_ALL_HEADER), 1)
        self.assertEqual(self._count("handrolled_see_all_link", PINNED_SEE_ALL_HEADER), 1)

    def test_heading_with_pinned_action_group_is_flagged(self):
        source = (
            '<div class="flex flex-col gap-3 sm:flex-row sm:justify-between">'
            '<h2>Contents</h2><div class="flex gap-3"><a href="/a">One</a><a href="/b">Two</a></div></div>'
        )
        self.assertEqual(self._count("heading_action_opposite", source), 1)

    def test_progress_beside_primary_cta_is_flagged(self):
        self.assertEqual(self._count("progress_cta_row", PROGRESS_BESIDE_CTA), 1)

    def test_documented_exceptions_are_not_flagged(self):
        cases = {
            "list row": '<li class="flex justify-between"><h3>Row</h3><a href="/x">Open</a></li>',
            "dialog close": (
                '<div class="flex justify-between"><h3>March 3</h3>'
                '<button aria-label="Close event details" onclick="this.closest(\'dialog\').close()">x</button></div>'
            ),
            "alert dismiss": (
                '<div class="flex justify-between"><h2>Carry over</h2>'
                '<button data-dismiss-card="k">Dismiss carry-over prompt</button></div>'
            ),
            "callout title block": (
                '<div class="flex sm:flex-row sm:justify-between"><div><h2>Due soon</h2><p>Tomorrow</p></div>'
                '<a href="/x">Open</a></div>'
            ),
            "metadata opposite heading": '<div class="flex justify-between"><h2>Progress</h2><span>2 of 5</span></div>',
            "stacked header": '<header><h2>Live sessions</h2><a class="mt-2" href="/x">See all live sessions</a></header>',
            "studio": PINNED_SEE_ALL_HEADER,
        }
        for name, source in cases.items():
            path = "templates/studio/page.html" if name == "studio" else "templates/content/sample.html"
            with self.subTest(name):
                self.assertEqual(self._count("heading_action_opposite", source, path), 0)
        self.assertEqual(
            self._count("progress_cta_row", PROGRESS_BESIDE_CTA.replace("sm:flex-row sm:items-center ", "")),
            0,
            "A flex-col stack keeps the CTA below the progress text.",
        )
        secondary = PROGRESS_BESIDE_CTA.replace("'primary'", "'secondary'")
        self.assertEqual(self._count("progress_cta_row", secondary), 0)

    def test_hand_rolled_see_all_link_is_only_allowed_in_its_owner(self):
        source = (
            '<section><h2>Next live session</h2>\n'
            '<p class="mt-2"><a href="/s">See all live sessions</a></p></section>'
        )
        self.assertEqual(self._count("handrolled_see_all_link", source), 1)
        self.assertEqual(self._count("handrolled_see_all_link", source, SECTION_HEADER_OWNER), 0)
        self.assertEqual(
            self._count("handrolled_see_all_link", '<h2>Events</h2><ul></ul><a href="/e">View all events</a>'),
            0,
            "A trailing link after the section content is not a header link.",
        )

    def test_comments_and_template_comments_are_ignored(self):
        source = "{% comment %}\n" + PINNED_SEE_ALL_HEADER + "\n{% endcomment %}<!-- " + PINNED_SEE_ALL_HEADER + " -->"
        self.assertEqual(self._count("heading_action_opposite", source), 0)


@tag("core")
class LayoutComponentsRenderTest(SimpleTestCase):
    """The shared owners render the documented order and pass the guard."""

    def _render(self, source, **context):
        return Template("{% load layout_components %}" + source).render(Context(context))

    def test_section_header_puts_the_discovery_link_below_the_title(self):
        html = self._render(
            '{% section_header "Next live session" heading_id="next" see_all_url=url '
            'see_all_label="See all live sessions" extra="mb-3" %}',
            url="/courses/c/sessions?cohort=a",
        )
        root = parse_template(html)
        header = next(node for node in root.walk() if node.tag == "header")
        self.assertEqual([child.tag for child in header.children], ["h2", "a"])
        heading, link = header.children
        self.assertEqual(heading.text(), "Next live session")
        self.assertEqual(link.attrs["href"], "/courses/c/sessions?cohort=a")
        self.assertEqual(link.text().strip(), "See all live sessions")
        # The owner's own output is the sanctioned see-all shape, so only the
        # two geometry rules apply to it.
        self.assertEqual(heading_action_opposite(root), [])
        self.assertEqual(progress_cta_row(root), [])

    def test_section_header_rejects_half_a_link(self):
        from django.template import TemplateSyntaxError

        with self.assertRaises(TemplateSyntaxError):
            self._render('{% section_header "Title" see_all_url="/x" %}')

    def test_progress_block_stacks_bar_count_breakdown_then_action(self):
        html = self._render(
            "{% progress_block 2 3 label='Module progress' breakdown='2 of 3 lessons' "
            "action_url='/next' action_label='Continue lesson' %}"
        )
        root = parse_template(html)
        block = next(node for node in root.walk() if "data-progress-block" in node.attrs)
        self.assertEqual([child.tag for child in block.children], ["div", "p", "p", "a"])
        bar, summary, breakdown, action = block.children
        self.assertEqual(bar.attrs["role"], "progressbar")
        self.assertEqual(bar.attrs["aria-valuenow"], "2")
        self.assertIn("width: 66%", bar.children[0].attrs["style"])
        self.assertEqual(summary.text(), "2 of 3 done")
        self.assertEqual(breakdown.text(), "2 of 3 lessons")
        self.assertEqual(action.text(), "Continue lesson")
        self.assertEqual(progress_cta_row(root), [])
