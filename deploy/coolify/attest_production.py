"""Attest the already deployed dual DEV/production release without mutation."""

import os
import sys
from argparse import Namespace
from datetime import datetime, timezone
from pathlib import Path

from .api import JsonClient, get_text
from .contracts import IMAGE_REPOSITORY, SHA_PATTERN, verify_persisted_compose
from .deploy_exact_release import config_from_environment
from .errors import ReleaseError
from .orchestrator import ReleaseDeployer
from .production_identity import (
    recorded_digest,
    release_attempt,
    require_green_run,
    require_registry_digest,
)
from .public_readiness import DEV_PING_URL, public_endpoints_ready
from .verification import boot_evidence_ready, web_application_ready


def require_current_runtime(deployer):
    plan = deployer.preflight()
    config = deployer.config
    verify_persisted_compose(
        plan.source_service, plan.source_compose, config.image, plan.runtime_names,
    )
    deployer._require_source_runtime(plan)
    if not web_application_ready(plan.web_application, config.web_app_uuid, config.image):
        raise ReleaseError("production-web-is-not-current-dev-release")
    payload = deployer.diagnostics.call("GET", "/api/diagnostics/boot-timing")
    if not boot_evidence_ready(payload, config.sha, datetime(1970, 1, 1, tzinfo=timezone.utc)):
        raise ReleaseError("source-boot-revision-mismatch")


def attest_runtime(config, coolify):
    diagnostics = JsonClient(config.diagnostics_url, config.verification_token, auth_scheme="Token")
    deployer = ReleaseDeployer(config, coolify, diagnostics, get_text)
    for _sample in range(3):
        require_current_runtime(deployer)
        if not public_endpoints_ready(get_text, config.ping_url, config.sha):
            raise ReleaseError("public-revision-differs-from-current-dev")
    require_current_runtime(deployer)


def write_summary(config, run_id, attempt):
    text = (
        "### Confirmed production release\n"
        f"- Fully green Deploy Dev: `{run_id}`, attempt `{attempt}` (all 12 jobs)\n"
        f"- Revision: `{config.sha}`\n"
        f"- Image: `{config.image}`\n"
        f"- Digest matching green deployment receipt: `{config.digest}`\n"
        "- Healthy exact-release source web/worker and public web verified\n"
        "- DEV and production `/ping`: three exact-revision samples\n"
        "- Canonical Deploy Dev already deployed production; no duplicate restart\n"
    )
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with Path(path).open("a", encoding="utf-8") as stream:
            stream.write(text)
    print(text)


def run():
    sha = get_text(DEV_PING_URL)
    if not SHA_PATTERN.fullmatch(sha):
        raise ReleaseError("dev-did-not-return-full-sha")
    image = f"{IMAGE_REPOSITORY}:deploy-0-0-{sha}"
    config = config_from_environment(Namespace(image=image, digest="sha256:" + "0" * 64, sha=sha))
    config.validate()
    coolify = JsonClient(config.coolify_url, config.coolify_token)
    app = coolify.call("GET", f"/api/v1/applications/{config.web_app_uuid}")
    tag = app.get("docker_registry_image_tag")
    run_id, attempt = release_attempt(tag, sha, os.environ.get("REQUESTED_TAG", ""))
    github = JsonClient("https://api.github.com", os.environ.get("GH_TOKEN", ""))
    job_id = require_green_run(github, run_id, attempt, sha)
    digest = recorded_digest(job_id, sha)
    image = f"{IMAGE_REPOSITORY}:{tag}"
    config = config_from_environment(Namespace(image=image, digest=digest, sha=sha))
    config.validate()
    require_registry_digest(image, digest)
    attest_runtime(config, coolify)
    write_summary(config, run_id, attempt)


def main():
    try:
        run()
    except ReleaseError as error:
        print(f"Production confirmation failed closed: {error}", file=sys.stderr)
        return 1
    # CLI trust boundary: never emit external response bodies or credentials.
    except Exception as error:
        print(f"Production confirmation failed closed: unexpected-{type(error).__name__}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
