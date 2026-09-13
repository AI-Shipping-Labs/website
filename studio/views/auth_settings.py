"""Compatibility endpoint for OAuth provider credentials.

The runtime settings dashboard is owned by ``community_base.config``. OAuth
credentials remain ``SocialApp`` records, so this small endpoint stays at the
established URL while the package dashboard owns integration settings.
"""

from django.contrib import messages
from django.contrib.sites.models import Site
from django.shortcuts import redirect
from django.views.decorators.http import require_POST

from studio.decorators import staff_required
from studio.services.auth_settings import is_supported_provider, save_auth_provider


@staff_required
@require_POST
def settings_save_auth_provider(request, provider):
    if not is_supported_provider(provider):
        messages.error(request, f"Unknown auth provider: {provider}")
        return redirect("studio_settings")

    client_id = request.POST.get("client_id", "").strip()
    secret = request.POST.get("client_secret", "").strip()
    save_auth_provider(provider, client_id, secret, Site.objects.get_current())
    messages.success(request, f"{provider.capitalize()} OAuth credentials saved.")
    return redirect(f"/studio/settings/#auth-{provider}")
