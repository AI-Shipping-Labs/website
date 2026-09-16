"""Site-owned Studio settings save, on the v0.3.9 typed-form contract.

The package save view (community-base v0.4.x) renders every group field as
a free-text CharField and coerces the raw POST, so a blank optional integer
raises, an unchecked boolean cannot be expressed, and no restart warning
fires. The site's Studio settings contract is the v0.3.9 typed-form
semantics the cutover tests pin (#1584/#1268/#1533): blank optional
integers clear their override, absent booleans save False, and a save that
changes a ``requires_restart`` key warns about the restart. Those
semantics are site-owned (D18 applies to behavior as well as markup), and
the package supplies every needed primitive (``service.get/set/unset``),
so the adapter runs the save loop itself. GET renders keep delegating to
the package view.
"""

from community_base.config import views as package_views
from community_base.config.registry import groups
from community_base.config.service import get as service_get
from community_base.config.service import set as service_set
from community_base.config.service import unset
from community_base.kernel.decorators import staff_required
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator, validate_email
from django.shortcuts import redirect

from integrations.config import clear_config_cache

_RESTART_WARNING = (
    'Restart the application for these settings to take effect.'
)


def _typed_updates(group_definitions, post):
    """Normalize a raw group POST into v0.3.9 typed-form semantics.

    Returns ``(updates, clears, error)``: ``updates`` maps keys to the raw
    values to save, ``clears`` lists blank optional integers whose
    override must be removed, and ``error`` is the first validation
    failure in v0.3.9 form-error shape.
    """
    updates = {}
    clears = []
    error = None
    for item in group_definitions:
        raw = post.get(item.key, '')
        if item.secret and not raw:
            # v0.3.9: blank secrets keep their stored value.
            continue
        if item.value_type == 'bool':
            # v0.3.9 BooleanField(required=False): absent/blank -> False.
            updates[item.key] = 'true' if raw.strip() else 'false'
        elif item.value_type == 'int':
            if not raw.strip():
                if item.optional:
                    clears.append(item.key)
                    continue
                error = f'{item.key}: This field is required.'
                break
            if not raw.strip().lstrip('-').isdigit():
                error = f'{item.key}: Enter a whole number.'
                break
            updates[item.key] = raw
        elif item.value_type in ('json', 'list'):
            if not raw.strip():
                # v0.3.9 coerced a blank JSON/list value to a form error.
                error = f'{item.key}: Value must be valid JSON.'
                break
            updates[item.key] = raw
        elif not raw.strip() and not item.optional:
            # Required text-ish field left blank.
            error = f'{item.key}: This field is required.'
            break
        else:
            if item.is_email and raw.strip():
                try:
                    validate_email(raw.strip())
                except ValidationError:
                    error = f'{item.key}: Enter a valid email address.'
                    break
            updates[item.key] = raw
    return updates, clears, error


@staff_required
def settings_save_group(request, group):
    """Save one settings group and handle the site's clear-button affordance."""
    clear_key = request.POST.get("clear_override") if request.method == "POST" else ""
    group_definitions = groups().get(group)
    allowed_keys = {item.key for item in (group_definitions or ())}
    if clear_key and clear_key in allowed_keys:
        if unset(
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
    if request.method != "POST":
        return package_views.settings_save_group(request, group)
    if group_definitions is None:
        messages.error(request, "Unknown settings group.")
        return redirect("/studio/settings/")
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
    updates, clears, error = _typed_updates(group_definitions, request.POST)
    if error:
        messages.error(request, f"Could not save {group} settings: {error}")
        section = request.POST.get("settings_section") or group
        return redirect(f"/studio/settings/#{section}")
    prior = {key: service_get(key) for key in updates}
    try:
        for key in updates:
            service_set(
                key,
                updates[key],
                actor_ref=f"user:{request.user.pk}",
                reason=f"Updated Studio group {group}",
            )
    except ValidationError as exc:
        # service.set re-coerces; surface any surprise the same way the
        # v0.3.9 form did instead of letting it escape as a 500.
        messages.error(request, f"Could not save {group} settings: {exc}")
        section = request.POST.get("settings_section") or group
        return redirect(f"/studio/settings/#{section}")
    cleared_keys = [
        key for key in clears
        if unset(
            key,
            actor_ref=f"user:{request.user.pk}",
            reason=f"Cleared Studio group {group}",
        )
    ]
    feedback = f"Saved {group} settings."
    if cleared_keys:
        feedback += (
            f" Cleared {len(cleared_keys)} override(s); "
            'fallback values now apply.'
        )
    messages.success(request, feedback)
    changed_keys = {
        key for key, value in updates.items()
        if service_get(key) != prior[key]
    } | set(cleared_keys)
    restart_keys = {
        item.key for item in group_definitions
        if item.key in changed_keys and item.requires_restart
    }
    if restart_keys:
        messages.warning(request, _RESTART_WARNING)
    # The site's donor-fallback cache in integrations.config stamps
    # independently of the package runtime, so readers would otherwise keep
    # serving pre-save values (issue #1627: saved SITE_BASE_URL overrides
    # never cleared the env mismatch banner).
    clear_config_cache()
    section = request.POST.get("settings_section") or group
    return redirect(f"/studio/settings/#{section}")


@staff_required
def settings_import(request):
    """Delegate package imports and refresh the site's fallback cache."""
    response = package_views.settings_import(request)
    if request.method == "POST":
        # Same reason as the group save: imported values must be visible
        # to integrations.config readers without a process restart.
        clear_config_cache()
    return response
