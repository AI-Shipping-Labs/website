"""Contracts for Deploy Dev validation and production serialization."""

from pathlib import Path

import yaml
from django.test import SimpleTestCase

WORKFLOW = (
    Path(__file__).resolve().parents[1]
    / '.github'
    / 'workflows'
    / 'deploy-dev.yml'
)


class DeployDevConcurrencyTest(SimpleTestCase):
    def setUp(self):
        self.workflow = yaml.safe_load(WORKFLOW.read_text())

    def test_validation_jobs_cancel_outdated_runs_independently(self):
        expected_groups = {
            'checks': 'deploy-dev-validation-${{ github.ref }}-checks',
            'test': 'deploy-dev-validation-${{ github.ref }}-test-${{ matrix.shard }}',
            'coverage': 'deploy-dev-validation-${{ github.ref }}-coverage',
            'postgres-verification': (
                'deploy-dev-validation-${{ github.ref }}-postgres-verification'
            ),
            'playwright-core': (
                'deploy-dev-validation-${{ github.ref }}-playwright-core-'
                '${{ matrix.shard_index }}'
            ),
        }
        self.assertNotIn('concurrency', self.workflow)
        for job_name, group in expected_groups.items():
            with self.subTest(job=job_name):
                concurrency = self.workflow['jobs'][job_name]['concurrency']
                self.assertEqual(concurrency['group'], group)
                self.assertTrue(concurrency['cancel-in-progress'])

    def test_deploy_is_non_cancelling_and_contains_the_deployment_steps(self):
        deploy = self.workflow['jobs']['deploy']
        concurrency = deploy['concurrency']
        deploy_step = next(
            step for step in deploy['steps']
            if step.get('name') == 'Deploy to Dev'
        )

        self.assertEqual(concurrency['group'], 'deploy-prod')
        self.assertFalse(concurrency['cancel-in-progress'])
        self.assertIn('deploy.coolify.deploy_exact_release', deploy_step['run'])
        self.assertNotIn('continue-on-error', deploy_step)
        self.assertNotIn('continue-on-error', deploy)
        self.assertEqual(
            deploy['needs'],
            ['checks', 'test', 'playwright-core', 'postgres-verification', 'coverage'],
        )
