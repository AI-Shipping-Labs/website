"""Single source of truth for the Studio sidebar shape.

Imported by both ``studio/tests/test_sidebar.py`` (runs in the regular
PR/main CI) and ``playwright_tests/test_studio_sidebar_reorg.py`` (runs in
the scheduled full Playwright suite). Adding or removing a sidebar entry
means updating this file; the Django test fails fast if it is stale.
"""

# Total sidebar anchors rendered for a staff user / a superuser.
STAFF_ANCHOR_COUNT = 51
SUPERUSER_ANCHOR_COUNT = 54

# Labels of the People section, in render order (superuser view).
PEOPLE_LABELS = [
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
    "New user",
    "CRM",
    "AI Assistant",
]
