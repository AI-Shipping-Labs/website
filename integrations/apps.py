from django.apps import AppConfig


class IntegrationsConfig(AppConfig):
    name = "integrations"

    def ready(self):
        # Declare the Studio-configurable keys in the package registry
        # (plan issue A0.2 step 2). Idempotent: community_base.config.ready()
        # imports this module too once the config app is installed.
        from integrations import settings_keys  # noqa: F401

        # Initialize Pydantic Logfire observability exactly once at startup,
        # behind the prod-only gate (issue #813). init_logfire() returns
        # immediately when the gate is closed (tests, evals, live judge, or
        # any run without an explicit opt-in), so no logfire import side
        # effects, network, or configure() call happen in those contexts.
        from integrations.services.observability import init_logfire

        init_logfire()
