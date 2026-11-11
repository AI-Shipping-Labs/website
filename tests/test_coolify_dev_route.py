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

    def test_https_pages_are_authenticated_but_ping_is_public(self):
        routers = self.http["routers"]
        self.assertEqual(
            set(routers),
            {"aisl-dev-https", "aisl-dev-ping-https", "aisl-dev-http"},
        )
        self.assertEqual(routers["aisl-dev-https"]["rule"], "Host(`dev.aishippinglabs.com`)")
        self.assertEqual(routers["aisl-dev-https"]["service"], "aisl-dev-web")
        self.assertEqual(routers["aisl-dev-https"]["entryPoints"], ["https"])
        self.assertEqual(routers["aisl-dev-https"]["tls"], {"certResolver": "letsencrypt"})
        self.assertEqual(routers["aisl-dev-https"]["priority"], 10)
        self.assertEqual(
            routers["aisl-dev-https"]["middlewares"],
            ["aisl-dev-noindex", "aisl-dev-auth"],
        )

        ping = routers["aisl-dev-ping-https"]
        self.assertEqual(ping["rule"], "Host(`dev.aishippinglabs.com`) && Path(`/ping`)")
        self.assertEqual(ping["priority"], 100)
        self.assertEqual(ping["middlewares"], ["aisl-dev-noindex"])
        self.assertEqual(ping["service"], "aisl-dev-web")

    def test_responses_keep_noindex_including_auth_failures(self):
        middlewares = self.http["middlewares"]
        self.assertEqual(
            middlewares["aisl-dev-noindex"]["headers"]["customResponseHeaders"],
            {"X-Robots-Tag": "noindex, nofollow, noarchive"},
        )
        self.assertEqual(
            self.http["routers"]["aisl-dev-https"]["middlewares"],
            ["aisl-dev-noindex", "aisl-dev-auth"],
        )
        self.assertEqual(
            middlewares["aisl-dev-auth"]["basicAuth"]["users"],
            ["aisl-scheduled:__REPLACE_WITH_HASH_OF_AISL_DEV_SITE_PASSWORD__"],
        )

    def test_http_redirect_is_noindexed_and_keeps_tls_resolver(self):
        routers = self.http["routers"]
        self.assertEqual(routers["aisl-dev-https"]["tls"], {"certResolver": "letsencrypt"})
        self.assertEqual(routers["aisl-dev-http"]["entryPoints"], ["http"])
        self.assertEqual(
            routers["aisl-dev-http"]["middlewares"],
            ["aisl-dev-noindex", "aisl-dev-redirect-https"],
        )
        self.assertEqual(
            self.http["middlewares"]["aisl-dev-redirect-https"],
            {"redirectScheme": {"scheme": "https", "permanent": True}},
        )

    def test_backend_uses_stable_source_dns_and_dev_host_healthcheck(self):
        self.assertEqual(set(self.http["services"]), {"aisl-dev-web"})
        backend = self.http["services"]["aisl-dev-web"]["loadBalancer"]
        self.assertEqual(backend["servers"], [{"url": "http://dev-web-mekt9kz74fvbixla6olinuma:8000"}])
        self.assertIs(backend["passHostHeader"], True)
        self.assertEqual(
            backend["healthCheck"],
            {"path": "/ping", "hostname": "dev.aishippinglabs.com", "interval": "10s", "timeout": "3s"},
        )
