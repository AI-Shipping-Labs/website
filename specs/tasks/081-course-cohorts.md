# 081 - Course Cohorts

**Status:** pending
**Tags:** `courses`, `admin`
**GitHub Issue:** [#81](https://github.com/AI-Shipping-Labs/website/issues/81)
**Specs:** 05 (cohorts section)
**Depends on:** [079-course-unit-pages](079-course-unit-pages.md)
**Blocks:** —

## Scope

- Cohort model: course FK, name, start_date, end_date, is_active, max_participants
- CohortEnrollment model: cohort FK, user FK, enrolled_at
- Course detail page shows active cohorts: "Next cohort: {name}, starts {date}"
- Users with required tier can enroll; enrollment capped at max_participants
- Optional drip schedule: available_after_days on Unit, relative to cohort start_date

## Acceptance Criteria

- [ ] Cohort model with fields: course FK, name, start_date, end_date, is_active, max_participants
- [ ] CohortEnrollment model with fields: cohort FK, user FK, enrolled_at; unique together (cohort, user)
- [ ] Course detail page shows active cohorts: "Next cohort: {name}, starts {date}"
- [ ] Users with tier.level >= course.required_level can enroll in a cohort
- [ ] Enrollment capped at max_participants; "Cohort is full" shown when capacity reached
- [ ] Optional drip: Unit with available_after_days is locked until cohort.start_date + available_after_days has passed
- [ ] Admin can create/edit/delete cohorts for any course
