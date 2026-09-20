"""Database session row with a queryable user mapping (plan issue A3.2, #1692).

``django.contrib.sessions`` owns ``django_session``; AISL only adds the
``account_id`` column (``accounts.0027``) and the session store that fills it.
The model is unmanaged, so moving its app label here changes no schema.
"""

from django.contrib.sessions.base_session import AbstractBaseSession
from django.db import models


class AccountSession(AbstractBaseSession):
    """Database session row in `django_session` with a queryable user mapping."""

    account_id = models.PositiveBigIntegerField(blank=True, db_index=True, null=True)

    class Meta(AbstractBaseSession.Meta):
        db_table = "django_session"
        # `django.contrib.sessions` already owns this table; we only add account_id.
        managed = False

    @classmethod
    def get_session_store_class(cls):
        from accounts_ext.session_backend import SessionStore

        return SessionStore


__all__ = ["AccountSession"]
