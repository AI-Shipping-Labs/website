import copy
import hashlib
import json
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path
from unittest.mock import patch

import yaml
from django.test import SimpleTestCase, tag

from scripts.extract_playwright_shard_weights import (
    canonical_inventory_digest,
    parse_job_log,
    prune_manifest_to_current_inventory,
    rebuild_manifest,
    validate_run,
)
from scripts.playwright_shard_plan import (
    build_shard_plan,
    canonical_weights_digest,
    measured_weights_from_manifest,
    round_robin_loads,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
WEIGHTS_PATH = REPO_ROOT / ".github" / "playwright-full-shard-weights.json"
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "scheduled-playwright.yml"

EXPECTED_SOURCES = {
    31679060660: {
        "head_sha": "89faea5780eebcd2ce5108fbdce0b706514731f3",
        "url": "https://github.com/AI-Shipping-Labs/website/actions/runs/31679060660",
        "job_ids": [94380223848, 94380224101, 94380223809, 94380223838],
        "weights_digest": "036a8aeb57af3e94736bfd0383b8742af0659e02cde3a170cf0b623019d4b301",
    },
    31690316576: {
        "head_sha": "0047ede3d6616b0fb35ce7c46474ddf6e099f000",
        "url": "https://github.com/AI-Shipping-Labs/website/actions/runs/31690316576",
        "job_ids": [94415906999, 94415906951, 94415906945, 94415907003],
        "weights_digest": "b4ee714b064aad5ce4964a07f365d6605aed3410e30e93eb0b63a6709c38906b",
    },
    31718675813: {
        "head_sha": "56f0a99a7708b8b0ab886bd1e7e34a3f66f7d104",
        "url": "https://github.com/AI-Shipping-Labs/website/actions/runs/31718675813",
        "job_ids": [94509841937, 94509842047, 94509841969, 94509841860],
        "weights_digest": "72f56ad6ad97738457b92d187da5af9b1eba37258eee17bfdd08c47db7483b29",
    },
}
EXPECTED_INVENTORY_DIGEST = "eb24a9351e8d6c07704ad5ccd23aefadd6c6a95063379e47d0d49cd9c5af9020"
SYNTHETIC_FILES = (
    "playwright_tests/test_measured_a.py",
    "playwright_tests/test_measured_b.py",
)


def _manifest():
    return json.loads(WEIGHTS_PATH.read_text())


def _current_inventory():
    return sorted(path.relative_to(REPO_ROOT).as_posix() for path in (REPO_ROOT / "playwright_tests").glob("test_*.py"))


def _workflow():
    return yaml.safe_load(WORKFLOW_PATH.read_text())


def _assert_exact_partition(test_case, plan, inventory):
    assigned = [filename for shard in plan.files for filename in shard]
    test_case.assertCountEqual(assigned, inventory)
    test_case.assertEqual(len(assigned), len(set(assigned)))


def _synthetic_job_log():
    return "\n".join(
        [
            "job\tstep\t2026-08-13T10:00:00.000Z collecting ... collected 2 items",
            "job\tstep\t2026-08-13T10:00:01.000Z playwright_tests/test_measured_a.py::test_a PASSED [ 50%]",
            "job\tstep\t2026-08-13T10:00:02.000Z playwright_tests/test_measured_b.py::test_b PASSED [100%]",
        ]
    )


def _synthetic_manifest():
    per_run_weights = {filename: 4_000 for filename in SYNTHETIC_FILES}
    weights_digest = canonical_weights_digest(per_run_weights)
    sources = []
    for run_id in (101, 102, 103):
        sources.append(
            {
                "expected_selected_nodes": 8,
                "file_weights_sha256": weights_digest,
                "head_sha": f"synthetic-sha-{run_id}",
                "job_ids": [run_id * 10 + index for index in range(4)],
                "observed_selected_nodes": 8,
                "run_id": run_id,
                "url": f"https://example.invalid/actions/runs/{run_id}",
            }
        )
    return {
        "aggregation": {"fixture": "deterministic synthetic evidence"},
        "file_weights_ms": {filename: [4_000, 4_000, 4_000] for filename in SYNTHETIC_FILES},
        "inventory_sha256": canonical_inventory_digest(list(SYNTHETIC_FILES)),
        "schema_version": 1,
        "source_runs": sources,
    }


def _rebuild_synthetic_manifest(seed):
    def fake_fetch_job_log(item):
        run_id, job_id = item
        return run_id, job_id, _synthetic_job_log()

    with (
        patch("scripts.extract_playwright_shard_weights.validate_run"),
        patch(
            "scripts.extract_playwright_shard_weights.inventory_at_sha",
            return_value=list(SYNTHETIC_FILES),
        ),
        patch(
            "scripts.extract_playwright_shard_weights.fetch_job_log",
            side_effect=fake_fetch_job_log,
        ),
    ):
        return rebuild_manifest(seed)


@tag("core")
class ScheduledPlaywrightMeasuredBalanceTest(SimpleTestCase):
    def test_pinned_green_sources_and_raw_weight_digests_are_exact(self):
        manifest = _manifest()
        self.assertEqual(manifest["schema_version"], 1)
        self.assertEqual(manifest["inventory_sha256"], EXPECTED_INVENTORY_DIGEST)
        self.assertEqual(len(manifest["file_weights_ms"]), 382)

        sources = manifest["source_runs"]
        self.assertEqual([source["run_id"] for source in sources], list(EXPECTED_SOURCES))
        for source_index, source in enumerate(sources):
            expected = EXPECTED_SOURCES[source["run_id"]]
            self.assertEqual(source["head_sha"], expected["head_sha"])
            self.assertEqual(source["url"], expected["url"])
            self.assertEqual(source["job_ids"], expected["job_ids"])
            self.assertEqual(source["expected_selected_nodes"], 2408)
            self.assertEqual(source["observed_selected_nodes"], 2408)
            self.assertEqual(source["file_weights_sha256"], expected["weights_digest"])
            per_run_weights = {
                filename: samples[source_index] for filename, samples in manifest["file_weights_ms"].items()
            }
            self.assertEqual(canonical_weights_digest(per_run_weights), expected["weights_digest"])

    def test_weight_manifest_has_complete_reproducible_samples(self):
        manifest = _manifest()
        measured = measured_weights_from_manifest(manifest)
        self.assertEqual(set(measured), set(manifest["file_weights_ms"]))
        self.assertTrue(all(len(samples) == 3 for samples in manifest["file_weights_ms"].values()))

    def test_regeneration_prunes_deleted_files_and_reseals_digests(self):
        manifest = _synthetic_manifest()
        pruned, removed = prune_manifest_to_current_inventory(
            manifest,
            ["playwright_tests/test_measured_a.py"],
        )

        self.assertEqual(removed, ["playwright_tests/test_measured_b.py"])
        self.assertEqual(
            pruned["file_weights_ms"],
            {"playwright_tests/test_measured_a.py": [4_000, 4_000, 4_000]},
        )
        self.assertEqual(
            pruned["inventory_sha256"],
            canonical_inventory_digest(["playwright_tests/test_measured_a.py"]),
        )
        expected_digest = canonical_weights_digest({
            "playwright_tests/test_measured_a.py": 4_000,
        })
        self.assertTrue(all(
            source["file_weights_sha256"] == expected_digest
            for source in pruned["source_runs"]
        ))

    def test_manifest_schema_and_samples_fail_closed(self):
        manifest = _manifest()
        first_filename = next(iter(manifest["file_weights_ms"]))
        cases = []

        unsupported_schema = copy.deepcopy(manifest)
        unsupported_schema["schema_version"] = 2
        cases.append((unsupported_schema, "unsupported Playwright shard-weight schema"))

        too_few_sources = copy.deepcopy(manifest)
        too_few_sources["source_runs"] = too_few_sources["source_runs"][:2]
        for samples in too_few_sources["file_weights_ms"].values():
            del samples[2:]
        cases.append((too_few_sources, "at least three source runs are required"))

        missing_sample = copy.deepcopy(manifest)
        missing_sample["file_weights_ms"][first_filename].pop()
        cases.append((missing_sample, "one weight per source run"))

        for invalid_sample in (-1, 1.5):
            invalid_weights = copy.deepcopy(manifest)
            invalid_weights["file_weights_ms"][first_filename][0] = invalid_sample
            cases.append(
                (
                    invalid_weights,
                    "weights must be non-negative integer milliseconds",
                )
            )

        for invalid_manifest, expected_error in cases:
            with self.subTest(expected_error=expected_error):
                with self.assertRaisesRegex(ValueError, expected_error):
                    measured_weights_from_manifest(invalid_manifest)

    def test_node_parser_normalizes_startup_and_sums_parametrized_nodes(self):
        log = "\n".join(
            [
                "job\tstep\t2026-08-13T10:00:00.000Z collecting ... collected 3 items",
                "job\tstep\t2026-08-13T10:00:30.000Z playwright_tests/test_a.py::test_x[one] PASSED [ 33%]",
                "job\tstep\t2026-08-13T10:00:32.000Z playwright_tests/test_a.py::test_x[two] PASSED [ 66%]",
                "job\tstep\t2026-08-13T10:00:35.000Z playwright_tests/test_b.py::test_y PASSED [100%]",
            ]
        )
        weights, node_count = parse_job_log(log)
        self.assertEqual(node_count, 3)
        self.assertEqual(weights, {"playwright_tests/test_a.py": 4000, "playwright_tests/test_b.py": 3000})

    def test_measured_plan_is_stable_complete_disjoint_and_better_balanced(self):
        manifest = _manifest()
        measured = measured_weights_from_manifest(manifest)
        inventory = sorted(manifest["file_weights_ms"])
        first = build_shard_plan(inventory, measured, 4)
        second = build_shard_plan(list(reversed(inventory)), measured, 4)
        self.assertEqual(first, second)
        self.assertEqual(len(inventory), 382)
        self.assertEqual(first.unknown_files, ())

        _assert_exact_partition(self, first, inventory)
        self.assertEqual(first.loads_ms, (890374, 890397, 890366, 890367))
        self.assertEqual([len(shard) for shard in first.files], [94, 93, 100, 95])

        baseline = round_robin_loads(inventory, measured, 4, first.unknown_weight_ms)
        self.assertEqual(baseline, (857247, 888927, 706247, 1109083))
        self.assertLess(max(first.loads_ms), max(baseline))
        self.assertGreaterEqual(max(baseline) - max(first.loads_ms), 180_000)

    def test_current_inventory_accepts_only_explicit_unknown_additions(self):
        measured = measured_weights_from_manifest(_manifest())
        inventory = _current_inventory()
        missing_measured = sorted(set(measured) - set(inventory))
        self.assertFalse(
            missing_measured,
            "measured Playwright paths missing from current inventory: " + ", ".join(missing_measured),
        )

        plan = build_shard_plan(inventory, measured, 4)
        additions = tuple(sorted(set(inventory) - set(measured)))
        self.assertEqual(plan.unknown_files, additions)
        _assert_exact_partition(self, plan, inventory)

    def test_unknown_file_is_conservatively_and_deterministically_assigned_once(self):
        measured = measured_weights_from_manifest(_manifest())
        inventory = sorted(measured) + ["playwright_tests/test_future_unknown.py"]
        first = build_shard_plan(inventory, measured, 4)
        second = build_shard_plan(list(reversed(inventory)), measured, 4)
        self.assertEqual(first, second)
        self.assertEqual(first.unknown_files, ("playwright_tests/test_future_unknown.py",))
        self.assertEqual(first.unknown_weight_ms, max(measured.values()))
        _assert_exact_partition(self, first, inventory)

    def test_multiple_unknown_files_are_input_order_independent(self):
        measured = measured_weights_from_manifest(_manifest())
        unknown = (
            "playwright_tests/test_future_alpha.py",
            "playwright_tests/test_future_zeta.py",
        )
        inventory = [*measured, *unknown]
        first = build_shard_plan(inventory, measured, 4)
        second = build_shard_plan(list(reversed(inventory)), measured, 4)

        self.assertEqual(first, second)
        self.assertEqual(first.unknown_files, unknown)
        self.assertEqual(first.unknown_weight_ms, max(measured.values()))
        _assert_exact_partition(self, first, inventory)

    def test_missing_measured_path_is_named_and_rejected(self):
        measured = measured_weights_from_manifest(_manifest())
        missing_path = sorted(measured)[0]
        inventory = [path for path in measured if path != missing_path]

        with self.assertRaisesRegex(ValueError, missing_path):
            build_shard_plan(inventory, measured, 4)

    def test_selector_includes_unknown_files_and_prints_actionable_diagnostics(self):
        tmp_root = REPO_ROOT / ".tmp"
        tmp_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=tmp_root) as temp_dir:
            root = Path(temp_dir)
            inventory_dir = root / "playwright_tests"
            inventory_dir.mkdir()
            inventory = [
                "playwright_tests/test_measured.py",
                "playwright_tests/test_unknown_alpha.py",
                "playwright_tests/test_unknown_zeta.py",
            ]
            for filename in inventory:
                (root / filename).write_text("")

            manifest = {
                "schema_version": 1,
                "source_runs": [{"run_id": run_id} for run_id in (1, 2, 3)],
                "file_weights_ms": {"playwright_tests/test_measured.py": [10, 20, 30]},
            }
            weights_path = root / "weights.json"
            weights_path.write_text(json.dumps(manifest))
            original_manifest = weights_path.read_bytes()
            selected = []

            for shard_index in range(4):
                output_path = root / f"shard-{shard_index}.txt"
                result = subprocess.run(
                    [
                        sys.executable,
                        str(REPO_ROOT / "scripts" / "playwright_shard_plan.py"),
                        "--weights",
                        str(weights_path),
                        "--inventory",
                        "playwright_tests",
                        "--shards",
                        "4",
                        "--shard-index",
                        str(shard_index),
                        "--output",
                        str(output_path),
                    ],
                    cwd=root,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("conservative fallback weight 20ms", result.stdout)
                self.assertIn("committed manifest remains unchanged", result.stdout)
                self.assertIn("playwright_tests/test_unknown_alpha.py", result.stdout)
                self.assertIn("playwright_tests/test_unknown_zeta.py", result.stdout)
                selected.extend(output_path.read_text().splitlines())

            self.assertEqual(Counter(selected), Counter({path: 1 for path in inventory}))
            self.assertEqual(weights_path.read_bytes(), original_manifest)

    def test_pinned_run_evidence_rejects_invalid_metadata(self):
        source = _synthetic_manifest()["source_runs"][0]
        valid_metadata = {
            "headSha": source["head_sha"],
            "conclusion": "success",
            "jobs": [{"databaseId": job_id, "conclusion": "success"} for job_id in source["job_ids"]],
        }
        cases = []

        wrong_sha = copy.deepcopy(valid_metadata)
        wrong_sha["headSha"] = "wrong-sha"
        cases.append((wrong_sha, "SHA does not match pinned evidence"))

        red_run = copy.deepcopy(valid_metadata)
        red_run["conclusion"] = "failure"
        cases.append((red_run, "is not green"))

        missing_job = copy.deepcopy(valid_metadata)
        missing_job["jobs"].pop()
        cases.append((missing_job, "is missing a pinned shard job"))

        red_job = copy.deepcopy(valid_metadata)
        red_job["jobs"][0]["conclusion"] = "failure"
        cases.append((red_job, "has a non-green pinned shard job"))

        for metadata, expected_error in cases:
            with self.subTest(expected_error=expected_error):
                with patch(
                    "scripts.extract_playwright_shard_weights.run_text",
                    return_value=json.dumps(metadata),
                ):
                    with self.assertRaisesRegex(ValueError, expected_error):
                        validate_run(source)

    def test_extractor_rebuilds_deterministic_fixture_byte_for_byte(self):
        seed = _synthetic_manifest()
        rebuilt = _rebuild_synthetic_manifest(copy.deepcopy(seed))

        expected_bytes = json.dumps(seed, indent=2, sort_keys=True) + "\n"
        rebuilt_bytes = json.dumps(rebuilt, indent=2, sort_keys=True) + "\n"
        self.assertEqual(rebuilt_bytes, expected_bytes)

    def test_extractor_rejects_source_inventory_disagreement(self):
        seed = _synthetic_manifest()
        inventories = [
            list(SYNTHETIC_FILES),
            list(SYNTHETIC_FILES),
            list(SYNTHETIC_FILES[:-1]),
        ]
        with (
            patch("scripts.extract_playwright_shard_weights.validate_run"),
            patch(
                "scripts.extract_playwright_shard_weights.inventory_at_sha",
                side_effect=inventories,
            ),
        ):
            with self.assertRaisesRegex(ValueError, "exact Playwright file inventory"):
                rebuild_manifest(seed)

    def test_extractor_rejects_selected_node_count_disagreement(self):
        seed = _synthetic_manifest()
        seed["source_runs"][1]["expected_selected_nodes"] = 9

        with self.assertRaisesRegex(ValueError, "yielded 8 nodes, expected 9"):
            _rebuild_synthetic_manifest(seed)

    def test_workflow_changes_only_full_suite_assignment_contract(self):
        workflow = _workflow()
        workflow_text = WORKFLOW_PATH.read_text()
        full = workflow["jobs"]["playwright-full"]
        self.assertEqual(full["timeout-minutes"], 30)
        self.assertFalse(full["strategy"]["fail-fast"])
        self.assertEqual(len(full["strategy"]["matrix"]["include"]), 4)
        self.assertEqual(workflow["concurrency"], {"group": "scheduled-playwright", "cancel-in-progress": True})
        self.assertIn("PLAYWRIGHT_DEFAULT_MARKERS: not manual_visual and not slow_platform", workflow_text)

        select_step = next(step for step in full["steps"] if step.get("name") == "Select Playwright shard files")
        self.assertIn("scripts/playwright_shard_plan.py", select_step["run"])
        self.assertIn(".github/playwright-full-shard-weights.json", select_step["run"])
        self.assertNotIn("awk -v shard", select_step["run"])
        run_step = next(step for step in full["steps"] if step.get("name") == "Run full Playwright shard")
        self.assertIn(
            'uv run pytest -m "${PLAYWRIGHT_DEFAULT_MARKERS}" "${files[@]}" -v --durations=25',
            run_step["run"],
        )

        self.assertEqual(workflow["jobs"]["changes"]["name"], "Change Gate")
        self.assertEqual(
            workflow["jobs"]["django-visual-regression"]["steps"][-1]["run"],
            "uv run python manage.py test --tag=visual_regression --parallel",
        )
        self.assertEqual(
            workflow["jobs"]["notify"]["needs"],
            ["changes", "playwright-full", "playwright-excluded-markers"],
        )
        self.assertNotIn(
            "scripts/playwright_shard_plan.py",
            (REPO_ROOT / ".github" / "workflows" / "deploy-dev.yml").read_text(),
        )
        self.assertNotIn(
            "scripts/playwright_shard_plan.py",
            (REPO_ROOT / ".github" / "workflows" / "scheduled-playwright-dev.yml").read_text(),
        )

    def test_manifest_inventory_digest_matches_pinned_paths(self):
        paths = sorted(_manifest()["file_weights_ms"])
        digest = hashlib.sha256("".join(f"{path}\n" for path in paths).encode()).hexdigest()
        self.assertEqual(digest, EXPECTED_INVENTORY_DIGEST)
