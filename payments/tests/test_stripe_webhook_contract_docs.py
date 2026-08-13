"""Section-scoped documentation contract for required Stripe events."""

import json
import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase, tag

from payments.services.stripe_endpoint_verifier import REQUIRED_EVENTS

STALE_COUNT_RE = re.compile(
    r"\b(?:six|eight|nine)\b[^.\n]{0,80}"
    r"\b(?:required|documented)\b[^.\n]{0,40}\bevents\b",
    re.IGNORECASE,
)


def _markdown_section(path, heading):
    """Return one Markdown section, stopping at the next peer heading."""
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    try:
        start = lines.index(heading)
    except ValueError as exc:
        raise AssertionError(f"Missing documentation heading: {heading}") from exc

    level = len(heading) - len(heading.lstrip("#"))
    end = len(lines)
    for index in range(start + 1, len(lines)):
        match = re.match(r"^(#+)\s", lines[index])
        if match and len(match.group(1)) <= level:
            end = index
            break
    return "\n".join(lines[start:end])


def _documented_events(section):
    """Extract only canonical event literals, ignoring other backtick copy."""
    return [
        event
        for event in re.findall(r"`([^`]+)`", section)
        if event in REQUIRED_EVENTS
    ]


@tag("core")
class StripeWebhookDocumentationContractTest(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        docs = Path(settings.BASE_DIR) / "_docs"
        cls.sections = {
            "api": _markdown_section(
                docs / "api.md", "## Stripe webhook diagnostics API",
            ),
            "configuration": _markdown_section(
                docs / "configuration.md", "## 4. Stripe (payments)",
            ),
            "product": _markdown_section(
                docs / "product.md", "### Membership & Payments",
            ),
            "integration": _markdown_section(
                docs / "integrations" / "stripe.md",
                "## Cancellation webhook verification and replay runbook",
            ),
        }
        cls.openapi = json.loads(
            (docs / "openapi.json").read_text(encoding="utf-8"),
        )

    def test_relevant_sections_use_eleven_without_stale_counts(self):
        for name, section in self.sections.items():
            with self.subTest(document=name):
                self.assertRegex(section, r"\beleven\b")
                self.assertIsNone(STALE_COUNT_RE.search(section))

    def test_configuration_and_runbook_enumerate_canonical_order(self):
        self.assertEqual(
            _documented_events(self.sections["configuration"]),
            REQUIRED_EVENTS,
        )
        self.assertEqual(
            _documented_events(self.sections["integration"]),
            REQUIRED_EVENTS,
        )

    def test_committed_openapi_describes_canonical_status_field(self):
        operation = self.openapi["paths"][
            "/api/payments/stripe-webhooks/status"
        ]["get"]
        description = operation["description"]
        self.assertIn("`required_events`", description)
        self.assertRegex(description, r"\beleven\b")
        self.assertIsNone(STALE_COUNT_RE.search(description))
