"""Studio API token management (issue #431, simplified per issue #619).

Superusers issue tokens to themselves so scripts can hit ``/api/contacts/...``
without a session cookie. The plaintext key is shown to the operator exactly
once on the ``/created/`` page (one-shot session stash, identical pattern to
``user_create_done``); afterwards the Studio surfaces only the ``key_prefix``.

All views are gated by ``superuser_required`` -- staff alone is not enough.
Tokens are always bound to ``request.user``: the create form no longer asks
the admin to pick a recipient, since each admin can sign in to Studio and
mint their own. ``Token.clean()`` still rejects non-staff users as defence
in depth at the model layer.
"""

from django import forms
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.models import Token
from studio.decorators import superuser_required

User = get_user_model()

RESERVED_SYSTEM_TOKEN_NAMES = frozenset(
    {
        "member-plan-editor",
        "studio-plan-editor",
    }
)


# Session key holding the one-shot stash of the new token's plaintext key
# and identifying primary key. The ``created`` view pops this and never
# writes it back, so refreshing or revisiting the page leaves nothing
# behind. Mirrors the ``SESSION_KEY`` pattern in ``studio.views.users``.
SESSION_KEY = "studio_api_token_create_result"


class TokenCreateForm(forms.Form):
    """Create-token form. Token is implicitly owned by the signed-in admin."""

    name = forms.CharField(
        max_length=100,
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": (
                    "w-full bg-secondary border border-border rounded-lg "
                    "px-4 py-2 text-sm text-foreground "
                    "placeholder-muted-foreground focus:outline-none "
                    "focus:ring-1 focus:ring-accent"
                ),
                "placeholder": "e.g. import script, valeriia laptop",
                "data-testid": "token-name-input",
            }
        ),
    )

    def clean_name(self):
        name = (self.cleaned_data.get("name") or "").strip()
        if name in RESERVED_SYSTEM_TOKEN_NAMES:
            raise forms.ValidationError(
                "That token name is reserved for system-managed tokens."
            )
        return name


@superuser_required
def studio_api_token_list(request):
    """List operator-created API tokens for audit and revocation."""
    tokens = (
        Token.objects.select_related("user")
        .exclude(name__in=RESERVED_SYSTEM_TOKEN_NAMES)
        .order_by("-created_at")
    )
    return render(
        request,
        "studio/api_tokens/list.html",
        {"tokens": tokens},
    )


@superuser_required
def studio_api_token_create(request):
    """Show or handle the create-token form.

    GET renders the form. POST validates, creates the row, stashes the
    plaintext key in the session, then redirects to the one-shot view.
    """
    if request.method == "POST":
        form = TokenCreateForm(request.POST)
        if form.is_valid():
            token = Token.objects.create(
                user=request.user,
                name=form.cleaned_data.get("name") or "",
            )
            request.session[SESSION_KEY] = {
                "key": token.key,
                "pk": token.pk,
            }
            return redirect("studio_api_token_created")
    else:
        form = TokenCreateForm()

    return render(
        request,
        "studio/api_tokens/create.html",
        {"form": form},
    )


@superuser_required
def studio_api_token_created(request):
    """Render the plaintext key exactly once, then drop the stash.

    If the operator reloads or navigates back to this URL the stash is gone
    and they're redirected to the list (mirroring ``user_create_done``).
    """
    stash = request.session.pop(SESSION_KEY, None)
    if not stash:
        return redirect("studio_api_token_list")

    token = Token.objects.filter(pk=stash.get("pk")).select_related("user").first()
    if token is None:
        # Edge case: token deleted between create and view.
        return redirect("studio_api_token_list")

    return render(
        request,
        "studio/api_tokens/created.html",
        {
            "token": token,
            "plaintext_key": stash.get("key", ""),
        },
    )


@superuser_required
@require_POST
def studio_api_token_revoke(request, key):
    """Delete the named token.

    Revocation is the only way to cut API access -- there's no rotation /
    expiration / scoping in v1. Idempotent: revoking a missing key flashes
    the same success message so a double-click doesn't 404.
    """
    token = get_object_or_404(Token, key=key)
    token.delete()
    messages.success(request, "Token revoked")
    return redirect("studio_api_token_list")
