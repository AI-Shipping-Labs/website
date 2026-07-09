# Sprints, Plans, and Onboarding — Journey Audit (Members + Studio)

Date: 2026-07-09
Scope: the paid-loop journey onboarding -> plan -> sprint, from both sides: member experience on the platform and operator experience in Studio. Evidence: code trace of `plans/`, `accounts/` onboarding, `studio/views/sprints.py`, `studio/views/plans.py`, notification/email services, plus screenshots of the member dashboard, onboarding chat, plan workspace, sprint pages, and Studio sprint/plan surfaces on local dev data. Companion docs from the same date: guest and Studio UX/UI audits.

## The journey as implemented

Member: pay (Main) -> onboarding (AI chat with form fallback, Basic+ gated) -> submit -> "Thanks — we'll use this to prepare your plan" + founder-call CTA -> [silence] -> staff share plan -> `plan_shared` bell + email -> member joins sprint (or was enrolled) -> plan workspace (goal edit, checkpoint toggles, drag/carry-over, week notes, cohort board, comments) -> weekly updates happen in Slack `#plan-sprints`; nightly ingest mirrors them into CRM and auto-marks checkpoints done (silently) -> sprint ends by date -> feedback questionnaire if staff distribute -> no next-sprint prompt.

Operator: create sprint (draft -> active) -> optionally link event series for calls -> enroll members (single, bulk emails, or pending plan-request inbox) -> create plan per member (empty shell) -> author content in the drag-drop autosave editor (or agents author via the bulk-import API) -> share plan (plan-ready emails, idempotent) -> assign accountability partners + intro emails -> monitor via per-plan Slack threads -> distribute end-of-sprint feedback -> AI-synthesize feedback; next-sprint AI draft per plan.

## What already works well

- The plan workspace is a genuine product: progress header, inline goal edit, checkbox toggles with markdown edit, drag reorder, "move all unfinished to next week", carry-over from the previous sprint, week notes, cohort visibility toggle, comments, markdown download.
- Studio sprint detail is job-shaped: pending plan-request inbox, roster with partner management, idempotent plan-ready and partner-intro email batches with dry-run stats, feedback distribution + AI synthesis, danger zone done right.
- The share gate (`shared_at` + `plan_shared` bell/email) cleanly separates staff drafting from member delivery.
- Slack ingest with reversible auto-apply (`AppliedProgressChange`) is a strong differentiator: members update in Slack, checkboxes update themselves.
- Onboarding chat degrades gracefully to a form; the Studio prepare page shows onboarding answers + CRM profile next to the create-plan button.

## Findings — member side

### M1. Dead air between onboarding submit and plan share (P0)
After submit the member sees a completion page and the flash "we'll use this to prepare your plan" — then nothing. No expectation of when, no visible state. The dashboard omits the plan card entirely when no plan exists (`templates/content/dashboard.html:70`), so a paying member who just invested 10 minutes in onboarding sees a dashboard with no trace of it. The "Your plan is being prepared" panel exists only on the cohort board after sprint enrollment. Fix: a dashboard "plan being prepared" card state from onboarding-submit until plan share (with expected timeline), and set the expectation on the completion page.

### M2. No product-generated weekly cadence (P0)
The six-week accountability loop has zero product touchpoints between plan share and sprint end: no week-start notification, no check-in prompt, no reminder to write the week note (the one first-class weekly input). Scheduled jobs confirm: event reminders, onboarding reminders, Slack ingest — nothing weekly for sprints. The rhythm lives entirely in Slack; members who are not Slack-active silently fall out of the loop, and that is precisely the churn cohort. Fix: weekly bell+email per sprint week ("Week 3 starts — your theme, 2 unfinished checkpoints, write last week's note"), driven off `Week.position` and the sprint window.

### M3. Slack-applied progress is silent (P1)
The nightly LLM pass auto-marks checkpoints done from Slack updates with no member notification (`crm/tasks/apply_plan_sprint_progress.py` creates no Notification/email). The best moment of the product — "you posted an update and your plan updated itself" — is invisible, and a wrong auto-apply goes unnoticed. Fix: bell notification "We marked N items done from your Slack update" with undo link.

### M4. Sprint end has no moment (P1)
Lifecycle is date-derived; the card heading flips to past tense and plan-requests close, but the member gets no recap ("you completed 9 of 12 checkpoints"), no automatic feedback prompt (staff must distribute), no carry-over nudge, no invite to the next sprint — despite carry-over and active-sprint-opportunity components already existing. Fix: sprint-end email/bell with progress recap + feedback CTA + next-sprint/carry-over CTA.

### M5. Onboarding prompt shows for members who already have a plan (P2)
Observed on the dashboard of a member with an active plan: the "Tell us a bit about you so we can build your plan" banner still renders above the plan card. Suppress once a plan exists (or once onboarding is submitted).

### M6. Members cannot add checkpoints to their own plan (product question)
Members can toggle, edit, reorder, and carry over items but cannot create a new checkpoint — "+ Add checkpoint" exists only in the Studio editor. For an "it's my plan" ownership loop this is a real limitation; deliberate curation may also be the intent. Needs a product call.

### M7. Sprints index copy and enrolled state (P2)
Cards say "Use the next step below to continue" with nothing below; a member's enrolled sprint shows no "You're in this sprint" state on the index; the member's own draft sprint (visible to them via their plan) is absent, which is consistent but combined with the copy reads confusingly.

### M8. Tier coherence between onboarding and sprints (product question)
Onboarding is Basic+ (`LEVEL_BASIC`), sprint self-join defaults to Main (`min_tier_level=LEVEL_MAIN`). A Basic member can complete onboarding ("so we can build your plan") but cannot join the sprint that plans are attached to. Decide: gate onboarding at Main, or define what a Basic member gets from onboarding.

## Findings — operator side

### S1. No weekly monitoring view (P0)
The sprint roster shows plan existence/visibility/email state but not progress. "Who is stuck, who has not updated this week" is unanswerable without opening each plan. The data exists (checkpoint `done_at`, ingested Slack threads, week notes timestamps). Fix: add progress (done/total), last-update (latest of Slack ingest / week note / checkpoint toggle), and this-week activity columns to the roster, plus a "no update this week" filter. This is the operator-side twin of M2.

### S2. Two parallel sprint lifecycles, no complete action (P1)
Stored `status` (draft/active/completed/cancelled) and the date-derived badge (upcoming/starting_soon/active/ending_soon/ended) are independent; Studio shows the stored status while members see the date badge, so operators literally see a different lifecycle than members. There is no first-class "complete sprint" action — only the edit-form dropdown (cancel/delete have buttons). Fix: show the date-derived badge in Studio alongside status, and add a complete action (or derive completed from the window and drop the stored state).

### S3. Onboarding -> first plan authoring is manual despite an AI path existing (P1)
The prepare page shows onboarding answers next to a create-plan button, but the button creates an empty shell that staff must hand-author week by week. Meanwhile an AI draft generator already exists for next-sprint plans (`NextSprintPlanDraft`). Fix: an AI first-plan draft on the prepare page seeded from onboarding answers + member profile, reviewed in the same editor before sharing — this closes the loop the external plan-from-onboarding skill currently covers, in-product.

### S4. Studio plan gaps (P2)
- `Plan.visibility` is display-only in Studio; staff cannot flip a plan private/cohort (API/member-only).
- Plans list filters by "Member ID (user pk)" — operators think in emails; use the existing user-search typeahead.
- The editor leaks an internal TODO to operators ("the editor will let you reorder via drag once the API in #433 is wired").
- The Studio create form never asks for a plan title (auto-derived); fine, but the list then shows derived titles inconsistently.
- User detail lacks a link to the member's plans (tracked in #1194).

## Notification inventory (member, entire paid loop)

Today: `plan_shared` (bell+email), event/call reminders, onboarding reminders. That is the entire product-generated cadence for the core paid experience — everything else depends on Slack habits or staff manual action. M1-M4 together define the missing lifecycle: submitted -> preparing -> shared -> week 1..N -> ending -> ended -> next.

## Related

Studio-wide findings (nav, lists, editors): `2026-07-09-studio-ux-audit.md`, `2026-07-09-studio-ui-design-audit.md`. Guest funnel: `2026-07-09-guest-ux-conversion-audit.md`. Sprint detail page redesign is separately tracked in #981.
