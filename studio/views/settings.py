"""Studio views for integration and auth-login settings management.

Provides:

- ``/studio/settings/`` — dashboard with navigable sections for Auth,
  Payments, Content, Messaging, Storage, Site, and uncategorized settings.
- ``/studio/settings/<group_name>/save/`` — save settings for a specific
  integration group.
- ``/studio/settings/auth/<provider>/save/`` — save OAuth credentials
  for a single login provider.
- ``/studio/settings/export/`` — download all settings as a JSON file.
- ``/studio/settings/import/`` — upload a previously-exported JSON file
  and upsert the settings.
"""

import json
import os
from datetime import datetime

from django.conf import settings as django_settings
from django.contrib import messages
from django.contrib.sites.models import Site
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from integrations.config import clear_config_cache, site_base_url
from integrations.models import IntegrationSetting
from integrations.settings_registry import INTEGRATION_GROUPS, get_group_by_name
from studio.decorators import staff_required
from studio.services.auth_settings import (
    get_all_auth_providers,
    is_supported_provider,
    save_auth_provider,
)
from studio.services.settings_io import (
    ImportError as SettingsImportError,
)
from studio.services.settings_io import (
    apply_import,
    build_export,
)

SETTINGS_SECTIONS = [
    {
        'id': 'auth',
        'label': 'Auth',
        'description': 'OAuth providers users see on the login page.',
        'group_names': {'auth'},
    },
    {
        'id': 'payments',
        'label': 'Payments',
        'description': 'Billing and checkout integrations.',
        'group_names': {'stripe'},
    },
    {
        'id': 'content',
        'label': 'Content',
        'description': 'Content sync, video, and live-session service credentials.',
        'group_names': {'github', 'youtube', 'zoom'},
    },
    {
        'id': 'messaging',
        'label': 'Messaging',
        'description': 'Email, notifications, and community messaging integrations.',
        'group_names': {'ses', 'slack'},
    },
    {
        'id': 'storage',
        'label': 'Storage',
        'description': 'Buckets and public storage locations for generated assets.',
        'group_names': {'s3_recordings', 's3_content'},
    },
    {
        'id': 'site',
        'label': 'Site',
        'description': 'Platform-level URL and display settings.',
        'group_names': {'site'},
    },
]

OTHER_SECTION = {
    'id': 'other',
    'label': 'Other',
    'description': 'Settings that are not yet mapped to a primary section.',
    'group_names': set(),
}

HIGH_RISK_GROUP_NAMES = {'stripe', 'ses', 'github', 's3_recordings', 's3_content'}
HIGH_RISK_AUTH_PROVIDERS = {'google', 'github', 'slack'}


def _section_id_for_group_name(group_name):
    """Return the settings section id for a registry group name."""
    for section in SETTINGS_SECTIONS:
        if group_name in section['group_names']:
            return section['id']
    return OTHER_SECTION['id']


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
            'is_boolean': key_def.get('is_boolean', False),
            'current_value': current_value,
            'source': source,
            'env_value': env_value,
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
        'section_id': _section_id_for_group_name(group_def['name']),
    }


def _build_settings_sections(groups, auth_providers):
    """Place every auth provider and integration group in one visible section."""
    groups_by_section = {section['id']: [] for section in SETTINGS_SECTIONS}
    other_groups = []
    for group in groups:
        section_id = group['section_id']
        if section_id in groups_by_section:
            groups_by_section[section_id].append(group)
        else:
            other_groups.append(group)

    sections = []
    for section in SETTINGS_SECTIONS:
        section_context = {
            'id': section['id'],
            'label': section['label'],
            'description': section['description'],
            'groups': groups_by_section[section['id']],
            'auth_providers': auth_providers if section['id'] == 'auth' else [],
        }
        if section_context['auth_providers'] or section_context['groups']:
            sections.append(section_context)

    if other_groups:
        sections.append({
            'id': OTHER_SECTION['id'],
            'label': OTHER_SECTION['label'],
            'description': OTHER_SECTION['description'],
            'groups': other_groups,
            'auth_providers': [],
        })

    return sections


def _build_status_summary(groups, auth_providers):
    """Summarize settings state using local metadata only."""
    configured_count = 0
    partial_count = 0
    missing_count = 0
    missing_required_values = 0
    db_override_count = 0
    env_backed_count = 0
    high_risk_items = []

    for provider in auth_providers:
        if provider['is_configured']:
            configured_count += 1
            status = 'configured'
        else:
            missing_count += 1
            missing_required_values += 1
            status = 'not_configured'

        if provider['provider'] in HIGH_RISK_AUTH_PROVIDERS:
            high_risk_items.append({
                'label': provider['label'],
                'section_label': 'Auth',
                'status': status,
            })

    section_labels = {
        section['id']: section['label']
        for section in [*SETTINGS_SECTIONS, OTHER_SECTION]
    }

    for group in groups:
        if group['status'] == 'configured':
            configured_count += 1
        elif group['status'] == 'partial':
            partial_count += 1
        else:
            missing_count += 1

        missing_required_values += group['total_keys'] - group['keys_set']
        db_override_count += sum(1 for field in group['fields'] if field['source'] == 'db')
        env_backed_count += sum(1 for field in group['fields'] if field['source'] == 'env')

        if group['name'] in HIGH_RISK_GROUP_NAMES:
            high_risk_items.append({
                'label': group['label'],
                'section_label': section_labels[group['section_id']],
                'status': group['status'],
            })

    return {
        'total_items': len(auth_providers) + len(groups),
        'configured_count': configured_count,
        'partial_count': partial_count,
        'missing_count': missing_count,
        'missing_required_values': missing_required_values,
        'db_override_count': db_override_count,
        'env_backed_count': env_backed_count,
        'high_risk_items': high_risk_items,
    }


@staff_required
def settings_dashboard(request):
    """Render the sectioned settings dashboard."""
    db_settings = dict(
        IntegrationSetting.objects.values_list('key', 'value')
    )

    groups = []
    for group_def in INTEGRATION_GROUPS:
        groups.append(_build_group_context(group_def, db_settings))

    resolved_site_base_url = site_base_url()
    auth_providers = get_all_auth_providers(
        resolved_site_base_url,
        django_settings.SOCIALACCOUNT_PROVIDERS,
    )

    sections = _build_settings_sections(groups, auth_providers)
    status_summary = _build_status_summary(groups, auth_providers)

    return render(request, 'studio/settings/dashboard.html', {
        'groups': groups,
        'auth_providers': auth_providers,
        'settings_sections': sections,
        'status_summary': status_summary,
        'site_base_url': resolved_site_base_url,
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
        if key_def.get('is_boolean'):
            # Booleans always store an explicit "true"/"false" — an unticked
            # checkbox means false, never absent. Don't delete these rows.
            value = 'true' if request.POST.get(key) == 'true' else 'false'
            IntegrationSetting.objects.update_or_create(
                key=key,
                defaults={
                    'value': value,
                    'is_secret': key_def.get('is_secret', False),
                    'group': group_name,
                    'description': key_def.get('description', ''),
                },
            )
        else:
            value = request.POST.get(key, '')
            if value == '':
                # Empty value clears the DB override and falls back to env.
                # ``key`` came from iterating the registry above, so we know
                # it is safe to delete — we never touch keys outside the
                # registry from this view.
                IntegrationSetting.objects.filter(key=key).delete()
            else:
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
    return redirect(f'/studio/settings/#{_section_id_for_group_name(group_name)}')


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


@staff_required
def settings_export(request):
    """Download all integration + auth-provider settings as a JSON file.

    Plaintext on purpose — see issue #323. ``staff_required`` is the only
    gate; the operator is trusted to handle the file like a password
    manager export.
    """
    payload = build_export()
    body = json.dumps(payload, indent=2, sort_keys=False)
    timestamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    filename = f'aishippinglabs-settings-{timestamp}.json'
    response = HttpResponse(body, content_type='application/json')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


@staff_required
@require_POST
def settings_import(request):
    """Upsert settings from a previously-exported JSON upload.

    Validation: malformed JSON and unknown ``format_version`` are rejected
    with a flash error and no DB writes. Unknown integration keys / auth
    providers are skipped and surfaced as a warning so schema drift between
    environments doesn't block a bootstrap.
    """
    upload = request.FILES.get('settings_file')
    if upload is None:
        messages.error(request, 'No file uploaded. Pick a settings JSON file and try again.')
        return redirect('studio_settings')

    try:
        raw = upload.read().decode('utf-8')
    except UnicodeDecodeError:
        messages.error(
            request,
            'Settings file must be UTF-8 encoded JSON.',
        )
        return redirect('studio_settings')

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        messages.error(
            request,
            f'Settings file is not valid JSON: {exc.msg} (line {exc.lineno}).',
        )
        return redirect('studio_settings')

    try:
        result = apply_import(payload)
    except SettingsImportError as exc:
        messages.error(request, str(exc))
        return redirect('studio_settings')

    clear_config_cache()

    summary_parts = []
    if result.integration_created or result.integration_updated:
        summary_parts.append(
            f'integrations: {result.integration_created} created, '
            f'{result.integration_updated} updated'
        )
    if result.auth_created or result.auth_updated:
        summary_parts.append(
            f'auth providers: {result.auth_created} created, '
            f'{result.auth_updated} updated'
        )
    if summary_parts:
        messages.success(
            request,
            'Settings imported (' + '; '.join(summary_parts) + ').',
        )
    else:
        messages.info(request, 'Settings file contained no recognised entries.')

    if result.skipped_integration_keys:
        messages.warning(
            request,
            'Skipped unknown integration keys: '
            + ', '.join(result.skipped_integration_keys),
        )
    if result.skipped_auth_providers:
        messages.warning(
            request,
            'Skipped unknown auth providers: '
            + ', '.join(result.skipped_auth_providers),
        )

    return redirect('studio_settings')
