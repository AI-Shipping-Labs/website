# Buildcamp Home: learner jobs and page responsibilities

Conceptual review by two Astra agents on September 24, 2026. This is a product direction from the current course structure and the learner-facing screenshots, not a validated learner interview or a UI specification.

## The learner's questions

Home should answer, in order:

1. What should I work on in this study session? Show the current learning focus and one useful next action.
2. What do I need to build or submit? Connect the current module to its homework and the ongoing project step. Show a real deadline and submission state when available.
3. Is there a live session I should attend or catch up on? Show the next relevant session or a recent recap.
4. Am I keeping up? Distinguish the cohort's scheduled module from the learner's own position. Unchecked lessons alone do not prove the learner is behind.
5. Where can I get help or find older material? Give stable routes to help and the full course.

An imminent deadline, assigned peer review, or live event can temporarily take priority. Returning learners need a small recovery path, not a backlog of every missed item.

## Page responsibilities

| Page | Responsibility |
| --- | --- |
| Home | A small, changing selection of what matters now. Each item appears once on Home. |
| Course materials | Full module hierarchy, lessons, exercises, and optional references in their parent context. |
| Assignments | Requirements, drafts, submissions, feedback, peer reviews, and history. Group alternative submission windows under one project. |
| Live sessions | Schedule, joining details, recordings, and recaps. |
| Help | Communication and office-hours guidance. |

The current Home repeats assignments across Coming up, Assignments, and Schedule, then repeats the curriculum. The two capstone submission attempts are alternatives in the authored instructions, yet Home presents them as two unfinished projects. The conceptual direction is to remove these repeated inventories from Home and surface only the current module, related homework/project step, and next session.

Project work begins in the early modules, so Home should show the relevant project step throughout the course. It should not wait until the final submission window to introduce the project.

## Data and language constraints

- `UserCourseProgress` records completion, not last viewed activity. Do not label the first incomplete lesson as “where you left off” without another signal.
- Curriculum homework units can exist without a submission form. Home should still surface the relevant exercise.
- Completion, submissions, reviews, and cohort dates represent different states; the current core-material count is not a course-success measure.
- The two capstone attempts need an explicit relationship if the UI is to present them as one requirement with alternate windows.
- Connect live-session units to actual cohort events before promising a specific session on Home.
- Preserve parent context for optional content; a flat list of identically titled “Optional Content” items is confusing.
- Use the selected cohort's verified schedule for deadlines, not stale dates embedded in lesson prose.

Open product decisions: what formally counts as course completion, how learners choose or switch project attempts, and whether homework is mandatory or recommended. These affect Home's wording and priority.
