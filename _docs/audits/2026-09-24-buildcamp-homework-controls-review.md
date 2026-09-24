# Buildcamp homework controls and navigation review

Date: 2026-09-24

## Findings

### The reader showed two competing completion rows

The stepper had its own save and continue actions, while the general course reader also rendered Previous, Mark as completed, and Next. That made the homework state unclear and allowed completion without submitting answers.

### Sidebar navigation could leave a dirty answer behind

Autosave guarded links inside the step panel only. A learner could change an answer and choose another unit in the sidebar before the newest value reached the server. The same listener also intercepted modifier and new-tab clicks.

### Submission state depended on a short-lived redirect receipt

The success message disappeared after the receipt expired or when the learner opened the assignment from another route. A successful submission did not mark the course unit complete.

### Homework hierarchy and labels obscured the next step

The assignment and capstone were wrapped by a `Homework` disclosure, and the nested step list numbered Introduction and Review as if they were questions. The stepper also repeated the `Homework` heading beneath the unit title.

## Changes made

- Stepped homework now has one state-specific action row. The generic course completion footer is omitted on this page.
- Same-tab reader and step links flush dirty values before navigation. Modified clicks, downloads, and non-`_self` targets retain browser behavior. A save failure or revision conflict keeps the learner on the page and announces what to do next.
- The saved `Submission` row now drives the submitted state and opens the learner at Review when no step is specified.
- A successful server-side submission marks the course unit complete. Visits and draft saves do not.
- Assignment and capstone rows are presented as siblings; the course module hierarchy itself is unchanged. Only question stops receive numeric badges.
- The duplicate Homework card heading is removed, and step and due-date metadata share a compact line.

## Verification

- `uv run python manage.py test content.tests.test_homework_step_reader --parallel 2` -> 17 passed.
- `uv run python manage.py test content.tests.test_homework_submission_view.HomeworkDeadlineTest --parallel 2` -> 5 passed, including the closed no-deadline fallback copy.
- `uv run --with-editable /home/alexey/git/community-base pytest -q playwright_tests/test_homework_steps_1778.py` -> 4 passed; the browser suite exercised the current shared-package code.
- `uv run pytest tests/homework_steps -q` in `community-base` -> 16 passed.
- `uv run ruff check community_base/homework_steps/views.py tests/homework_steps/test_flow.py` and `uv run ruff format --check community_base/homework_steps/views.py tests/homework_steps/test_flow.py` -> passed.
- `uv run python testproject/manage.py check`, `uv run python testproject/manage.py makemigrations --check --dry-run`, and `uv run pytest tests/test_boundaries.py -q` -> passed.
- `uv run pytest courses/tests/test_unit_pages.py courses/tests/test_homework_page_design.py -q` in a detached DTC worktree linked to the local package -> 56 passed, 20 subtests passed.
- `git diff --check` in AISL and `community-base` -> passed.

## Visual evidence

The local browser run captured the introduction, desktop question, mobile question, and review states:

- `.tmp/screenshots/homework-stepper/final-homework-reader.png` — current Buildcamp question view with capstone as a first-level sibling and no generic completion footer.
- `.tmp/astra-homework-intro-desktop.png`
- `.tmp/astra-homework-q1-desktop.png`
- `.tmp/astra-homework-q1-mobile.png`
- `.tmp/astra-homework-review-desktop.png`
