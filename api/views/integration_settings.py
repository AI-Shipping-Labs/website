"""Integration settings API (issues #633, #640).

Single endpoint, ``/api/integrations/settings``, that surfaces the
``integrations.settings_registry.INTEGRATION_GROUPS`` allowlist:

- ``GET`` (issue #640) lists every registered key with its group, label,
  description, ``is_secret``/``is_boolean`` flags, a ``configured``
  boolean, and a ``source`` enum (``db`` / ``env`` / ``django_settings``
  / ``default`` / ``null``). The response NEVER contains the actual
  value of any setting — operators learn which keys are set and where
  the value resolves from, without exposing the value itself.
- ``POST`` (issue #633) mutates rows in
  ``integrations.models.IntegrationSetting`` for keys in the same
  allowlist. The response NEVER echoes stored or submitted values, key
  names, or the literal substring ``"value"``.

There is no ``DELETE`` / ``PUT`` / ``PATCH`` method — clearing happens
as a side effect of a POST with an empty-string value, matching Studio
parity.

Auth model: ``Authorization: Token <key>`` via ``accounts.auth.token_required``
scoped to staff users (mirrors every other operator API in this codebase, e.g.
``api/views/sync_sources.py``, ``api/views/contacts.py``).

After every successful write the view calls
``integrations.config.clear_config_cache()`` exactly once so other gunicorn
workers / qcluster processes see the new value on their next
``get_config()`` call.

NOTE: ``S3_ENABLED`` is currently an env var read once at process startup in
``website/settings.py`` — it is NOT in ``INTEGRATION_GROUPS`` and therefore
cannot be written through this endpoint. A separate ticket must promote it
into the registry first.
"""

import json

from django.db import transaction
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from accounts.auth import token_required
from api.safety import error_response
from api.utils import require_methods
from integrations.config import clear_config_cache, resolve_source
from integrations.models import IntegrationSetting
from integrations.settings_registry import INTEGRATION_GROUPS


def _build_registry_index():
    """Return ``{key: key_def}`` flattened across all registry groups.

    Building this at request time (rather than module import) keeps the
    registry the single source of truth — if a new key is added to
    ``settings_registry.py`` the API picks it up without code changes
    here.
    """
    index = {}
    for group in INTEGRATION_GROUPS:
        for key_def in group['keys']:
            index[key_def['key']] = {
                'key_def': key_def,
                'group': group['name'],
            }
    return index


def _coerce_boolean_value(raw_value):
    """Normalise a boolean-key value to the strings ``"true"``/``"false"``.

    Accepts the JSON literals ``true``/``false`` AND the strings
    ``"true"``/``"false"`` (case-insensitive) — matching the convention
    already used by ``studio.views.settings.settings_save_group`` (which
    persists booleans as the literal strings).

    Returns ``(stored_value, ok)`` where ``ok`` is False when the value
    can't be coerced.
    """
    if isinstance(raw_value, bool):
        return ('true' if raw_value else 'false'), True
    if isinstance(raw_value, str):
        normalised = raw_value.strip().lower()
        if normalised == 'true':
            return 'true', True
        if normalised == 'false':
            return 'false', True
    return None, False


@token_required
@csrf_exempt
@require_methods("GET", "POST")
def integration_settings(request):
    """Dispatch GET (list, no values — issue #640) and POST (write — #633).

    Method-based dispatch keeps the URL stable at
    ``/api/integrations/settings`` (Studio still owns its own UI).
    Anything other than GET/POST returns 405 via ``require_methods``.
    """
    if request.method == "GET":
        return _integration_settings_list(request)
    return _integration_settings_set(request)


def _integration_settings_list(request):
    """Return one entry per registered key, NEVER including any value.

    Response shape::

        {"settings": [
            {
              "key": "STRIPE_SECRET_KEY",
              "group": "stripe",
              "label": "Stripe",
              "description": "...",
              "is_secret": true,
              "is_boolean": false,
              "configured": true,
              "source": "db"  # or env / django_settings / default / null
            },
            ...
        ]}

    Ordering follows ``INTEGRATION_GROUPS`` (group order, then key
    order within each group). ``source`` is resolved by
    ``integrations.config.resolve_source`` which probes each layer
    separately — see that function for the no-value-leakage contract.
    """
    entries = []
    for group in INTEGRATION_GROUPS:
        group_name = group['name']
        group_label = group['label']
        for key_def in group['keys']:
            key = key_def['key']
            registry_default = key_def.get('default', '')
            source = resolve_source(key, registry_default=registry_default)
            entries.append({
                'key': key,
                'group': group_name,
                'label': group_label,
                'description': key_def.get('description', ''),
                'is_secret': key_def.get('is_secret', False),
                'is_boolean': key_def.get('is_boolean', False),
                'configured': source is not None,
                'source': source,
            })
    return JsonResponse({'settings': entries})


def _integration_settings_set(request):
    """Write-only batch update of ``IntegrationSetting`` rows.

    Request body shape::

        {"updates": [{"key": "CONTENT_CDN_BASE", "value": "https://..."}, ...]}

    Behaviour:

    - All keys must be present in ``INTEGRATION_GROUPS``. Any unknown key
      makes the entire batch fail with ``400 invalid_key`` — no row is
      written until every key has been validated (all-or-nothing).
    - Boolean keys (``is_boolean: True`` in the registry) accept the JSON
      literals ``true``/``false`` AND the strings ``"true"``/``"false"``;
      both forms persist as the strings ``"true"``/``"false"``.
    - Empty-string value on a NON-boolean key clears the DB override
      (deletes the row), matching Studio parity. This is a side effect
      of a write, not a separate delete endpoint.
    - After any successful write, ``clear_config_cache()`` is called
      exactly once so other workers see the new values.

    Response on success::

        {"status": "ok", "updated": N}

    Where ``N`` is the integer count of keys touched (created, updated,
    or cleared). The response NEVER echoes key names, values, the
    previous value, or the literal substring ``"value"``.
    """
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return error_response(
            "Body must be valid JSON",
            "invalid_json",
        )

    if not isinstance(data, dict):
        return error_response(
            "Body must be a JSON object",
            "invalid_type",
            details={"field": "body", "expected": "object"},
        )

    updates = data.get("updates")
    if not isinstance(updates, list):
        return error_response(
            "updates must be a list of {key, value} objects",
            "invalid_type",
            details={"field": "updates", "expected": "list"},
        )

    registry = _build_registry_index()

    # Phase 1: validate every entry before touching the DB. This is the
    # all-or-nothing guarantee — invalid_key and invalid_value cannot
    # leave a half-applied batch behind.
    normalised = []
    invalid_keys = []
    for index, entry in enumerate(updates):
        if not isinstance(entry, dict):
            return error_response(
                "Each update entry must be an object with key and value",
                "invalid_type",
                details={"field": f"updates[{index}]", "expected": "object"},
            )
        key = entry.get("key")
        if not isinstance(key, str) or not key:
            return error_response(
                "Each update entry must include a non-empty 'key' string",
                "invalid_type",
                details={"field": f"updates[{index}].key"},
            )
        raw_value = entry.get("value")

        if key not in registry:
            invalid_keys.append(key)
            continue

        key_def = registry[key]['key_def']
        group_name = registry[key]['group']
        is_boolean = key_def.get('is_boolean', False)

        if is_boolean:
            coerced, ok = _coerce_boolean_value(raw_value)
            if not ok:
                # Do NOT include the offending value in the response —
                # see test_post_invalid_key_response_does_not_echo_value
                # for the contract.
                return error_response(
                    "Boolean key requires true/false",
                    "invalid_value",
                    details={"key": key},
                )
            normalised.append({
                'key': key,
                'stored_value': coerced,
                'is_boolean': True,
                'key_def': key_def,
                'group': group_name,
            })
        else:
            if raw_value is None:
                raw_value = ''
            if not isinstance(raw_value, str):
                return error_response(
                    "Value must be a string or null",
                    "invalid_value",
                    details={"key": key},
                )
            normalised.append({
                'key': key,
                'stored_value': raw_value,
                'is_boolean': False,
                'key_def': key_def,
                'group': group_name,
            })

    if invalid_keys:
        return error_response(
            "One or more keys are not in the integration registry",
            "invalid_key",
            details={"invalid_keys": invalid_keys},
        )

    # Phase 2: apply all writes inside a transaction. Studio uses the
    # same update_or_create / delete-on-empty-string pattern; we mirror
    # it here so the two surfaces stay consistent.
    updated = 0
    with transaction.atomic():
        for item in normalised:
            key = item['key']
            stored_value = item['stored_value']
            key_def = item['key_def']
            group_name = item['group']
            if not item['is_boolean'] and stored_value == '':
                # Empty-string on a non-boolean clears the override row.
                deleted, _ = IntegrationSetting.objects.filter(key=key).delete()
                if deleted:
                    updated += 1
                continue
            IntegrationSetting.objects.update_or_create(
                key=key,
                defaults={
                    'value': stored_value,
                    'is_secret': key_def.get('is_secret', False),
                    'group': group_name,
                    'description': key_def.get('description', ''),
                },
            )
            updated += 1

    clear_config_cache()

    return JsonResponse({"status": "ok", "updated": updated})
