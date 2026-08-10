"""Shared responsive rendering for Article cover and inline images."""

import html
import re
from html.parser import HTMLParser

from django import template
from django.utils.safestring import mark_safe

register = template.Library()

_IMG_RE = re.compile(r"<img\b[^>]*>", re.IGNORECASE)


class _ImageAttributes(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.attrs = {}

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "img":
            self.attrs = dict(attrs)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)


def _escape(value):
    return html.escape(str(value), quote=True)


def _srcset(variants, mime):
    return ", ".join(
        f"{item['url']} {item['width']}w"
        for item in variants
        if item.get("type") == mime and item.get("url") and item.get("width")
    )


def render_responsive_image(
    article,
    *,
    src,
    alt="",
    title="",
    sizes="",
    css_class="",
    eager=False,
):
    """Render one image from the Article manifest, or a safe original fallback."""
    loading = (
        ' fetchpriority="high"'
        if eager
        else ' loading="lazy" decoding="async"'
    )
    title_attr = f' title="{_escape(title)}"' if title else ""
    class_attr = f' class="{_escape(css_class)}"' if css_class else ""
    common = f'alt="{_escape(alt)}"{title_attr}{class_attr}{loading}'
    entry = (article.image_manifest or {}).get(src)
    if not entry:
        return mark_safe(f'<img src="{_escape(src)}" {common}>')

    width = entry.get("width")
    height = entry.get("height")
    variants = entry.get("variants") or []
    if not (isinstance(width, int) and width > 0 and isinstance(height, int) and height > 0 and variants):
        return mark_safe(f'<img src="{_escape(src)}" {common}>')

    webp_srcset = _srcset(variants, "image/webp")
    fallback_mime = entry.get("source_type")
    fallback_srcset = _srcset(variants, fallback_mime)
    source_tags = []
    if webp_srcset:
        source_tags.append(f'<source type="image/webp" srcset="{_escape(webp_srcset)}" sizes="{_escape(sizes)}">')
    if fallback_srcset and fallback_mime != "image/webp":
        source_tags.append(
            f'<source type="{_escape(fallback_mime)}" srcset="{_escape(fallback_srcset)}" sizes="{_escape(sizes)}">'
        )
    return mark_safe(
        "<picture>"
        + "".join(source_tags)
        + f'<img src="{_escape(src)}" width="{width}" height="{height}" '
        + f"{common}>"
        + "</picture>"
    )


@register.simple_tag
def article_cover_image(article, sizes, css_class, eager=False):
    return render_responsive_image(
        article,
        src=article.cover_image_url,
        alt=article.title,
        sizes=sizes,
        css_class=css_class,
        eager=eager,
    )


@register.filter
def render_article_images(html_content, article):
    """Replace inline ``img`` tags while preserving surrounding authored HTML."""
    if not html_content or not article:
        return html_content

    def replace(match):
        parser = _ImageAttributes()
        parser.feed(match.group(0))
        src = parser.attrs.get("src", "")
        if not src:
            return match.group(0)
        return str(
            render_responsive_image(
                article,
                src=src,
                alt=parser.attrs.get("alt", ""),
                title=parser.attrs.get("title", ""),
                sizes="(min-width: 768px) 48rem, calc(100vw - 2rem)",
            )
        )

    return mark_safe(_IMG_RE.sub(replace, str(html_content)))
