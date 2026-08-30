from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path

from django.test import SimpleTestCase, tag

from tests.assert_called_once_ceilings import (
    DIRECT_BARE_CALL_CEILING,
    DIRECT_BARE_CALL_GOLDEN_SHA256,
    EXPECTED_CEILING_COUNTS,
    NONCALL_REFERENCE_CEILING,
    NONCALL_REFERENCE_GOLDEN_SHA256,
    UNKNOWN_CALL_SHAPE_CEILING,
    UNKNOWN_CALL_SHAPE_GOLDEN_SHA256,
    UNKNOWN_DYNAMIC_REFERENCE_CEILING,
    UNKNOWN_DYNAMIC_REFERENCE_GOLDEN_SHA256,
)
from tests.assert_called_once_ratchet import (
    CLASSIFICATIONS,
    DIRECT_BARE_CALL,
    DIRECT_REWRITE_REASON,
    DYNAMIC_REWRITE_REASON,
    NONCALL_REFERENCE,
    NONCALL_REWRITE_REASON,
    REPO_ROOT,
    RULE,
    UNKNOWN_CALL_REWRITE_REASON,
    UNKNOWN_CALL_SHAPE,
    UNKNOWN_DYNAMIC_REFERENCE,
    candidates_from_source,
    compare_repository,
    discover_test_sources,
    load_live_manifest,
    occurrences_from_source,
    ratchet_categories,
    scan_repository,
)
from tests.lexical_ast_ratchet import (
    FailureKind,
    RatchetCategory,
    ceiling_sha256,
    compare_ratchet,
)


def _classifications(source: str) -> list[str]:
    return [occurrence.classification for occurrence in occurrences_from_source("tests/test_sample.py", source)]


def _source_for(classification: str) -> str:
    return {
        DIRECT_BARE_CALL: "def test_x():\n    target.assert_called_once()\n",
        UNKNOWN_CALL_SHAPE: "def test_x():\n    target.assert_called_once(expected)\n",
        NONCALL_REFERENCE: "def test_x():\n    verify = target.assert_called_once\n",
        UNKNOWN_DYNAMIC_REFERENCE: ('def test_x():\n    verify = getattr(target, "assert_called_once")\n'),
    }[classification]


def _one_occurrence(classification: str):
    return occurrences_from_source(
        "tests/test_sample.py",
        _source_for(classification),
    )[0]


def _category_for(occurrence, *, classification=None, reason="reviewed rewrite debt"):
    selected_classification = classification or occurrence.classification
    return RatchetCategory(
        name=selected_classification,
        rule=RULE,
        classification=selected_classification,
        live={occurrence.occurrence_id: reason},
        ceiling=(occurrence.occurrence_id,),
        ceiling_golden_sha256=ceiling_sha256((occurrence.occurrence_id,)),
    )


def _duplicate_occurrences(classification: str):
    statement = {
        DIRECT_BARE_CALL: "target.assert_called_once()",
        UNKNOWN_CALL_SHAPE: "target.assert_called_once(expected)",
        NONCALL_REFERENCE: "aliases = [target.assert_called_once]",
        UNKNOWN_DYNAMIC_REFERENCE: 'aliases = [getattr(target, "assert_called_once")]',
    }[classification]
    return occurrences_from_source(
        "tests/test_sample.py",
        f"def test_x():\n    {statement}\n    {statement}\n",
    )


@tag("core")
class AssertCalledOnceClassificationTests(SimpleTestCase):
    def test_direct_bare_calls_are_receiver_agnostic_and_emit_one_attribute_anchor(self):
        receivers = (
            "notify",
            "checker",
            "service.mock",
            "factory()",
            "items[0]",
        )
        for receiver in receivers:
            with self.subTest(receiver=receiver):
                source = f"def test_x():\n    ({receiver}).assert_called_once()\n"
                candidates = candidates_from_source("tests/test_sample.py", source)
                self.assertEqual(
                    [candidate.classification for candidate in candidates],
                    [DIRECT_BARE_CALL],
                )
                self.assertEqual(len(candidates), 1)
                self.assertIsInstance(candidates[0].anchor, ast.Attribute)

    def test_any_visible_argument_or_keyword_is_an_unknown_call_shape(self):
        calls = (
            "target.assert_called_once(expected)",
            "target.assert_called_once(value=expected)",
            "target.assert_called_once(*expected)",
            "target.assert_called_once(**expected)",
            "target.assert_called_once(expected, value=other)",
        )
        for call in calls:
            with self.subTest(call=call):
                self.assertEqual(
                    _classifications(f"def test_x():\n    {call}\n"),
                    [UNKNOWN_CALL_SHAPE],
                )

    def test_every_other_loaded_attribute_form_is_a_noncall_reference(self):
        statements = (
            "verify = target.assert_called_once",
            "aliases = [target.assert_called_once]",
            "return target.assert_called_once",
            "consume(target.assert_called_once)",
            "assert target.assert_called_once == expected",
        )
        for statement in statements:
            with self.subTest(statement=statement):
                self.assertEqual(
                    _classifications(f"def test_x():\n    {statement}\n"),
                    [NONCALL_REFERENCE],
                )

        decorator_source = """
@target.assert_called_once
def test_x():
    pass
"""
        self.assertEqual(_classifications(decorator_source), [NONCALL_REFERENCE])

    def test_attribute_assignment_target_is_not_a_loaded_reference(self):
        self.assertEqual(
            _classifications("def test_x():\n    target.assert_called_once = replacement\n"),
            [],
        )

    def test_literal_getattr_is_dynamic_but_opaque_or_computed_names_are_non_goals(self):
        literal_cases = (
            'getattr(target, "assert_called_once")',
            'getattr(target, "assert_" "called_once")',
            'getattr(factory(), "assert_called_once")()',
            'getattr(target, "assert_called_once", fallback)',
        )
        for expression in literal_cases:
            with self.subTest(expression=expression):
                self.assertEqual(
                    _classifications(f"def test_x():\n    value = {expression}\n"),
                    [UNKNOWN_DYNAMIC_REFERENCE],
                )

        ignored_cases = (
            "getattr(target, name)",
            'getattr(target, "assert_" + "called_once")',
            'builtins.getattr(target, "assert_called_once")',
        )
        for expression in ignored_cases:
            with self.subTest(expression=expression):
                self.assertEqual(
                    _classifications(f"def test_x():\n    value = {expression}\n"),
                    [],
                )

    def test_exact_names_only_and_neighboring_argument_proof_never_exempts_bare_anchor(self):
        source = """
def helper(target):
    target.assert_called_once()
    target.assert_called_once_with(expected)
    target.assert_called_with(expected)
    target.my_assert_called_once_helper()
"""
        occurrences = occurrences_from_source("tests/test_sample.py", source)
        self.assertEqual(len(occurrences), 1)
        self.assertEqual(occurrences[0].classification, DIRECT_BARE_CALL)
        self.assertEqual(occurrences[0].lexical_scope, "helper")

    def test_module_helpers_and_nested_scopes_are_all_scanned(self):
        source = """
module_target.assert_called_once()
def helper():
    helper_target.assert_called_once()
    def nested():
        return nested_target.assert_called_once
"""
        occurrences = occurrences_from_source("tests/test_sample.py", source)
        self.assertEqual(
            [(item.lexical_scope, item.classification) for item in occurrences],
            [
                ("<module>", DIRECT_BARE_CALL),
                ("helper", DIRECT_BARE_CALL),
                ("helper.<locals>.nested", NONCALL_REFERENCE),
            ],
        )

    def test_multiple_duplicate_anchors_keep_separate_stable_ordinals(self):
        for classification in CLASSIFICATIONS:
            with self.subTest(classification=classification):
                occurrences = _duplicate_occurrences(classification)
                self.assertEqual([item.duplicate_ordinal for item in occurrences], [1, 2])
                self.assertEqual(occurrences[0].fingerprint, occurrences[1].fingerprint)

    def test_formatting_comments_and_line_movement_do_not_change_identity(self):
        cases = (
            (
                "def test_x():\n    target.assert_called_once()\n",
                "# comment\n\n\ndef test_x( ) :\n    (target).assert_called_once( )  # tail\n",
            ),
            (
                'def test_x():\n    getattr(target, "assert_called_once")\n',
                "# comment\n\n\ndef test_x( ) :\n    getattr( target, 'assert_called_once' )  # tail\n",
            ),
        )
        for compact_source, formatted_source in cases:
            with self.subTest(compact_source=compact_source):
                compact = occurrences_from_source("tests/test_sample.py", compact_source)[0]
                formatted = occurrences_from_source("tests/test_sample.py", formatted_source)[0]
                self.assertEqual(compact.occurrence_id, formatted.occurrence_id)

    def test_strings_comments_and_exact_name_near_misses_are_not_occurrences(self):
        source = '''
"""target.assert_called_once()"""
# target.assert_called_once()
MESSAGE = "getattr(target, 'assert_called_once')"
def test_policy(self):
    self.assertEqual(MESSAGE, "assert_called_once")
    target.assert_called_once_with(expected)
    target.assert_called_once_extra()
'''
        self.assertEqual(occurrences_from_source("tests/test_policy.py", source), ())

    def test_visible_attribute_inside_literal_getattr_receiver_is_an_independent_anchor(self):
        source = """
def test_x():
    getattr(target.assert_called_once, "assert_called_once")
"""
        self.assertEqual(
            _classifications(source),
            [UNKNOWN_DYNAMIC_REFERENCE, NONCALL_REFERENCE],
        )


@tag("core")
class AssertCalledOnceRatchetContractTests(SimpleTestCase):
    def test_each_category_rejects_new_stale_replacement_and_duplicate_ordinal(self):
        for classification in CLASSIFICATIONS:
            with self.subTest(classification=classification):
                original = _one_occurrence(classification)
                category = _category_for(original)

                new_report = compare_ratchet((original,), (replace(category, live={}),))
                self.assertEqual(
                    {failure.kind for failure in new_report.failures},
                    {FailureKind.NEW},
                )

                stale_report = compare_ratchet((), (category,))
                self.assertEqual(
                    {failure.kind for failure in stale_report.failures},
                    {FailureKind.STALE},
                )

                actual_new = replace(original, occurrence_id=f"{original.occurrence_id[:-1]}2")
                replacement = compare_ratchet((actual_new,), (category,))
                self.assertEqual(
                    {failure.kind for failure in replacement.failures},
                    {FailureKind.NEW, FailureKind.STALE, FailureKind.REPLACEMENT},
                )

                duplicates = _duplicate_occurrences(classification)
                duplicate_category = _category_for(duplicates[0])
                duplicate = compare_ratchet(duplicates, (duplicate_category,))
                self.assertEqual(
                    {failure.kind for failure in duplicate.failures},
                    {FailureKind.NEW},
                )
                self.assertTrue(duplicates[1].occurrence_id.endswith("::2"))

    def test_each_category_rejects_missing_reason_ceiling_growth_and_golden_drift(self):
        for classification in CLASSIFICATIONS:
            with self.subTest(classification=classification):
                occurrence = _one_occurrence(classification)
                category = _category_for(occurrence)
                missing_reason = compare_ratchet(
                    (occurrence,),
                    (replace(category, live={occurrence.occurrence_id: " "}),),
                )
                self.assertEqual(
                    {failure.kind for failure in missing_reason.failures},
                    {FailureKind.MISSING_REASON},
                )

                extra_id = f"{occurrence.occurrence_id[:-1]}2"
                extra_occurrence = replace(occurrence, occurrence_id=extra_id)
                live_growth = replace(category, live={extra_id: "attempted growth"})
                growth = compare_ratchet((extra_occurrence,), (live_growth,))
                self.assertEqual(
                    {failure.kind for failure in growth.failures},
                    {FailureKind.CEILING_GROWTH},
                )

                golden_drift = replace(category, ceiling=(*category.ceiling, extra_id))
                drift = compare_ratchet((occurrence,), (golden_drift,))
                self.assertEqual(
                    {failure.kind for failure in drift.failures},
                    {FailureKind.GOLDEN_DRIFT},
                )

    def test_each_category_rejects_overlap_with_every_other_bucket(self):
        for classification in CLASSIFICATIONS:
            occurrence = _one_occurrence(classification)
            for other in CLASSIFICATIONS:
                if other == classification:
                    continue
                with self.subTest(classification=classification, other=other):
                    first = _category_for(occurrence)
                    second = _category_for(occurrence, classification=other)
                    report = compare_ratchet((occurrence,), (first, second))
                    self.assertIn(
                        FailureKind.OVERLAP,
                        {failure.kind for failure in report.failures},
                    )


@tag("core")
class RepositoryAssertCalledOnceRatchetTests(SimpleTestCase):
    def test_discovery_scans_executable_policy_self_tests_and_all_test_trees(self):
        discovered = discover_test_sources()
        self.assertIn("tests/test_assert_called_once_ratchet.py", discovered)
        self.assertIn("tests/assert_called_once_ratchet.py", discovered)
        self.assertIn("tests/assert_called_once_live.py", discovered)
        self.assertIn("tests/assert_called_once_ceilings.py", discovered)
        self.assertIn("asl_cli/tests/test_cli.py", discovered)
        self.assertTrue(any(path.startswith("playwright_tests/") for path in discovered))
        self.assertTrue(any(path.startswith("accounts/tests/") for path in discovered))

        policy_surface_occurrences = [
            item
            for item in scan_repository()
            if item.path
            in {
                "tests/test_assert_called_once_ratchet.py",
                "tests/assert_called_once_ratchet.py",
                "tests/assert_called_once_live.py",
                "tests/assert_called_once_ceilings.py",
            }
        ]
        self.assertEqual(policy_surface_occurrences, [])

    def test_live_manifest_has_exact_categories_and_non_empty_rewrite_reasons(self):
        live = load_live_manifest()
        expected_reasons = {
            DIRECT_BARE_CALL: DIRECT_REWRITE_REASON,
            UNKNOWN_CALL_SHAPE: UNKNOWN_CALL_REWRITE_REASON,
            NONCALL_REFERENCE: NONCALL_REWRITE_REASON,
            UNKNOWN_DYNAMIC_REFERENCE: DYNAMIC_REWRITE_REASON,
        }
        self.assertEqual(set(live), set(CLASSIFICATIONS))
        for classification, expected_reason in expected_reasons.items():
            with self.subTest(classification=classification):
                self.assertTrue(all(reason.strip() for reason in live[classification].values()))
                if live[classification]:
                    self.assertEqual(set(live[classification].values()), {expected_reason})

    def test_immutable_ceilings_have_their_frozen_counts_and_golden_digests(self):
        ceilings = {
            DIRECT_BARE_CALL: (
                DIRECT_BARE_CALL_CEILING,
                DIRECT_BARE_CALL_GOLDEN_SHA256,
            ),
            UNKNOWN_CALL_SHAPE: (
                UNKNOWN_CALL_SHAPE_CEILING,
                UNKNOWN_CALL_SHAPE_GOLDEN_SHA256,
            ),
            NONCALL_REFERENCE: (
                NONCALL_REFERENCE_CEILING,
                NONCALL_REFERENCE_GOLDEN_SHA256,
            ),
            UNKNOWN_DYNAMIC_REFERENCE: (
                UNKNOWN_DYNAMIC_REFERENCE_CEILING,
                UNKNOWN_DYNAMIC_REFERENCE_GOLDEN_SHA256,
            ),
        }
        expected_counts = {
            DIRECT_BARE_CALL: 239,
            UNKNOWN_CALL_SHAPE: 0,
            NONCALL_REFERENCE: 0,
            UNKNOWN_DYNAMIC_REFERENCE: 0,
        }
        expected_goldens = {
            DIRECT_BARE_CALL: "817b1e341b15f459bda995e8740d7f3bcfc55b874856994c9f8a9dc17ce5f267",
            UNKNOWN_CALL_SHAPE: "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945",
            NONCALL_REFERENCE: "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945",
            UNKNOWN_DYNAMIC_REFERENCE: "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945",
        }
        self.assertEqual(EXPECTED_CEILING_COUNTS, expected_counts)
        for classification, (ceiling, golden) in ceilings.items():
            with self.subTest(classification=classification):
                self.assertEqual(len(ceiling), EXPECTED_CEILING_COUNTS[classification])
                self.assertEqual(golden, expected_goldens[classification])
                self.assertEqual(ceiling_sha256(ceiling), golden)

    def test_current_repository_matches_the_shrink_only_manifests(self):
        report = compare_repository()
        self.assertTrue(report.passed, report.diagnostic())

    def test_loaded_categories_are_independent_and_cover_all_classifications(self):
        categories = ratchet_categories()
        self.assertEqual(
            {category.classification for category in categories},
            set(CLASSIFICATIONS),
        )
        for index, category in enumerate(categories):
            for other in categories[index + 1 :]:
                self.assertTrue(set(category.ceiling).isdisjoint(other.ceiling))

    def test_manifest_path_is_repository_owned(self):
        manifest = REPO_ROOT / "tests" / "assert_called_once_live.py"
        self.assertTrue(manifest.is_file())
        self.assertTrue(manifest.resolve().is_relative_to(Path(__file__).resolve().parent.parent))
