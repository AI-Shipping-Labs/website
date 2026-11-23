"""PR browser shards preserve the complete former serial pytest scope."""

from pathlib import Path

import yaml
from django.test import SimpleTestCase

from scripts.playwright_shard_plan import build_shard_plan, load_measured_weights

ROOT = Path(__file__).resolve().parents[1]


def _job():
    workflow = yaml.safe_load((ROOT / '.github/workflows/ci.yml').read_text())
    return workflow['jobs']['playwright-tests']


def _step(job, name):
    for step in job['steps']:
        if step['name'] == name:
            return step['run']
    raise AssertionError(f'Missing CI step: {name}')


class PrPlaywrightShardingTest(SimpleTestCase):
    def test_four_runners_preserve_dependencies_and_serial_execution(self):
        job = _job()
        self.assertEqual(job['needs'], ['checks', 'unit-tests'])
        self.assertFalse(job['strategy']['fail-fast'])
        self.assertEqual(job['timeout-minutes'], 120)
        shards = job['strategy']['matrix']['include']
        self.assertEqual([item['shard_index'] for item in shards], [0, 1, 2, 3])
        self.assertEqual([item['shard_total'] for item in shards], [4, 4, 4, 4])
        command = _step(job, 'Run Playwright shard and CLI tests')
        self.assertIn('"${files[@]}"', command)
        self.assertIn('-m "not manual_visual and not visual_regression"', command)
        self.assertNotIn('not slow_platform', command)
        self.assertNotIn(' -n ', command)
        self.assertNotIn('|| true', command)

    def test_every_browser_file_is_assigned_exactly_once(self):
        job = _job()
        select = _step(job, 'Select Playwright shard files')
        self.assertIn('scripts/playwright_shard_plan.py', select)
        self.assertIn('--weights .github/playwright-full-shard-weights.json', select)
        self.assertIn('--inventory playwright_tests', select)
        self.assertIn('--shards "${{ matrix.shard_total }}"', select)
        self.assertIn('--shard-index "${{ matrix.shard_index }}"', select)
        measured, _manifest = load_measured_weights(ROOT / '.github/playwright-full-shard-weights.json')
        inventory = []
        for path in (ROOT / 'playwright_tests').rglob('test_*.py'):
            inventory.append(path.relative_to(ROOT).as_posix())
        plan = build_shard_plan(inventory, measured, 4)
        assigned = []
        for shard in plan.files:
            assigned.extend(shard)
        self.assertCountEqual(assigned, inventory)
        self.assertEqual(len(assigned), len(set(assigned)))

    def test_cli_suite_is_added_only_to_first_shard(self):
        command = _step(_job(), 'Run Playwright shard and CLI tests')
        self.assertIn('mapfile -t files < .playwright-shard-files', command)
        self.assertRegex(command, r'if \[ "\$\{\{ matrix.shard_index \}\}" -eq 0 \]; then\s+files\+=\(asl_cli/\)\s+fi')
        self.assertEqual(command.count('asl_cli/'), 1)
        self.assertEqual(command.count('uv run pytest '), 1)
