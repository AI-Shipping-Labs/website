"""Display and import helpers for sprint plan resources."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

_MARKDOWN_LINK_RE = re.compile(r"\[([^\]\n]+)\]\(([^)\s]+)\)")
_BARE_URL_RE = re.compile(r"(?P<url>https?://[^\s<>\]]+)")
_SAFE_LINK_SCHEMES = {"http", "https", "mailto"}
_TRAILING_URL_PUNCTUATION = ".,;:"


@dataclass(frozen=True)
class ResourceDisplay:
    title: str
    url: str
    note: str


@dataclass(frozen=True)
class _LinkCandidate:
    title: str
    url: str
    start: int
    end: int
    kind: str


def _is_safe_link_url(value: str) -> bool:
    if not value:
        return False
    return urlsplit(value).scheme.lower() in _SAFE_LINK_SCHEMES


def _clean_url(value: str) -> str:
    value = (value or "").strip().rstrip(_TRAILING_URL_PUNCTUATION)
    return value if _is_safe_link_url(value) else ""


def _first_markdown_link(value: str) -> _LinkCandidate | None:
    for match in _MARKDOWN_LINK_RE.finditer(value or ""):
        url = _clean_url(match.group(2))
        if url:
            return _LinkCandidate(
                title=match.group(1).strip(),
                url=url,
                start=match.start(),
                end=match.end(),
                kind="markdown",
            )
    return None


def _first_bare_url(value: str) -> _LinkCandidate | None:
    for match in _BARE_URL_RE.finditer(value or ""):
        url = _clean_url(match.group("url"))
        if url:
            return _LinkCandidate(
                title=url,
                url=url,
                start=match.start(),
                end=match.start() + len(url),
                kind="bare",
            )
    return None


def _first_link(value: str) -> _LinkCandidate | None:
    return _first_markdown_link(value) or _first_bare_url(value)


def _replace_markdown_links(value: str) -> str:
    def replace(match: re.Match) -> str:
        url = _clean_url(match.group(2))
        if not url:
            return match.group(1).strip()
        return match.group(1).strip()

    return _MARKDOWN_LINK_RE.sub(replace, value or "")


def _strip_promoted_bare_url(value: str, url: str) -> str:
    if not url:
        return value
    cleaned = value.replace(url, "")
    return _strip_separators(cleaned)


def _strip_separators(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", (value or "").strip(" -–—:\t\n")).strip()
    if cleaned.endswith(")") and "(" not in cleaned:
        cleaned = cleaned.rstrip(")").strip()
    return cleaned


def clean_resource_text(value: str, *, promoted_url: str = "") -> str:
    """Return readable text with markdown-link syntax and promoted URLs hidden."""
    cleaned = _replace_markdown_links(value or "")
    cleaned = _strip_promoted_bare_url(cleaned, promoted_url)
    return _strip_separators(cleaned)


def normalize_resource_display(title: str, url: str = "", note: str = "") -> ResourceDisplay:
    """Normalize a stored ``Resource`` for display without mutating storage.

    Legacy plans sometimes put the real link in ``title`` or ``note`` as a
    markdown link or bare URL while ``Resource.url`` is blank. The returned
    object exposes a clean primary title and URL while leaving notes suitable
    for the plan-safe markdown renderer.
    """
    raw_title = (title or "").strip()
    raw_note = (note or "").strip()
    explicit_url = _clean_url(url)
    title_link = _first_link(raw_title)
    note_link = _first_link(raw_note)
    promoted = explicit_url
    if not promoted and title_link is not None:
        promoted = title_link.url
    if not promoted and note_link is not None:
        promoted = note_link.url

    display_title = clean_resource_text(raw_title, promoted_url=promoted)
    if not display_title and title_link is not None:
        display_title = title_link.title
    if not display_title and note_link is not None:
        display_title = note_link.title
    if not display_title:
        display_title = promoted or raw_title

    display_note = raw_note
    if promoted and _strip_separators(_replace_markdown_links(raw_note)) == promoted:
        display_note = ""

    return ResourceDisplay(
        title=display_title[:300],
        url=promoted,
        note=display_note,
    )


def parse_resource_bullet(item: str, *, position: int) -> dict[str, object]:
    """Parse one markdown Resources bullet into API-ready resource fields."""
    raw = (item or "").strip()
    if " - " in raw:
        title_part, note_part = raw.split(" - ", 1)
    else:
        title_part, note_part = raw, ""

    link = _first_link(note_part) or _first_link(title_part)
    url = link.url if link is not None else ""
    title = clean_resource_text(title_part, promoted_url=url)

    if not title and link is not None:
        title = link.title
    if not title:
        title = clean_resource_text(raw, promoted_url=url) or url

    note = note_part.strip()
    if note:
        note_text = clean_resource_text(note, promoted_url=url)
        if (
            not note_text
            or note_text == title
            or (link is not None and note_text == link.title)
        ):
            note = ""

    return {
        "title": title[:300],
        "url": url,
        "note": note,
        "position": position,
    }
