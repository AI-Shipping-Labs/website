"""Studio view that renders integration setup docs from ``_docs/integrations/``.

This view backs the (?) help-icon links rendered next to each integration
setting field (issue #641). Each registry entry MAY define an optional
``docs_url`` like ``_docs/integrations/stripe.md#stripe_webhook_secret``;
the Studio template rewrites that to
``/studio/docs/integrations/stripe#stripe_webhook_secret`` and opens it
in a new tab.

The docs themselves live in the repo as markdown so they review
naturally in PRs and can be edited without a deploy. At request time the
file is read from disk and converted to HTML via the same ``markdown``
library the email templates and content sync already depend on.

Security: the ``group`` path argument is constrained to a known set of
group names (the names registered in ``INTEGRATION_GROUPS``) before
touching the filesystem, so this view cannot be tricked into serving
arbitrary files via path traversal.
"""

from pathlib import Path

import markdown
from django.conf import settings
from django.http import Http404
from django.shortcuts import render

from integrations.settings_registry import INTEGRATION_GROUPS
from studio.decorators import staff_required

DOCS_DIR_NAME = "_docs/integrations"


def _allowed_group_names():
    """Set of registry group names that may be served as docs.

    Built from the registry at call time so a new group becomes
    serveable as soon as it is added — no second list to maintain.
    """
    return {group['name'] for group in INTEGRATION_GROUPS}


@staff_required
def integration_docs(request, group):
    """Render ``_docs/integrations/<group>.md`` as HTML.

    The fragment identifier (``#stripe_webhook_secret``) is preserved by
    the browser — the markdown library generates matching ``id``
    attributes via the ``toc`` extension, so anchor navigation works
    without any additional server logic.

    Returns 404 if the group name is not in the registry or the file
    does not exist. Returns 404 (not 403) on traversal attempts so the
    surface is indistinguishable from a missing doc.
    """
    if group not in _allowed_group_names():
        raise Http404("Unknown integration group")

    docs_path = Path(settings.BASE_DIR) / DOCS_DIR_NAME / f"{group}.md"
    try:
        resolved = docs_path.resolve(strict=True)
    except (FileNotFoundError, OSError) as exc:
        raise Http404("Docs not yet authored for this integration") from exc

    docs_root = (Path(settings.BASE_DIR) / DOCS_DIR_NAME).resolve()
    # Belt-and-suspenders against path traversal — the group allowlist
    # above already prevents arbitrary input, but resolve() also catches
    # the case where _docs/integrations/<group>.md is a symlink that
    # escapes the docs dir.
    try:
        resolved.relative_to(docs_root)
    except ValueError as exc:
        raise Http404("Docs not yet authored for this integration") from exc

    source = resolved.read_text(encoding="utf-8")
    rendered_html = markdown.markdown(
        source,
        extensions=["extra", "toc", "fenced_code"],
    )

    group_label = next(
        (g['label'] for g in INTEGRATION_GROUPS if g['name'] == group),
        group,
    )

    return render(
        request,
        "studio/docs/integration_docs.html",
        {
            "group": group,
            "group_label": group_label,
            "rendered_html": rendered_html,
        },
    )
