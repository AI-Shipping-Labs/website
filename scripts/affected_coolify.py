"""Focused owners for the DEV routing repair and its offline deployment proof."""

RELEASE_TESTS = (
    "tests.test_coolify_release_api",
    "tests.test_coolify_release_concurrency",
    "tests.test_coolify_release_contracts",
    "tests.test_coolify_release_orchestrator",
    "tests.test_coolify_release_verification",
    "tests.test_coolify_dev_readiness",
)

FOCUSED_COOLIFY_CONTRACT_PATHS = (
    ("deploy/coolify/verify_dual_runtime.py", ("tests.test_coolify_dual_runtime",)),
    ("deploy/coolify/dual_runtime_snapshot.py", ("tests.test_coolify_dual_runtime",)),
    ("deploy/coolify/dev-route.yml", ("tests.test_coolify_dev_route",)),
    ("deploy/coolify/public_readiness.py", ("tests.test_coolify_dev_readiness",)),
    ("deploy/coolify/orchestrator.py", RELEASE_TESTS),
    ("deploy/coolify/deploy_exact_release.py", RELEASE_TESTS),
    ("scripts/affected_coolify.py", ("tests.test_affected_tests", "tests.test_affected_coolify")),
)

# Only these reviewed deployment/checker paths skip the local browser suite.
# Any additional app, template, workflow or other tooling path keeps its usual
# core/full selection. The public route is proved by live HTTP and Actions.
DEV_REPAIR_PATHS = {
    "deploy/coolify/dev-route.yml",
    "deploy/coolify/public_readiness.py",
    "deploy/coolify/orchestrator.py",
    "deploy/coolify/deploy_exact_release.py",
    "scripts/affected_tests.py",
    "scripts/affected_coolify.py",
    "tests/test_coolify_dev_readiness.py",
    "tests/test_coolify_dev_route.py",
    "tests/test_coolify_release_concurrency.py",
    "tests/test_affected_coolify.py",
}


def coolify_playwright_scope(escalations, relevant_paths):
    if escalations:
        return "full"
    if relevant_paths and set(relevant_paths).issubset(DEV_REPAIR_PATHS):
        return "none"
    return "core"
