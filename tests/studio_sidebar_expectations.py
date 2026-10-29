"""Shared Studio sidebar expectations for both test layers (issue #2020).

``templates/studio/base.html`` owns the rendered sidebar; this module owns
the contract that both test layers assert against it:

- the ordered People link labels, with ``Bookings`` (added for #2018)
  grouped immediately after ``Call requests`` and before ``Imports``;
- the total number of anchors rendered inside ``#studio-sidebar-nav`` for
  staff (non-superuser) and superuser operators, counting the
  superuser-only entries (``New user``, ``API tokens``, ``API keys``).

Both ``studio/tests/test_sidebar.py`` (the rendered Django sidebar guard
that runs in regular main CI) and
``playwright_tests/test_studio_sidebar_reorg.py`` (the scheduled
sidebar-reorg suite) import these constants. Adding a sidebar entry to
the template without updating this module in the same change fails the
Django guard on regular main CI instead of waiting for the next
scheduled Playwright run.
"""

EXPECTED_ANCHOR_COUNT_STAFF = 51
EXPECTED_ANCHOR_COUNT_SUPERUSER = 54

# Ordered link labels rendered inside the People section
# (``#studio-section-people``) for a staff operator who is not a
# superuser. ``Bookings`` must stay grouped immediately after
# ``Call requests`` and before ``Imports`` (issues #2018 and #2020).
EXPECTED_PEOPLE_LINK_LABELS_STAFF = [
    "Users",
    "Call profiles",
    "Call requests",
    "Bookings",
    "Imports",
    "Instructors",
    "Tier overrides",
    "Tags",
    "Merge accounts",
    "Payment mismatches",
    "Subscription reconciliation",
    "CRM",
    "AI Assistant",
]

# Superusers additionally see the ``New user`` link directly before
# ``CRM``, exactly where the template renders its superuser-only block.
_CRM_INDEX = EXPECTED_PEOPLE_LINK_LABELS_STAFF.index("CRM")
EXPECTED_PEOPLE_LINK_LABELS_SUPERUSER = (
    EXPECTED_PEOPLE_LINK_LABELS_STAFF[:_CRM_INDEX]
    + ["New user"]
    + EXPECTED_PEOPLE_LINK_LABELS_STAFF[_CRM_INDEX:]
)
