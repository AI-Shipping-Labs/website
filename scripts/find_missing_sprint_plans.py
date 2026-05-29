#!/usr/bin/env python3
"""Find sprint enrollments that do not have plans via the AI Shipping Labs API."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key, value.strip().strip('"').strip("'"))


def api_get(base_url: str, token: str, path: str) -> dict:
    url = base_url.rstrip("/") + "/api" + path
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Token {token}",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="List enrolled sprint members missing sprint plans.",
    )
    parser.add_argument("--sprint", required=True, help="Sprint slug, e.g. may-2026")
    parser.add_argument(
        "--query",
        default="",
        help="Optional user search query to print alongside missing plans.",
    )
    parser.add_argument(
        "--base-url",
        default="https://aishippinglabs.com",
        help="Production/site base URL. Defaults to https://aishippinglabs.com.",
    )
    parser.add_argument(
        "--env-file",
        default=".env",
        help="Env file containing API_SHIPPING_LABS_API_TOKEN.",
    )
    parser.add_argument(
        "--token-env",
        default="API_SHIPPING_LABS_API_TOKEN",
        help="Environment variable name for the API token.",
    )
    args = parser.parse_args()

    load_env(Path(args.env_file))
    token = os.environ.get(args.token_env)
    if not token:
        print(f"Missing {args.token_env}", file=sys.stderr)
        return 2

    enrollments = api_get(
        args.base_url,
        token,
        f"/sprints/{urllib.parse.quote(args.sprint)}/enrollments",
    ).get("enrollments", [])
    plans = api_get(
        args.base_url,
        token,
        f"/sprints/{urllib.parse.quote(args.sprint)}/plans",
    ).get("plans", [])
    plan_emails = {row["user_email"].lower() for row in plans}
    missing = [
        row for row in enrollments
        if row.get("user_email", "").lower() not in plan_emails
    ]

    output = {
        "sprint": args.sprint,
        "enrollments": len(enrollments),
        "plans": len(plans),
        "missing_count": len(missing),
        "missing": missing,
    }
    if args.query:
        users = api_get(
            args.base_url,
            token,
            "/users?q="
            + urllib.parse.quote(args.query)
            + "&limit=50",
        ).get("users", [])
        output["user_search"] = users

    print(json.dumps(output, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
