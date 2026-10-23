"""Recover one retained legacy secret without declaring or enabling its consumer."""

from community_base.config.crypto import decrypt, encrypt
from community_base.config.models import Setting, SettingChange
from community_base.config.service import runtime
from community_base.kernel.redaction import REDACTED
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction

LEGACY_ZOOM_KEY = "ZOOM_WEBHOOK_SECRET_TOKEN"


def _matches_secret(ciphertext, replacement):
    try:
        return decrypt(ciphertext) == replacement
    except (ImproperlyConfigured, ValueError, TypeError):
        # An undecryptable previous value is precisely the supported recovery case.
        return False


def recover_legacy_zoom_secret(replacement, *, actor_ref):
    if not isinstance(replacement, str) or not replacement.strip():
        raise ValueError("A nonempty secret string is required.")
    with transaction.atomic():
        row = Setting.objects.select_for_update().get(key=LEGACY_ZOOM_KEY)
        if _matches_secret(row.value, replacement):
            return {"recovered": True, "decryptable": True}
        row.value = encrypt(replacement)
        row.save(update_fields=["value", "updated_at"])
        row.refresh_from_db(fields=["value"])
        if not _matches_secret(row.value, replacement):
            raise ImproperlyConfigured("Secret recovery verification failed.")
        SettingChange.objects.create(
            setting_key=LEGACY_ZOOM_KEY, old_value=REDACTED, new_value=REDACTED,
            old_value_redacted=True, new_value_redacted=True,
            actor_ref=actor_ref, reason="Recovered legacy Zoom secret with active key.",
        )
        runtime.reset()
        transaction.on_commit(runtime.publish)
    return {"recovered": True, "decryptable": True}
