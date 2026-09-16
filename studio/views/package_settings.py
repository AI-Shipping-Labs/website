"""Small site adapter for package settings compatibility controls."""

from community_base.config import views as package_views
from community_base.config.registry import groups
from community_base.config.service import (
    REDACTED,
    Setting,
    SettingChange,
    decrypt,
    definition,
    mask_sensitive_spans,
    runtime,
)
from community_base.kernel.decorators import staff_required
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db import transaction
from django.shortcuts import redirect

from integrations.config import clear_config_cache


def _unset_setting(key, actor_ref, reason=""):
    """Remove one database override and restore normal fallback resolution.

    Compatibility replacement for ``community_base.config.service.unset``,
    which the package removed in v0.4.x without a successor: the Studio
    clear-override affordance is site-owned, so the exact v0.3.9 semantics
    (delete the row, write the audit change, reset and republish the
    runtime stamp) live here until the package re-ships the function.
    """
    item = definition(key)
    with transaction.atomic():
        previous = Setting.objects.select_for_update().filter(key=key).first()
        if previous is None:
            return False
        previous.delete()
        SettingChange.objects.create(
            setting_key=key,
            old_value=REDACTED if item.secret else previous.value,
            old_value_redacted=item.secret,
            new_value=None,
            new_value_redacted=False,
            actor_ref=mask_sensitive_spans(str(actor_ref)),
            reason=mask_sensitive_spans(
                str(reason),
                canaries=(
                    (str(decrypt(previous.value)),) if item.secret else ()
                ),
            ),
        )
        runtime.reset()
        transaction.on_commit(runtime.publish)
    return True


@staff_required
def settings_save_group(request, group):
    """Delegate package saves and handle the site's clear-button affordance."""
    clear_key = request.POST.get("clear_override") if request.method == "POST" else ""
    group_definitions = groups().get(group, ())
    allowed_keys = {item.key for item in group_definitions}
    if clear_key and clear_key in allowed_keys:
        if _unset_setting(
            clear_key,
            actor_ref=f"user:{request.user.pk}",
            reason=f"Cleared Studio group {group}",
        ):
            clear_config_cache()
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
    if request.method == "POST":
        # The package view publishes only the package stamp channel; the
        # site's donor-fallback cache in integrations.config stamps
        # independently and would keep serving pre-save values (issue
        # #1627: saved SITE_BASE_URL overrides never cleared the env
        # mismatch banner).
        clear_config_cache()
    section = request.POST.get("settings_section")
    if response.status_code in {301, 302, 303, 307, 308} and section:
        return redirect(f"/studio/settings/#{section}")
    return response


@staff_required
def settings_import(request):
    """Delegate package imports and refresh the site's fallback cache."""
    response = package_views.settings_import(request)
    if request.method == "POST":
        # Same reason as the group save: imported values must be visible
        # to integrations.config readers without a process restart.
        clear_config_cache()
    return response
