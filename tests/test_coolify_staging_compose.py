"""Regression coverage for the Coolify staging service identity.

The box that serves https://dev.aishippinglabs.com is the shared
``aisl-staging`` Coolify service, which answers for BOTH the production
hostnames (apex, www) and the dev hostname with a single container.
Pinning ``SITE_BASE_URL`` to the dev URL in this template (as a05609047
tried) can never be right for every request: applied to the live
service it would noindex production, and the Hetzner pin+deploy poller
only replaces the image tag, so the template value never even reached
the running container. The app therefore derives the crawler surfaces
per request host (website/search_indexing.py: a dev-host request always
gets the dev sitemap origin, minimal robots, and noindex, regardless of
the configured origin). This pins the shared-container invariant: the
template must not pin a static origin.
"""

from pathlib import Path

import yaml
from django.test import SimpleTestCase


class CoolifyStagingComposeTest(SimpleTestCase):
    def _staging_environment(self):
        compose_path = Path(__file__).resolve().parent.parent / "deploy" / "coolify" / "docker-compose.staging.yml"
        with open(compose_path) as handle:
            compose = yaml.safe_load(handle)
        return compose["services"]["web"]["environment"]

    def test_staging_service_does_not_pin_a_static_site_base_url(self):
        self.assertNotIn("SITE_BASE_URL", self._staging_environment())
