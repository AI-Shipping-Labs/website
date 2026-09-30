"""Static guard for the design-system reading-order rules.

``_docs/design-system.md`` (Spacing and Layout) says a section title comes
first and its links or actions sit below it and wrap, rather than being pinned
opposite it, and that module progress and the next action stay in one vertical
reading order.  The shared owners are ``{% section_header %}`` and
``{% progress_block %}`` from ``content.templatetags.layout_components``.

This guard parses every non-Studio template under ``templates/`` into a
tolerant element tree and flags five hand-rolled patterns:

``heading_action_opposite``
    A ``justify-between`` container whose direct children include an
    ``h1``-``h3`` and a link or button (or a group of them).
``progress_cta_row``
    A row (``flex`` without ``flex-col``, or ``*:flex-row``) whose direct
    children hold a progress element and a primary ``button_classes`` CTA.
``row_actions_beside_meta``
    Inside a list row, a ``justify-between`` container that pins a group of
    two or more actions (``{% if %}`` arms count once, a ``{% for %}`` loop
    counts as many) beside the row's meta block.
``mixed_row_action_placement``
    A list (``ul``/``ol`` or a ``divide-y`` container) whose rows, across
    rows or ``{% if %}`` arms, put some actions pinned beside the meta with
    ``justify-between`` and others in an action row below the meta.  An
    ``{% include %}`` of a template whose name contains ``action`` counts as
    an action at the include site.
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
TEMPLATE_FLOW_RE = re.compile(r"\{%\s*(for|endfor|else|elif|empty)\b")
SEE_ALL_RE = re.compile(r"^\s*(see|view) all\b", re.IGNORECASE)
ACTION_INCLUDE_RE = re.compile(r"\{%\s*include\s+['\"][^'\"]*action[^'\"]*['\"]")
SKIP_LABEL_RE = re.compile(r"^\s*skip\b", re.IGNORECASE)
DISMISS_LABEL_RE = re.compile(r"^\s*(close|dismiss)\b", re.IGNORECASE)


@dataclass(eq=False)
class Element:
    tag: str
    attrs: dict[str, str]
    line: int
    children: list[Element] = field(default_factory=list)
    own_text: list[str] = field(default_factory=list)
    # Template control flow seen in the parent before this element opened:
    # ``branch`` numbers the ``{% if %}``/``{% else %}`` arm it renders in, and
    # ``looped`` is true inside a ``{% for %}`` body.  Only
    # ``row_actions_beside_meta`` reads them, to count rendered actions.
    branch: int = 0
    looped: bool = False
    open_branch: int = 0
    loop_depth: int = 0

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
        parent = self.stack[-1]
        element = Element(tag, {name: value or "" for name, value in attrs}, self.getpos()[0])
        element.branch = parent.open_branch
        element.looped = parent.loop_depth > 0
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
        parent = self.stack[-1]
        parent.own_text.append(data)
        for keyword in TEMPLATE_FLOW_RE.findall(data):
            if keyword in {"else", "elif", "empty"}:
                parent.open_branch += 1
            elif keyword == "for":
                parent.loop_depth += 1
            elif keyword == "endfor":
                parent.loop_depth = max(parent.loop_depth - 1, 0)


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


def _is_justify_between(element: Element) -> bool:
    return any(token.split(":")[-1] == "justify-between" for token in element.classes)


def _is_skip(element: Element) -> bool:
    return element.tag == "button" and SKIP_LABEL_RE.match(element.attrs.get("aria-label", "")) is not None


def _is_inline_group(element: Element) -> bool:
    """A group that lays its children out on one line at every width."""
    classes = element.classes
    if not {"flex", "inline-flex"} & set(classes):
        return False
    return not any(token.split(":")[-1] == "flex-col" for token in classes)


def _action_count(element: Element) -> int:
    """How many real actions ``element`` renders at most.

    Mutually exclusive ``{% if %}``/``{% else %}`` arms count once, and an
    action repeated by a ``{% for %}`` loop counts as two.  An ``{% include %}``
    of an action partial counts as one action.  A quiet Skip inline beside
    exactly one other action in a one-line group is part of that action's
    group (``_docs/design-system.md``, Row actions), so it does not count; a
    Skip stacked in a ``flex-col`` group, or alone in its own container,
    still does.
    """
    if _is_action(element):
        return 0 if _is_dismissal(element) else 1
    arms: dict[int, int] = {}
    skips: dict[int, int] = {}
    for child in element.children:
        count = _action_count(child)
        if child.looped and count:
            count = max(count, 2)
        arms[child.branch] = arms.get(child.branch, 0) + count
        if _is_skip(child) and not child.looped:
            skips[child.branch] = skips.get(child.branch, 0) + 1
    included = len(ACTION_INCLUDE_RE.findall("".join(element.own_text)))
    totals = []
    for arm in arms.keys() | {0}:
        total = arms.get(arm, 0) + included
        skip = skips.get(arm, 0)
        if _is_inline_group(element) and skip == 1 and total - skip == 1:
            total -= 1
        totals.append(total)
    return max(totals)


def _pinned_action_count(container: Element) -> int:
    """Actions ``container`` renders beside its meta: the biggest action group
    child, or the direct actions of one ``{% if %}`` arm, whichever is larger."""
    groups = [0]
    direct: dict[int, int] = {}
    for child in container.children:
        if _has_meta(child):
            continue
        count = _action_count(child)
        if child.looped and count:
            count = max(count, 2)
        if _is_action(child):
            direct[child.branch] = direct.get(child.branch, 0) + count
        else:
            groups.append(count)
    included = len(ACTION_INCLUDE_RE.findall("".join(container.own_text)))
    if included:
        direct[0] = direct.get(0, 0) + included
    return max(max(groups), max(direct.values(), default=0))


def _includes_action(element: Element) -> bool:
    return any(ACTION_INCLUDE_RE.search("".join(node.own_text)) for node in element.walk())


def _has_meta(element: Element) -> bool:
    if _real_actions(element) or _includes_action(element):
        return False
    return bool(element.text().strip() or element.children)


def _is_pinning_container(element: Element) -> bool:
    return _is_justify_between(element) and any(_has_meta(child) for child in element.children)


def _row_pinned_total(element: Element) -> int:
    """Actions pinned beside meta anywhere inside one row, summed across
    every ``justify-between`` container that pins some (``{% if %}`` arms
    count once, like ``_action_count``).  Nested rows count on their own."""
    if _is_pinning_container(element):
        return _pinned_action_count(element)
    if any(token.startswith("divide-y") for token in element.classes):
        return 0
    arms: dict[int, int] = {}
    for child in element.children:
        if child.tag == "li":
            continue
        arms[child.branch] = arms.get(child.branch, 0) + _row_pinned_total(child)
    return max(arms.values(), default=0)


def row_actions_beside_meta(root: Element) -> list[Element]:
    """A list row that pins two or more actions beside its meta.

    Two or more row actions always sit in an action row below the meta, so a
    list row (``li``, or a child of a ``divide-y`` list) must not pin 2+
    rendered actions beside its meta with ``justify-between`` (at any
    breakpoint).  That covers one container pinning a group of actions (the
    pattern that squeezed course Home's session rows) and several containers
    that each pin one action, which stacks the actions in a detached column
    at the row's edge (the Getting started checklist's CTA on the title line
    and Skip on the description line).  Banners, cards, and toolbars outside
    list rows are out of scope; a single pinned action is left to the
    rendered guard.
    """
    matches = []

    def visit(element: Element, in_row: bool, row_root: bool):
        in_row = in_row or row_root or element.tag == "li"
        before = len(matches)
        if in_row and _is_pinning_container(element) and _pinned_action_count(element) >= 2:
            matches.append(element)
        row_list = any(token.startswith("divide-y") for token in element.classes)
        for child in element.children:
            visit(child, in_row, row_list)
        is_row = row_root or element.tag == "li"
        if is_row and len(matches) == before and _row_pinned_total(element) >= 2:
            matches.append(element)

    visit(root, False, False)
    return matches


LIST_TAGS = frozenset({"ul", "ol"})
INLINE_TEXT_TAGS = frozenset({"p", "span", "h1", "h2", "h3", "h4", "h5", "h6", "label", "summary"})


def _is_list(element: Element) -> bool:
    return element.tag in LIST_TAGS or any(token.startswith("divide-y") for token in element.classes)


def _placements(element: Element, pinned: bool, found: set[str]) -> None:
    """Record where the row actions under ``element`` sit.

    ``pinned`` means a non-meta child of a ``justify-between`` container that
    holds the row's meta; ``below`` is any other block-level action.  Links in
    running text (inside a ``p``, heading, or ``span``) are not row actions,
    and nested lists are judged on their own.
    """
    if _is_list(element):
        return
    if _is_action(element):
        if not _is_dismissal(element):
            found.add("pinned" if pinned else "below")
        return
    if element.tag in INLINE_TEXT_TAGS:
        return
    pinning = _is_pinning_container(element)
    # An ``{% include %}`` of an action partial renders the action in place,
    # as a direct child of this element.
    if ACTION_INCLUDE_RE.search("".join(element.own_text)):
        found.add("pinned" if pinned or pinning else "below")
    for child in element.children:
        _placements(child, pinned or (pinning and not _has_meta(child)), found)


def mixed_row_action_placement(root: Element) -> list[Element]:
    """A list whose rows mix right-pinned and below-meta actions.

    Rows in one list share one action placement: a list template must not
    render some rows (or some ``{% if %}`` arms of its row) with the action
    pinned beside the meta by ``justify-between`` and others with the action
    in a row below the meta.  This is the Getting started checklist as it
    shipped after its two-action rows stacked: skippable steps put CTA and
    Skip below the description while done and skipped steps kept one action
    pinned right, so the actions jumped between the right edge and the text
    column within one list.
    """
    matches = []
    for element in root.walk():
        if not _is_list(element):
            continue
        found: set[str] = set()
        for child in element.children:
            _placements(child, False, found)
        if found == {"pinned", "below"}:
            matches.append(element)
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
    Rule("row_actions_beside_meta", row_actions_beside_meta),
    Rule("mixed_row_action_placement", mixed_row_action_placement),
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


# The course Home session row before it stacked its actions: "Watch recording"
# and "Read recap" were pinned beside the title, badge, and date, which
# squeezed the date into a wrapping column at 390px.
SESSION_ROW_ACTIONS_BESIDE_META = (
    '<li class="flex min-w-0 items-center justify-between gap-3 py-2">\n'
    '  <div class="min-w-0 flex-1"><h4>{{ session.display_title }}</h4><p>{{ session.when }}</p></div>\n'
    "  {% if session.actions %}\n"
    '  <div class="flex shrink-0 flex-wrap items-center justify-end gap-x-4">\n'
    '    {% for action in session.actions %}<a href="{{ action.url }}">{{ action.label }}</a>{% endfor %}\n'
    "  </div>\n"
    "  {% endif %}\n"
    "</li>"
)
SESSION_ROW_ACTIONS_BELOW_META = (
    '<li class="min-w-0 py-2">\n'
    '  <div class="min-w-0"><h4>{{ session.display_title }}</h4><p>{{ session.when }}</p></div>\n'
    '  <div class="mt-2 flex flex-wrap gap-x-4 gap-y-1">\n'
    '    {% for action in session.actions %}<a href="{{ action.url }}">{{ action.label }}</a>{% endfor %}\n'
    "  </div>\n"
    "</li>"
)

# The Getting started checklist row before it stacked its actions: the CTA was
# pinned on the title line and Skip on the description line by two separate
# ``justify-between`` containers, so each held one action and the row showed a
# detached column of two actions at its right edge.
CHECKLIST_ROW_SPLIT_PINNED = (
    '<ol class="divide-y">{% for item in items %}<li class="flex items-start gap-3">\n'
    '  <span>{{ forloop.counter }}</span>\n'
    '  <div class="flex min-w-0 flex-1 flex-col sm:block">\n'
    '    <div class="contents sm:flex sm:items-start sm:justify-between sm:gap-3">\n'
    '      <h4>{{ item.title }}</h4>\n'
    "      {% if item.completed %}<a href=\"{{ item.url }}\">Review</a>"
    '{% else %}<a href="{{ item.url }}">{{ item.cta_label }}</a>{% endif %}\n'
    "    </div>\n"
    '    <div class="mt-0.5 flex items-start justify-between gap-3">\n'
    "      <p>{{ item.description }}</p>\n"
    '      {% if not item.completed %}<button type="button" aria-label="Skip {{ item.title }}">Skip</button>{% endif %}\n'
    "    </div>\n"
    "  </div>\n"
    "</li>{% endfor %}</ol>"
)
CHECKLIST_ROW_ACTION_ROW = (
    '<ol class="divide-y">{% for item in items %}<li class="flex items-start gap-3">\n'
    '  <span>{{ forloop.counter }}</span>\n'
    "  {% if item.skippable %}\n"
    '  <div class="min-w-0 flex-1"><h4>{{ item.title }}</h4><p>{{ item.description }}</p>\n'
    '    <div class="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1">'
    '<a href="{{ item.url }}">{{ item.cta_label }}</a>'
    '<button type="button" aria-label="Skip {{ item.title }}">Skip</button></div>\n'
    "  </div>\n"
    "  {% else %}\n"
    '  <div class="flex min-w-0 flex-1 flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">\n'
    '    <div class="min-w-0"><h4>{{ item.title }}</h4><p>{{ item.description }}</p></div>\n'
    '    <a href="{{ item.url }}">Review</a>\n'
    "  </div>\n"
    "  {% endif %}\n"
    "</li>{% endfor %}</ol>"
)


@tag("core")
class RowActionsBesideMetaRuleTest(SimpleTestCase):
    def _count(self, source, path="templates/content/sample.html"):
        return len(find_matches(RULE_BY_ID["row_actions_beside_meta"], path, source))

    def test_old_session_row_is_flagged_and_the_stacked_row_is_not(self):
        self.assertEqual(self._count(SESSION_ROW_ACTIONS_BESIDE_META), 1)
        self.assertEqual(self._count(SESSION_ROW_ACTIONS_BELOW_META), 0)

    def test_two_pinned_actions_are_flagged_even_when_pinned_only_from_sm(self):
        cases = {
            "group of two": (
                '<li><div class="flex flex-col sm:flex-row sm:justify-between"><span>Ana · Mar 3</span>'
                '<span><button>Edit</button><button>Delete</button></span></div></li>'
            ),
            "two direct actions": (
                '<ul class="divide-y"><div class="flex justify-between"><p>Session 2</p>'
                '<a href="/r">Watch recording</a><a href="/c">Read recap</a></div></ul>'
            ),
        }
        for name, source in cases.items():
            with self.subTest(name):
                self.assertEqual(self._count(source), 1)

    def test_actions_pinned_by_separate_containers_in_one_row_are_flagged(self):
        self.assertEqual(self._count(CHECKLIST_ROW_SPLIT_PINNED), 1)
        self.assertEqual(self._count(CHECKLIST_ROW_ACTION_ROW), 0)

    def test_split_pinning_counts_if_arms_once_and_nested_rows_separately(self):
        cases = {
            "each if arm pins one action": (
                '<li>{% if a %}<div class="flex justify-between"><h4>Step</h4><a href="/a">Start</a></div>'
                '{% else %}<div class="flex justify-between"><h4>Step</h4><a href="/b">Review</a></div>'
                "{% endif %}</li>"
            ),
            "nested rows pin one action each": (
                '<li><div class="flex justify-between"><h4>Module</h4><a href="/m">Open</a></div>'
                '<ul><li class="flex justify-between"><p>Lesson</p><a href="/l">Start</a></li></ul></li>'
            ),
        }
        for name, source in cases.items():
            with self.subTest(name):
                self.assertEqual(self._count(source), 0)

    def test_one_action_or_non_row_layouts_are_not_flagged(self):
        cases = {
            "one pinned action": (
                '<li class="flex flex-col sm:flex-row sm:justify-between"><div><h4>Homework</h4></div>'
                '<a href="/h">Continue</a></li>'
            ),
            "if/else arms render one action": (
                '<li><div class="flex justify-between"><h4>Step</h4>'
                '{% if done %}<a href="/a">Review</a>{% else %}<a href="/b">Start</a>{% endif %}</div></li>'
            ),
            "banner outside a list row": (
                '<div role="alert"><div class="flex sm:flex-row sm:justify-between"><p>Checkout failed</p>'
                '<div><a href="/a">View tiers</a><a href="/b">Contact support</a></div></div></div>'
            ),
            "icon navigation beside an eyebrow outside a list": (
                '<div class="flex justify-between"><p>Module</p>'
                '<div><a href="/up" aria-label="Up">^</a><a href="/next" aria-label="Next">&gt;</a></div></div>'
            ),
            "row with a dismiss control": (
                '<li class="flex justify-between"><p>Tip</p>'
                '<div><a href="/a">Open</a><button aria-label="Dismiss tip">x</button></div></li>'
            ),
            "studio": SESSION_ROW_ACTIONS_BESIDE_META,
        }
        for name, source in cases.items():
            path = "templates/studio/page.html" if name == "studio" else "templates/content/sample.html"
            with self.subTest(name):
                self.assertEqual(self._count(source, path), 0)


# The Getting started checklist as shipped after only its two-action rows
# stacked: a skippable step puts the CTA (an action partial include) and Skip
# below the description, while a done or skipped step pins its one action
# right, so actions jump between the right edge and the text column.
CHECKLIST_MIXED_PLACEMENT = (
    '<ol class="divide-y">{% for item in items %}<li class="flex items-start gap-3">\n'
    "  {% if item.skippable %}\n"
    '  <div class="min-w-0 flex-1">{% include "content/_item_meta.html" %}\n'
    '    <div class="mt-2 flex flex-wrap gap-x-4">{% include "content/_item_action.html" %}'
    '<button type="button" aria-label="Skip {{ item.title }}">Skip</button></div>\n'
    "  </div>\n"
    "  {% else %}\n"
    '  <div class="flex min-w-0 flex-1 flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">\n'
    '    <div class="min-w-0">{% include "content/_item_meta.html" %}</div>\n'
    '    {% include "content/_item_action.html" %}\n'
    "  </div>\n"
    "  {% endif %}\n"
    "</li>{% endfor %}</ol>"
)
CHECKLIST_SHARED_PLACEMENT = (
    '<ol class="divide-y">{% for item in items %}<li class="flex items-start gap-3">\n'
    '  <div class="min-w-0 flex-1">{% include "content/_item_meta.html" %}\n'
    '    <div class="mt-2 flex flex-wrap gap-x-4">{% include "content/_item_action.html" %}'
    '{% if item.skippable %}<button type="button" aria-label="Skip {{ item.title }}">Skip</button>{% endif %}'
    "</div>\n"
    "  </div>\n"
    "</li>{% endfor %}</ol>"
)

# The shipped checklist row: every row pins one action group right from sm
# up, and an open step's quiet Skip sits inline just before its button.
CHECKLIST_PINNED_WITH_INLINE_SKIP = (
    '<ol class="divide-y">{% for item in items %}<li class="flex items-start gap-3">\n'
    '  <div class="flex min-w-0 flex-1 flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">\n'
    '    <div class="min-w-0">{% include "content/_item_meta.html" %}</div>\n'
    '    <div class="flex shrink-0 items-center gap-x-4">\n'
    '      {% if item.skippable %}<button type="button" aria-label="Skip {{ item.title }}" '
    'class="order-last sm:order-none">Skip</button>{% endif %}\n'
    '      {% include "content/_item_action.html" %}\n'
    "    </div>\n"
    "  </div>\n"
    "</li>{% endfor %}</ol>"
)


@tag("core")
class InlineSkipGroupTest(SimpleTestCase):
    def _count(self, rule_id, source):
        return len(find_matches(RULE_BY_ID[rule_id], "templates/content/sample.html", source))

    def test_skip_inline_before_one_button_is_one_pinned_group(self):
        for rule_id in ("row_actions_beside_meta", "mixed_row_action_placement"):
            with self.subTest(rule_id):
                self.assertEqual(self._count(rule_id, CHECKLIST_PINNED_WITH_INLINE_SKIP), 0)

    def test_skip_stacked_under_its_button_or_beside_two_buttons_is_flagged(self):
        cases = {
            "stacked in a flex-col group": CHECKLIST_PINNED_WITH_INLINE_SKIP.replace(
                "flex shrink-0 items-center gap-x-4", "flex shrink-0 flex-col items-end"),
            "beside two real buttons": CHECKLIST_PINNED_WITH_INLINE_SKIP.replace(
                '{% include "content/_item_action.html" %}',
                '{% include "content/_item_action.html" %}<a href="/more">More</a>'),
        }
        for name, source in cases.items():
            with self.subTest(name):
                self.assertEqual(self._count("row_actions_beside_meta", source), 1)


@tag("core")
class MixedRowActionPlacementRuleTest(SimpleTestCase):
    def _count(self, source, path="templates/content/sample.html"):
        return len(find_matches(RULE_BY_ID["mixed_row_action_placement"], path, source))

    def test_list_mixing_pinned_and_below_meta_actions_is_flagged(self):
        self.assertEqual(self._count(CHECKLIST_MIXED_PLACEMENT), 1)
        self.assertEqual(self._count(CHECKLIST_ROW_ACTION_ROW), 1)
        self.assertEqual(self._count(CHECKLIST_SHARED_PLACEMENT), 0)
        self.assertEqual(self._count(CHECKLIST_PINNED_WITH_INLINE_SKIP), 0)

    def test_consistent_or_non_row_placements_are_not_flagged(self):
        cases = {
            "every row pins one action": (
                '<ul>{% for s in sessions %}<li class="flex flex-col sm:flex-row sm:justify-between">'
                '<div><h4>{{ s.title }}</h4></div><a href="{{ s.url }}">Open session</a></li>{% endfor %}</ul>'
            ),
            "inline link in the description of a pinned row": (
                '<ul><li class="flex sm:justify-between"><div><h4>Slack</h4>'
                '<p>Read the <a href="/g">guide</a> first.</p></div><a href="/j">Join</a></li></ul>'
            ),
            "nested list judged on its own": (
                '<ul><li><h4>Module</h4><a href="/m">Open module</a>'
                '<ul><li class="flex justify-between"><p>Lesson</p><a href="/l">Start</a></li></ul></li></ul>'
            ),
            "pinned dismiss beside a below-meta action": (
                '<ul><li class="flex justify-between"><div><h4>Tip</h4><a class="mt-2" href="/t">Open</a></div>'
                '<button aria-label="Dismiss tip">x</button></li></ul>'
            ),
            "studio": CHECKLIST_MIXED_PLACEMENT,
        }
        for name, source in cases.items():
            path = "templates/studio/page.html" if name == "studio" else "templates/content/sample.html"
            with self.subTest(name):
                self.assertEqual(self._count(source, path), 0)


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
