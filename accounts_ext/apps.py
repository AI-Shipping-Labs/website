from django.apps import AppConfig


class AccountsExtConfig(AppConfig):
    """Site-owned extension models that the shared accounts donor does not own.

    Plan issue A3.2 (#1692 / #1656): when AISL adopts
    ``community_base.accounts`` the shared ``User`` model carries only the
    C3.1 field table. Everything AISL keeps on top of that lives here so the
    swap in A3.3 can delete the local ``accounts`` app without taking contact
    tags or the queryable session mapping with it.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "accounts_ext"
    verbose_name = "Accounts extensions"

    def ready(self):
        from accounts_ext import signals  # noqa: F401
