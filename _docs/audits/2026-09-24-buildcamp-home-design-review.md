# Buildcamp Home: design review

Astra review of the September 24, 2026 learner screenshots and the current Home template. This records visual and information-architecture feedback; it is not a new page design.

## Findings

1. Home presents the same work in several sections. The opening “Coming up” panel, the Assignments section, and the Schedule section can all show the same project deadline. The full curriculum appears again under “Your course.” The learner must compare several versions of the same information to understand what to do.
2. The page gives collection views the same weight as the next action. Large lists and repeated cards obscure the current module, current homework, and next live session.
3. The two capstone submission attempts read as separate pending projects. They are alternative windows in the authored course instructions. That is a misleading status presentation, not just a visual repetition.
4. Flattened “Optional Content” rows lose their module context and become hard to distinguish.
5. The top navigation implies Home, Schedule, Assignments, and Help are destinations, but the last three are anchors into a long Home page. Their content then competes with Home’s purpose. “Course materials” is a separate destination while the complete course map is also rendered on Home.

## Direction for a design pass

- Give Home one clear focus: the current learning context, its practical work, and the next relevant live session.
- Keep a single primary action for the current study session. Use compact secondary links for the full materials, assignment history, session schedule, and help.
- Remove the “Coming up” inventory and the full Assignments and Schedule inventories from Home. A time-sensitive item may still appear once next to the action it affects.
- Treat a capstone project as one project with alternative submission windows; do not render attempts as separate obligations.
- Keep module and optional-content context in Course materials rather than repeating the full hierarchy on Home.
- Use the existing page shell, type scale, spacing, card treatment, and interaction patterns from `_docs/design-system.md` when the UI is redesigned.

## Proposed sequence

1. Confirm the data relationships and statuses that Home can state truthfully: cohort module, learner position, homework, project attempts, and session association.
2. Replace the repeated inventories with a small selection for the current moment.
3. Make detailed Assignments and Live sessions destinations, or keep their data in their existing course pages until those destinations exist. Avoid anchor tabs that imply independent pages.
4. Check four learner states in rendered pages: just starting, active, behind the cohort, and returning after a break. Inspect desktop and mobile, then refine wording and density.

The companion learner-jobs analysis is in `2026-09-24-buildcamp-home-jtbd.md`.
