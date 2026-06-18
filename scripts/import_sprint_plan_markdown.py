#!/usr/bin/env python3
"""Import an AI Shipping Labs markdown sprint plan into the plans API."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from plans.resource_display import parse_resource_bullet

DEFAULT_PLAN_ROOTS = (
    Path("~/git/telegram-writing-assistant/articles/ai-shipping-labs/plans").expanduser(),
    Path("~/git/zoom-calls").expanduser(),
)

INTERNAL_NOTE_SECTIONS = (
    ("Persona", "persona"),
    ("Background", "background"),
    ("Initial Input", "intake"),
    ("Questions and Answers", "intake"),
    ("Meeting Notes", "meeting"),
    ("Internal Recommendations", "recommendation"),
    ("Internal Action Items", "action_item"),
    ("Sources", "source"),
)


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key, value.strip().strip('"').strip("'"))


class ApiClient:
    def __init__(self, *, base_url: str, token: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token

    def request(
        self,
        path: str,
        *,
        method: str = "GET",
        payload: dict[str, Any] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {
            "Authorization": f"Token {self.token}",
            "Accept": "application/json",
        }
        if payload is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            self.base_url + "/api" + path,
            data=data,
            method=method,
            headers=headers,
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read().decode("utf-8"))


def strip_frontmatter(text: str) -> str:
    if not text.startswith("---\n"):
        return text
    end = text.find("\n---\n", 4)
    if end == -1:
        return text
    return text[end + 5:]


def split_sections(text: str) -> dict[str, str]:
    sections: dict[str, str] = {}
    current: str | None = None
    buffer: list[str] = []
    for line in strip_frontmatter(text).splitlines():
        if line.startswith("## "):
            if current:
                sections[current] = "\n".join(buffer).strip()
            current = line[3:].strip()
            buffer = []
        elif current:
            buffer.append(line)
    if current:
        sections[current] = "\n".join(buffer).strip()
    return sections


def bullet_items(body: str) -> list[str]:
    items: list[str] = []
    current: str | None = None
    for line in body.splitlines():
        if line.startswith("- "):
            if current is not None:
                items.append(current.strip())
            current = line[2:].strip()
        elif current is not None and (line.startswith("  ") or line.strip()):
            current += "\n" + line.strip()
        elif current is not None:
            current += "\n"
    if current is not None:
        items.append(current.strip())
    return items


def labelled_bullet(body: str, label: str) -> str:
    pattern = re.compile(
        rf"^-\s*{re.escape(label)}:\s*(.*?)(?=\n-\s*[A-Z][^:\n]+:|\Z)",
        re.M | re.S,
    )
    match = pattern.search(body)
    return match.group(1).strip() if match else ""


def parse_focus(body: str) -> dict[str, Any]:
    main = ""
    supporting: list[str] = []
    for item in bullet_items(body):
        if item.lower().startswith("main focus:"):
            main = item.split(":", 1)[1].strip()
        elif item.lower().startswith("supporting focus:"):
            supporting.append(item.split(":", 1)[1].strip())
        else:
            supporting.append(item)
    return {"main": main, "supporting": supporting}


def parse_weeks(body: str, existing_weeks: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    weeks: list[dict[str, Any]] = []
    parts = re.split(r"(?m)^Week (\d+):\s*$", body)
    for index in range(1, len(parts), 2):
        week_number = int(parts[index])
        content = parts[index + 1]
        existing = existing_weeks.get(week_number, {})
        week: dict[str, Any] = {
            "week_number": week_number,
            "theme": existing.get("theme", ""),
            "position": week_number - 1,
            "checkpoints": [
                {"description": item, "position": item_index}
                for item_index, item in enumerate(bullet_items(content))
            ],
        }
        if existing.get("id"):
            week["id"] = existing["id"]
        weeks.append(week)
    return weeks


def parse_resources(body: str) -> list[dict[str, Any]]:
    resources = []
    for index, item in enumerate(bullet_items(body)):
        resources.append(parse_resource_bullet(item, position=index))
    return resources


def parse_internal_notes(sections: dict[str, str]) -> list[dict[str, str]]:
    notes = []
    for section, kind in INTERNAL_NOTE_SECTIONS:
        body = sections.get(section, "").strip()
        if body:
            notes.append({
                "visibility": "internal",
                "kind": kind,
                "body": body,
            })
    return notes


def build_payload(
    sections: dict[str, str],
    *,
    existing_plan: dict[str, Any] | None = None,
    goal: str | None = None,
) -> dict[str, Any]:
    required = [
        "Summary",
        "Focus",
        "Timeline",
        "Resources",
        "Deliverables",
        "Accountability",
        "Next Steps",
    ]
    missing = [section for section in required if section not in sections]
    if missing:
        raise ValueError(f"Missing required markdown sections: {', '.join(missing)}")

    existing_weeks = {
        week["week_number"]: week
        for week in (existing_plan or {}).get("weeks", [])
    }
    headline = goal
    if headline is None:
        headline = (existing_plan or {}).get("goal", "")
    if not headline:
        headline = labelled_bullet(sections["Summary"], "Goal for the next 6 weeks")
    return {
        "goal": headline[:280],
        "summary": {
            "current_situation": labelled_bullet(sections["Summary"], "Current situation"),
            "goal": labelled_bullet(sections["Summary"], "Goal for the next 6 weeks"),
            "main_gap": labelled_bullet(sections["Summary"], "Main gap to close"),
            "weekly_hours": labelled_bullet(sections["Summary"], "Weekly time commitment"),
            "why_this_plan": labelled_bullet(
                sections["Summary"],
                "Why this plan is the right next step",
            ),
        },
        "focus": parse_focus(sections["Focus"]),
        "weeks": parse_weeks(sections["Timeline"], existing_weeks),
        "resources": parse_resources(sections["Resources"]),
        "deliverables": [
            {"description": item, "position": index}
            for index, item in enumerate(bullet_items(sections["Deliverables"]))
        ],
        "accountability": sections["Accountability"].strip(),
        "next_steps": [
            {"description": item, "position": index}
            for index, item in enumerate(bullet_items(sections["Next Steps"]))
        ],
        "interview_notes": parse_internal_notes(sections),
    }


def slugify_name(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug


def find_source(name_or_email: str, roots: list[Path]) -> Path | None:
    candidates = [slugify_name(name_or_email.split("@", 1)[0])]
    if " " in name_or_email:
        candidates.insert(0, slugify_name(name_or_email))
    for root in roots:
        if not root.exists():
            continue
        for file_path in root.rglob("*.md"):
            lowered = file_path.name.lower()
            if any(candidate and candidate in lowered for candidate in candidates):
                return file_path
    return None


def find_plan_id(client: ApiClient, *, sprint: str, email: str) -> int | None:
    _, data = client.request(f"/sprints/{urllib.parse.quote(sprint)}/plans")
    for row in data.get("plans", []):
        if row.get("user_email", "").lower() == email.lower():
            return int(row["id"])
    return None


def create_empty_plan(client: ApiClient, *, sprint: str, email: str) -> dict[str, Any]:
    _, sprint_data = client.request(f"/sprints/{urllib.parse.quote(sprint)}")
    duration = int(sprint_data.get("duration_weeks") or 6)
    payload = {
        "user_email": email,
        "weeks": [
            {"week_number": number, "position": number - 1, "theme": ""}
            for number in range(1, duration + 1)
        ],
    }
    _, plan = client.request(
        f"/sprints/{urllib.parse.quote(sprint)}/plans",
        method="POST",
        payload=payload,
    )
    return plan


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create/update a sprint plan from a local markdown plan file.",
    )
    parser.add_argument("--sprint", required=True, help="Sprint slug, e.g. may-2026")
    parser.add_argument("--email", required=True, help="Plan owner email")
    parser.add_argument("--source", help="Markdown plan path")
    parser.add_argument(
        "--goal",
        help=(
            "Optional concise top-level plan headline. Without this, an "
            "existing API goal is preserved; otherwise the Summary goal is used."
        ),
    )
    parser.add_argument(
        "--name",
        help="Name used to discover a source file when --source is omitted.",
    )
    parser.add_argument(
        "--create-if-missing",
        action="store_true",
        help="Create an empty API plan before patching when none exists.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print payload only")
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

    source = Path(args.source).expanduser() if args.source else None
    if source is None:
        source = find_source(args.name or args.email, list(DEFAULT_PLAN_ROOTS))
    if source is None or not source.exists():
        print("Could not find markdown plan source", file=sys.stderr)
        return 2

    client = ApiClient(base_url=args.base_url, token=token)
    plan_id = find_plan_id(client, sprint=args.sprint, email=args.email)
    existing_plan = None
    if plan_id is None:
        if not args.create_if_missing:
            print("No API plan exists; re-run with --create-if-missing", file=sys.stderr)
            return 3
        if not args.dry_run:
            existing_plan = create_empty_plan(client, sprint=args.sprint, email=args.email)
            plan_id = int(existing_plan["id"])
    else:
        _, existing_plan = client.request(f"/plans/{plan_id}")

    sections = split_sections(source.read_text(encoding="utf-8"))
    payload = build_payload(sections, existing_plan=existing_plan, goal=args.goal)
    if args.dry_run:
        print(json.dumps({
            "source": str(source),
            "plan_id": plan_id,
            "payload": payload,
        }, indent=2, ensure_ascii=False))
        return 0

    assert plan_id is not None
    status, updated = client.request(f"/plans/{plan_id}", method="PATCH", payload=payload)
    print(json.dumps({
        "status": status,
        "id": updated["id"],
        "user_email": updated["user_email"],
        "sprint": updated["sprint"],
        "weeks": len(updated.get("weeks", [])),
        "checkpoints": sum(len(w.get("checkpoints", [])) for w in updated.get("weeks", [])),
        "resources": len(updated.get("resources", [])),
        "deliverables": len(updated.get("deliverables", [])),
        "next_steps": len(updated.get("next_steps", [])),
        "interview_notes": len(updated.get("interview_notes", [])),
    }, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except urllib.error.HTTPError as exc:
        print(f"HTTP {exc.code}: {exc.read().decode('utf-8')}", file=sys.stderr)
        raise SystemExit(1)
