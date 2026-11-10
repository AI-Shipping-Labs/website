"""Django-importing workflows must use an explicit non-production key."""

from pathlib import Path

import yaml
from django.test import SimpleTestCase

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_JOBS = {
    "ci.yml": ("checks", "unit-tests", "postgres-verification", "playwright-tests"),
    "deploy-dev.yml": ("checks", "test", "postgres-verification", "playwright-core"),
    "scheduled-playwright.yml": (
        "playwright-full",
        "playwright-excluded-markers",
        "django-visual-regression",
    ),
    "scheduled-playwright-dev.yml": ("playwright-dev",),
}
TEST_SECRET = "ci-django-only-not-a-real-secret"
PRODUCTION_CHECK_JOBS = {("ci.yml", "checks"), ("deploy-dev.yml", "checks")}


class CiSecretKeyContractTests(SimpleTestCase):
    def test_django_jobs_use_explicit_test_only_settings(self):
        for workflow_name, job_names in WORKFLOW_JOBS.items():
            path = ROOT / ".github" / "workflows" / workflow_name
            workflow = yaml.safe_load(path.read_text())
            for job_name in job_names:
                with self.subTest(workflow=workflow_name, job=job_name):
                    environment = workflow["jobs"][job_name].get("env", {})
                    expected_debug = "1"
                    if (workflow_name, job_name) in PRODUCTION_CHECK_JOBS:
                        expected_debug = "0"
                    self.assertEqual(environment.get("DEBUG"), expected_debug)
                    self.assertEqual(environment.get("SECRET_KEY"), TEST_SECRET)
