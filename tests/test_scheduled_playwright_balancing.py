import copy
import hashlib
import json
from pathlib import Path

import yaml
from django.test import SimpleTestCase, tag

from scripts.extract_playwright_shard_weights import parse_job_log
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
        "weights_digest": "0bc8d9cc341d76e0d0ba2358609b4a3a66bf3008eb74c5dc3d2c4add170e81f5",
    },
    31690316576: {
        "head_sha": "0047ede3d6616b0fb35ce7c46474ddf6e099f000",
        "url": "https://github.com/AI-Shipping-Labs/website/actions/runs/31690316576",
        "job_ids": [94415906999, 94415906951, 94415906945, 94415907003],
        "weights_digest": "2eab1e31cfe77a07da8c30ac5db17cb301edd24be658d70710ee5978da3a4db5",
    },
    31718675813: {
        "head_sha": "56f0a99a7708b8b0ab886bd1e7e34a3f66f7d104",
        "url": "https://github.com/AI-Shipping-Labs/website/actions/runs/31718675813",
        "job_ids": [94509841937, 94509842047, 94509841969, 94509841860],
        "weights_digest": "99b03610f82e658c43855a4c648d0102ab0feb747e309c9b53776a74a35f933d",
    },
}
EXPECTED_INVENTORY_DIGEST = "aee7dfd363f6317ad03d066262591cdd6d008cf4ebaebeb3c356baf2ac0b5c39"


def _manifest():
    return json.loads(WEIGHTS_PATH.read_text())


def _current_inventory():
    return sorted(path.relative_to(REPO_ROOT).as_posix() for path in (REPO_ROOT / "playwright_tests").glob("test_*.py"))


def _workflow():
    return yaml.safe_load(WORKFLOW_PATH.read_text())


@tag("core")
class ScheduledPlaywrightMeasuredBalanceTest(SimpleTestCase):
    def test_pinned_green_sources_and_raw_weight_digests_are_exact(self):
        manifest = _manifest()
        self.assertEqual(manifest["schema_version"], 1)
        self.assertEqual(manifest["inventory_sha256"], EXPECTED_INVENTORY_DIGEST)
        self.assertEqual(len(manifest["file_weights_ms"]), 386)

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
                filename: samples[source_index]
                for filename, samples in manifest["file_weights_ms"].items()
            }
            self.assertEqual(
                canonical_weights_digest(per_run_weights), expected["weights_digest"]
            )

    def test_weight_manifest_has_complete_reproducible_samples(self):
        manifest = _manifest()
        measured = measured_weights_from_manifest(manifest)
        self.assertEqual(set(measured), set(manifest["file_weights_ms"]))
        self.assertTrue(set(measured).issubset(_current_inventory()))
        self.assertTrue(all(len(samples) == 3 for samples in manifest["file_weights_ms"].values()))

        missing_sample = copy.deepcopy(manifest)
        first_filename = next(iter(missing_sample["file_weights_ms"]))
        missing_sample["file_weights_ms"][first_filename].pop()
        with self.assertRaisesRegex(ValueError, "one weight per source run"):
            measured_weights_from_manifest(missing_sample)

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
        measured = measured_weights_from_manifest(_manifest())
        inventory = _current_inventory()
        first = build_shard_plan(inventory, measured, 4)
        second = build_shard_plan(list(reversed(inventory)), measured, 4)
        self.assertEqual(first, second)

        assigned = [filename for shard in first.files for filename in shard]
        self.assertCountEqual(assigned, inventory)
        self.assertEqual(len(assigned), len(set(assigned)))
        self.assertEqual(first.loads_ms, (893753, 893759, 893763, 893765))
        self.assertEqual([len(shard) for shard in first.files], [98, 96, 96, 96])

        baseline = round_robin_loads(inventory, measured, 4, first.unknown_weight_ms)
        self.assertEqual(baseline, (897507, 873003, 727554, 1076976))
        self.assertLess(max(first.loads_ms), max(baseline))
        self.assertGreaterEqual(max(baseline) - max(first.loads_ms), 180_000)

    def test_unknown_file_is_conservatively_and_deterministically_assigned_once(self):
        measured = measured_weights_from_manifest(_manifest())
        inventory = _current_inventory() + ["playwright_tests/test_future_unknown.py"]
        first = build_shard_plan(inventory, measured, 4)
        second = build_shard_plan(inventory, measured, 4)
        self.assertEqual(first, second)
        self.assertEqual(first.unknown_files, ("playwright_tests/test_future_unknown.py",))
        self.assertEqual(first.unknown_weight_ms, max(measured.values()))
        self.assertEqual(
            sum("playwright_tests/test_future_unknown.py" in shard for shard in first.files),
            1,
        )

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
