"""Regression coverage for the Coolify staging service identity.

The box that serves https://dev.aishippinglabs.com runs from
deploy/coolify/docker-compose.staging.yml. When that template omits
SITE_BASE_URL, the app falls back to the production literal in
website/settings.py and staging presents as production (canonical
sitemap/robots/OG URLs), which fails the scheduled dev Playwright
sitemap-origin and robots noindex smoke checks. This pins the dev
identity in the template against the same per-environment contract the
ECS path enforces in deploy/update_task_def.py.
"""

import importlib.util
from pathlib import Path

import yaml
from django.test import SimpleTestCase


def _load_update_task_def_module():
    module_path = Path(__file__).resolve().parent.parent / "deploy" / "update_task_def.py"
    spec = importlib.util.spec_from_file_location("deploy_update_task_def", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


update_task_def = _load_update_task_def_module()


class CoolifyStagingComposeTest(SimpleTestCase):
    def _staging_environment(self):
        compose_path = Path(__file__).resolve().parent.parent / "deploy" / "coolify" / "docker-compose.staging.yml"
        with open(compose_path) as handle:
            compose = yaml.safe_load(handle)
        return compose["services"]["web"]["environment"]

    def test_staging_service_uses_dev_site_base_url(self):
        self.assertEqual(
            self._staging_environment().get("SITE_BASE_URL"),
            update_task_def.SITE_BASE_URL_BY_ENV["dev"],
        )
