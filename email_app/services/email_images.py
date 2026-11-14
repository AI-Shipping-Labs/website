"""Render-time sizing for Markdown images in Relay campaign email bodies.

Relay renders campaign bodies with Python-Markdown and the ``attr_list``
extension enabled. Images arrive at their natural size, so a wide hero
image (e.g. a 2560px social preview) overflows the 600px email container
and gets clipped. This pass appends an attribute list to every Markdown
image so it renders as::

    <img ... style="max-width:100%;height:auto" width="536" />

``max-width:100%; height:auto`` scales the image to the container in
clients that honor CSS; the ``width`` attribute covers Outlook desktop,
which ignores ``max-width``. 536 is the content width of the 600px layout
minus its 32px horizontal padding.

The stored campaign Markdown is never mutated; this only shapes the
context sent to Relay. Images that already carry an attribute list
(``![x](y){: width=200 }``) and images inside fenced code blocks are
left untouched.
"""

import re

EMAIL_IMAGE_WIDTH = 536
EMAIL_IMAGE_ATTRS = f"{{: style=max-width:100%;height:auto width={EMAIL_IMAGE_WIDTH} }}"

# Inline ``![alt](url "title")`` and reference ``![alt][ref]`` images that are
# not already followed by an attribute list.
_IMAGE_RE = re.compile(
    r"!\[[^\]\n]*\](?:\([^)\n]*\)|\[[^\]\n]*\])(?!\{)"
)
_FENCE_RE = re.compile(r"^\s*(```|~~~)")


def size_markdown_images_for_email(body_markdown):
    """Return ``body_markdown`` with email-safe sizing on every image."""
    if not body_markdown or "![" not in body_markdown:
        return body_markdown
    out = []
    fence = None
    for line in body_markdown.split("\n"):
        match = _FENCE_RE.match(line)
        if match:
            marker = match.group(1)
            if fence is None:
                fence = marker
            elif fence == marker:
                fence = None
            out.append(line)
            continue
        if fence is None and "![" in line:
            line = _IMAGE_RE.sub(lambda m: m.group(0) + EMAIL_IMAGE_ATTRS, line)
        out.append(line)
    return "\n".join(out)
