"""Studio views for integration and auth-login settings management.

Provides:

- ``/studio/settings/`` — dashboard with two zones: Auth & Login (OAuth
  providers backed by ``SocialApp``) and Integrations (outbound service
  credentials backed by ``IntegrationSetting``).
- ``/studio/settings/<group_name>/save/`` — save settings for a specific
  integration group.
- ``/studio/settings/auth/<provider>/save/`` — save OAuth credentials
  for a single login provider.
"""

import os

from django.conf import settings as django_settings
from django.contrib import messages
from django.contrib.sites.models import Site
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from integrations.config import clear_config_cache
from integrations.models import IntegrationSetting
from integrations.settings_registry import INTEGRATION_GROUPS, get_group_by_name
from studio.decorators import staff_required
from studio.services.auth_settings import (
    get_all_auth_providers,
    is_supported_provider,
    save_auth_provider,
)


def _build_group_context(group_def, db_settings):
    """Build template context for a single integration group.

    Args:
        group_def: Group definition dict from the registry.
        db_settings: Dict mapping key -> value from the database.

    Returns:
        dict with group metadata and field list.
    """
    fields = []
    keys_set = 0
    total_keys = len(group_def['keys'])

    for key_def in group_def['keys']:
        key = key_def['key']
        db_value = db_settings.get(key, '')
        env_value = os.environ.get(key, '')

        if db_value:
            current_value = db_value
            source = 'db'
        elif env_value:
            current_value = env_value
            source = 'env'
        else:
            current_value = ''
            source = ''

        if current_value:
            keys_set += 1

        fields.append({
            'key': key,
            'description': key_def.get('description', key),
            'is_secret': key_def.get('is_secret', False),
            'multiline': key_def.get('multiline', False),
            'current_value': current_value,
            'source': source,
        })

    if keys_set == total_keys:
        status = 'configured'
    elif keys_set > 0:
        status = 'partial'
    else:
        status = 'not_configured'

    return {
        'name': group_def['name'],
        'label': group_def['label'],
        'fields': fields,
        'status': status,
        'keys_set': keys_set,
        'total_keys': total_keys,
    }


@staff_required
def settings_dashboard(request):
    """Render the two-zone settings dashboard (Auth & Login + Integrations)."""
    db_settings = dict(
        IntegrationSetting.objects.values_list('key', 'value')
    )

    groups = []
    for group_def in INTEGRATION_GROUPS:
        groups.append(_build_group_context(group_def, db_settings))

    auth_providers = get_all_auth_providers(
        django_settings.SITE_BASE_URL,
        django_settings.SOCIALACCOUNT_PROVIDERS,
    )

    return render(request, 'studio/settings/dashboard.html', {
        'groups': groups,
        'auth_providers': auth_providers,
        'site_base_url': django_settings.SITE_BASE_URL,
    })


@staff_required
@require_POST
def settings_save_group(request, group_name):
    """Save all settings for a specific integration group."""
    group_def = get_group_by_name(group_name)
    if not group_def:
        messages.error(request, f'Unknown integration group: {group_name}')
        return redirect('studio_settings')

    saved_count = 0
    for key_def in group_def['keys']:
        key = key_def['key']
        value = request.POST.get(key, '')

        IntegrationSetting.objects.update_or_create(
            key=key,
            defaults={
                'value': value,
                'is_secret': key_def.get('is_secret', False),
                'group': group_name,
                'description': key_def.get('description', ''),
            },
        )
        saved_count += 1

    clear_config_cache()
    messages.success(request, f'{group_def["label"]} settings saved ({saved_count} keys).')
    return redirect('studio_settings')


@staff_required
@require_POST
def settings_save_auth_provider(request, provider):
    """Save OAuth credentials for a single login provider.

    Mirrors ``settings_save_group`` but writes to ``SocialApp`` instead
    of ``IntegrationSetting``. Whitelists the provider against
    ``SUPPORTED_PROVIDERS`` so only Google / GitHub / Slack are touched —
    a typo in the URL renders an error message rather than creating a
    bogus row.
    """
    if not is_supported_provider(provider):
        messages.error(request, f'Unknown auth provider: {provider}')
        return redirect('studio_settings')

    client_id = request.POST.get('client_id', '').strip()
    secret = request.POST.get('client_secret', '').strip()
    site = Site.objects.get_current()

    save_auth_provider(provider, client_id, secret, site)

    messages.success(
        request,
        f'{provider.capitalize()} OAuth credentials saved.',
    )
    # Redirect back to the card the operator just saved so they can
    # confirm the status badge flipped without scrolling.
    return redirect(f'/studio/settings/#auth-{provider}')
