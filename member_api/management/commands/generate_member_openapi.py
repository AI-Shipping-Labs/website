"""Generate ``_docs/member-openapi.json`` from member API URL patterns."""

import difflib
import json
import sys
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from api.openapi.builder import build_spec

DEFAULT_OUTPUT_PATH = Path("_docs") / "member-openapi.json"
MEMBER_API_USAGE_GUIDE_URL = (
    "https://github.com/AI-Shipping-Labs/website/blob/main/"
    "docs/member-api/plans.md"
)

MEMBER_API_DESCRIPTION = (
    "Member API for AI Shipping Labs. All endpoints accept JSON in and "
    "return JSON out. Authentication is via the ``Authorization: Token "
    "<key>`` header where ``<key>`` is a member-owned API key starting "
    "with ``asl_member_``. Member keys are scoped to the owner's data; "
    "plan endpoints only return or update plans owned by that key owner."
)

MEMBER_TOKEN_DESCRIPTION = (
    "Send the header ``Authorization: Token <asl_member_...>``. Keys are "
    "member-owned, scoped to the owner's data, and cannot authenticate "
    "against the staff/operator ``/api/`` surface."
)


class Command(BaseCommand):
    help = "Generate the OpenAPI 3.1 document for the member API."

    def add_arguments(self, parser):
        parser.add_argument(
            "--check",
            action="store_true",
            help="Exit 1 if _docs/member-openapi.json has drifted.",
        )

    def handle(self, *args, **options):
        from member_api.urls import urlpatterns

        document = build_spec(
            urlpatterns,
            title="AI Shipping Labs Member API",
            version="1.0.0",
            path_prefix="/member-api",
            docs_route_names={"member_api_openapi_json", "member_api_docs"},
            description=MEMBER_API_DESCRIPTION,
            token_description=MEMBER_TOKEN_DESCRIPTION,
        )
        document["externalDocs"] = {
            "description": "Member Plans API usage guide",
            "url": MEMBER_API_USAGE_GUIDE_URL,
        }
        generated_bytes = self._serialize(document)
        output_path = Path(settings.BASE_DIR) / DEFAULT_OUTPUT_PATH

        if options["check"]:
            self._check_against_committed(output_path, generated_bytes)
            return

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(generated_bytes)
        self.stdout.write(
            self.style.SUCCESS(
                f"Wrote member OpenAPI spec to {output_path} "
                f"({len(document.get('paths', {}))} paths)."
            ),
        )

    @staticmethod
    def _serialize(document):
        text = json.dumps(document, indent=2, sort_keys=True)
        if not text.endswith("\n"):
            text += "\n"
        return text.encode("utf-8")

    def _check_against_committed(self, path, generated_bytes):
        if not path.exists():
            self.stderr.write(
                f"Member OpenAPI spec missing at {path}. Run "
                "``uv run python manage.py generate_member_openapi`` and commit.",
            )
            sys.exit(1)

        committed_bytes = path.read_bytes()
        if committed_bytes == generated_bytes:
            self.stdout.write(
                self.style.SUCCESS("Member OpenAPI spec is up to date."),
            )
            return

        diff_lines = list(difflib.unified_diff(
            committed_bytes.decode("utf-8").splitlines(keepends=True),
            generated_bytes.decode("utf-8").splitlines(keepends=True),
            fromfile=str(path),
            tofile=f"{path} (regenerated)",
        ))
        self.stderr.write(
            f"Member OpenAPI spec drift detected at {path}. Run "
            "``uv run python manage.py generate_member_openapi`` and commit "
            "the result.\n\nDiff:\n"
            + "".join(diff_lines),
        )
        sys.exit(1)
