"""Helpers for preserving safe post-auth return URLs."""

from urllib.parse import urlencode, urlsplit

DEFAULT_RETURN_URL = "/"


def sanitize_next_url(value, default=DEFAULT_RETURN_URL):
    """Return a local path/query/fragment URL, or ``default`` if unsafe."""
    if not isinstance(value, str):
        return default

    value = value.strip()
    if not value or not value.startswith("/") or value.startswith("//"):
        return default
    if "\\" in value or any(ord(char) < 32 for char in value):
        return default

    try:
        parsed = urlsplit(value)
    except ValueError:
        return default

    if parsed.scheme or parsed.netloc or not parsed.path.startswith("/"):
        return default
    return value


def get_next_url(request, default=DEFAULT_RETURN_URL):
    """Read and sanitize the ``next`` value from query string or form data."""
    return sanitize_next_url(
        request.GET.get("next") or request.POST.get("next") or "",
        default=default,
    )


def append_next(url, next_url):
    """Append a safe ``next`` query parameter to ``url`` when present."""
    safe_next = sanitize_next_url(next_url, default="")
    if not safe_next:
        return url
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}{urlencode({'next': safe_next})}"
