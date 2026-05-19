"""Studio views for contact-tag management (issue #694).

The per-user tag chip in ``templates/studio/users/detail.html`` already
exposes "add" and "remove this user's copy" actions; these two views add
the global namespace operations: rename a tag everywhere it appears, and
delete a tag from every user that carries it.

Both endpoints are staff-only, POST-only, and ``Referer``-aware: they
redirect back to the page the operator clicked from (typically a user
detail page) so the operator stays in context.
"""

from urllib.parse import urlsplit

from django.contrib import messages
from django.shortcuts import redirect
from django.urls import reverse
from django.views.decorators.http import require_POST

from accounts.utils.tags import delete_tag, rename_tag
from studio.decorators import staff_required


def _safe_redirect_url(request):
    """Return a safe redirect target derived from the ``Referer`` header.

    Falls back to the Studio user list if no referer is present or the
    referer points to a different host (defence-in-depth against open
    redirects, even though staff_required already gates the surface).
    """
    referer = request.META.get('HTTP_REFERER', '')
    if not referer:
        return reverse('studio_user_list')

    parsed = urlsplit(referer)
    request_host = request.get_host()
    # An absolute URL on a different host is rejected.
    if parsed.netloc and parsed.netloc != request_host:
        return reverse('studio_user_list')

    # Strip the scheme + host so we redirect to a relative URL only; this
    # avoids leaking host info into the Location header.
    relative = parsed.path
    if parsed.query:
        relative = f'{relative}?{parsed.query}'
    if parsed.fragment:
        relative = f'{relative}#{parsed.fragment}'
    return relative or reverse('studio_user_list')


@staff_required
@require_POST
def tag_rename(request, name):
    """Rename ``name`` to ``request.POST['new']`` across every user.

    ``name`` is the pre-normalized slug from the URL; the helper
    re-normalizes both arguments defensively. ``ValueError`` (empty new
    name) is surfaced as a flash error so the operator can re-submit.
    """
    new_value = (request.POST.get('new') or '').strip()
    redirect_url = _safe_redirect_url(request)

    try:
        result = rename_tag(name, new_value)
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect(redirect_url)

    if result['old'] and result['old'] == result['new']:
        # No-op same-name rename. Surface a neutral flash so the operator
        # still gets feedback that the click registered.
        messages.info(request, f'Tag "{result["new"]}" unchanged.')
    elif not result['old']:
        # The URL slug normalized to empty (e.g. "/studio/tags//rename").
        # Treat it as bad input rather than a silent success.
        messages.error(request, 'Original tag name is empty.')
    else:
        affected = result['affected']
        messages.success(
            request,
            f'Renamed "{result["old"]}" to "{result["new"]}" '
            f'on {affected} user(s).',
        )
    return redirect(redirect_url)


@staff_required
@require_POST
def tag_delete(request, name):
    """Delete ``name`` from every user that carries it.

    ``name`` is the pre-normalized slug from the URL; the helper
    re-normalizes defensively so a typo'd URL is rejected with a flash.
    """
    redirect_url = _safe_redirect_url(request)
    result = delete_tag(name)

    if not result['name']:
        messages.error(request, 'Tag name is empty.')
        return redirect(redirect_url)

    affected = result['affected']
    messages.success(
        request,
        f'Deleted tag "{result["name"]}" from {affected} user(s).',
    )
    return redirect(redirect_url)
