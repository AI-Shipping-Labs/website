"""Helpers for preserving safe post-auth return URLs."""

from urllib.parse import urlencode, urlsplit

DEFAULT_RETURN_URL = "/"

# Path prefixes that must NEVER be used as the post-logout return target.
# Signing out from these surfaces always sends the user to ``/`` because
# their anonymous variant either does not exist or only renders a login
# redirect (which would create a bounce loop). Issue #519.
LOGOUT_REDIRECT_EXCLUDED_PREFIXES = (
    "/account",      # member-only settings page (and child paths)
    "/accounts",     # auth flow itself: login/register/verify/reset
    "/studio",       # staff-only content management
    "/admin",        # Django admin
    "/notifications",  # member-only feed
)


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


def should_skip_logout_redirect(path):
    """Return True when ``path`` is a surface that must redirect to ``/``.

    The logout view uses this to ignore an attacker- or self-supplied
    ``next`` that points at member-only or admin-only paths whose
    anonymous variant is meaningless (account settings, the auth flow
    itself, Studio, Django admin, notifications). The header template
    uses the same helper so the ``Log out`` link omits ``?next=`` when
    rendered on those pages — keeping the URL clean. Issue #519.
    """
    if not isinstance(path, str) or not path.startswith("/"):
        return True

    # Compare against the path part only — strip query string / fragment
    # so ``/studio?foo=bar`` and ``/studio#tab`` are still excluded.
    bare_path = path.split("?", 1)[0].split("#", 1)[0]

    for prefix in LOGOUT_REDIRECT_EXCLUDED_PREFIXES:
        if bare_path == prefix or bare_path.startswith(prefix + "/"):
            return True
    return False
