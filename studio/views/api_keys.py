"""Studio management for ``community_base.api.APIKey`` (issue #1737).

These are the scoped bearer credentials that authenticate the versioned
``/api/v1/`` surface. They are a different credential from ``accounts.Token``
(``/studio/api-tokens/``), which authenticates the site's own ``/api/...``
endpoints, so the two Studio pages cross-link and keep separate vocabulary:
this page says "key" throughout, the token page says "token".

The site owns these views rather than mounting ``community_base.api.urls``
because the package list view queries ``APIKey.objects.select_related("user")``
with no scoping at all. Investigation #1656 pinned that the resulting metadata
never reached HTML only because the package template puts its body in
``{% block content %}`` while the site Studio shell defines
``{% block studio_content %}`` -- the block was silently dropped and the page
rendered as an empty shell. Making the page render and scoping the queryset
therefore have to happen together; a template-only fix cannot touch a queryset
that lives in package code.

Scoping rule, mirroring ``studio/views/api_tokens.py``: superuser-only, and
staff-kind keys only. ``APIKey.clean()`` refuses a staff-kind key whose owner
is not ``is_staff``, so every listed row is an operator automation credential
by construction. Member-kind keys are member credentials; the site's rule for
those is owner-only at ``/account/#api-keys``, so they appear nowhere here and
``revoke`` 404s on a member-kind id instead of silently succeeding.

Create is constrained the same way ``TokenCreateForm`` is: no recipient
picker and no kind picker. The owner is always ``request.user`` and the kind
is always ``staff``, whatever the POST body says. The plaintext is shown
exactly once, on ``/studio/api-keys/created/``, via a one-shot session stash.
"""

from community_base.api import registry
from community_base.api.forms import APIKeyCreateForm
from community_base.api.models import APIKey
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from studio.decorators import superuser_required

# Session key holding the one-shot stash of the new key's plaintext value and
# its row id. ``studio_api_key_created`` pops this and never writes it back,
# so a reload or a revisit finds nothing. Same pattern as
# ``studio.views.api_tokens.SESSION_KEY``.
SESSION_KEY = "studio_api_key_create_result"

TEXT_INPUT_CLASS = (
    "w-full bg-secondary border border-border rounded-lg "
    "px-4 py-2 text-sm text-foreground "
    "placeholder-muted-foreground focus:outline-none "
    "focus:ring-1 focus:ring-accent"
)


def declared_scopes():
    """Scope names actually declared by the mounted ``/api/v1/`` routes.

    Read from the package registry so the create form's help text stays true
    as routes are added, instead of an operator having to guess.
    """
    return sorted({route.scope for route in registry.routes() if route.scope})


class StudioAPIKeyCreateForm(APIKeyCreateForm):
    """Name + scopes only. Owner and kind are decided by the view.

    Subclasses the package form so scope parsing and ``_SCOPE_RE`` validation
    stay in one place, then drops the recipient and kind fields entirely --
    they are not operator choices on this page.
    """

    def __init__(self, *args, owner=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.owner = owner
        del self.fields["user"]
        del self.fields["kind"]
        self.fields["name"].widget.attrs.update(
            {
                "class": TEXT_INPUT_CLASS,
                "placeholder": "e.g. settings sync script",
                "data-testid": "key-name-input",
            }
        )
        self.fields["scopes"].widget.attrs.update(
            {
                "class": TEXT_INPUT_CLASS,
                "placeholder": "e.g. settings.read, content_sync.read",
                "data-testid": "key-scopes-input",
            }
        )
        scopes = declared_scopes()
        self.fields["scopes"].help_text = (
            "Comma-separated scopes. Declared by the mounted /api/v1/ routes: "
            f"{', '.join(scopes)}. "
            "Use * only for deliberately unrestricted keys."
        )

    def clean(self):
        cleaned = super().clean()
        # A superuser who is not also is_staff cannot own a staff-kind key
        # (``APIKey.clean()``). Catch it here so the operator gets a form
        # error instead of a ValidationError escaping ``full_clean()`` as a
        # 500 from ``create_for_user``.
        if self.owner is not None and not self.owner.is_staff:
            self.add_error(
                None,
                "Staff API keys require a staff user. Your account is a "
                "superuser but not staff, so it cannot own a key.",
            )
        return cleaned


@superuser_required
def studio_api_key_list(request):
    """List staff-kind API keys for audit and revocation.

    Staff-kind only: member-kind rows are member credentials and are absent
    from the context entirely, not merely hidden by the template.
    """
    api_keys = (
        APIKey.objects.select_related("user")
        .filter(kind=APIKey.Kind.STAFF)
        .order_by("-created_at")
    )
    return render(
        request,
        "studio/api_keys/list.html",
        {"api_keys": api_keys},
    )


@superuser_required
def studio_api_key_create(request):
    """Show or handle the create-key form.

    GET renders the form. POST validates, mints a staff-kind key owned by the
    signed-in superuser, stashes the plaintext, then redirects to the
    one-shot page.
    """
    if request.method == "POST":
        form = StudioAPIKeyCreateForm(request.POST, owner=request.user)
        if form.is_valid():
            api_key, plaintext = APIKey.create_for_user(
                user=request.user,
                name=form.cleaned_data["name"],
                scopes=form.cleaned_data["scopes"],
                kind=APIKey.Kind.STAFF,
            )
            request.session[SESSION_KEY] = {
                "key": plaintext,
                "pk": str(api_key.pk),
            }
            return redirect("studio_api_key_created")
    else:
        form = StudioAPIKeyCreateForm(owner=request.user)

    return render(
        request,
        "studio/api_keys/create.html",
        {"form": form},
    )


@superuser_required
def studio_api_key_created(request):
    """Render the plaintext key exactly once, then drop the stash."""
    stash = request.session.pop(SESSION_KEY, None)
    if not stash:
        return redirect("studio_api_key_list")

    api_key = (
        APIKey.objects.filter(pk=stash.get("pk"), kind=APIKey.Kind.STAFF)
        .select_related("user")
        .first()
    )
    if api_key is None:
        # Edge case: the key was revoked and deleted between create and view.
        return redirect("studio_api_key_list")

    return render(
        request,
        "studio/api_keys/created.html",
        {
            "api_key": api_key,
            "plaintext_key": stash.get("key", ""),
        },
    )


@superuser_required
@require_POST
def studio_api_key_revoke(request, key_id):
    """Revoke a staff-kind key by its non-secret row id.

    Filtering on ``kind`` here is what makes a guessed member-kind id a 404
    rather than a silent cross-boundary revocation.
    """
    api_key = get_object_or_404(APIKey, pk=key_id, kind=APIKey.Kind.STAFF)
    api_key.revoke()
    messages.success(request, "API key revoked.")
    return redirect("studio_api_key_list")
