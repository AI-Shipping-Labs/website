from django.contrib.sessions.backends.db import SessionStore as DatabaseSessionStore

from accounts.models.session import AccountSession


def account_id_from_session_data(data):
    """Return the integer user id stored as `_auth_user_id`, or None."""
    raw = (data or {}).get("_auth_user_id")
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


class SessionStore(DatabaseSessionStore):
    """Database sessions that persist `account_id` from `_auth_user_id`."""

    @classmethod
    def get_model_class(cls):
        return AccountSession

    def create_model_instance(self, data):
        obj = super().create_model_instance(data)
        obj.account_id = account_id_from_session_data(data)
        return obj

    async def acreate_model_instance(self, data):
        obj = await super().acreate_model_instance(data)
        obj.account_id = account_id_from_session_data(data)
        return obj
