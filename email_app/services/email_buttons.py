"""Render-time styling for Markdown CTA button links in Relay campaign emails.

Relay renders campaign bodies with Python-Markdown and ``attr_list`` enabled,
while ``AISL_LAYOUT_HTML`` styles ``.email-body a`` as an underlined olive text
link. Authors opt a link into CTA button rendering with::

    [Apply for free](https://example.com){: .email-btn }

At render time (without mutating stored campaign Markdown), this pass adds
inline button CSS to any ``.email-btn`` link so it renders as a high-contrast
button in both Studio's preview iframe and email clients that strip ``<style>``
tags. Normal Markdown links without ``.email-btn`` and links inside fenced code
blocks are left untouched.
"""

import re

from email_app.services.email_images import size_markdown_images_for_email

EMAIL_BUTTON_STYLE = (
    "display:inline-block;padding:12px 24px;background-color:#6b8e23;"
    "color:#ffffff;text-decoration:none;border-radius:6px;font-weight:600;"
    "line-height:1.2"
)

_BUTTON_LINK_RE = re.compile(
    r"(?P<link>(?<!!)\[[^\]\n]+\](?:\([^)\n]*\)|\[[^\]\n]*\]))\{:(?P<attrs>[^\n}]*)\}"
)
_DOT_EMAIL_BTN_RE = re.compile(r"(?<!\S)\.email-btn(?!\S)")
_CLASS_EMAIL_BTN_RE = re.compile(
    r"""(?<!\S)class=(["'])([^"']*\bemail-btn\b[^"']*)\1"""
)
_HAS_STYLE_RE = re.compile(r"(?<!\S)style=")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")


def _rewrite_button_match(match):
    attrs = match.group("attrs")
    if not _DOT_EMAIL_BTN_RE.search(attrs) and not _CLASS_EMAIL_BTN_RE.search(attrs):
        return match.group(0)
    if _HAS_STYLE_RE.search(attrs):
        return match.group(0)
    updated_attrs = f'{attrs.rstrip()} style="{EMAIL_BUTTON_STYLE}" '
    return f"{match.group('link')}{{:{updated_attrs}}}"


def style_markdown_buttons_for_email(body_markdown):
    """Return ``body_markdown`` with inline CTA button styles on ``.email-btn`` links."""
    if not body_markdown or "email-btn" not in body_markdown:
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
        if fence is None and "email-btn" in line:
            line = _BUTTON_LINK_RE.sub(_rewrite_button_match, line)
        out.append(line)
    return "\n".join(out)


def prepare_campaign_body_markdown(body_markdown):
    """Apply email-safe image sizing and CTA button styling for Relay rendering."""
    sized = size_markdown_images_for_email(body_markdown)
    return style_markdown_buttons_for_email(sized)
