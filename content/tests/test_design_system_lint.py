"""Shrink-only source lint for objective design-system template signatures.

This guard complements, but does not replace, the contextual changed-line review
from #1239.  It intentionally covers only the six regex-safe signatures below.

False-positive and exception policy:

* If a matcher is objectively broader than the binding design-system section,
  narrow it and add both a positive self-check and a negative boundary example.
* A genuinely unavoidable new occurrence needs its own issue and the narrowest
  path/count allowance, with that ``#NNNN`` rationale adjacent to the entry.
* Never add template ignores, wildcard/directory allowances, generated or
  environment-dependent baselines, skips, xfails, or an auto-update mode.

The checked-in mapping is diagnostic debt accounting, not authorization to add
debt.  Removing debt must tighten or delete the corresponding allowance.
"""

from __future__ import annotations

import re
import tempfile
from bisect import bisect_right
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from django.conf import settings
from django.test import SimpleTestCase, tag


@dataclass(frozen=True)
class Match:
    line: int
    excerpt: str


@dataclass(frozen=True)
class Rule:
    rule_id: str
    section: str
    matcher: Callable[[str], Iterable[re.Match[str]]]
    excluded_prefixes: tuple[str, ...] = ()


HTML_START_TAG_RE = re.compile(
    r"<(?P<tag>[A-Za-z][\w:-]*)(?P<attributes>(?:[^<>'\"]|'[^']*'|\"[^\"]*\")*)>",
    re.DOTALL,
)
HTML_COMMENT_RE = re.compile(r"<!--.*?(?:-->|\Z)", re.DOTALL)
HTML_RAW_TEXT_END_RE = {tag: re.compile(rf"</{tag}\s*>", re.IGNORECASE) for tag in ("script", "style")}
QUOTED_CLASS_ATTRIBUTE_RE = re.compile(
    r"(?<![\w:-])class\s*=\s*(?P<quote>['\"])(?P<value>.*?)(?P=quote)",
    re.IGNORECASE | re.DOTALL,
)
QUOTED_VALUE_RE = re.compile(
    r"(?P<quote>['\"])(?P<value>.*?)(?P=quote)",
    re.DOTALL,
)
DEPRECATED_INCLUDE_RE = re.compile(
    r"\{%\s*include\s+(?P<quote>['\"])"
    r"includes/content_gated\.html(?P=quote)"
    r"(?=\s|%\})(?:[^%]|%(?!\}))*?%\}",
    re.DOTALL,
)


def _masked(source: str, start: int, end: int) -> str:
    """Hide source while retaining offsets and line numbers."""
    return (
        source[:start] + "".join("\n" if character == "\n" else " " for character in source[start:end]) + source[end:]
    )


def _markup_only_source(source: str) -> str:
    """Mask comments and raw-text bodies where tag-shaped text is not markup."""
    masked = source
    for comment in reversed(tuple(HTML_COMMENT_RE.finditer(masked))):
        masked = _masked(masked, comment.start(), comment.end())

    search_from = 0
    while start_tag := HTML_START_TAG_RE.search(masked, search_from):
        tag = start_tag.group("tag").lower()
        search_from = start_tag.end()
        if tag not in HTML_RAW_TEXT_END_RE:
            continue
        end_tag = HTML_RAW_TEXT_END_RE[tag].search(masked, search_from)
        body_end = end_tag.start() if end_tag else len(masked)
        masked = _masked(masked, search_from, body_end)
        search_from = end_tag.end() if end_tag else len(masked)
    return masked


def _quoted_html_class_attributes(source: str) -> Iterable[re.Match[str]]:
    """Yield quoted class attributes that belong to actual HTML start tags."""
    markup = _markup_only_source(source)
    class_attributes = iter(QUOTED_CLASS_ATTRIBUTE_RE.finditer(markup))
    current_class = next(class_attributes, None)
    for start_tag in HTML_START_TAG_RE.finditer(markup):
        quoted_spans = tuple(
            (start_tag.start() + value.start(), start_tag.start() + value.end())
            for value in QUOTED_VALUE_RE.finditer(start_tag.group(0))
        )
        while current_class is not None and current_class.start() < start_tag.start():
            current_class = next(class_attributes, None)
        while current_class is not None and current_class.end() <= start_tag.end():
            nested_in_another_attribute = any(start < current_class.start() < end for start, end in quoted_spans)
            if current_class.start() >= start_tag.start() and not nested_in_another_attribute:
                yield current_class
            current_class = next(class_attributes, None)


def _class_attributes_with(*tokens: str) -> Callable[[str], Iterable[re.Match[str]]]:
    token_patterns = tuple(re.compile(rf"(?<!\S){re.escape(token)}(?!\S)") for token in tokens)

    def matcher(source: str) -> Iterable[re.Match[str]]:
        return (
            match
            for match in _quoted_html_class_attributes(source)
            if all(pattern.search(match.group("value")) for pattern in token_patterns)
        )

    return matcher


RULES = (
    Rule(
        "deprecated_content_gated_include",
        "Partials and Component Index → Deprecated; Gated Content",
        DEPRECATED_INCLUDE_RE.finditer,
    ),
    Rule(
        "legacy_px5_py25_pair",
        "Buttons",
        _class_attributes_with("px-5", "py-2.5"),
    ),
    Rule(
        "public_font_bold",
        "Typography Scale",
        _class_attributes_with("font-bold"),
        ("templates/studio/", "templates/emails/"),
    ),
    Rule(
        "public_tracking_wider",
        "Typography Scale",
        _class_attributes_with("tracking-wider"),
        ("templates/studio/", "templates/emails/"),
    ),
    Rule(
        "grid_gap5",
        "Layout Frames and Spacing",
        _class_attributes_with("grid", "gap-5"),
    ),
    Rule(
        "handrolled_empty_state_signature",
        "Empty States",
        _class_attributes_with("p-12", "text-center"),
    ),
)
RULE_BY_ID = {rule.rule_id: rule for rule in RULES}


# Initial frozen allowances seeded and inspected under #1240. Any later new or
# increased entry needs a separate issue-number rationale adjacent to the entry.
BASELINE: dict[str, dict[str, int]] = {
    "deprecated_content_gated_include": {  # Initial legacy debt: #1240.
        "templates/content/blog_detail.html": 1,
        "templates/content/tutorial_detail.html": 1,
    },
    "grid_gap5": {},
    "handrolled_empty_state_signature": {  # Initial legacy debt: #1240.
        "templates/content/peer_review/certificate.html": 2,
        "templates/integrations/admin_sync.html": 1,
        "templates/studio/events/list.html": 2,
        "templates/studio/includes/empty_state.html": 1,
        "templates/studio/sync/_repos_section.html": 1,
        "templates/studio/utm_analytics/campaign_detail.html": 1,
        "templates/studio/utm_analytics/dashboard.html": 1,
    },
    "legacy_px5_py25_pair": {  # Initial legacy debt: #1240.
        "templates/content/_gated_access_card.html": 1,
        "templates/content/_verify_email_required.html": 1,
        "templates/content/course_detail.html": 4,
        "templates/content/reader/_bottom_nav.html": 2,
        "templates/content/reader/_completion_button.html": 1,
        "templates/content/workshops_list.html": 2,
    },
    "public_font_bold": {  # Initial legacy debt: #1240.
        "templates/base.html": 1,
        "templates/content/request_a_call.html": 1,
        "templates/includes/header.html": 2,
        "templates/integrations/admin_sync.html": 1,
        "templates/integrations/admin_sync_history.html": 1,
    },
    "public_tracking_wider": {  # Initial legacy debt: #1240.
        "templates/content/_starting_soon_card.html": 1,
        "templates/content/peer_review/review_form.html": 4,
        "templates/events/_event_post_resources.html": 1,
        "templates/events/_recording_materials.html": 1,
        "templates/events/events_calendar.html": 8,
        "templates/integrations/admin_sync_history.html": 3,
    },
}


def discover_templates(template_root: Path) -> tuple[Path, ...]:
    """Return every regular HTML file below ``template_root`` in stable order."""
    return tuple(
        path for path in sorted(template_root.rglob("*.html"), key=lambda item: item.as_posix()) if path.is_file()
    )


def _relative_posix(path: Path, base_dir: Path) -> str:
    return path.relative_to(base_dir).as_posix()


def _one_line_excerpt(source: str, match: re.Match[str], limit: int = 140) -> str:
    excerpt = " ".join(match.group(0).split())
    if len(excerpt) > limit:
        excerpt = excerpt[: limit - 1].rstrip() + "…"
    return excerpt


def _line_starts(source: str) -> tuple[int, ...]:
    return (0, *(index + 1 for index, character in enumerate(source) if character == "\n"))


def find_rule_matches(
    rule: Rule,
    relative_path: str,
    source: str,
    line_starts: tuple[int, ...] | None = None,
) -> tuple[Match, ...]:
    if any(relative_path.startswith(prefix) for prefix in rule.excluded_prefixes):
        return ()
    starts = line_starts if line_starts is not None else _line_starts(source)
    return tuple(
        Match(
            line=bisect_right(starts, match.start()),
            excerpt=_one_line_excerpt(source, match),
        )
        for match in rule.matcher(source)
    )


def scan_templates(template_root: Path, base_dir: Path) -> dict[str, dict[str, tuple[Match, ...]]]:
    """Read each discovered template once, then apply all applicable rules."""
    results = {rule.rule_id: {} for rule in RULES}
    for path in discover_templates(template_root):
        relative_path = _relative_posix(path, base_dir)
        source = path.read_text(encoding="utf-8")
        line_starts = _line_starts(source)
        for rule in sorted(RULES, key=lambda item: item.rule_id):
            matches = find_rule_matches(rule, relative_path, source, line_starts)
            if matches:
                results[rule.rule_id][relative_path] = matches
    return results


def _current_counts(
    results: dict[str, dict[str, tuple[Match, ...]]],
) -> dict[str, dict[str, int]]:
    return {
        rule.rule_id: {path: len(results[rule.rule_id][path]) for path in sorted(results[rule.rule_id])}
        for rule in RULES
    }


def _diagnostic_baseline_block(counts: dict[str, dict[str, int]]) -> str:
    lines = ["BASELINE = {"]
    for rule_id in sorted(counts):
        lines.append(f'    "{rule_id}": {{')
        for path, count in counts[rule_id].items():
            lines.append(f'        "{path}": {count},')
        lines.append("    },")
    lines.append("}")
    return "\n".join(lines)


@tag("core")
class DesignSystemLintTest(SimpleTestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.base_dir = Path(settings.BASE_DIR)
        cls.template_root = cls.base_dir / "templates"
        cls.results = scan_templates(cls.template_root, cls.base_dir)
        cls.counts = _current_counts(cls.results)

    def test_repository_does_not_exceed_baseline(self):
        offenders: list[str] = []
        for rule in sorted(RULES, key=lambda item: item.rule_id):
            for path, matches in self.results[rule.rule_id].items():
                actual = len(matches)
                allowed = BASELINE.get(rule.rule_id, {}).get(path, 0)
                if actual <= allowed:
                    continue
                delta = actual - allowed
                for match in matches:
                    offenders.append(
                        f"{rule.rule_id} [contract: _docs/design-system.md → "
                        f"{rule.section}] {path}:{match.line} "
                        f"{match.excerpt!r} allowed={allowed} actual={actual} "
                        f"delta=+{delta}"
                    )

        self.assertEqual(
            offenders,
            [],
            "Design-system source regressions detected:\n"
            + "\n".join(offenders)
            + "\nRemediation: use the documented owner/pattern or fix the legacy "
            "occurrence; do not raise the baseline.\n"
            "Diagnostic current-count block (ready to paste for inspected "
            "initial seeding/debugging; this is not authorization):\n" + _diagnostic_baseline_block(self.counts),
        )

    def test_allowlists_are_exact_and_shrink_only(self):
        problems: list[str] = []
        expected_rule_ids = sorted(RULE_BY_ID)
        baseline_rule_ids = list(BASELINE)
        unknown = sorted(set(BASELINE) - set(RULE_BY_ID))
        missing = sorted(set(RULE_BY_ID) - set(BASELINE))
        if unknown:
            problems.append(f"unknown rule ids: {unknown}")
        if missing:
            problems.append(f"missing rule ids: {missing}")
        if baseline_rule_ids != expected_rule_ids:
            problems.append(
                f"baseline rule ids must be sorted: expected={expected_rule_ids!r} actual={baseline_rule_ids!r}"
            )

        discovered_paths = {_relative_posix(path, self.base_dir) for path in discover_templates(self.template_root)}
        for rule_id in sorted(set(BASELINE) & set(RULE_BY_ID)):
            allowances = BASELINE[rule_id]
            if list(allowances) != sorted(allowances):
                problems.append(f"{rule_id}: paths must be sorted")
            for path, allowed in allowances.items():
                pure_path = PurePosixPath(path)
                if (
                    "\\" in path
                    or pure_path.as_posix() != path
                    or not path.startswith("templates/")
                    or ".." in pure_path.parts
                ):
                    problems.append(f"{rule_id}/{path}: path must be repository-relative POSIX")
                valid_allowance = type(allowed) is int and allowed > 0
                if not valid_allowance:
                    problems.append(f"{rule_id}/{path}: allowance must be a positive integer, got {allowed!r}")
                if path not in discovered_paths:
                    problems.append(f"{rule_id}/{path}: template is missing; delete this stale entry")
                actual = self.counts[rule_id].get(path, 0)
                if valid_allowance and actual < allowed:
                    instruction = "delete the entry" if actual == 0 else f"lower the allowance to {actual}"
                    problems.append(f"{rule_id}/{path}: old allowance={allowed}, actual={actual}; {instruction}")

        self.assertEqual(
            problems,
            [],
            "The design-system baseline is invalid or stale:\n" + "\n".join(problems),
        )

    def test_rule_self_checks(self):
        samples = {
            "deprecated_content_gated_include": ('<p>before</p>\n{% include "includes/content_gated.html" %}'),
            "legacy_px5_py25_pair": '<a class="px-5 py-2.5">Go</a>',
            "public_font_bold": '<h2 class="font-bold">Title</h2>',
            "public_tracking_wider": '<p class="tracking-wider">Label</p>',
            "grid_gap5": '<div class="grid gap-5"></div>',
            "handrolled_empty_state_signature": ('<div class="p-12 text-center">Nothing here</div>'),
        }
        for rule in RULES:
            with self.subTest(rule=rule.rule_id):
                matches = find_rule_matches(
                    rule,
                    "templates/__design_system_lint_self_check.html",
                    samples[rule.rule_id],
                )
                self.assertEqual(len(matches), 1)
                expected_line = 2 if rule.rule_id == "deprecated_content_gated_include" else 1
                self.assertEqual(matches[0].line, expected_line)

    def test_rule_boundary_examples(self):
        cases = (
            ("public_tracking_wider", "templates/public.html", '<p class="tracking-widest">x</p>', 0),
            (
                "deprecated_content_gated_include",
                "templates/public.html",
                "Prose names includes/content_gated.html without an include tag.",
                0,
            ),
            ("grid_gap5", "templates/public.html", '<div class="flex gap-5"></div>', 0),
            ("public_font_bold", "templates/studio/page.html", '<b class="font-bold">x</b>', 0),
            (
                "public_tracking_wider",
                "templates/studio/page.html",
                '<p class="tracking-wider">x</p>',
                0,
            ),
            ("public_font_bold", "templates/emails/message.html", '<b class="font-bold">x</b>', 0),
            (
                "public_tracking_wider",
                "templates/emails/message.html",
                '<p class="tracking-wider">x</p>',
                0,
            ),
            ("grid_gap5", "templates/studio/page.html", '<div class="grid gap-5"></div>', 1),
            (
                "legacy_px5_py25_pair",
                "templates/emails/message.html",
                '<a class="px-5 py-2.5">x</a>',
                1,
            ),
            (
                "deprecated_content_gated_include",
                "templates/public.html",
                "{%  include\n 'includes/content_gated.html'  %}",
                1,
            ),
            ("legacy_px5_py25_pair", "templates/public.html", "<a class='py-2.5\n px-5'>x</a>", 1),
            ("grid_gap5", "templates/public.html", '<div class="gap-5\n grid"></div>', 1),
            (
                "legacy_px5_py25_pair",
                "templates/public.html",
                '<a\n data-kind="cta"\n class = "py-2.5 text-white px-5">x</a>',
                1,
            ),
            ("public_font_bold", "templates/public.html", '<b class="font-bolder">x</b>', 0),
            ("public_tracking_wider", "templates/public.html", '<b class="md:tracking-wider">x</b>', 0),
            ("legacy_px5_py25_pair", "templates/public.html", '<b class="px-50 py-2.50">x</b>', 0),
            ("grid_gap5", "templates/public.html", '<b class="subgrid gap-50">x</b>', 0),
            ("public_font_bold", "templates/public.html", '<b data-class="font-bold">x</b>', 0),
            (
                "public_font_bold",
                "templates/public.html",
                "<b data-example='class=\"font-bold\"'>x</b>",
                0,
            ),
            (
                "public_font_bold",
                "templates/public.html",
                'Prose explains class="font-bold" without an HTML start tag.',
                0,
            ),
            (
                "public_font_bold",
                "templates/public.html",
                '<!-- <strong class="font-bold">commented out</strong> -->',
                0,
            ),
            (
                "public_font_bold",
                "templates/public.html",
                "<script>const token = 'class=\"font-bold\"'; "
                "const markup = '<strong class=\"font-bold\">x</strong>';</script>",
                0,
            ),
            (
                "handrolled_empty_state_signature",
                "templates/public.html",
                '<b class="p-120 text-centered">x</b>',
                0,
            ),
        )
        for rule_id, path, source, expected in cases:
            with self.subTest(rule=rule_id, source=source):
                self.assertEqual(
                    len(find_rule_matches(RULE_BY_ID[rule_id], path, source)),
                    expected,
                )

    def test_discovery_includes_untracked_templates(self):
        local_tmp = self.base_dir / ".tmp"
        local_tmp.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="design-system-lint-",
            dir=local_tmp,
        ) as temporary_directory:
            temporary_base = Path(temporary_directory)
            temporary_templates = temporary_base / "templates"
            nested = temporary_templates / "new" / "untracked.html"
            nested.parent.mkdir(parents=True)
            nested.write_text('<p class="font-bold">Untracked</p>', encoding="utf-8")

            discovered = discover_templates(temporary_templates)
            results = scan_templates(temporary_templates, temporary_base)

            self.assertEqual(discovered, (nested,))
            self.assertEqual(
                len(results["public_font_bold"]["templates/new/untracked.html"]),
                1,
            )


@tag("core")
class DesignSystemWorkshopMediaContractTest(SimpleTestCase):
    """Keep the Workshop media documentation aligned with deployed #1237."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.base_dir = Path(settings.BASE_DIR)
        cls.design_system = (cls.base_dir / "_docs/design-system.md").read_text(encoding="utf-8")
        cls.workshop_model = (cls.base_dir / "content/models/workshop.py").read_text(encoding="utf-8")
        cls.catalog_template = (cls.base_dir / "templates/content/_workshops_catalog.html").read_text(
            encoding="utf-8"
        )
        cls.banner_resolver = (
            cls.base_dir / "integrations/services/banner_generator/resolve.py"
        ).read_text(encoding="utf-8")

    def test_workshop_media_decision_documents_conditional_explicit_policy(self):
        card_media_section = self.design_system.split("## Card Media Slots", 1)[1].split(
            "## Breakpoints and Mobile Carousels", 1
        )[0]

        self.assertIn(
            "| Workshops | Conditional explicit media: render exactly one slot for an authored "
            "`cover_image_url` or operator `custom_banner_url`; render no slot for coverless or "
            "auto-only cards. Generated `auto_banner_url` remains social/Studio media only. |",
            card_media_section,
        )
        self.assertNotIn("| Workshops | Render |", card_media_section)
        self.assertNotIn("Never branch on cover presence per card.", card_media_section)
        self.assertIn("Workshop.card_image_url", card_media_section)
        self.assertIn("Workshop.display_image_url", card_media_section)
        self.assertIn("no fallback, empty wrapper, or reserved `aspect-video` space", card_media_section)

    def test_content_preview_owner_names_the_workshop_exception(self):
        component_index = self.design_system.split("## Partials and Component Index", 1)[1].split(
            "### Deprecated", 1
        )[0]

        self.assertIn(
            "Workshop cards use it only for explicit cover/custom media; coverless and auto-only "
            "workshops omit the slot entirely.",
            component_index,
        )
        self.assertIn("{% if workshop.card_image_url %}", component_index)
        self.assertIn('preview_cover_url=workshop.card_image_url', component_index)

    def test_documented_workshop_policy_matches_runtime_selection_and_rendering(self):
        card_image_property = self.workshop_model.split("def card_image_url", 1)[1].split(
            "def user_can_access_landing", 1
        )[0]
        self.assertIn("return self.cover_image_url or self.custom_banner_url or ''", card_image_property)
        self.assertNotIn("auto_banner_url", card_image_property)

        display_image_property = self.workshop_model.split("def display_image_url", 1)[1].split(
            "def card_image_url", 1
        )[0]
        self.assertIn("return effective_banner_url(self)", display_image_property)
        self.assertIn(
            "cover_image_url`` -> ``custom_banner_url`` ->\n    ``auto_banner_url",
            self.banner_resolver,
        )

        conditional_start = self.catalog_template.index("{% if workshop.card_image_url %}")
        preview_include = self.catalog_template.index(
            '{% include "content/_content_preview.html"',
            conditional_start,
        )
        conditional_end = self.catalog_template.index("{% endif %}", preview_include)
        card_body = self.catalog_template.index('class="min-w-0 p-4 sm:p-5"', conditional_end)
        self.assertLess(conditional_start, preview_include)
        self.assertLess(preview_include, conditional_end)
        self.assertLess(conditional_end, card_body)
        self.assertNotIn("preview_decorative_fallback", self.catalog_template)
