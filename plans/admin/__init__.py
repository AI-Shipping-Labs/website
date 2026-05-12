"""Django admin registrations for the plans app.

Plans were previously not registered in the Django admin -- staff used
Studio for plan management. Issue #585 adds a staff-only
"Open in Django admin" link from member-facing plan pages so staff can
inspect raw rows quickly; that link points at
``/admin/plans/plan/<plan_id>/change/``, which requires the model to
be registered.
"""

from .plan import *  # noqa: F401,F403
