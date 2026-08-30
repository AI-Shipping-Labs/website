from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path

from django.test import SimpleTestCase, tag

from tests.lexical_ast_ratchet import FailureKind, RatchetCategory, ceiling_sha256, compare_ratchet
from tests.source_inspection_ceilings import (
    DIRECT_PY_OPEN_CEILING,
    DIRECT_PY_OPEN_GOLDEN_SHA256,
    DIRECT_PY_PATH_READ_CEILING,
    DIRECT_PY_PATH_READ_GOLDEN_SHA256,
    EXPECTED_CEILING_COUNTS,
    INSPECT_API_IMPORT_CEILING,
    INSPECT_API_IMPORT_GOLDEN_SHA256,
    INSPECT_API_REFERENCE_CEILING,
    INSPECT_API_REFERENCE_GOLDEN_SHA256,
    UNKNOWN_DYNAMIC_INSPECT_REFERENCE_CEILING,
    UNKNOWN_DYNAMIC_INSPECT_REFERENCE_GOLDEN_SHA256,
    UNKNOWN_VISIBLE_PY_PATH_READ_CEILING,
    UNKNOWN_VISIBLE_PY_PATH_READ_GOLDEN_SHA256,
)
from tests.source_inspection_ratchet import (
    CLASSIFICATIONS,
    DIRECT_OPEN_REWRITE_REASON,
    DIRECT_PATH_READ_REWRITE_REASON,
    DIRECT_PY_OPEN,
    DIRECT_PY_PATH_READ,
    DYNAMIC_INSPECT_REWRITE_REASON,
    INSPECT_API_IMPORT,
    INSPECT_API_REFERENCE,
    INSPECT_IMPORT_REWRITE_REASON,
    INSPECT_NAMES,
    INSPECT_REFERENCE_REWRITE_REASON,
    REPO_ROOT,
    REWRITE_REASONS,
    RULE,
    UNKNOWN_DYNAMIC_INSPECT_REFERENCE,
    UNKNOWN_PATH_REWRITE_REASON,
    UNKNOWN_VISIBLE_PY_PATH_READ,
    candidates_from_source,
    compare_repository,
    discover_test_sources,
    load_live_manifest,
    occurrences_from_source,
    ratchet_categories,
    repository_failure_diagnostic,
    scan_repository,
)


def _classifications(source: str) -> list[str]:
    return [item.classification for item in occurrences_from_source("tests/test_sample.py", source)]


def _source_for(classification: str) -> str:
    return {
        INSPECT_API_REFERENCE: "def test_x():\n    inspect.getsource(target)\n",
        INSPECT_API_IMPORT: "from inspect import getsource\n",
        UNKNOWN_DYNAMIC_INSPECT_REFERENCE: 'def test_x():\n    getattr(target, "getsource")\n',
        DIRECT_PY_OPEN: 'def test_x():\n    open("target.py")\n',
        DIRECT_PY_PATH_READ: 'def test_x():\n    Path("target.py").read_text()\n',
        UNKNOWN_VISIBLE_PY_PATH_READ: (
            'def test_x():\n    Path(prefix, "target.py").read_text()\n'
        ),
    }[classification]


def _one_occurrence(classification: str):
    return occurrences_from_source("tests/test_sample.py", _source_for(classification))[0]


def _category_for(occurrence, *, classification=None, reason="reviewed rewrite debt"):
    selected = classification or occurrence.classification
    return RatchetCategory(
        name=selected,
        rule=RULE,
        classification=selected,
        live={occurrence.occurrence_id: reason},
        ceiling=(occurrence.occurrence_id,),
        ceiling_golden_sha256=ceiling_sha256((occurrence.occurrence_id,)),
    )


def _duplicate_occurrences(classification: str):
    statement = {
        INSPECT_API_REFERENCE: "inspect.getsource(target)",
        INSPECT_API_IMPORT: "from inspect import getsource",
        UNKNOWN_DYNAMIC_INSPECT_REFERENCE: 'getattr(target, "getsource")',
        DIRECT_PY_OPEN: 'open("target.py")',
        DIRECT_PY_PATH_READ: 'Path("target.py").read_text()',
        UNKNOWN_VISIBLE_PY_PATH_READ: 'Path(prefix, "target.py").read_text()',
    }[classification]
    return occurrences_from_source("tests/test_sample.py", f"{statement}\n{statement}\n")


@tag("core")
class SourceInspectionClassificationTests(SimpleTestCase):
    def test_all_exact_inspect_attributes_are_one_reference_each_in_every_visible_use(self):
        use_templates = (
            "inspect.{name}(target)",
            "alias = inspect.{name}",
            "consume(inspect.{name})",
            "return inspect.{name}",
            "items = [inspect.{name}]",
        )
        for name in INSPECT_NAMES:
            for template in use_templates:
                with self.subTest(name=name, template=template):
                    statement = template.format(name=name)
                    source = f"def helper():\n    {statement}\n"
                    candidates = candidates_from_source("tests/test_sample.py", source)
                    self.assertEqual([item.classification for item in candidates], [INSPECT_API_REFERENCE])
                    self.assertIsInstance(candidates[0].anchor, ast.Attribute)

    def test_exact_inspect_import_emits_each_matching_alias_and_preserves_as_aliases(self):
        source = (
            "from inspect import getsource as source_of, signature, "
            "getsourcelines, findsource as find_source\n"
        )
        candidates = candidates_from_source("tests/test_sample.py", source)
        self.assertEqual([item.classification for item in candidates], [INSPECT_API_IMPORT] * 3)
        self.assertEqual(
            [(item.anchor.name, item.anchor.asname) for item in candidates],
            [("getsource", "source_of"), ("getsourcelines", None), ("findsource", "find_source")],
        )

    def test_only_absolute_exact_from_inspect_imports_are_anchors(self):
        source = """
import inspect
import inspect as inspection
from .inspect import getsource
from package.inspect import getsourcelines
from inspect import signature
"""
        self.assertEqual(occurrences_from_source("tests/test_sample.py", source), ())

    def test_literal_dynamic_inspect_references_cover_builtins_and_all_names(self):
        for name in INSPECT_NAMES:
            spellings = (f'"{name}"', repr(name[:3]) + " " + repr(name[3:]))
            for callable_name in ("getattr", "builtins.getattr"):
                for spelling in spellings:
                    with self.subTest(name=name, callable_name=callable_name, spelling=spelling):
                        source = f"def helper():\n    {callable_name}(target, {spelling}, fallback)\n"
                        self.assertEqual(_classifications(source), [UNKNOWN_DYNAMIC_INSPECT_REFERENCE])

    def test_inspect_attribute_assignment_target_is_not_a_loaded_reference(self):
        for name in INSPECT_NAMES:
            with self.subTest(name=name):
                source = f"def helper():\n    inspect.{name} = replacement\n"
                self.assertEqual(_classifications(source), [])

    def test_dynamic_near_misses_and_opaque_names_are_outside_v1(self):
        expressions = (
            "getattr(target, name)",
            'getattr(target, "get" + "source")',
            'custom.getattr(target, "getsource")',
            'getattr(target, "getsource_extra")',
            'getattr(target, "source")',
        )
        for expression in expressions:
            with self.subTest(expression=expression):
                self.assertEqual(_classifications(f"def helper():\n    {expression}\n"), [])

    def test_visible_attribute_inside_dynamic_receiver_is_an_independent_anchor(self):
        source = 'def helper():\n    getattr(inspect.getsource, "findsource")\n'
        self.assertEqual(
            _classifications(source),
            [UNKNOWN_DYNAMIC_INSPECT_REFERENCE, INSPECT_API_REFERENCE],
        )

    def test_module_helpers_nested_scopes_and_bound_aliases_are_scanned_at_anchor(self):
        source = """
inspect.getsource(module_target)
def helper():
    bound = inspect.getsourcelines
    def nested():
        return inspect.findsource
"""
        occurrences = occurrences_from_source("tests/test_sample.py", source)
        self.assertEqual(
            [(item.lexical_scope, item.classification) for item in occurrences],
            [
                ("<module>", INSPECT_API_REFERENCE),
                ("helper", INSPECT_API_REFERENCE),
                ("helper.<locals>.nested", INSPECT_API_REFERENCE),
            ],
        )

    def test_exact_open_forms_accept_modes_and_python_suffixes(self):
        expressions = (
            'open("source.py")',
            'open("source.pyw", "rb")',
            'builtins.open("source.py", mode="r", encoding="utf-8")',
            'open(Path("tests") / "source.py")',
            'builtins.open(pathlib.Path("tests") / pathlib.Path("source.pyw"))',
        )
        for expression in expressions:
            with self.subTest(expression=expression):
                self.assertEqual(_classifications(f"def helper():\n    {expression}\n"), [DIRECT_PY_OPEN])

    def test_exact_path_reads_accept_path_constructors_composition_and_both_methods(self):
        expressions = (
            'Path("source.py").read_text()',
            'pathlib.Path("source.pyw").read_bytes()',
            '(Path("tests") / "source.py").read_text(encoding="utf-8")',
            '(pathlib.Path("tests") / Path("nested") / "source.pyw").read_bytes()',
            '"source.py".read_text()',
        )
        for expression in expressions:
            with self.subTest(expression=expression):
                self.assertEqual(
                    _classifications(f"def helper():\n    {expression}\n"),
                    [DIRECT_PY_PATH_READ],
                )

    def test_unsupported_visible_python_path_shapes_fail_closed_in_unknown_category(self):
        expressions = (
            'open(os.path.join("tests", "source.py"))',
            'open(Path("tests").joinpath("source.py"))',
            'Path("source").with_suffix(".py").read_text()',
            'open(f"{prefix}/source.py")',
            'open(["source.py"][0])',
            'open({"path": "source.py"}["path"])',
            'open(prefix + "source.py")',
            'Path(prefix, "source.py").read_bytes()',
            'custom.Path("source.py").read_text()',
        )
        for expression in expressions:
            with self.subTest(expression=expression):
                self.assertEqual(
                    _classifications(f"def helper():\n    {expression}\n"),
                    [UNKNOWN_VISIBLE_PY_PATH_READ],
                )

    def test_non_python_and_wholly_opaque_paths_are_not_claimed(self):
        expressions = (
            'open("source.txt")',
            'Path("source.md").read_text()',
            "open(source_path)",
            "source_path.read_text()",
            "Path(source_path).read_bytes()",
            'open("source.PY")',
            'open(file="source.py")',
            'source.read()',
            'source.readlines()',
        )
        for expression in expressions:
            with self.subTest(expression=expression):
                self.assertEqual(_classifications(f"def helper():\n    {expression}\n"), [])

    def test_nested_exact_open_and_path_read_anchors_are_each_inventoried(self):
        source = 'def helper():\n    open("inner.py").read_text()\n'
        self.assertEqual(
            _classifications(source),
            [DIRECT_PY_OPEN, UNKNOWN_VISIBLE_PY_PATH_READ],
        )

    def test_exact_name_near_misses_comments_prose_and_snippet_strings_are_ignored(self):
        source = '''
"""inspect.getsource(target) and open("source.py")"""
# inspect.findsource(target)
SNIPPET = 'Path("source.py").read_text()'
def helper():
    inspect.getsourcefile(target)
    inspect.get_source(target)
    Path("source.py").read()
    Path("source.py").read_text_extra()
'''
        self.assertEqual(occurrences_from_source("tests/test_sample.py", source), ())

    def test_every_category_preserves_repeated_identical_anchor_ordinals(self):
        for classification in CLASSIFICATIONS:
            with self.subTest(classification=classification):
                occurrences = _duplicate_occurrences(classification)
                self.assertEqual([item.duplicate_ordinal for item in occurrences], [1, 2])
                self.assertEqual(occurrences[0].fingerprint, occurrences[1].fingerprint)

    def test_formatting_comments_and_line_movement_do_not_change_identity(self):
        cases = (
            (
                "def helper():\n    inspect.getsource(target)\n",
                "# lead\n\ndef helper( ) :\n    (inspect).getsource( target )  # tail\n",
            ),
            (
                'def helper():\n    open(Path("tests") / "source.py", "r")\n',
                '# lead\n\ndef helper( ) :\n    open( Path(\'tests\') / \'source.py\', \'r\' )\n',
            ),
        )
        for compact_source, formatted_source in cases:
            with self.subTest(compact_source=compact_source):
                compact = occurrences_from_source("tests/test_sample.py", compact_source)[0]
                formatted = occurrences_from_source("tests/test_sample.py", formatted_source)[0]
                self.assertEqual(compact.occurrence_id, formatted.occurrence_id)


@tag("core")
class SourceInspectionRatchetContractTests(SimpleTestCase):
    def test_each_category_rejects_new_stale_replacement_and_duplicate_ordinal(self):
        for classification in CLASSIFICATIONS:
            with self.subTest(classification=classification):
                original = _one_occurrence(classification)
                category = _category_for(original)

                new_report = compare_ratchet((original,), (replace(category, live={}),))
                self.assertEqual({item.kind for item in new_report.failures}, {FailureKind.NEW})

                stale_report = compare_ratchet((), (category,))
                self.assertEqual({item.kind for item in stale_report.failures}, {FailureKind.STALE})

                actual_new = replace(original, occurrence_id=f"{original.occurrence_id[:-1]}2")
                replacement = compare_ratchet((actual_new,), (category,))
                self.assertEqual(
                    {item.kind for item in replacement.failures},
                    {FailureKind.NEW, FailureKind.STALE, FailureKind.REPLACEMENT},
                )

                duplicates = _duplicate_occurrences(classification)
                duplicate = compare_ratchet(duplicates, (_category_for(duplicates[0]),))
                self.assertEqual({item.kind for item in duplicate.failures}, {FailureKind.NEW})
                self.assertTrue(duplicates[1].occurrence_id.endswith("::2"))

    def test_each_category_rejects_missing_reason_ceiling_growth_and_golden_drift(self):
        for classification in CLASSIFICATIONS:
            with self.subTest(classification=classification):
                occurrence = _one_occurrence(classification)
                category = _category_for(occurrence)

                missing = compare_ratchet(
                    (occurrence,),
                    (replace(category, live={occurrence.occurrence_id: " "}),),
                )
                self.assertEqual({item.kind for item in missing.failures}, {FailureKind.MISSING_REASON})

                extra_id = f"{occurrence.occurrence_id[:-1]}2"
                extra = replace(occurrence, occurrence_id=extra_id)
                growth = compare_ratchet((extra,), (replace(category, live={extra_id: "growth"}),))
                self.assertEqual({item.kind for item in growth.failures}, {FailureKind.CEILING_GROWTH})

                drift = compare_ratchet(
                    (occurrence,),
                    (replace(category, ceiling=(*category.ceiling, extra_id)),),
                )
                self.assertEqual({item.kind for item in drift.failures}, {FailureKind.GOLDEN_DRIFT})

    def test_each_category_rejects_overlap_with_every_other_bucket(self):
        for classification in CLASSIFICATIONS:
            occurrence = _one_occurrence(classification)
            for other in CLASSIFICATIONS:
                if other == classification:
                    continue
                with self.subTest(classification=classification, other=other):
                    report = compare_ratchet(
                        (occurrence,),
                        (_category_for(occurrence), _category_for(occurrence, classification=other)),
                    )
                    self.assertIn(FailureKind.OVERLAP, {item.kind for item in report.failures})

    def test_simulated_new_anchor_diagnostic_includes_only_its_actionable_rewrite(self):
        for classification in CLASSIFICATIONS:
            with self.subTest(classification=classification):
                occurrence = _one_occurrence(classification)
                category = _category_for(occurrence)
                report = compare_ratchet((occurrence,), (replace(category, live={}),))

                diagnostic = repository_failure_diagnostic(report)

                self.assertIn(occurrence.occurrence_id, diagnostic)
                self.assertIn(REWRITE_REASONS[classification], diagnostic)
                for other, guidance in REWRITE_REASONS.items():
                    if other != classification:
                        self.assertNotIn(guidance, diagnostic)


@tag("core")
class RepositorySourceInspectionRatchetTests(SimpleTestCase):
    def test_discovery_scans_executable_policy_files_and_all_test_trees(self):
        discovered = discover_test_sources()
        policy_paths = {
            "tests/test_source_inspection_ratchet.py",
            "tests/source_inspection_ratchet.py",
            "tests/source_inspection_live.py",
            "tests/source_inspection_ceilings.py",
        }
        self.assertTrue(policy_paths.issubset(discovered))
        self.assertIn("asl_cli/tests/test_cli.py", discovered)
        self.assertTrue(any(path.startswith("playwright_tests/") for path in discovered))
        self.assertTrue(any(path.startswith("accounts/tests/") for path in discovered))

        policy_occurrences = [item for item in scan_repository() if item.path in policy_paths]
        self.assertEqual(policy_occurrences, [])

    def test_live_manifest_has_exact_categories_and_actionable_non_empty_reasons(self):
        live = load_live_manifest()
        expected_reasons = {
            INSPECT_API_REFERENCE: INSPECT_REFERENCE_REWRITE_REASON,
            INSPECT_API_IMPORT: INSPECT_IMPORT_REWRITE_REASON,
            UNKNOWN_DYNAMIC_INSPECT_REFERENCE: DYNAMIC_INSPECT_REWRITE_REASON,
            DIRECT_PY_OPEN: DIRECT_OPEN_REWRITE_REASON,
            DIRECT_PY_PATH_READ: DIRECT_PATH_READ_REWRITE_REASON,
            UNKNOWN_VISIBLE_PY_PATH_READ: UNKNOWN_PATH_REWRITE_REASON,
        }
        self.assertEqual(set(live), set(CLASSIFICATIONS))
        for classification, reason in expected_reasons.items():
            with self.subTest(classification=classification):
                self.assertTrue(all(item.strip() for item in live[classification].values()))
                if live[classification]:
                    self.assertEqual(set(live[classification].values()), {reason})
        self.assertIn("behavioral test", " ".join(expected_reasons.values()))
        self.assertIn("opaque runtime paths are outside v1", UNKNOWN_PATH_REWRITE_REASON)

    def test_immutable_ceilings_have_frozen_counts_and_golden_digests(self):
        ceilings = {
            INSPECT_API_REFERENCE: (INSPECT_API_REFERENCE_CEILING, INSPECT_API_REFERENCE_GOLDEN_SHA256),
            INSPECT_API_IMPORT: (INSPECT_API_IMPORT_CEILING, INSPECT_API_IMPORT_GOLDEN_SHA256),
            UNKNOWN_DYNAMIC_INSPECT_REFERENCE: (
                UNKNOWN_DYNAMIC_INSPECT_REFERENCE_CEILING,
                UNKNOWN_DYNAMIC_INSPECT_REFERENCE_GOLDEN_SHA256,
            ),
            DIRECT_PY_OPEN: (DIRECT_PY_OPEN_CEILING, DIRECT_PY_OPEN_GOLDEN_SHA256),
            DIRECT_PY_PATH_READ: (DIRECT_PY_PATH_READ_CEILING, DIRECT_PY_PATH_READ_GOLDEN_SHA256),
            UNKNOWN_VISIBLE_PY_PATH_READ: (
                UNKNOWN_VISIBLE_PY_PATH_READ_CEILING,
                UNKNOWN_VISIBLE_PY_PATH_READ_GOLDEN_SHA256,
            ),
        }
        expected_counts = {
            INSPECT_API_REFERENCE: 9,
            INSPECT_API_IMPORT: 0,
            UNKNOWN_DYNAMIC_INSPECT_REFERENCE: 0,
            DIRECT_PY_OPEN: 0,
            DIRECT_PY_PATH_READ: 0,
            UNKNOWN_VISIBLE_PY_PATH_READ: 3,
        }
        expected_goldens = {
            INSPECT_API_REFERENCE: "1bb0c3f04f5d2aa45559b998479c31a671dbcfd87b19ba031afa0921da51b96d",
            INSPECT_API_IMPORT: "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945",
            UNKNOWN_DYNAMIC_INSPECT_REFERENCE: (
                "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"
            ),
            DIRECT_PY_OPEN: "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945",
            DIRECT_PY_PATH_READ: (
                "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"
            ),
            UNKNOWN_VISIBLE_PY_PATH_READ: (
                "eae2abd83d4097f252b6287ad9a3e925701d03987ff81e0bd3753e947eb9bdbe"
            ),
        }
        self.assertEqual(EXPECTED_CEILING_COUNTS, expected_counts)
        for classification, (ceiling, golden) in ceilings.items():
            with self.subTest(classification=classification):
                self.assertEqual(len(ceiling), EXPECTED_CEILING_COUNTS[classification])
                self.assertEqual(golden, expected_goldens[classification])
                self.assertEqual(ceiling_sha256(ceiling), golden)

    def test_current_repository_matches_the_shrink_only_manifests(self):
        report = compare_repository()
        self.assertTrue(report.passed, repository_failure_diagnostic(report))

    def test_loaded_categories_are_independent_and_cover_all_classifications(self):
        categories = ratchet_categories()
        self.assertEqual({item.classification for item in categories}, set(CLASSIFICATIONS))
        for index, category in enumerate(categories):
            for other in categories[index + 1 :]:
                self.assertTrue(set(category.ceiling).isdisjoint(other.ceiling))

    def test_manifest_path_is_repository_owned(self):
        manifest = REPO_ROOT / "tests" / "source_inspection_live.py"
        self.assertTrue(manifest.is_file())
        self.assertTrue(manifest.resolve().is_relative_to(Path(__file__).resolve().parent.parent))
