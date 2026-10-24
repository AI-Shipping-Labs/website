"""Protect the DEV file-provider route's isolation and stable source backend."""

from pathlib import Path

import yaml
from django.test import SimpleTestCase, tag


@tag("core")
class DevRouteContractTest(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        root = Path(__file__).resolve().parents[1]
        cls.http = yaml.safe_load((root / "deploy/coolify/dev-route.yml").read_text())["http"]

    def test_routers_match_only_dev_and_preserve_tls_and_redirect(self):
        routers = self.http["routers"]
        self.assertEqual(set(routers), {"aisl-dev-https", "aisl-dev-http"})
        for router in routers.values():
            self.assertEqual(router["rule"], "Host(`dev.aishippinglabs.com`)")
            self.assertEqual(router["service"], "aisl-dev-source-web")
        self.assertEqual(routers["aisl-dev-https"]["entryPoints"], ["https"])
        self.assertEqual(routers["aisl-dev-https"]["tls"], {"certResolver": "letsencrypt"})
        self.assertEqual(routers["aisl-dev-http"]["entryPoints"], ["http"])
        self.assertEqual(routers["aisl-dev-http"]["middlewares"], ["aisl-dev-redirect-https"])
        self.assertEqual(
            self.http["middlewares"],
            {"aisl-dev-redirect-https": {"redirectScheme": {"scheme": "https", "permanent": True}}},
        )

    def test_backend_uses_stable_source_dns_and_dev_host_healthcheck(self):
        self.assertEqual(set(self.http["services"]), {"aisl-dev-source-web"})
        backend = self.http["services"]["aisl-dev-source-web"]["loadBalancer"]
        self.assertEqual(backend["servers"], [{"url": "http://web-mekt9kz74fvbixla6olinuma:8000"}])
        self.assertIs(backend["passHostHeader"], True)
        self.assertEqual(
            backend["healthCheck"],
            {"path": "/ping", "hostname": "dev.aishippinglabs.com", "interval": "10s", "timeout": "3s"},
        )
