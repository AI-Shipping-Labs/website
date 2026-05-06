"""Safe markdown rendering for member-authored plan content."""

from html import escape
from html.parser import HTMLParser
from urllib.parse import urlsplit

from django import template
from django.utils.safestring import mark_safe

from content.utils.markdown import render_markdown

register = template.Library()

_ALLOWED_TAGS = {
    "a",
    "blockquote",
    "br",
    "code",
    "div",
    "em",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "hr",
    "li",
    "ol",
    "p",
    "pre",
    "span",
    "strong",
    "table",
    "tbody",
    "td",
    "th",
    "thead",
    "tr",
    "ul",
}
_VOID_TAGS = {"br", "hr"}
_ALLOWED_ATTRS = {
    "a": {"href", "title", "target", "rel"},
    "code": {"class"},
    "div": {"class"},
    "span": {"class"},
    "table": {"class"},
    "td": {"align"},
    "th": {"align"},
}
_SAFE_SCHEMES = {"", "http", "https", "mailto"}


def _is_safe_url(value):
    if not value:
        return False
    if value.startswith(("#", "/", "?")):
        return True
    return urlsplit(value).scheme.lower() in _SAFE_SCHEMES


class _PlanHTMLSanitizer(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self._blocked_depth = 0

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in {"script", "style"}:
            self._blocked_depth += 1
            return
        if self._blocked_depth or tag not in _ALLOWED_TAGS:
            return

        allowed_attrs = _ALLOWED_ATTRS.get(tag, set())
        cleaned = []
        for name, value in attrs:
            name = name.lower()
            if name not in allowed_attrs or value is None:
                continue
            if name == "href" and not _is_safe_url(value.strip()):
                continue
            if name == "target" and value not in {"_blank", "_self"}:
                continue
            cleaned.append(f' {name}="{escape(value, quote=True)}"')

        if tag == "a":
            attr_names = {name.lower() for name, _value in attrs}
            if "rel" not in attr_names:
                cleaned.append(' rel="noopener noreferrer"')

        self.parts.append(f"<{tag}{''.join(cleaned)}>")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in {"script", "style"}:
            self._blocked_depth = max(0, self._blocked_depth - 1)
            return
        if self._blocked_depth or tag not in _ALLOWED_TAGS or tag in _VOID_TAGS:
            return
        self.parts.append(f"</{tag}>")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_data(self, data):
        if not self._blocked_depth:
            self.parts.append(escape(data))

    def handle_entityref(self, name):
        if not self._blocked_depth:
            self.parts.append(f"&{name};")

    def handle_charref(self, name):
        if not self._blocked_depth:
            self.parts.append(f"&#{name};")

    def sanitized(self):
        return "".join(self.parts)


def render_plan_markdown(value):
    """Render markdown with plan-safe HTML sanitization."""
    if not value:
        return ""
    html = render_markdown(
        str(value),
        include_mermaid=False,
        include_external_links=False,
    )
    sanitizer = _PlanHTMLSanitizer()
    sanitizer.feed(html)
    sanitizer.close()
    return sanitizer.sanitized()


@register.filter(name="plan_markdown")
def plan_markdown(value):
    return mark_safe(render_plan_markdown(value))
