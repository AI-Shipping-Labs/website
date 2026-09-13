"""Small site adapter for package settings compatibility controls."""

from community_base.config import views as package_views
from community_base.config.registry import groups
from community_base.config.service import unset
from community_base.kernel.decorators import staff_required
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.shortcuts import redirect


@staff_required
def settings_save_group(request, group):
    """Delegate package saves and handle the site's clear-button affordance."""
    clear_key = request.POST.get("clear_override") if request.method == "POST" else ""
    group_definitions = groups().get(group, ())
    allowed_keys = {item.key for item in group_definitions}
    if clear_key and clear_key in allowed_keys:
        if unset(
            clear_key,
            actor_ref=f"user:{request.user.pk}",
            reason=f"Cleared Studio group {group}",
        ):
            messages.success(
                request,
                f"Cleared override for {clear_key} — now using env/default.",
            )
        else:
            messages.info(request, f"No override exists for {clear_key}.")
        section = request.POST.get("settings_section") or group
        return redirect(f"/studio/settings/#{section}")
    for item in group_definitions:
        value = request.POST.get(item.key, "")
        if item.key.endswith("_URL") and value:
            try:
                URLValidator()(value)
            except ValidationError:
                messages.error(
                    request,
                    f"{item.key} must be a valid URL. No settings were saved.",
                )
                section = request.POST.get("settings_section") or group
                return redirect(f"/studio/settings/#{section}")
    response = package_views.settings_save_group(request, group)
    section = request.POST.get("settings_section")
    if response.status_code in {301, 302, 303, 307, 308} and section:
        return redirect(f"/studio/settings/#{section}")
    return response
