"""Deploy one exact-head AISL image through the verified Coolify topology."""

import argparse
import json
import os
import sys
from pathlib import Path

from .api import JsonClient, get_text
from .contracts import REQUIRED_ROLES, ReleaseConfig
from .errors import ReleaseError
from .orchestrator import ReleaseDeployer


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--digest", required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--timeout-seconds", type=int, default=1500)
    parser.add_argument("--poll-seconds", type=int, default=10)
    args = parser.parse_args(argv)
    if args.timeout_seconds <= 0 or args.poll_seconds <= 0:
        parser.error("timeouts and poll intervals must be positive")
    return args


def config_from_environment(args):
    return ReleaseConfig(
        coolify_url=os.environ.get("COOLIFY_URL", ""),
        coolify_token=os.environ.get("COOLIFY_TOKEN", ""),
        service_uuid=os.environ.get("COOLIFY_AISL_SERVICE_UUID", ""),
        web_app_uuid=os.environ.get("COOLIFY_AISL_WEB_APPLICATION_UUID", ""),
        verification_token=os.environ.get("AISL_DEPLOY_VERIFICATION_API_TOKEN", ""),
        image=args.image,
        digest=args.digest,
        sha=args.sha,
    )


def allowlisted_source_receipt(receipt):
    proofs = {}
    for item in receipt["roles"]:
        proofs[item["role"]] = item
    roles = []
    for role in sorted(REQUIRED_ROLES):
        proof = proofs[role]
        roles.append(
            {
                "role": proof["role"],
                "tag": proof["tag"],
                "recorded_at": proof["recorded_at"],
            }
        )
    return {"cutoff": receipt["cutoff"], "roles": roles}


def source_receipt_lines(receipt):
    safe = allowlisted_source_receipt(receipt)
    lines = [f"- Source boot cutoff: `{safe['cutoff']}`\n"]
    for proof in safe["roles"]:
        lines.append(
            "- Source boot proof: "
            f"role=`{proof['role']}` tag=`{proof['tag']}` "
            f"recorded_at=`{proof['recorded_at']}`\n"
        )
    return lines


def write_receipt_log(receipt):
    safe = allowlisted_source_receipt(receipt)
    encoded = json.dumps(safe, separators=(",", ":"), sort_keys=True)
    print(f"COOLIFY_SOURCE_BOOT_RECEIPT={encoded}")


def write_summary(config, result):
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    lines = [
        "### Verified Coolify deployment\n",
        f"- Image: `{config.image}`\n",
        f"- Digest: `{config.digest}`\n",
        f"- Full revision: `{config.sha}`\n",
        f"- Application deployment: `{result.deployment_uuid}` (`finished`)\n",
        "- Source Service: web and worker exact revision, `running:healthy`\n",
        "- Production and DEV `/ping`: three consecutive exact-revision responses each\n",
    ]
    lines.extend(source_receipt_lines(result.source_receipt))
    with Path(summary_path).open("a", encoding="utf-8") as stream:
        stream.writelines(lines)


def run(args):
    config = config_from_environment(args)
    config.validate()
    coolify = JsonClient(config.coolify_url, config.coolify_token)
    diagnostics = JsonClient(
        config.diagnostics_url,
        config.verification_token,
        auth_scheme="Token",
    )
    deployer = ReleaseDeployer(config, coolify, diagnostics, get_text)
    result = deployer.deploy(args.timeout_seconds, args.poll_seconds)
    write_summary(config, result)
    write_receipt_log(result.source_receipt)
    print(
        "Verified exact-head Coolify release "
        f"sha={config.sha} digest={config.digest} deployment={result.deployment_uuid}"
    )


def main(argv=None):
    try:
        run(parse_args(argv))
    except ReleaseError as error:
        print(f"Deploy to Dev failed closed: {error}", file=sys.stderr)
        return 1
    # This is the CLI security boundary: emit only the exception type so an
    # unexpected client/library error cannot expose credentials or API bodies.
    except Exception as error:
        print(
            f"Deploy to Dev failed closed: unexpected-{type(error).__name__}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
