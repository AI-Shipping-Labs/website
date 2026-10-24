"""Sync and publish email templates to Relay (AC 2).

Reads markdown template files in ``email_app/email_templates/*.md``,
extracts YAML frontmatter (subject, category, required_context, example_context),
maps the appropriate default sender (noreply, content, or welcome),
and upserts and publishes them to Relay transactional template catalog.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import frontmatter
import requests
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from integrations.config import get_config

logger = logging.getLogger(__name__)

WELCOME_TEMPLATES = {
    "welcome",
    "free_welcome",
    "basic_welcome",
    "premium_welcome",
    "cofounder_welcome",
    "maven_welcome",
    "welcome_back",
    "welcome_imported",
}

CONTENT_TEMPLATES = {
    "course_announcement",
    "workshop_announcement",
    "lead_magnet_delivery",
    "download_delivery",
}


def resolve_default_sender_id(template_key: str) -> str:
    """Map template key to approved Relay sender ID."""
    if template_key in WELCOME_TEMPLATES:
        return "welcome"
    if template_key in CONTENT_TEMPLATES:
        return "content"
    return "noreply"


def normalize_required_context(raw_requirements: Any) -> list[dict[str, str]]:
    """Normalize required context definitions into [{name, description}]."""
    normalized: list[dict[str, str]] = []
    if not raw_requirements:
        return normalized

    if isinstance(raw_requirements, dict):
        raw_requirements = [
            {"name": k, "description": v} for k, v in raw_requirements.items()
        ]

    for item in raw_requirements:
        if isinstance(item, str):
            normalized.append({"name": item.strip(), "description": ""})
        elif isinstance(item, dict) and "name" in item:
            normalized.append({
                "name": str(item["name"]).strip(),
                "description": str(item.get("description", "")).strip(),
            })

    return normalized


class Command(BaseCommand):
    help = "Sync and publish email_app/email_templates/*.md to Relay."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dir",
            default=str(settings.BASE_DIR / "email_app" / "email_templates"),
            help="Directory containing markdown email templates.",
        )
        parser.add_argument(
            "--no-publish",
            action="store_true",
            help="Upsert draft only; do not publish new version.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Validate local templates without making network calls.",
        )
        parser.add_argument(
            "--base-url",
            default="",
            help="Relay base URL (defaults to RELAY_BASE_URL config).",
        )
        parser.add_argument(
            "--api-key",
            default="",
            help="Relay API key (defaults to RELAY_API_KEY config).",
        )

    def handle(self, *args, **options):
        template_dir = Path(options["dir"])
        if not template_dir.is_dir():
            raise CommandError(f"Directory not found: {template_dir}")

        files = sorted(template_dir.glob("*.md"))
        if not files:
            raise CommandError(f"No .md template files found in {template_dir}")

        dry_run = options["dry_run"]
        publish = not options["no_publish"]

        base_url = (options["base_url"] or get_config("RELAY_BASE_URL", getattr(settings, "RELAY_BASE_URL", "")) or "").rstrip("/")
        api_key = options["api_key"] or get_config("RELAY_API_KEY", getattr(settings, "RELAY_API_KEY", ""))

        if not dry_run:
            if not base_url:
                raise CommandError("RELAY_BASE_URL must be configured or passed via --base-url")
            if not api_key:
                raise CommandError("RELAY_API_KEY must be configured or passed via --api-key")

        self.stdout.write(f"Processing {len(files)} template(s) from {template_dir}...")

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        synced_count = 0
        published_count = 0

        for path in files:
            key = path.stem
            post = frontmatter.load(str(path))
            subject = str(post.metadata.get("subject", "")).strip()
            if not subject:
                raise CommandError(f"Template {path.name} is missing 'subject' frontmatter.")

            category = str(post.metadata.get("category", "")).strip()
            name = str(post.metadata.get("name") or key)
            required_context = normalize_required_context(post.metadata.get("required_context"))
            example_context = post.metadata.get("example_context") or {}
            if not isinstance(example_context, dict):
                example_context = {}
            default_sender = resolve_default_sender_id(key)

            if dry_run:
                synced_count += 1
                continue

            payload = {
                "name": name,
                "subject": subject,
                "markdown_body": post.content,
                "category": category,
                "required_context": required_context,
                "example_context": example_context,
                "default_sender_id": default_sender,
                "is_transactional": True,
                "is_active": True,
            }

            # PUT /api/transactional/templates/{key}
            upsert_url = f"{base_url}/api/transactional/templates/{key}"
            res = requests.put(upsert_url, json=payload, headers=headers, timeout=15)
            if res.status_code not in (200, 201):
                raise CommandError(
                    f"Failed to upsert template {key} (HTTP {res.status_code}): {res.text[:200]}"
                )
            synced_count += 1

            if publish:
                publish_url = f"{base_url}/api/transactional/templates/{key}/publish"
                pub_res = requests.post(publish_url, json={}, headers=headers, timeout=15)
                # 201 created or 400 with identical version already published
                if pub_res.status_code in (200, 201):
                    published_count += 1
                elif pub_res.status_code == 400 and "already published" in pub_res.text.lower():
                    published_count += 1
                else:
                    self.stderr.write(
                        f"Warning: publish for {key} returned HTTP {pub_res.status_code}: {pub_res.text[:150]}"
                    )

        action = "Validated" if dry_run else "Synced"
        self.stdout.write(
            self.style.SUCCESS(
                f"{action} {synced_count} templates. Published: {published_count}."
            )
        )
