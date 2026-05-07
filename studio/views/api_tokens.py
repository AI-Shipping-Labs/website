"""Studio API token management (issue #431).

Superusers issue tokens to other admins so scripts can hit ``/api/contacts/...``
without a session cookie. The plaintext key is shown to the operator exactly
once on the ``/created/`` page (one-shot session stash, identical pattern to
``user_create_done``); afterwards the Studio surfaces only the ``key_prefix``.

All views are gated by ``superuser_required`` -- staff alone is not enough.
The user dropdown on the create form is filtered to admin accounts so tokens
can only be issued to ``is_staff`` or ``is_superuser`` users; minting a token
for a Free / Basic / Main / Premium contact is rejected at form-validation
time.
"""

from django import forms
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.models import Token
from studio.decorators import superuser_required

User = get_user_model()


# Session key holding the one-shot stash of the new token's plaintext key
# and identifying primary key. The ``created`` view pops this and never
# writes it back, so refreshing or revisiting the page leaves nothing
# behind. Mirrors the ``SESSION_KEY`` pattern in ``studio.views.users``.
SESSION_KEY = "studio_api_token_create_result"


class TokenCreateForm(forms.Form):
    """Create-token form. The user dropdown is restricted to admin accounts."""

    user = forms.ModelChoiceField(
        queryset=User.objects.none(),
        empty_label="Select a user...",
        widget=forms.Select(
            attrs={
                "class": (
                    "w-full bg-secondary border border-border rounded-lg "
                    "px-4 py-2 text-sm text-foreground focus:outline-none "
                    "focus:ring-1 focus:ring-accent"
                ),
                "data-testid": "token-user-select",
                "autocomplete": "off",
            }
        ),
    )
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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Filter to admin accounts -- regular members must NOT appear in the
        # dropdown. Order by email so the list is predictable for operators.
        self.fields["user"].queryset = (
            User.objects.filter(Q(is_superuser=True) | Q(is_staff=True))
            .order_by("email")
        )


@superuser_required
def studio_api_token_list(request):
    """List every token row so operators can audit and revoke."""
    tokens = (
        Token.objects.select_related("user")
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
                user=form.cleaned_data["user"],
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
