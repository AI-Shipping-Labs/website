"""Personal sprint plans for AI Shipping Labs members.

This app stores per-member sprint plans in the database. It replaces the
legacy markdown plan files that lived in a sibling repo
(``telegram-writing-assistant/articles/ai-shipping-labs/plans/``), where
each plan was a hand-curated ``YYYYMMDD_{person-name}.md`` file derived
from the canonical template ``_plan.md``.

Why a database, not the ``AI-Shipping-Labs/content`` sync pipeline:

- The drag-and-drop reorder editor (issue #434) requires high-write
  per-record state.
- Internal interview / intake notes are security-sensitive and must
  never live in any content repository.
- Most plans are bespoke per person, not reusable templates.

The canonical shareable plan structure (Summary, Plan, Focus, Timeline,
Resources, Deliverables, Accountability, Next Steps) and the internal
sections (Persona, Background, Intake, Meeting Notes, Internal
Recommendations, Internal Action Items, Sources) are documented in
``_plan.md`` in the legacy repo. Issue #432 introduces this app with
the data model and Studio admin scaffold; #433 adds API endpoints; #434
adds the drag-and-drop editor.
"""

default_app_config = 'plans.apps.PlansConfig'
