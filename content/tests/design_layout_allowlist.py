"""Shrink-only allowlist for ``content/tests/test_design_layout_lint.py``.

Each entry is ``rule_id -> {template_path: (count, reason)}``.  The count must
equal the current number of matches in that template, and the reason must name
the documented ``_docs/design-system.md`` exception (or the in-flight branch
that removes the entry).

Rules for editing this file:

* Fixing a violation means lowering the count or deleting the entry; the test
  fails on a stale entry until you do.
* Never add an entry or raise a count.  ``DESIGN_LAYOUT_CEILING`` below is the
  frozen upper bound, and the test fails when an entry is missing from it or
  exceeds it.  A genuinely new documented exception needs its own issue and a
  reviewed change to both mappings.
"""

DESIGN_LAYOUT_ALLOWLIST: dict[str, dict[str, tuple[int, str]]] = {
    "handrolled_see_all_link": {},
    "mixed_row_action_placement": {},
    "heading_action_opposite": {
        "templates/plans/_plan_body.html": (
            1,
            "Repeated plan-week card header: the week title and its "
            "'Move all unfinished tasks to next week' control are chrome inside "
            "a repeated card, which _docs/design-system.md (Spacing and Layout) "
            "exempts from the title-first header rule.",
        ),
    },
    "progress_cta_row": {},
    "row_actions_beside_meta": {},
}

# Frozen upper bound (seeded 2026-09-29 from origin/main 9505beb90). Entries may
# only disappear or shrink; see the module docstring.
DESIGN_LAYOUT_CEILING: dict[tuple[str, str], int] = {
    ("heading_action_opposite", "templates/plans/_plan_body.html"): 1,
}
