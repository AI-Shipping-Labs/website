"""tiers.yaml sync dispatcher."""

import os

import yaml
from django.db import DatabaseError

from integrations.services.github_sync.common import logger

# Per-tier fields synced onto payments.Tier rows (matched by stripe_key == Tier.slug).
# yaml-wins semantics: a non-empty yaml value overwrites the DB row, even if a
# different value was set via admin. An empty/missing yaml value leaves the DB
# row alone (no destructive overwrite). tiers.yaml is the source of truth from
# this point on.
#
# Mapping: (yaml_key, tier_model_field, value_kind)
# value_kind drives type-validation:
#   'str'        -> non-empty string. Empty string / missing key -> no change.
#   'price_id'   -> non-empty string starting with 'price_'.
#   'level'      -> non-negative int.
#   'price_int'  -> positive int (>= 1). null / missing -> no change.
_TIER_FIELD_MAP = (
    ('name', 'name', 'str'),
    ('level', 'level', 'level'),
    ('price_monthly', 'price_eur_month', 'price_int'),
    ('price_annual', 'price_eur_year', 'price_int'),
    ('description', 'description', 'str'),
    ('stripe_price_id_monthly', 'stripe_price_id_monthly', 'price_id'),
    ('stripe_price_id_yearly', 'stripe_price_id_yearly', 'price_id'),
)


def _value_is_omitted(value, kind):
    """Return True when this yaml value should be treated as "no change"."""
    if value is None:
        return True
    if kind in ('str', 'price_id') and value == '':
        return True
    return False


def _validate_field(yaml_key, value, kind):
    """Return None if value is valid for kind, else a string reason for rejection.

    Omitted values (None, or '' for string-kinds) are valid and produce no
    change. This helper is only called once we know the value is present.
    """
    if kind in ('str', 'price_id'):
        if not isinstance(value, str):
            return f'{yaml_key!r} must be a string, got {type(value).__name__}'
        if kind == 'price_id' and not value.startswith('price_'):
            return f'{yaml_key!r} must start with "price_", got {value!r}'
        return None
    if kind == 'level':
        # bool is a subclass of int in Python; reject it explicitly.
        if isinstance(value, bool) or not isinstance(value, int):
            return f'{yaml_key!r} must be a non-negative integer, got {value!r}'
        if value < 0:
            return f'{yaml_key!r} must be non-negative, got {value!r}'
        return None
    if kind == 'price_int':
        if isinstance(value, bool) or not isinstance(value, int):
            return f'{yaml_key!r} must be a positive integer, got {value!r}'
        if value <= 0:
            return f'{yaml_key!r} must be a positive integer, got {value!r}'
        return None
    return f'unknown field kind {kind!r}'


def _validate_tier_rows(tiers_data):
    """Pre-flight check the full yaml payload before any Tier row write.

    Returns (ok, reason). ok=False means the dispatcher must skip all Tier row
    updates (SiteConfig writes still happen so the pricing page survives).
    Validation rules:
      - No two entries may share the same ``stripe_key``.
      - No two entries may share the same ``level`` (when both provide one).
      - Per-field type/range rules enforced via ``_validate_field``.

    Entries without ``stripe_key`` are ignored here (they cannot match a Tier
    row anyway; the per-entry pass logs a WARNING for them).
    """
    seen_keys = {}
    seen_levels = {}
    for index, entry in enumerate(tiers_data):
        if not isinstance(entry, dict):
            continue
        stripe_key = entry.get('stripe_key')
        if not stripe_key:
            continue
        if stripe_key in seen_keys:
            return (
                False,
                f'duplicate stripe_key {stripe_key!r} at entries '
                f'{seen_keys[stripe_key]} and {index}',
            )
        seen_keys[stripe_key] = index

        for yaml_key, _model_field, kind in _TIER_FIELD_MAP:
            if yaml_key not in entry:
                continue
            value = entry[yaml_key]
            if _value_is_omitted(value, kind):
                continue
            reason = _validate_field(yaml_key, value, kind)
            if reason is not None:
                return (False, f'entry {index} ({stripe_key!r}): {reason}')

        # Cross-entry uniqueness on level only after the level value itself
        # passed its per-field validation above.
        level = entry.get('level')
        if level is not None and isinstance(level, int) and not isinstance(level, bool):
            if level in seen_levels:
                return (
                    False,
                    f'duplicate level {level!r} at entries '
                    f'{seen_levels[level]} and {index}',
                )
            seen_levels[level] = index

    return (True, '')


def _sync_tier_fields(tiers_data):
    """Apply yaml-managed columns from tiers.yaml entries onto payments.Tier rows.

    For each yaml entry, look up payments.Tier by ``slug == entry['stripe_key']``
    and copy every yaml-managed field whose yaml value is present and
    non-empty. Missing or empty yaml values leave the DB column unchanged.
    Unknown ``stripe_key`` values log a WARNING and are skipped without
    raising.

    Wrapped per-entry in try/except for DatabaseError so a transient DB hiccup
    on one row does not block the rest of the sync.

    Assumes the payload already passed ``_validate_tier_rows``.
    """
    from payments.models import Tier

    for entry in tiers_data:
        if not isinstance(entry, dict):
            continue
        stripe_key = entry.get('stripe_key')
        if not stripe_key:
            continue

        try:
            tier = Tier.objects.filter(slug=stripe_key).first()
            if tier is None:
                logger.warning(
                    'tiers.yaml: stripe_key %r has no matching payments.Tier row',
                    stripe_key,
                )
                continue

            changed_fields = []
            for yaml_key, model_field, kind in _TIER_FIELD_MAP:
                if yaml_key not in entry:
                    continue
                value = entry[yaml_key]
                if _value_is_omitted(value, kind):
                    continue
                if getattr(tier, model_field) != value:
                    setattr(tier, model_field, value)
                    changed_fields.append(model_field)

            if changed_fields:
                tier.save(update_fields=changed_fields)
        except DatabaseError as e:
            logger.warning(
                'tiers.yaml: failed to sync Tier %r: %s',
                stripe_key,
                e,
            )


def _sync_tiers_yaml(repo_dir):
    """Sync tiers.yaml from the repo root into SiteConfig and payments.Tier.

    The yaml list is written verbatim into SiteConfig['tiers'] (used by the
    pricing page renderer). Additionally, for each entry that carries a
    ``stripe_key`` matching a ``payments.Tier.slug``, every yaml-managed
    column (``name``, ``level``, ``price_eur_month``, ``price_eur_year``,
    ``description``, ``stripe_price_id_monthly``, ``stripe_price_id_yearly``)
    is copied onto the Tier row. yaml-wins on collision with admin edits;
    empty/missing yaml values leave the DB row alone.

    A pre-flight validation pass runs over the full payload before any Tier
    row write. On validation failure the SiteConfig blob is still written
    (so the pricing page keeps rendering) but every Tier row is left
    untouched.
    """
    tiers_path = os.path.join(repo_dir, 'tiers.yaml')
    if not os.path.isfile(tiers_path):
        return {'synced': False, 'count': 0}

    from content.models import SiteConfig

    try:
        with open(tiers_path, encoding='utf-8') as f:
            tiers_data = yaml.safe_load(f) or []
    except OSError as e:
        logger.warning('Failed to read tiers.yaml: %s', e)
        return {'synced': False, 'count': 0}
    except yaml.YAMLError as e:
        logger.warning('Failed to parse tiers.yaml: %s', e)
        return {'synced': False, 'count': 0}

    if not isinstance(tiers_data, list):
        logger.warning(
            'Failed to sync tiers.yaml: expected a list, got %s',
            type(tiers_data).__name__,
        )
        return {'synced': False, 'count': 0}

    try:
        SiteConfig.objects.update_or_create(
            key='tiers',
            defaults={'data': tiers_data},
        )
    except DatabaseError as e:
        logger.warning('Failed to write tiers.yaml to SiteConfig: %s', e)
        return {'synced': False, 'count': 0}

    ok, reason = _validate_tier_rows(tiers_data)
    if not ok:
        logger.warning(
            'tiers.yaml: validation failed, skipping Tier row updates (%s)',
            reason,
        )
    else:
        _sync_tier_fields(tiers_data)

    logger.info('tiers.yaml synced to SiteConfig (%d tiers)', len(tiers_data))
    return {'synced': True, 'count': len(tiers_data)}
