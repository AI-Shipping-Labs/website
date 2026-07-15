"""Event-widget shortcode support for python-markdown (issue #1070).

Authors embed a claim widget in synced ``.md`` content with a fenced
``eventwidget`` directive:

    ```eventwidget
    slug: v0-claim
    ```

This preprocessor (priority 31, ahead of ``fenced_code`` at 25 and the
mermaid preprocessor at 30) intercepts the fence, extracts the ``slug:``
key, and emits a STABLE, user-agnostic placeholder as a raw HTML block:

    <div class="event-widget" data-event-widget="<slug>">
        <span class="event-widget-loading">Loading…</span>
    </div>

The div is surrounded by blank lines so ``md_in_html`` treats it as a raw
HTML block (it has no ``markdown="..."`` attribute, so its inner content is
left verbatim and never re-parsed). The placeholder is emitted directly
rather than via ``htmlStash`` because an EMPTY/attribute-only ``<div>`` is
not recognised as block-level by python-markdown's raw-HTML unstash step,
which would leak the bare stash marker wrapped in ``<p>`` (verified against
markdown 3.x + ``md_in_html``).

Because content HTML is rendered ONCE at save (cacheable, user-agnostic),
NO per-user state is baked in here. ``static/js/event-widget.js`` hydrates
the placeholder client-side from an authed endpoint at request time — the
same hydration pattern the notification bell uses.

An empty/missing slug renders nothing (no error, no leaked raw shortcode).
Unknown/inactive slugs are resolved client-side at hydration time (the
endpoint returns an ``unavailable`` state and the script renders nothing),
so the markdown layer stays user-agnostic and never queries the DB.
"""

import re

from django.utils.text import slugify
from markdown.extensions import Extension
from markdown.preprocessors import Preprocessor


class EventWidgetPreprocessor(Preprocessor):
    """Replace ```` ```eventwidget ```` fences with a stashed placeholder div."""

    def __init__(self, md=None, *, render_placeholder=True):
        super().__init__(md)
        self.render_placeholder = render_placeholder

    PATTERN = re.compile(
        r"^```eventwidget\s*\n(.*?)(?:\n^```\s*$|\Z)",
        re.DOTALL | re.MULTILINE,
    )
    SLUG_LINE = re.compile(r"^\s*slug\s*:\s*(?P<slug>.+?)\s*$", re.MULTILINE)

    def run(self, lines):
        text = "\n".join(lines)

        def replace(match):
            body = match.group(1)
            slug_match = self.SLUG_LINE.search(body)
            if not slug_match:
                # Malformed directive (no slug): render nothing.
                return "\n\n"
            slug = slugify(slug_match.group("slug"))
            if not slug:
                return "\n\n"
            if not self.render_placeholder:
                # Plain-text derivatives (descriptions, excerpts, metadata)
                # have no hydration runtime. Consume the directive as a
                # semantic block instead of exposing its implementation text.
                return "\n\n"
            # ``slugify`` guarantees ``slug`` is ``[a-z0-9-]`` only, so it is
            # safe to interpolate directly into the attribute. The inner
            # ``<span>`` is a pre-hydration placeholder that
            # ``static/js/event-widget.js`` replaces with the per-user state;
            # with JS off it simply reads "Loading…". Surrounding blank lines
            # make ``md_in_html`` treat this as a standalone raw HTML block.
            return (
                f'\n\n<div class="event-widget" data-event-widget="{slug}">'
                f'<span class="event-widget-loading">Loading…</span></div>\n\n'
            )

        return self.PATTERN.sub(replace, text).split("\n")


class EventWidgetExtension(Extension):
    """Register :class:`EventWidgetPreprocessor` at priority 31.

    Priority 31 sits above the mermaid preprocessor (30) and
    ``fenced_code`` (25) so the ``eventwidget`` fence is intercepted first
    and never reaches codehilite.
    """

    def __init__(self, **kwargs):
        self.config = {
            "render_placeholder": [
                True,
                "Emit the browser hydration placeholder instead of dropping it.",
            ],
        }
        super().__init__(**kwargs)

    def extendMarkdown(self, md):
        md.preprocessors.register(
            EventWidgetPreprocessor(
                md,
                render_placeholder=self.getConfig("render_placeholder"),
            ),
            "event_widget",
            31,
        )
