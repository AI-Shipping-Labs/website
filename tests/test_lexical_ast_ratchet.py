from __future__ import annotations

import ast
import math
from dataclasses import replace
from pathlib import Path
from unittest import TestCase, mock

from tests.lexical_ast_ratchet import (
    MODULE_SCOPE,
    AstCandidate,
    AstOccurrence,
    FailureKind,
    FingerprintCollisionError,
    OccurrenceOrderError,
    RatchetCategory,
    SourceDiscoveryError,
    SourceParseError,
    build_occurrences,
    canonicalize_candidate,
    ceiling_sha256,
    compare_ratchet,
    discover_python_sources,
    lexical_scope_for,
    parse_source,
)


def _anchors(source: str, node_type: type[ast.AST] = ast.Assert) -> tuple[ast.AST, ...]:
    tree = ast.parse(source)
    return tuple(node for node in ast.walk(tree) if isinstance(node, node_type))


def _candidate(
    source: str = "def test_x():\n    assert value == 1\n",
    *,
    path: str = "tests/test_sample.py",
    scope: str = "test_x",
    rule: str = "example-rule",
    classification: str = "debt",
    metadata=None,
    anchor_index: int = 0,
) -> AstCandidate:
    anchor = _anchors(source)[anchor_index]
    return AstCandidate(
        path=path,
        lexical_scope=scope,
        rule=rule,
        classification=classification,
        anchor=anchor,
        metadata={} if metadata is None else metadata,
        line=anchor.lineno,
    )


def _category(
    occurrences: tuple[AstOccurrence, ...],
    *,
    name: str = "known-debt",
    rule: str = "example-rule",
    classification: str = "debt",
    live=None,
    ceiling=None,
    golden: str | None = None,
) -> RatchetCategory:
    ids = tuple(occurrence.occurrence_id for occurrence in occurrences)
    selected_live = {occurrence_id: "reviewed exact occurrence" for occurrence_id in ids}
    if live is not None:
        selected_live = live
    selected_ceiling = ids if ceiling is None else ceiling
    return RatchetCategory(
        name=name,
        rule=rule,
        classification=classification,
        live=selected_live,
        ceiling=selected_ceiling,
        ceiling_golden_sha256=ceiling_sha256(selected_ceiling) if golden is None else golden,
    )


class ParsingAndDiscoveryTests(TestCase):
    def test_parse_never_imports_or_executes_source(self):
        parsed = parse_source(
            "tests/test_never_execute.py",
            "raise RuntimeError('must not run')\nimport definitely_missing_package\n",
        )
        self.assertIsInstance(parsed.tree, ast.Module)

    def test_parse_error_is_a_separate_actionable_failure(self):
        with self.assertRaises(SourceParseError) as raised:
            parse_source("tests/test_broken.py", "def broken(:\n    pass\n")

        self.assertEqual(raised.exception.failure.kind, FailureKind.PARSE)
        self.assertEqual(raised.exception.path, "tests/test_broken.py")
        self.assertEqual(raised.exception.line, 1)
        self.assertIn("tests/test_broken.py:1:", str(raised.exception))

    def test_discovery_uses_explicit_roots_deduplicates_and_sorts(self):
        repo_root = Path(__file__).resolve().parent.parent
        discovered = discover_python_sources(
            repo_root,
            ("tests/test_lexical_ast_ratchet.py", "tests/lexical_ast_ratchet.py", "tests"),
        )

        self.assertEqual(discovered, tuple(sorted(set(discovered))))
        self.assertIn("tests/lexical_ast_ratchet.py", discovered)
        self.assertIn("tests/test_lexical_ast_ratchet.py", discovered)

    def test_discovery_rejects_missing_outside_and_non_python_roots(self):
        repo_root = Path(__file__).resolve().parent.parent
        cases = ((), ("does-not-exist",), (repo_root.parent,), ("README.md",))
        for source_root in cases:
            with self.subTest(source_root=source_root):
                with self.assertRaises(SourceDiscoveryError) as raised:
                    discover_python_sources(repo_root, source_root)
                self.assertEqual(raised.exception.failure.kind, FailureKind.DISCOVERY)

    def test_discovery_reports_a_changing_filesystem_separately(self):
        repo_root = Path(__file__).resolve().parent.parent
        with mock.patch(
            "tests.lexical_ast_ratchet._discover_once",
            side_effect=({"tests/a.py"}, {"tests/b.py"}),
        ):
            with self.assertRaises(SourceDiscoveryError) as raised:
                discover_python_sources(repo_root, ("tests",))

        self.assertEqual(raised.exception.failure.kind, FailureKind.DISCOVERY)
        self.assertIn("changed while scanning", str(raised.exception))


class LexicalScopeTests(TestCase):
    def test_scope_is_module_or_exact_nested_qualname(self):
        tree = ast.parse(
            """
module_value = call()
class Outer:
    class Inner:
        def method(self):
            def nested():
                return call()
            return call()
"""
        )
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
        scopes = [lexical_scope_for(tree, node) for node in calls]

        self.assertEqual(
            scopes,
            [MODULE_SCOPE, "Outer.Inner.method", "Outer.Inner.method.<locals>.nested"],
        )

        lambda_tree = ast.parse("def outer():\n    return lambda: call()\n")
        lambda_call = next(node for node in ast.walk(lambda_tree) if isinstance(node, ast.Call))
        self.assertEqual(lexical_scope_for(lambda_tree, lambda_call), "outer.<locals>.<lambda>")

    def test_anchor_must_belong_to_supplied_tree(self):
        with self.assertRaisesRegex(ValueError, "does not belong"):
            lexical_scope_for(ast.parse("value = 1"), ast.parse("value = 2").body[0])


class CanonicalOccurrenceTests(TestCase):
    def test_canonical_payload_and_full_sha256_are_golden(self):
        candidate = _candidate(metadata={"z": [2, {"b": False, "a": None}], "a": "é"})
        payload = canonicalize_candidate(candidate)
        occurrence = build_occurrences((candidate,))[0]

        self.assertEqual(
            payload,
            '{"anchor":"Assert(test=Compare(left=Name(id=\'value\', ctx=Load()), '
            'ops=[Eq()], comparators=[Constant(value=1)]))","classification":"debt",'
            '"metadata":{"a":"é","z":[2,{"a":null,"b":false}]},'
            '"rule":"example-rule","version":1}',
        )
        self.assertEqual(
            occurrence.fingerprint,
            "2b7cf799ae99eb743028370b182c35dd220380ff7d06b60010a8c73f38fc9f9e",
        )
        self.assertEqual(len(occurrence.fingerprint), 64)

    def test_formatting_comments_blank_lines_and_input_order_do_not_churn_ids(self):
        compact = _candidate("def test_x():\n    assert value == 1\n")
        moved = _candidate(
            "# module comment\n\n\ndef test_x( ) :\n    # local comment\n    assert ( value == 1 )  # tail\n"
        )

        forward = build_occurrences((compact, moved))
        reverse = build_occurrences((moved, compact))
        self.assertEqual(
            [occurrence.occurrence_id for occurrence in forward],
            [occurrence.occurrence_id for occurrence in reverse],
        )
        self.assertEqual([occurrence.duplicate_ordinal for occurrence in forward], [1, 2])

    def test_identical_anchors_in_one_scope_get_deterministic_ordinals(self):
        source = "def test_x():\n    assert value == 1\n    assert value == 1\n"
        candidates = (_candidate(source, anchor_index=0), _candidate(source, anchor_index=1))
        occurrences = build_occurrences(reversed(candidates))

        self.assertEqual(len(occurrences), 2)
        self.assertEqual(occurrences[0].fingerprint, occurrences[1].fingerprint)
        self.assertEqual([item.duplicate_ordinal for item in occurrences], [1, 2])
        self.assertTrue(occurrences[0].occurrence_id.endswith("::1"))
        self.assertTrue(occurrences[1].occurrence_id.endswith("::2"))

    def test_each_identity_field_or_payload_change_is_stale_old_plus_new_current(self):
        original = _candidate(metadata={"mode": "old"})
        original_occurrence = build_occurrences((original,))[0]
        changes = {
            "path": replace(original, path="other/test_sample.py"),
            "scope": replace(original, lexical_scope="Other.test_x"),
            "rule": replace(original, rule="other-rule"),
            "classification": replace(original, classification="complex"),
            "anchor": _candidate("def test_x():\n    assert value != 1\n", metadata={"mode": "old"}),
            "metadata": replace(original, metadata={"mode": "new"}),
        }
        for changed_field, changed in changes.items():
            with self.subTest(changed_field=changed_field):
                changed_occurrence = build_occurrences((changed,))[0]
                self.assertNotEqual(original_occurrence.occurrence_id, changed_occurrence.occurrence_id)
                self.assertEqual(
                    {original_occurrence.occurrence_id} - {changed_occurrence.occurrence_id},
                    {original_occurrence.occurrence_id},
                )
                self.assertEqual(
                    {changed_occurrence.occurrence_id} - {original_occurrence.occurrence_id},
                    {changed_occurrence.occurrence_id},
                )

        path_changed = build_occurrences((changes["path"],))[0]
        scope_changed = build_occurrences((changes["scope"],))[0]
        self.assertEqual(original_occurrence.fingerprint, path_changed.fingerprint)
        self.assertEqual(original_occurrence.fingerprint, scope_changed.fingerprint)

    def test_metadata_rejects_non_json_and_non_finite_values_without_repr(self):
        for value in (object(), ("tuple",), math.nan, math.inf):
            with self.subTest(value_type=type(value).__name__):
                with self.assertRaisesRegex(ValueError, "metadata"):
                    canonicalize_candidate(_candidate(metadata={"value": value}))

    def test_injected_sha256_collision_fails_closed(self):
        candidates = (
            _candidate("def test_x():\n    assert value == 1\n"),
            _candidate("def test_x():\n    assert value == 2\n"),
        )
        with mock.patch("tests.lexical_ast_ratchet._sha256_hex", return_value="0" * 64):
            with self.assertRaises(FingerprintCollisionError) as raised:
                build_occurrences(candidates)
        self.assertEqual(raised.exception.failure.kind, FailureKind.FINGERPRINT_COLLISION)

    def test_ambiguous_lexical_order_fails_separately(self):
        candidate = _candidate()
        with self.assertRaises(OccurrenceOrderError) as raised:
            build_occurrences((candidate, candidate))
        self.assertEqual(raised.exception.failure.kind, FailureKind.ORDER)


class ShrinkOnlyComparatorTests(TestCase):
    def test_exact_actual_live_and_ceiling_pass(self):
        occurrences = build_occurrences((_candidate(),))
        self.assertTrue(compare_ratchet(occurrences, (_category(occurrences),)).passed)

    def test_adding_duplicate_anchor_emits_ordinal_two_and_is_new(self):
        source = "def test_x():\n    assert value == 1\n    assert value == 1\n"
        first = build_occurrences((_candidate(source, anchor_index=0),))
        actual = build_occurrences(
            (_candidate(source, anchor_index=0), _candidate(source, anchor_index=1))
        )
        report = compare_ratchet(actual, (_category(first),))

        new_failures = report.failures_of_kind(FailureKind.NEW)
        self.assertEqual(len(new_failures), 1)
        self.assertTrue(new_failures[0].occurrence_id.endswith("::2"))

    def test_semantic_replacement_reports_new_stale_and_replacement(self):
        old = build_occurrences((_candidate(),))
        new = build_occurrences((_candidate("def test_x():\n    assert value != 1\n"),))
        report = compare_ratchet(new, (_category(old),))

        self.assertEqual(
            {failure.kind for failure in report.failures},
            {FailureKind.NEW, FailureKind.STALE, FailureKind.REPLACEMENT},
        )

    def test_retired_live_id_can_shrink_but_immutable_ceiling_cannot(self):
        original = build_occurrences((_candidate(),))
        original_ceiling = tuple(item.occurrence_id for item in original)
        golden = ceiling_sha256(original_ceiling)
        retired = _category(original, live={}, ceiling=original_ceiling, golden=golden)
        shrunk_ceiling = replace(retired, ceiling=())

        self.assertTrue(compare_ratchet((), (retired,)).passed)
        drift = compare_ratchet((), (shrunk_ceiling,))
        self.assertEqual(
            {failure.kind for failure in drift.failures},
            {FailureKind.GOLDEN_DRIFT},
        )

    def test_allowance_expansion_cannot_hide_by_editing_live_or_ceiling(self):
        original = build_occurrences((_candidate(),))
        added = build_occurrences((_candidate(metadata={"new": True}),))
        old_ids = tuple(item.occurrence_id for item in original)
        new_id = added[0].occurrence_id
        original_golden = ceiling_sha256(old_ids)

        live_growth = _category(
            added,
            live={new_id: "attempted allowance"},
            ceiling=old_ids,
            golden=original_golden,
        )
        ceiling_growth = replace(live_growth, ceiling=(*old_ids, new_id))

        self.assertIn(
            FailureKind.CEILING_GROWTH,
            {failure.kind for failure in compare_ratchet(added, (live_growth,)).failures},
        )
        self.assertIn(
            FailureKind.GOLDEN_DRIFT,
            {failure.kind for failure in compare_ratchet(added, (ceiling_growth,)).failures},
        )

    def test_duplicate_overlap_missing_reason_and_collision_are_distinct(self):
        occurrences = build_occurrences((_candidate(),))
        occurrence = occurrences[0]
        occurrence_id = occurrence.occurrence_id
        first = _category(
            occurrences,
            live={occurrence_id: " "},
            ceiling=(occurrence_id, occurrence_id),
        )
        second = _category(
            (),
            name="other",
            classification="complex",
            live={occurrence_id: "reviewed"},
            ceiling=(occurrence_id,),
        )
        collided = replace(
            occurrence,
            occurrence_id=f"tests/other.py::<module>::example-rule::{occurrence.fingerprint}::1",
            path="tests/other.py",
            lexical_scope="<module>",
            canonical_payload="different payload",
        )
        report = compare_ratchet((occurrence, occurrence, collided), (first, second))

        kinds = {failure.kind for failure in report.failures}
        self.assertTrue(
            {
                FailureKind.DUPLICATE_ID,
                FailureKind.OVERLAP,
                FailureKind.MISSING_REASON,
                FailureKind.FINGERPRINT_COLLISION,
            }.issubset(kinds)
        )

    def test_unconfigured_classification_is_new_not_silently_dropped(self):
        occurrence = build_occurrences((_candidate(classification="unknown"),))
        report = compare_ratchet(occurrence, ())
        self.assertEqual(
            {failure.kind for failure in report.failures},
            {FailureKind.NEW},
        )
