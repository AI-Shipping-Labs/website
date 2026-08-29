from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path

from django.test import SimpleTestCase, tag

from tests.lexical_ast_ratchet import (
    FailureKind,
    RatchetCategory,
    ceiling_sha256,
    compare_ratchet,
)
from tests.status_200_assertion_ceilings import (
    DIRECT_STATUS_200_ASSERTION_CEILING,
    DIRECT_STATUS_200_ASSERTION_GOLDEN_SHA256,
    EXPECTED_CEILING_COUNTS,
    UNKNOWN_STATUS_200_ASSERTION_SHAPE_CEILING,
    UNKNOWN_STATUS_200_ASSERTION_SHAPE_GOLDEN_SHA256,
)
from tests.status_200_assertion_ratchet import (
    DIRECT_REWRITE_REASON,
    DIRECT_STATUS_200_ASSERTION,
    REPO_ROOT,
    RULE,
    UNKNOWN_REWRITE_REASON,
    UNKNOWN_STATUS_200_ASSERTION_SHAPE,
    candidates_from_source,
    compare_repository,
    discover_test_sources,
    load_live_manifest,
    occurrences_from_source,
    ratchet_categories,
    scan_repository,
)


def _classifications(source: str) -> list[str]:
    return [occurrence.classification for occurrence in occurrences_from_source("tests/test_sample.py", source)]


def _one_occurrence(classification: str):
    if classification == DIRECT_STATUS_200_ASSERTION:
        source = "def test_x():\n    assert response.status_code == 200\n"
    else:
        source = "def test_x():\n    self.assertTrue(response.status_code == 200)\n"
    return occurrences_from_source("tests/test_sample.py", source)[0]


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
    if classification == DIRECT_STATUS_200_ASSERTION:
        statement = "assert response.status_code == 200"
    else:
        statement = "self.assertTrue(response.status_code == 200)"
    return occurrences_from_source(
        "tests/test_sample.py",
        f"def test_x(self):\n    {statement}\n    {statement}\n",
    )


@tag("core")
class Status200ClassificationTests(SimpleTestCase):
    def test_plain_assert_accepts_exact_spellings_orders_and_comparison_operators(self):
        expected_spellings = (
            "200",
            "HTTPStatus.OK",
            "status.HTTP_200_OK",
            "rest_framework.status.HTTP_200_OK",
        )
        for spelling in expected_spellings:
            for expression in (
                f"response.status_code == {spelling}",
                f"{spelling} == response.status_code",
                f"response.status_code is {spelling}",
                f"{spelling} is response.status_code",
            ):
                with self.subTest(expression=expression):
                    self.assertEqual(
                        _classifications(f"def test_x():\n    assert {expression}\n"),
                        [DIRECT_STATUS_200_ASSERTION],
                    )

    def test_unittest_direct_grammar_is_literal_self_method_and_operand_order(self):
        for method in ("assertEqual", "assertIs"):
            for spelling in (
                "200",
                "HTTPStatus.OK",
                "status.HTTP_200_OK",
                "rest_framework.status.HTTP_200_OK",
            ):
                with self.subTest(method=method, spelling=spelling):
                    self.assertEqual(
                        _classifications(f"def test_x(self):\n    self.{method}(response.status_code, {spelling})\n"),
                        [DIRECT_STATUS_200_ASSERTION],
                    )

    def test_visible_unsupported_shapes_enter_the_unknown_bucket(self):
        cases = (
            "assert 100 < response.status_code == 200",
            "assert response.status_code == 200 and response.content",
            "assert check(response.status_code == 200)",
            "assert helper(response.status_code, 200)",
            "assert normalize(response.status_code) == 200",
            "assert response.status_code in [200]",
            "assert response.status_code == int(200)",
            "self.assertTrue(response.status_code == 200)",
            "self.assertTrue(helper(response.status_code, 200))",
            "self.assertEqual(normalize(response.status_code), 200)",
            "self.assertEqual(response.status_code, 200, 'message')",
            "self.assertEqual(response.status_code, 200, msg='message')",
            "self.assertEqual(actual=response.status_code, expected=200)",
            "self.assertEqual(200, response.status_code)",
            "self.assertEqual(response.status_code, {'ok': 200})",
            "checker.assertEqual(response.status_code, 200)",
            "checker.assertEqual(normalize(response.status_code), 200)",
            "self.assertNotEqual(response.status_code, 200)",
        )
        for statement in cases:
            with self.subTest(statement=statement):
                self.assertEqual(
                    _classifications(f"def test_x(self):\n    {statement}\n"),
                    [UNKNOWN_STATUS_200_ASSERTION_SHAPE],
                )

    def test_nested_visibility_is_independent_of_exact_expected_spelling(self):
        for spelling in (
            "200",
            "HTTPStatus.OK",
            "status.HTTP_200_OK",
            "rest_framework.status.HTTP_200_OK",
        ):
            direct = f"assert response.status_code == {spelling}"
            nested_cases = (
                f"assert helper(response.status_code, {spelling})",
                f"self.assertTrue(helper(response.status_code, {spelling}))",
                f"self.assertEqual(normalize(response.status_code), {spelling})",
                f"checker.assertEqual(normalize(response.status_code), {spelling})",
            )
            with self.subTest(spelling=spelling, statement=direct):
                self.assertEqual(
                    _classifications(f"def test_x(self):\n    {direct}\n"),
                    [DIRECT_STATUS_200_ASSERTION],
                )
            for statement in nested_cases:
                with self.subTest(spelling=spelling, statement=statement):
                    self.assertEqual(
                        _classifications(f"def test_x(self):\n    {statement}\n"),
                        [UNKNOWN_STATUS_200_ASSERTION_SHAPE],
                    )

    def test_diagnostic_messages_cannot_supply_either_visible_operand(self):
        cases = (
            "assert response.status_code, 200",
            "assert 200, response.status_code",
            "assert False, f'{response.status_code} {200}'",
            "self.assertTrue(response.status_code, 200)",
            "self.assertTrue(200, response.status_code)",
            "self.assertEqual(post.status_code, 302, post.content[:200])",
            "self.assertEqual(302, 200, msg=response.status_code)",
            "self.assertEqual(response.status_code, 302, msg=f'{200}')",
            "checker.assertEqual(response.status_code, 302, 200)",
            "self.assertNotAlmostEqual(response.status_code, 302, None, 200)",
        )
        for statement in cases:
            with self.subTest(statement=statement):
                self.assertEqual(_classifications(f"def test_x(self):\n    {statement}\n"), [])

    def test_real_operand_pair_survives_diagnostic_message_filtering(self):
        cases = (
            ("assert response.status_code == 200, diagnostic", DIRECT_STATUS_200_ASSERTION),
            (
                "assert helper(response.status_code, 200), diagnostic",
                UNKNOWN_STATUS_200_ASSERTION_SHAPE,
            ),
            (
                "self.assertTrue(helper(response.status_code, 200), diagnostic)",
                UNKNOWN_STATUS_200_ASSERTION_SHAPE,
            ),
            (
                "self.assertEqual(response.status_code, 200, diagnostic)",
                UNKNOWN_STATUS_200_ASSERTION_SHAPE,
            ),
            (
                "self.assertEqual(response.status_code, 200, msg=diagnostic)",
                UNKNOWN_STATUS_200_ASSERTION_SHAPE,
            ),
            (
                "checker.assertEqual(normalize(response.status_code), 200, diagnostic)",
                UNKNOWN_STATUS_200_ASSERTION_SHAPE,
            ),
        )
        for statement, classification in cases:
            with self.subTest(statement=statement):
                self.assertEqual(
                    _classifications(f"def test_x(self):\n    {statement}\n"),
                    [classification],
                )

    def test_boolean_alias_and_lexically_hidden_value_non_goals_are_not_inferred(self):
        cases = (
            "assert response.status_code == True",
            "assert response.status_code == OK",
            "assert response.status_code == codes.OK",
            "self.assertEqual(getattr(response, 'status_code'), 200)",
            "self.assertEqual(response.status_code, expected())",
        )
        for statement in cases:
            with self.subTest(statement=statement):
                self.assertEqual(_classifications(f"def test_x(self):\n    {statement}\n"), [])

    def test_receiver_provenance_and_neighboring_strength_never_exempt_an_anchor(self):
        source = """
def helper(response):
    assert response.status_code == 200
    assert response.json() == {"saved": True}

def no_known_response_type(arbitrary):
    assert arbitrary.status_code == HTTPStatus.OK
"""
        occurrences = occurrences_from_source("tests/test_sample.py", source)
        self.assertEqual(len(occurrences), 2)
        self.assertTrue(all(item.classification == DIRECT_STATUS_200_ASSERTION for item in occurrences))
        self.assertEqual({item.lexical_scope for item in occurrences}, {"helper", "no_known_response_type"})

    def test_multiple_identical_anchors_keep_separate_stable_ordinals(self):
        source = """
def helper(response):
    assert response.status_code == 200
    assert response.status_code == 200
"""
        occurrences = occurrences_from_source("tests/test_sample.py", source)
        self.assertEqual([item.duplicate_ordinal for item in occurrences], [1, 2])
        self.assertEqual(occurrences[0].fingerprint, occurrences[1].fingerprint)

    def test_formatting_comments_and_line_movement_do_not_change_identity(self):
        compact = occurrences_from_source(
            "tests/test_sample.py",
            "def test_x():\n    assert response.status_code == 200\n",
        )[0]
        formatted = occurrences_from_source(
            "tests/test_sample.py",
            "# comment\n\n\ndef test_x( ) :\n    assert (response.status_code == 200)  # tail\n",
        )[0]
        self.assertEqual(compact.occurrence_id, formatted.occurrence_id)

    def test_snippet_strings_comments_messages_and_diagnostics_are_not_occurrences(self):
        source = '''
"""assert response.status_code == 200"""
# self.assertEqual(response.status_code, 200)
MESSAGE = "checker.assertEqual(response.status_code, 200)"
def test_policy(self):
    self.assertEqual(MESSAGE, "assert response.status_code == 200")
'''
        self.assertEqual(occurrences_from_source("tests/test_policy.py", source), ())

    def test_candidate_anchor_is_the_complete_assertion_not_a_nested_fragment(self):
        candidates = candidates_from_source(
            "tests/test_sample.py",
            "def test_x(self):\n    self.assertTrue(response.status_code == 200)\n",
        )
        self.assertEqual(len(candidates), 1)
        self.assertIsInstance(candidates[0].anchor, ast.Expr)


@tag("core")
class Status200RatchetContractTests(SimpleTestCase):
    def test_each_category_rejects_new_stale_replacement_and_duplicate_ordinal(self):
        for classification in (
            DIRECT_STATUS_200_ASSERTION,
            UNKNOWN_STATUS_200_ASSERTION_SHAPE,
        ):
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
        for classification in (
            DIRECT_STATUS_200_ASSERTION,
            UNKNOWN_STATUS_200_ASSERTION_SHAPE,
        ):
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
                live_growth = replace(
                    category,
                    live={extra_id: "attempted growth"},
                )
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

    def test_each_category_rejects_overlap_with_the_other_bucket(self):
        for classification, other in (
            (DIRECT_STATUS_200_ASSERTION, UNKNOWN_STATUS_200_ASSERTION_SHAPE),
            (UNKNOWN_STATUS_200_ASSERTION_SHAPE, DIRECT_STATUS_200_ASSERTION),
        ):
            with self.subTest(classification=classification):
                occurrence = _one_occurrence(classification)
                first = _category_for(occurrence)
                second = _category_for(occurrence, classification=other)
                report = compare_ratchet((occurrence,), (first, second))
                self.assertIn(
                    FailureKind.OVERLAP,
                    {failure.kind for failure in report.failures},
                )


@tag("core")
class RepositoryStatus200RatchetTests(SimpleTestCase):
    def test_discovery_scans_policy_self_tests_and_all_repository_test_trees(self):
        discovered = discover_test_sources()
        self.assertIn("tests/test_status_200_assertion_ratchet.py", discovered)
        self.assertIn("asl_cli/tests/test_cli.py", discovered)
        self.assertTrue(any(path.startswith("playwright_tests/") for path in discovered))
        self.assertTrue(any(path.startswith("accounts/tests/") for path in discovered))

        self_test_occurrences = [
            item for item in scan_repository() if item.path == "tests/test_status_200_assertion_ratchet.py"
        ]
        self.assertEqual(self_test_occurrences, [])

    def test_live_manifest_has_exact_categories_and_non_empty_rewrite_reasons(self):
        live = load_live_manifest()
        self.assertEqual(set(live), {DIRECT_STATUS_200_ASSERTION, UNKNOWN_STATUS_200_ASSERTION_SHAPE})
        self.assertTrue(live[DIRECT_STATUS_200_ASSERTION])
        self.assertTrue(live[UNKNOWN_STATUS_200_ASSERTION_SHAPE])
        self.assertEqual(set(live[DIRECT_STATUS_200_ASSERTION].values()), {DIRECT_REWRITE_REASON})
        self.assertEqual(set(live[UNKNOWN_STATUS_200_ASSERTION_SHAPE].values()), {UNKNOWN_REWRITE_REASON})

    def test_immutable_ceilings_have_their_frozen_golden_digests(self):
        self.assertEqual(
            EXPECTED_CEILING_COUNTS,
            {
                DIRECT_STATUS_200_ASSERTION: 2377,
                UNKNOWN_STATUS_200_ASSERTION_SHAPE: 59,
            },
        )
        self.assertEqual(
            DIRECT_STATUS_200_ASSERTION_GOLDEN_SHA256,
            "2c7252542d669b5a77adbeea5c8dd2c3041abc83e7a75ea7f12bbde49511b54d",
        )
        self.assertEqual(
            UNKNOWN_STATUS_200_ASSERTION_SHAPE_GOLDEN_SHA256,
            "5458cbc6ee08f48df08fd51ad2dd7c54f7d5d5ee8b71a404fb60b66a51c5f50c",
        )
        self.assertEqual(
            {
                DIRECT_STATUS_200_ASSERTION: len(DIRECT_STATUS_200_ASSERTION_CEILING),
                UNKNOWN_STATUS_200_ASSERTION_SHAPE: len(UNKNOWN_STATUS_200_ASSERTION_SHAPE_CEILING),
            },
            EXPECTED_CEILING_COUNTS,
        )
        self.assertEqual(
            ceiling_sha256(DIRECT_STATUS_200_ASSERTION_CEILING),
            DIRECT_STATUS_200_ASSERTION_GOLDEN_SHA256,
        )
        self.assertEqual(
            ceiling_sha256(UNKNOWN_STATUS_200_ASSERTION_SHAPE_CEILING),
            UNKNOWN_STATUS_200_ASSERTION_SHAPE_GOLDEN_SHA256,
        )

    def test_current_repository_matches_the_shrink_only_manifests(self):
        report = compare_repository()
        self.assertTrue(
            report.passed,
            f"{report.diagnostic()}\n"
            f"Direct rewrite: {DIRECT_REWRITE_REASON}\n"
            f"Unknown rewrite: {UNKNOWN_REWRITE_REASON}",
        )

    def test_loaded_categories_are_independent_and_cover_both_classifications(self):
        categories = ratchet_categories()
        self.assertEqual(
            {category.classification for category in categories},
            {DIRECT_STATUS_200_ASSERTION, UNKNOWN_STATUS_200_ASSERTION_SHAPE},
        )
        self.assertTrue(set(categories[0].ceiling).isdisjoint(categories[1].ceiling))

    def test_manifest_path_is_repository_owned(self):
        manifest = REPO_ROOT / "tests" / "status_200_assertion_live.py"
        self.assertTrue(manifest.is_file())
        self.assertTrue(manifest.resolve().is_relative_to(Path(__file__).resolve().parent.parent))
