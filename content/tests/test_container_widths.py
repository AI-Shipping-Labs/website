"""Guard the container-width contract from ``_docs/design-system.md``.

Audit and remediation: ``_docs/width-audit.md`` (2026-07-21).

The site frames every user-facing page with one of four sanctioned widths.  Two
guards run here:

* ``test_page_containers_use_a_sanctioned_width`` discovers page templates
  automatically, so a brand-new page that invents ``max-w-4xl`` fails without
  anyone remembering to register it.
* ``test_audited_pages_keep_their_assigned_tier`` pins the specific pages the
  2026-07-21 audit moved, so a later edit cannot silently re-tier them back.

Do not add blanket ignores or widen ``SANCTIONED_WIDTHS`` to make a failure go
away.  A page that genuinely needs a new width needs a design-system change
first, then an update here.
"""

from __future__ import annotations

import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase, tag

# _docs/design-system.md -> Spacing and Layout.  Tier name kept for failure messages.
SANCTIONED_WIDTHS = {
    "max-w-7xl": "Frame (index/grid/marketing/dashboard; matches header+footer chrome)",
    "max-w-5xl": "Detail (mixed-layout detail pages)",
    "max-w-3xl": "Reader (long-form prose and multi-step forms)",
    "max-w-2xl": "Narrow (status interstitials and single-purpose forms)",
}

STANDARD_GUTTER = ("px-4", "sm:px-6", "lg:px-8")

# Staff/admin surfaces run their own layout and are exempt per
# _docs/design-system.md -> "Studio pages use their own admin layout."
NON_MEMBER_PREFIXES = (
    "templates/studio/",
    "templates/emails/",
    "templates/integrations/",
)

# Site chrome intentionally uses a px-6 gutter while page bodies use px-4.
# Reconciling the two is an open design-system question (_docs/width-audit.md ->
# Open PM questions); until it is decided these two nav bars stay as they are.
CHROME_GUTTER_EXEMPT = frozenset(
    {
        "templates/includes/header.html",
        "templates/events/host_management_denied.html",
    }
)

# Pages that delegate their outer frame to an include.  The include owns the
# container and is pinned directly in AUDITED_PAGE_WIDTHS below.
FRAME_DELEGATED_TO_INCLUDE = {
    "templates/accounts/login.html": "templates/accounts/includes/_auth_card.html",
    "templates/accounts/register.html": "templates/accounts/includes/_auth_card.html",
    "templates/accounts/password_reset_request.html": "templates/accounts/includes/_auth_card.html",
    "templates/content/workshops_catalog.html": "templates/content/_workshops_catalog.html",
}

# Pages the 2026-07-21 audit changed, plus the pair that prompted it
# (/sprints had drifted to max-w-5xl while every sibling index page was 7xl).
AUDITED_PAGE_WIDTHS = {
    # The reported complaint: index pages must match each other and the chrome.
    "templates/content/sprints_index.html": "max-w-7xl",
    "templates/events/events_list.html": "max-w-7xl",
    # Was max-w-4xl, the only 4xl on the site.
    "templates/events/host_management.html": "max-w-5xl",
    # Were max-w-lg, off the scale entirely.
    "templates/events/cancel_registration_confirm.html": "max-w-2xl",
    "templates/events/cancel_registration_result.html": "max-w-2xl",
    "templates/email_app/verify_result.html": "max-w-2xl",
    "templates/email_app/unsubscribe_result.html": "max-w-2xl",
    # Was max-w-3xl; a status interstitial belongs in the Narrow tier.
    "templates/content/curated_link_verify_required.html": "max-w-2xl",
    # Frame-owning includes for the delegating pages above.
    "templates/accounts/includes/_auth_card.html": "max-w-7xl",
    "templates/content/_workshops_catalog.html": "max-w-7xl",
}

EXTENDS_BASE_RE = re.compile(r"\{%\s*extends\s+[\"']base\.html[\"']\s*%\}")
# Page containers are div/section/main/article.  nav/header are chrome, and
# matching on tag name keeps an inline nav bar from being read as the frame.
CONTAINER_TAG_RE = re.compile(
    r"<(?:div|section|main|article)\b[^>]*\bclass=\"(?P<classes>[^\"]*)\"",
    re.IGNORECASE,
)
MAX_WIDTH_RE = re.compile(r"^max-w-")


def _repo_relative(path: Path, base_dir: Path) -> str:
    return path.relative_to(base_dir).as_posix()


def _page_container(source: str) -> tuple[str, str] | None:
    """Return ``(width_class, class_attribute)`` for a template's outer frame.

    The outer frame is the first ``mx-auto`` block-level element carrying a
    ``max-w-*`` class.  Returns ``None`` when the template delegates its frame.
    """
    for match in CONTAINER_TAG_RE.finditer(source):
        classes = match.group("classes").split()
        if "mx-auto" not in classes:
            continue
        widths = [token for token in classes if MAX_WIDTH_RE.match(token)]
        if widths:
            return widths[0], " ".join(classes)
    return None


def _discover_member_pages(base_dir: Path) -> dict[str, str]:
    """Map every user-facing page template to its source."""
    pages = {}
    for path in sorted((base_dir / "templates").rglob("*.html")):
        relative = _repo_relative(path, base_dir)
        if relative.startswith(NON_MEMBER_PREFIXES):
            continue
        source = path.read_text(encoding="utf-8")
        if EXTENDS_BASE_RE.search(source):
            pages[relative] = source
    return pages


@tag("core")
class ContainerWidthContractTest(SimpleTestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.base_dir = Path(settings.BASE_DIR)
        cls.pages = _discover_member_pages(cls.base_dir)

    def test_discovery_finds_the_known_page_templates(self):
        """Fail loudly if the discovery regex stops matching real pages."""
        self.assertGreater(len(self.pages), 50)
        for expected in ("templates/events/events_list.html", "templates/content/sprints_index.html"):
            self.assertIn(expected, self.pages)

    def test_page_containers_use_a_sanctioned_width(self):
        offenders = []
        for relative, source in self.pages.items():
            if relative in FRAME_DELEGATED_TO_INCLUDE:
                continue
            container = _page_container(source)
            if container is None:
                offenders.append(
                    f"{relative}: no `mx-auto max-w-*` page container found; either add one "
                    f"or register the frame-owning include in FRAME_DELEGATED_TO_INCLUDE"
                )
                continue
            width, classes = container
            if width not in SANCTIONED_WIDTHS:
                offenders.append(
                    f"{relative}: outer container uses {width!r} which is not a sanctioned tier "
                    f"(got: {classes!r}). Allowed: {sorted(SANCTIONED_WIDTHS)}"
                )

        self.assertEqual(
            offenders,
            [],
            "Unsanctioned page container widths (contract: _docs/design-system.md -> "
            "Spacing and Layout; rationale: _docs/width-audit.md):\n" + "\n".join(offenders),
        )

    def test_audited_pages_keep_their_assigned_tier(self):
        mismatches = []
        for relative, expected_width in sorted(AUDITED_PAGE_WIDTHS.items()):
            path = self.base_dir / relative
            if not path.exists():
                mismatches.append(f"{relative}: template is missing; update AUDITED_PAGE_WIDTHS")
                continue
            container = _page_container(path.read_text(encoding="utf-8"))
            if container is None:
                mismatches.append(f"{relative}: no page container found, expected {expected_width}")
                continue
            width, _ = container
            if width != expected_width:
                mismatches.append(f"{relative}: expected {expected_width}, found {width}")

        self.assertEqual(
            mismatches,
            [],
            "Audited pages drifted off their assigned width tier "
            "(see _docs/width-audit.md, audit date 2026-07-21):\n" + "\n".join(mismatches),
        )

    def test_page_containers_use_the_standard_horizontal_gutter(self):
        """Catch the `px-6 lg:px-8` drift: wider mobile gutters than sibling pages."""
        offenders = []
        for relative, source in self.pages.items():
            if relative in CHROME_GUTTER_EXEMPT or relative in FRAME_DELEGATED_TO_INCLUDE:
                continue
            container = _page_container(source)
            if container is None:
                continue
            _, classes = container
            tokens = classes.split()
            # A container with no horizontal padding inherits it from a parent
            # section; only flag containers that pad themselves incorrectly.
            if not any(token.endswith(("px-4", "px-6", "px-8")) for token in tokens):
                continue
            missing = [token for token in STANDARD_GUTTER if token not in tokens]
            if missing:
                offenders.append(f"{relative}: container {classes!r} is missing {missing}")

        self.assertEqual(
            offenders,
            [],
            "Page containers must use the standard `px-4 sm:px-6 lg:px-8` gutter "
            "(_docs/design-system.md -> Spacing and Layout):\n" + "\n".join(offenders),
        )

    def test_no_stale_registry_entries(self):
        problems = []
        for relative, include in sorted(FRAME_DELEGATED_TO_INCLUDE.items()):
            if relative not in self.pages:
                problems.append(f"FRAME_DELEGATED_TO_INCLUDE/{relative}: page no longer exists")
            elif _page_container(self.pages[relative]) is not None:
                problems.append(
                    f"FRAME_DELEGATED_TO_INCLUDE/{relative}: page now owns a container; delete this entry"
                )
            if not (self.base_dir / include).exists():
                problems.append(f"FRAME_DELEGATED_TO_INCLUDE/{relative}: include {include} is missing")
        for relative in sorted(CHROME_GUTTER_EXEMPT):
            if not (self.base_dir / relative).exists():
                problems.append(f"CHROME_GUTTER_EXEMPT/{relative}: template is missing; delete this entry")

        self.assertEqual(problems, [], "Stale width-contract registry entries:\n" + "\n".join(problems))

    def test_container_matcher_self_check(self):
        cases = (
            ('<div class="mx-auto max-w-7xl px-4">x</div>', "max-w-7xl"),
            ('<section class="relative mx-auto max-w-3xl px-4 py-16">x</section>', "max-w-3xl"),
            # Chrome nav must not be mistaken for the page frame.
            (
                '<nav class="mx-auto flex max-w-7xl px-6"></nav>'
                '<div class="mx-auto max-w-2xl px-4">x</div>',
                "max-w-2xl",
            ),
            # A non-centered wrapper is not a page frame.
            ('<div class="max-w-4xl">x</div><div class="mx-auto max-w-5xl">y</div>', "max-w-5xl"),
        )
        for source, expected in cases:
            with self.subTest(source=source):
                container = _page_container(source)
                self.assertIsNotNone(container)
                self.assertEqual(container[0], expected)

        for source in ('<div class="max-w-7xl">x</div>', "<p>no container</p>"):
            with self.subTest(source=source):
                self.assertIsNone(_page_container(source))
