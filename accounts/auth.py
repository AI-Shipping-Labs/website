"""Token authentication decorator for the JSON API (issue #431).

Reads the ``Authorization: Token <key>`` header, looks up the matching
``accounts.models.Token`` row, sets ``request.user`` to the token's owner, and
bumps ``last_used_at``. Returns a JSON 401 (NOT a redirect to /accounts/login/)
when the header is missing or the token does not exist; clients are scripts
that should never be redirected to a browser login page.
"""

from functools import wraps

from django.http import JsonResponse
from django.utils import timezone

from accounts.models import Token


def token_required(view_func):
    """Require a valid ``Authorization: Token <key>`` header.

    On success: sets ``request.user`` to the token's owner and updates
    ``last_used_at`` via ``update_fields=['last_used_at']`` to avoid touching
    unrelated columns (and to skip the User.save() free-tier branch on every
    API call).

    Tokens are NOT downgraded when ``user.is_staff`` flips to False -- the
    token's owner can lose staff privileges and still authenticate, because
    revocation is the explicit way to cut API access. The Studio token-list
    page shows the owner's email so the operator can audit this.
    """

    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        header = request.headers.get("Authorization", "")
        if not header:
            return JsonResponse(
                {"error": "Authentication token required"},
                status=401,
            )

        # Accept the literal "Token <key>" prefix. Anything else (e.g.
        # "Bearer", or a key with no scheme) is treated as missing rather
        # than invalid -- the client almost certainly forgot the scheme.
        parts = header.split(" ", 1)
        if len(parts) != 2 or parts[0] != "Token" or not parts[1]:
            return JsonResponse(
                {"error": "Authentication token required"},
                status=401,
            )

        key = parts[1].strip()
        token = Token.objects.filter(key=key).select_related("user").first()
        if token is None:
            return JsonResponse({"error": "Invalid token"}, status=401)
        if not token.user.is_staff:
            return JsonResponse({"error": "Invalid token"}, status=401)

        token.last_used_at = timezone.now()
        token.save(update_fields=["last_used_at"])

        request.user = token.user
        return view_func(request, *args, **kwargs)

    return wrapper
