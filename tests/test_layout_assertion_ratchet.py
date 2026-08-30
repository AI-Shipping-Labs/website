from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from django.test import SimpleTestCase, tag

from tests.layout_assertion_ceilings import (
    EXPECTED_CEILING_COUNTS,
    UNKNOWN_UNMARKED_ASSERTION_CALL_CEILING,
    UNKNOWN_UNMARKED_ASSERTION_CALL_GOLDEN_SHA256,
    UNMARKED_DIRECT_LAYOUT_ASSERTION_CEILING,
    UNMARKED_DIRECT_LAYOUT_ASSERTION_GOLDEN_SHA256,
)
from tests.layout_assertion_ratchet import (
    ASCII_WHITESPACE_TOKENIZER_VERSION,
    CLASSIFICATIONS,
    DIRECT_MARKER_REMEDIATION,
    LAYOUT_TOKEN_GRAMMAR_VERSION,
    LAYOUT_VOCABULARY_GOLDEN_SHA256,
    PLAYWRIGHT_ASSERTION_REGISTRY_GOLDEN_SHA256,
    PLAYWRIGHT_ASSERTION_REGISTRY_VERSION,
    PLAYWRIGHT_MATCHER_OPERANDS,
    PYTHON_ASSERTION_REGISTRY_VERSION,
    REPO_ROOT,
    RULE,
    UNITTEST_ASSERTION_OPERANDS,
    UNITTEST_ASSERTION_REGISTRY_GOLDEN_SHA256,
    UNKNOWN_MARKER_REMEDIATION,
    UNKNOWN_UNMARKED_ASSERTION_CALL,
    UNMARKED_DIRECT_LAYOUT_ASSERTION,
    compare_repository,
    discover_test_sources,
    is_layout_token,
    load_live_manifest,
    occurrences_from_source,
    operand_registry_sha256,
    ratchet_categories,
    scan_repository,
    vocabulary_sha256,
)
from tests.lexical_ast_ratchet import FailureKind, RatchetCategory, ceiling_sha256, compare_ratchet


def _occurrences(source: str):
    return occurrences_from_source("tests/test_sample.py", source)


def _tokens(occurrence) -> tuple[str, ...]:
    payload = json.loads(occurrence.canonical_payload)
    return tuple(payload["metadata"]["tokens"])


def _one(classification: str):
    statement = (
        'self.assertIn("max-w-5xl", classes)'
        if classification == UNMARKED_DIRECT_LAYOUT_ASSERTION
        else 'checker.assertVisible("max-w-5xl")'
    )
    return _occurrences(f"def test_x(self):\n    {statement}\n")[0]


def _category(occurrence, *, classification: str | None = None, reason: str = "reviewed debt"):
    selected = classification or occurrence.classification
    return RatchetCategory(
        name=selected,
        rule=RULE,
        classification=selected,
        live={occurrence.occurrence_id: reason},
        ceiling=(occurrence.occurrence_id,),
        ceiling_golden_sha256=ceiling_sha256((occurrence.occurrence_id,)),
    )


@pytest.mark.visual_regression
@tag("core")
class LayoutMarkerGrammarTests(SimpleTestCase):
    def test_exact_function_async_function_and_direct_class_markers_suppress_both_categories(self):
        cases = (
            """
@pytest.mark.visual_regression
def test_x(self):
    self.assertIn("max-w-5xl", classes)
    checker.assertVisible("grid-cols-2")
""",
            """
@pytest.mark.visual_regression
async def test_x(self):
    self.assertIn("max-w-5xl", classes)
""",
            """
@pytest.mark.visual_regression
class TestLayout:
    def test_x(self):
        self.assertIn("max-w-5xl", classes)
        checker.assertVisible("grid-cols-2")
""",
            """
@pytest.mark.visual_regression
class TestLayout:
    self.assertIn("max-w-5xl", classes)
""",
        )
        for source in cases:
            with self.subTest(source=source):
                self.assertEqual(_occurrences(source), ())

    def test_near_miss_module_alias_django_call_and_dynamic_markers_do_not_suppress(self):
        decorators = (
            "@visual_regression",
            "@pytest.mark.visual_regression()",
            "@pytest.mark.not_visual_regression",
            "@tag('visual_regression')",
            "@marker_factory(pytest.mark.visual_regression)",
        )
        for decorator in decorators:
            with self.subTest(decorator=decorator):
                source = f"{decorator}\ndef test_x(self):\n    self.assertIn('max-w-5xl', classes)\n"
                self.assertEqual(len(_occurrences(source)), 1)

        module_marker = """
pytestmark = pytest.mark.visual_regression
def test_x(self):
    self.assertIn("max-w-5xl", classes)
"""
        self.assertEqual(len(_occurrences(module_marker)), 1)

    def test_marker_does_not_flow_through_nested_function_class_outer_class_or_inheritance(self):
        cases = (
            """
@pytest.mark.visual_regression
def outer():
    def test_x(self):
        self.assertIn("max-w-5xl", classes)
""",
            """
@pytest.mark.visual_regression
class Outer:
    class Inner:
        def test_x(self):
            self.assertIn("max-w-5xl", classes)
""",
            """
@pytest.mark.visual_regression
class Outer:
    def method(self):
        def test_x(self):
            self.assertIn("max-w-5xl", classes)
""",
            """
@pytest.mark.visual_regression
class Base:
    pass
class Child(Base):
    def test_x(self):
        self.assertIn("max-w-5xl", classes)
""",
        )
        for source in cases:
            with self.subTest(source=source):
                self.assertEqual(len(_occurrences(source)), 1)

    def test_marker_removal_or_alias_rebinding_exposes_every_anchor_independently(self):
        marked = """
@pytest.mark.visual_regression
def test_x(self):
    self.assertIn("max-w-5xl", classes)
    checker.assertVisible("grid-cols-2")
"""
        self.assertEqual(_occurrences(marked), ())
        removed = marked.replace("@pytest.mark.visual_regression\n", "")
        rebound = marked.replace("@pytest.mark.visual_regression", "@visual")
        self.assertEqual(len(_occurrences(removed)), 2)
        self.assertEqual(len(_occurrences(rebound)), 2)

    def test_contract_is_syntactic_and_accepts_exact_spelling_even_if_pytest_is_rebound(self):
        source = """
pytest = fake_pytest
@pytest.mark.visual_regression
def test_x(self):
    self.assertIn("max-w-5xl", classes)
"""
        self.assertEqual(_occurrences(source), ())


@pytest.mark.visual_regression
@tag("core")
class LayoutOperandGrammarTests(SimpleTestCase):
    def test_plain_unittest_and_playwright_direct_operands_are_recognized(self):
        cases = (
            'assert "max-w-5xl" in classes',
            'self.assertIn("max-w-5xl", classes)',
            'self.assertEqual(first="grid-cols-2", second=actual)',
            'expect(locator).to_have_class("md:grid-cols-2")',
            'expect("flex-col").to_be_visible()',
        )
        for statement in cases:
            with self.subTest(statement=statement):
                occurrences = _occurrences(f"def test_x(self):\n    {statement}\n")
                self.assertEqual(len(occurrences), 1)
                self.assertEqual(occurrences[0].classification, UNMARKED_DIRECT_LAYOUT_ASSERTION)

    def test_boolean_not_comparison_and_fstring_literal_segments_are_the_only_descent(self):
        cases = (
            ('assert "max-w-5xl" in classes and "grid-cols-2" in classes', ("grid-cols-2", "max-w-5xl")),
            ('assert not ("max-w-5xl" not in classes)', ("max-w-5xl",)),
            ('assert actual == f"grid-cols-2 {dynamic}"', ("grid-cols-2",)),
        )
        for statement, expected in cases:
            with self.subTest(statement=statement):
                occurrence = _occurrences(f"def test_x():\n    {statement}\n")[0]
                self.assertEqual(_tokens(occurrence), expected)

    def test_calls_collections_subscripts_formatters_and_runtime_values_are_explicit_non_goals(self):
        cases = (
            'assert helper("max-w-5xl")',
            'assert ["max-w-5xl"]',
            'assert {"max-w-5xl": value}',
            'assert VALUES["max-w-5xl"]',
            'assert "max-w-{}".format(size)',
            'assert "max-w-%s" % size',
            'assert " ".join(["max-w-5xl"])',
            'assert f"{layout_token}"',
            'self.assertTrue(EXPECTED_LAYOUT)',
        )
        for statement in cases:
            with self.subTest(statement=statement):
                self.assertEqual(_occurrences(f"def test_x(self):\n    {statement}\n"), ())

    def test_messages_timeouts_diagnostics_and_unknown_keywords_are_excluded(self):
        cases = (
            'assert condition, "max-w-5xl"',
            'self.assertEqual(actual, expected, "max-w-5xl")',
            'self.assertEqual(actual, expected, msg="max-w-5xl")',
            'expect(locator, "max-w-5xl").to_be_visible(timeout="grid-cols-2")',
            'checker.assertVisible(actual, diagnostic="max-w-5xl")',
        )
        for statement in cases:
            with self.subTest(statement=statement):
                self.assertEqual(_occurrences(f"def test_x(self):\n    {statement}\n"), ())

    def test_nested_assertion_anchors_in_excluded_outer_positions_are_still_discovered(self):
        statements = (
            'assert condition, checker.assertVisible("max-w-5xl")',
            'self.assertTrue(condition, checker.assertVisible("max-w-5xl"))',
            'self.assertTrue(condition, msg=checker.assertVisible("max-w-5xl"))',
            'expect(locator, checker.assertVisible("max-w-5xl")).to_be_visible()',
            'expect(locator).to_be_visible(timeout=checker.assertVisible("max-w-5xl"))',
            'checker.assertVisible(actual, diagnostic=other.to_layout("max-w-5xl"))',
        )
        for statement in statements:
            with self.subTest(statement=statement):
                occurrences = _occurrences(f"def test_x(self):\n    {statement}\n")
                self.assertEqual(len(occurrences), 1)
                self.assertEqual(occurrences[0].classification, UNKNOWN_UNMARKED_ASSERTION_CALL)
                self.assertEqual(_tokens(occurrences[0]), ("max-w-5xl",))

    def test_unknown_attribute_prefixes_are_independent_and_keywords_never_supply_tokens(self):
        cases = (
            'checker.assertVisible("max-w-5xl")',
            'checker.to_have_layout("grid-cols-2")',
            'checker.not_to_overlap("flex-col")',
            'other.assertEqual("max-w-5xl", actual)',
            'expect(locator).to_have_classes("grid-cols-2")',
        )
        for statement in cases:
            with self.subTest(statement=statement):
                occurrences = _occurrences(f"def test_x(self):\n    {statement}\n")
                self.assertEqual(len(occurrences), 1)
                self.assertEqual(occurrences[0].classification, UNKNOWN_UNMARKED_ASSERTION_CALL)

    def test_complete_ascii_token_boundaries_and_normalized_token_sets(self):
        direct = _occurrences(
            'def test_x():\n    assert "max-w-5xl\\tgrid-cols-2\\nmax-w-5xl" in classes\n'
        )[0]
        self.assertEqual(_tokens(direct), ("grid-cols-2", "max-w-5xl"))
        for lookalike in ("prose-max-w-5xl", "max-w-5xl,", "amax-w-5xl", "max-w-"):
            with self.subTest(lookalike=lookalike):
                self.assertEqual(
                    _occurrences(f'def test_x():\n    assert "{lookalike}" in classes\n'),
                    (),
                )

    def test_innermost_assertion_anchor_claims_its_literal_without_outer_duplication(self):
        occurrences = _occurrences(
            'def test_x():\n    assert checker.assertVisible("max-w-5xl") == "grid-cols-2"\n'
        )
        self.assertEqual(len(occurrences), 2)
        self.assertEqual(
            {item.classification: _tokens(item) for item in occurrences},
            {
                UNKNOWN_UNMARKED_ASSERTION_CALL: ("max-w-5xl",),
                UNMARKED_DIRECT_LAYOUT_ASSERTION: ("grid-cols-2",),
            },
        )

    def test_multiple_and_duplicate_assertions_have_stable_ordinals_and_formatting(self):
        source = """
def test_x(self):
    self.assertIn("max-w-5xl", classes)
    self.assertIn("max-w-5xl", classes)
"""
        occurrences = _occurrences(source)
        self.assertEqual([item.duplicate_ordinal for item in occurrences], [1, 2])
        self.assertEqual(occurrences[0].fingerprint, occurrences[1].fingerprint)
        formatted = _occurrences(
            '# comment\n\ndef test_x( self ):\n    self.assertIn( "max-w-5xl", classes )\n'
        )[0]
        self.assertEqual(occurrences[0].occurrence_id, formatted.occurrence_id)


@pytest.mark.visual_regression
@tag("core")
class LayoutRegistryAndVocabularyTests(SimpleTestCase):
    def test_versioned_unittest_registry_has_exact_python_313_methods_and_operand_digest(self):
        self.assertEqual(PYTHON_ASSERTION_REGISTRY_VERSION, "python>=3.13-unittest-v1")
        self.assertEqual(len(UNITTEST_ASSERTION_OPERANDS), 33)
        self.assertEqual(
            operand_registry_sha256(UNITTEST_ASSERTION_OPERANDS),
            UNITTEST_ASSERTION_REGISTRY_GOLDEN_SHA256,
        )
        for method in UNITTEST_ASSERTION_OPERANDS:
            with self.subTest(method=method):
                source = f'def test_x(self):\n    self.{method}("max-w-5xl")\n'
                occurrences = _occurrences(source)
                self.assertEqual(len(occurrences), 1)
                self.assertEqual(occurrences[0].classification, UNMARKED_DIRECT_LAYOUT_ASSERTION)

    def test_versioned_playwright_registry_has_exact_158_matchers_and_operand_digest(self):
        self.assertEqual(PLAYWRIGHT_ASSERTION_REGISTRY_VERSION, "playwright-1.58.0-sync-expect-v1")
        self.assertEqual(len(PLAYWRIGHT_MATCHER_OPERANDS), 58)
        self.assertEqual(
            operand_registry_sha256(PLAYWRIGHT_MATCHER_OPERANDS),
            PLAYWRIGHT_ASSERTION_REGISTRY_GOLDEN_SHA256,
        )
        for matcher in PLAYWRIGHT_MATCHER_OPERANDS:
            with self.subTest(matcher=matcher):
                source = f'def test_x():\n    expect("max-w-5xl").{matcher}()\n'
                occurrences = _occurrences(source)
                self.assertEqual(len(occurrences), 1)
                self.assertEqual(occurrences[0].classification, UNMARKED_DIRECT_LAYOUT_ASSERTION)

    def test_each_playwright_matcher_owns_only_its_documented_keywords(self):
        expected_keywords = {
            "not_to_be_attached": ("attached",),
            "not_to_be_checked": (),
            "not_to_be_disabled": (),
            "not_to_be_editable": ("editable",),
            "not_to_be_empty": (),
            "not_to_be_enabled": ("enabled",),
            "not_to_be_focused": (),
            "not_to_be_hidden": (),
            "not_to_be_in_viewport": ("ratio",),
            "not_to_be_ok": (),
            "not_to_be_visible": ("visible",),
            "not_to_contain_class": ("expected",),
            "not_to_contain_text": ("expected", "use_inner_text", "ignore_case"),
            "not_to_have_accessible_description": ("name", "ignore_case"),
            "not_to_have_accessible_error_message": ("error_message", "ignore_case"),
            "not_to_have_accessible_name": ("name", "ignore_case"),
            "not_to_have_attribute": ("name", "value", "ignore_case"),
            "not_to_have_class": ("expected",),
            "not_to_have_count": ("count",),
            "not_to_have_css": ("name", "value"),
            "not_to_have_id": ("id",),
            "not_to_have_js_property": ("name", "value"),
            "not_to_have_role": ("role",),
            "not_to_have_text": ("expected", "use_inner_text", "ignore_case"),
            "not_to_have_title": ("title_or_reg_exp",),
            "not_to_have_url": ("url_or_reg_exp", "ignore_case"),
            "not_to_have_value": ("value",),
            "not_to_have_values": ("values",),
            "not_to_match_aria_snapshot": ("expected",),
            "to_be_attached": ("attached",),
            "to_be_checked": ("checked", "indeterminate"),
            "to_be_disabled": (),
            "to_be_editable": ("editable",),
            "to_be_empty": (),
            "to_be_enabled": ("enabled",),
            "to_be_focused": (),
            "to_be_hidden": (),
            "to_be_in_viewport": ("ratio",),
            "to_be_ok": (),
            "to_be_visible": ("visible",),
            "to_contain_class": ("expected",),
            "to_contain_text": ("expected", "use_inner_text", "ignore_case"),
            "to_have_accessible_description": ("description", "ignore_case"),
            "to_have_accessible_error_message": ("error_message", "ignore_case"),
            "to_have_accessible_name": ("name", "ignore_case"),
            "to_have_attribute": ("name", "value", "ignore_case"),
            "to_have_class": ("expected",),
            "to_have_count": ("count",),
            "to_have_css": ("name", "value"),
            "to_have_id": ("id",),
            "to_have_js_property": ("name", "value"),
            "to_have_role": ("role",),
            "to_have_text": ("expected", "use_inner_text", "ignore_case"),
            "to_have_title": ("title_or_reg_exp",),
            "to_have_url": ("url_or_reg_exp", "ignore_case"),
            "to_have_value": ("value",),
            "to_have_values": ("values",),
            "to_match_aria_snapshot": ("expected",),
        }
        actual_keywords = {
            matcher: spec.keywords for matcher, spec in PLAYWRIGHT_MATCHER_OPERANDS.items()
        }
        self.assertEqual(actual_keywords, expected_keywords)

        all_keywords = {"timeout"}
        all_keywords.update(
            keyword for keywords in expected_keywords.values() for keyword in keywords
        )
        for matcher, owned_keywords in expected_keywords.items():
            with self.subTest(matcher=matcher, boundary="accepted"):
                for keyword in owned_keywords:
                    source = f'def test_x():\n    expect(locator).{matcher}({keyword}="max-w-5xl")\n'
                    occurrences = _occurrences(source)
                    self.assertEqual(len(occurrences), 1, keyword)
                    self.assertEqual(
                        occurrences[0].classification,
                        UNMARKED_DIRECT_LAYOUT_ASSERTION,
                    )
            with self.subTest(matcher=matcher, boundary="rejected"):
                for keyword in all_keywords.difference(owned_keywords):
                    source = f'def test_x():\n    expect(locator).{matcher}({keyword}="max-w-5xl")\n'
                    self.assertEqual(_occurrences(source), (), keyword)

    def test_tokenizer_vocabulary_variants_hex_and_golden_are_exact(self):
        self.assertEqual(ASCII_WHITESPACE_TOKENIZER_VERSION, "ascii-whitespace-v1")
        self.assertEqual(LAYOUT_TOKEN_GRAMMAR_VERSION, "tailwind-layout-token-v1")
        self.assertEqual(vocabulary_sha256(), LAYOUT_VOCABULARY_GOLDEN_SHA256)
        for token in ("px-5", "min-h-screen", "bg-card", "flex-col", "max-w-7xl", "md:grid-cols-2", "#fff"):
            with self.subTest(token=token):
                self.assertTrue(is_layout_token(token))
        for token in ("prefixmax-w-7xl", "max-w-7xl,", "visual-layout", "content-type"):
            with self.subTest(token=token):
                self.assertFalse(is_layout_token(token))


@pytest.mark.visual_regression
@tag("core")
class LayoutRatchetContractTests(SimpleTestCase):
    def test_each_category_rejects_new_stale_replacement_duplicate_id_and_ordinal(self):
        for classification in CLASSIFICATIONS:
            with self.subTest(classification=classification):
                original = _one(classification)
                category = _category(original)
                self.assertEqual(
                    {item.kind for item in compare_ratchet((original,), (replace(category, live={}),)).failures},
                    {FailureKind.NEW},
                )
                self.assertEqual(
                    {item.kind for item in compare_ratchet((), (category,)).failures},
                    {FailureKind.STALE},
                )
                changed_source = (
                    'def test_x(self):\n    self.assertIn("grid-cols-2", classes)\n'
                    if classification == UNMARKED_DIRECT_LAYOUT_ASSERTION
                    else 'def test_x(self):\n    checker.assertVisible("grid-cols-2")\n'
                )
                changed = _occurrences(changed_source)[0]
                replacement = compare_ratchet((changed,), (category,))
                self.assertEqual(
                    {item.kind for item in replacement.failures},
                    {FailureKind.NEW, FailureKind.STALE, FailureKind.REPLACEMENT},
                )
                duplicate_id = compare_ratchet((original, original), (category,))
                self.assertIn(FailureKind.DUPLICATE_ID, {item.kind for item in duplicate_id.failures})
                statement = (
                    'self.assertIn("max-w-5xl", classes)'
                    if classification == UNMARKED_DIRECT_LAYOUT_ASSERTION
                    else 'checker.assertVisible("max-w-5xl")'
                )
                duplicates = _occurrences(f"def test_x(self):\n    {statement}\n    {statement}\n")
                ordinal = compare_ratchet(duplicates, (category,))
                self.assertEqual({item.kind for item in ordinal.failures}, {FailureKind.NEW})
                self.assertTrue(duplicates[1].occurrence_id.endswith("::2"))

    def test_each_category_rejects_overlap_missing_reason_ceiling_growth_and_golden_drift(self):
        for classification, other in (
            (UNMARKED_DIRECT_LAYOUT_ASSERTION, UNKNOWN_UNMARKED_ASSERTION_CALL),
            (UNKNOWN_UNMARKED_ASSERTION_CALL, UNMARKED_DIRECT_LAYOUT_ASSERTION),
        ):
            with self.subTest(classification=classification):
                occurrence = _one(classification)
                category = _category(occurrence)
                missing = compare_ratchet(
                    (occurrence,),
                    (replace(category, live={occurrence.occurrence_id: " "}),),
                )
                self.assertEqual({item.kind for item in missing.failures}, {FailureKind.MISSING_REASON})
                extra_id = occurrence.occurrence_id[:-1] + "2"
                extra = replace(occurrence, occurrence_id=extra_id)
                growth = compare_ratchet(
                    (extra,),
                    (replace(category, live={extra_id: "attempted growth"}),),
                )
                self.assertEqual({item.kind for item in growth.failures}, {FailureKind.CEILING_GROWTH})
                drift = compare_ratchet(
                    (occurrence,),
                    (replace(category, ceiling=(*category.ceiling, extra_id)),),
                )
                self.assertEqual({item.kind for item in drift.failures}, {FailureKind.GOLDEN_DRIFT})
                overlap = compare_ratchet(
                    (occurrence,),
                    (category, _category(occurrence, classification=other)),
                )
                self.assertIn(FailureKind.OVERLAP, {item.kind for item in overlap.failures})

    def test_adding_exact_marker_retires_live_debt_and_removing_it_is_new(self):
        unmarked = _one(UNMARKED_DIRECT_LAYOUT_ASSERTION)
        category = _category(unmarked)
        marked = _occurrences(
            '@pytest.mark.visual_regression\ndef test_x(self):\n    self.assertIn("max-w-5xl", classes)\n'
        )
        self.assertEqual(marked, ())
        self.assertEqual(
            {item.kind for item in compare_ratchet(marked, (category,)).failures},
            {FailureKind.STALE},
        )
        empty = replace(category, live={})
        self.assertEqual(
            {item.kind for item in compare_ratchet((unmarked,), (empty,)).failures},
            {FailureKind.NEW},
        )


@pytest.mark.visual_regression
@tag("core")
class RepositoryLayoutRatchetTests(SimpleTestCase):
    def test_discovery_scans_executable_policy_self_tests_and_every_test_tree(self):
        discovered = discover_test_sources()
        self.assertIn("tests/test_layout_assertion_ratchet.py", discovered)
        self.assertIn("asl_cli/tests/test_cli.py", discovered)
        self.assertTrue(any(path.startswith("playwright_tests/") for path in discovered))
        self.assertTrue(any(path.startswith("accounts/tests/") for path in discovered))
        self.assertEqual(
            [item for item in scan_repository() if item.path == "tests/test_layout_assertion_ratchet.py"],
            [],
        )

    def test_snippets_comments_docstrings_manifests_diagnostics_and_excluded_literals_are_not_occurrences(self):
        source = '''
"""self.assertIn("max-w-5xl", classes)"""
# assert "grid-cols-2" in classes
SNIPPET = "checker.assertVisible('flex-col')"
VALUES = ["max-w-5xl"]
def helper(self):
    assert condition, "grid-cols-2"
    self.assertTrue(VALUES)
'''
        self.assertEqual(_occurrences(source), ())

    def test_live_manifest_ceilings_and_repository_match_independent_categories(self):
        live = load_live_manifest()
        self.assertEqual(set(live), set(CLASSIFICATIONS))
        self.assertTrue(live[UNMARKED_DIRECT_LAYOUT_ASSERTION])
        self.assertTrue(live[UNKNOWN_UNMARKED_ASSERTION_CALL])
        self.assertEqual(set(live[UNMARKED_DIRECT_LAYOUT_ASSERTION].values()), {DIRECT_MARKER_REMEDIATION})
        self.assertEqual(set(live[UNKNOWN_UNMARKED_ASSERTION_CALL].values()), {UNKNOWN_MARKER_REMEDIATION})
        self.assertEqual(
            {
                UNMARKED_DIRECT_LAYOUT_ASSERTION: len(UNMARKED_DIRECT_LAYOUT_ASSERTION_CEILING),
                UNKNOWN_UNMARKED_ASSERTION_CALL: len(UNKNOWN_UNMARKED_ASSERTION_CALL_CEILING),
            },
            EXPECTED_CEILING_COUNTS,
        )
        self.assertEqual(
            ceiling_sha256(UNMARKED_DIRECT_LAYOUT_ASSERTION_CEILING),
            UNMARKED_DIRECT_LAYOUT_ASSERTION_GOLDEN_SHA256,
        )
        self.assertEqual(
            ceiling_sha256(UNKNOWN_UNMARKED_ASSERTION_CALL_CEILING),
            UNKNOWN_UNMARKED_ASSERTION_CALL_GOLDEN_SHA256,
        )
        report = compare_repository()
        self.assertTrue(report.passed, report.diagnostic())

    def test_manifest_files_are_repository_owned_and_categories_do_not_overlap(self):
        for filename in ("layout_assertion_live.py", "layout_assertion_ceilings.py"):
            path = REPO_ROOT / "tests" / filename
            self.assertTrue(path.is_file())
            self.assertTrue(path.resolve().is_relative_to(Path(__file__).resolve().parent.parent))
        categories = ratchet_categories()
        self.assertTrue(set(categories[0].ceiling).isdisjoint(categories[1].ceiling))
