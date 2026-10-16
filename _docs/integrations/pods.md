# Pods

Pods are small groups of members inside a community activity (issue #1918).
Phase 1 attaches pods to dated course cohorts: enrolled cohort members see a
`Study groups` tab between `Syllabus` and `Live sessions` on course Home,
start a pod of one, send join requests, join a
waiting list when a pod is full, add weekly availability in their own
timezone, and see suggested meeting times. Since issue #1919 they turn a
suggested time into an agreed pod meeting (propose, confirm, `Can't make it`
with the next best time, move, cancel, held, a weekly series and one pod call
link), with bell notifications and a reminder before each meeting. Staff
manage pods in Studio (`Planning -> Pods`) and through the staff API
(`_docs/api.md`, `Pods API`). Since issue #1921 a community sprint is a second
pod activity with the same member experience; see [Sprint pods](#sprint-pods).

Every key below is set in Studio (`Operations -> Settings`, `Pods` group). An
environment variable with the same name is an optional fallback.

## PODS_COURSE_SLUGS

Comma-separated course slugs whose dated-cohort members and staff viewing that
cohort see the `Study groups` tab, for example `ai-buildcamp`. Course-only:
sprint pods are switched on per sprint with
`Sprint.pods_enabled` (see [Sprint pods](#sprint-pods)).

- Type: string.
- Default: empty.
- When empty, or when a course is not listed: every member pod URL for that
  course returns 404 and course Home renders no `Study groups` tab.
- Studio and the staff API work regardless, so pods can be prepared before a
  course is switched on.

## PODS_DEFAULT_MAX_MEMBERS

Default size limit for a new pod.

- Type: integer, 1-12.
- Default: `4`.
- Applies to the member `Start a pod` form, Studio create and `POST /api/pods`
  when no `max_members` is given.

## PODS_DEFAULT_MEETING_COUNT

Default number of meetings for a new pod.

- Type: integer, 1-20.
- Default: `1`.

## PODS_DEFAULT_MEETING_MINUTES

Default meeting length for a new pod.

- Type: integer, one of `30`, `45`, `60`, `90`.
- Default: `60`.
- The meeting length sizes the suggested meeting times on the pod page.

## PODS_MAX_OPEN_REQUESTS_PER_MEMBER

How many open (`pending` or `waitlisted`) join requests one member may hold
across the pods of one cohort.

- Type: integer.
- Default: `3`.
- Over the limit the member sees `You already have 3 open requests in this
  cohort. Withdraw one to request another pod.` Requests in other cohorts do
  not count.

## PODS_MAX_CREATED_PER_MEMBER

How many non-archived pods one member may start in one cohort.

- Type: integer.
- Default: `2`.
- Over the limit the member sees `You can start up to 2 pods in this cohort.`
  Staff-created pods do not count.

## PODS_SUGGESTION_HORIZON_DAYS

How many days ahead suggested meeting times look.

- Type: integer.
- Default: `14`.
- Suggestions start 12 hours from now and end this many days from now.
- The next best time after `Can't make it` does not use this horizon: it
  looks within a week either side of the meeting (see Pod meetings).

## PODS_SUGGESTION_COUNT

Maximum number of suggested meeting times shown for a pod.

- Type: integer.
- Default: `5`.

## PODS_STALE_REQUEST_DAYS

Studio highlight for unanswered join requests.

- Type: integer.
- Default: `5`.
- On `/studio/pods/`, a pod whose oldest pending request is older than this many
  days shows its age in an amber pill so staff can approve or decline as an
  override. Nothing is approved automatically.

Pending requests only: since issue #1927 the Studio `stale` badge looks at
the oldest `pending` request. A waitlisted request is never stale, because
nobody can approve it until a seat opens. The same threshold drives the daily
staff Slack alert (`PODS_STALE_REQUEST_ALERT_ENABLED`).

## PODS_REREQUEST_COOLDOWN_DAYS

How long a student waits after a declined request before asking the same pod
again.

- Type: integer, `0` or more. Invalid values fall back to `14`.
- Default: `14`.
- Counted per pod and per student, from the decline's `decided_at`. Withdrawn
  and cancelled requests do not count.
- First decline: the pod page hides the request form and shows `Your request
  was not accepted. You can ask again on Oct 23.` After the cooldown the form
  comes back with `Your last request was not accepted. You can ask once more.`
- Second decline on the same pod: final. The student sees `This pod declined
  your request twice, so you can't ask to join it again.` with a `Browse other
  pods` link. Staff can still add the student in Studio or through the API.
- `0` allows an immediate second request; the second decline is still final.
- A refused request creates no request and no owner notification.

## PODS_STALE_REQUEST_ALERT_ENABLED

Kill switch for the daily staff Slack alert about stale pod requests.

- Type: boolean.
- Default: `true`.
- Job: `pods-stale-request-alert`, daily at 09:00 UTC
  (`pods.tasks.stale_requests.send_stale_request_alert`).
- Stale request: `pending`, pod not archived, created more than
  `PODS_STALE_REQUEST_DAYS` days ago.
- One message per run, only when at least one stale request has not been
  announced yet (`PodJoinRequest.stale_alerted_at` is empty). Up to 10 request
  lines, then `and N more`; already announced requests still waiting are
  counted in a `N earlier requests are still waiting.` line. No message on
  days with nothing new.
- Channel: `STAFF_COMMENT_NOTIFY_CHANNEL_ID`, falling back to
  `STAFF_SIGNUP_NOTIFY_CHANNEL_ID`. Also needs `SLACK_ENABLED` and
  `SLACK_BOT_TOKEN`. With any of these missing, or on a Slack error, nothing
  is marked as announced and the job retries the next day.
- Since issue #1935 the same daily message also carries stuck pod meeting
  proposals (`PODS_STUCK_PROPOSAL_ALERT_ENABLED`). This key gates only the
  requests section: with it off, stuck proposals still post.

## PODS_STUCK_PROPOSAL_HOURS

How long a pod meeting proposal may wait for answers before it is stuck
(issue #1935), or half the time to the meeting when that is shorter (issue
#1938).

- Type: integer, 1-336. Invalid or out-of-range values fall back to `48`.
- Default: `48`.
- Stuck proposal: `proposed`, start still in the future and not expired, pod
  not archived, and it has waited more than its threshold since it was
  proposed or last moved (`moved_at`, else `created_at`; a move resets the
  answers, so the clock restarts). A weekly proposal counts once, on its first
  meeting, where the answers live.
- Threshold (halfway rule): the smaller of this many hours and half the time
  from when it was proposed or last moved to its start. With `48`: proposed
  10 days ahead is stuck after more than 48 hours, 72 hours ahead after more
  than 36 hours, 20 hours ahead after more than 10 hours; a move to a time 30
  hours away restarts the clock and is stuck after more than 15 hours. The
  daily alert then flags a short-notice proposal while there is still time to
  nudge the pod.
- Can't be confirmed: a live proposal whose current members minus the
  `Can't make it` answers fall below the required attendance (the same rule
  as the member page, issue #1937). It is a label only and does not change the
  stuck clock; it clears when a member switches back or the proposal moves.
- Waiting on: pod members with no answer on that meeting, the same rule as
  the member-side `Waiting on` line.
- Studio: the pod's Meetings table shows `No answer: Mike K.` under a live
  proposal's status, a `Can't be confirmed` badge on one that can no longer
  be confirmed, and a `Stuck` badge on a stuck one. `/studio/pods/` shows a
  `Stuck proposal` badge on pods with a stuck proposal, and a pod with no
  scheduled meeting but live proposals reads `1 proposal waiting` /
  `2 proposals waiting` (a weekly proposal counts once) instead of
  `No meeting planned`.
- Staff API: each meeting has `waiting_on` (emails), `stuck`, `confirmable`
  and `stuck_alerted_at`. `confirmable` is `true`/`false` on a live proposal
  head and `null` on every other meeting.

## PODS_STUCK_PROPOSAL_ALERT_ENABLED

Kill switch for the stuck proposals section of the daily pods staff Slack
message.

- Type: boolean.
- Default: `true`.
- Job: the same `pods-stale-request-alert` (daily at 09:00 UTC). One message
  per run covers stale requests and stuck proposals; it posts only when at
  least one of them is new.
- Section: `*3 pod proposals have waited more than 48 hours or half the time
  to the meeting*`, then up to 10
  lines, oldest first, then `and N more`:
  `Pod name (Course, Cohort) - Tue Oct 20, 18:00 UTC, proposed by Anna K. 3
  days ago - waiting on Mike K. (mike@example.com, last sign-in 12 days ago)`.
  A moved proposal reads `moved by`. Members who answered `Can't make it`
  follow as `- can't make it: Raj P.`; a proposal that can no longer be
  confirmed ends with `- can't be confirmed, needs another time`. A member
  who never signed in reads `never signed in`. A line longer than Slack's
  3000-character limit drops whole names from the end of its `waiting on`
  (then `can't make it`) list, ending it with `and N more`. Already announced proposals still stuck are counted in
  `N earlier proposals are still waiting.`
- Each stuck proposal is announced once: `PodMeeting.stuck_alerted_at` is set
  on its first meeting after Slack accepts the post, and cleared when the
  proposal is moved, so a moved proposal that gets stuck again is announced
  again.
- Fallback text: `Stale pod requests need attention`, `Stuck pod proposals
  need attention`, or `Pod requests and proposals need attention`.
- Same channel and Slack gates as `PODS_STALE_REQUEST_ALERT_ENABLED`. Off
  drops only this section; stale requests still post.

## PODS_MEETING_REMINDER_HOURS

How many hours before a scheduled pod meeting members get the bell reminder
(issue #1919).

- Type: integer, 1-72. Invalid values fall back to `24`.
- Default: `24`.
- Job: `pods-meeting-reminders`, hourly at minute 0
  (`pods.tasks.meeting_reminders.send_meeting_reminders`).
- Sends `Reminder: <pod name> meets Tue Oct 14, 18:00 Europe/Berlin` (in each
  member's timezone) to every member of a `scheduled` meeting that starts
  within this many hours, except members who answered `Can't make it`.
- Each meeting is reminded once (`PodMeeting.reminder_sent_at`); running the
  job twice in an hour sends nothing new. Moving a meeting clears the mark, so
  a moved meeting is reminded again.
- Reminders are on-site only (the bell and the dashboard `Your week` list).
  There is no email and no Slack message.

## PODS_CHAT_ENABLED

Kill switch for the pod chat (issue #1920).

- Type: boolean.
- Default: `true`.
- Off: no `Chat` section on the member pod page, no `N new messages` line on
  the `Study groups` tab, and `POST /courses/<slug>/home/pods/<pod_id>/messages`
  returns 404.
- Studio (`/studio/pods/<id>/#chat`) and the staff API
  (`GET /api/pods/<id>/messages`, hide and restore) keep working, so staff can
  still read and moderate.

## PODS_CHAT_MESSAGES_PER_HOUR

How many pod chat messages one member may post in a rolling 60 minutes
(issue #1920).

- Type: integer, 1-500. Invalid or out-of-range values (for example `abc` or
  `0`) fall back to `30`.
- Default: `30`.
- Counted per member across all pods, including messages staff later hid.
- Over the limit: `You are sending messages quickly. Try again in a few
  minutes.` and the draft stays in the message box.

## PODS_FIRST_MEETING_NUDGE_DAYS

How many days after a pod's second member joins before the members see a
`Plan your first meeting with <pod name>` row on the dashboard (issue #1928).

- Type: integer, 0-30. Invalid or out-of-range values fall back to `3`.
- Default: `3`. `0` shows the row as soon as the second member joins.
- Where: the dashboard `Your week` list, after the dated rows (events and pod
  meetings) and before the Book Club deadline row. Eyebrow `Pod`, body `No
  meeting planned yet. Pick a time from your pod's suggested times.` The row
  links to the pod page at `#suggested-times`. `Your week` shows even when
  this row is its only content.
- A pod gets the row, for each of its current members, when all of these
  hold:
  - The pod is not archived and its course is in `PODS_COURSE_SLUGS`.
  - The pod has 2 or more current members, and the second earliest current
    membership joined at least this many days ago.
  - `Pod.slack_channel_url` is empty.
  - The pod has no live meeting: no proposed (not expired), scheduled or held
    meeting. Cancelled meetings and expired `Not confirmed` proposals do not
    count, so a pod whose only proposal expired is nudged again.
- Several eligible pods give one row each, oldest second join first.
- The row goes away on the next dashboard load once anyone proposes a time,
  staff schedules a meeting, a member saves the Slack channel link, the viewer
  leaves, the pod is archived, or the pod drops below 2 members.
- On-site only: no email, Slack message or bell notification, and no nudge on
  the pod page itself.

## PODS_ARTIFACTS_ENABLED

Kill switch for pod meeting notes and transcripts (issue #1922).

- Type: boolean.
- Default: `true`.
- Off: no `Notes and transcripts` section on the member pod page, no
  recording tip and no `Add notes` action on meeting rows (a past scheduled
  meeting offers `Mark as held` as its primary action again), and every member
  notes URL (`.../pods/<pod_id>/notes/new`, `.../notes/<artifact_id>` and
  `.../notes/<artifact_id>/delete`) returns 404.
- Studio (`/studio/pods/<id>/#notes`) and the staff API
  (`GET /api/pods/<id>/artifacts`) keep working, so staff can still read and
  delete what members added.

## PODS_ARTIFACT_MAX_KB

Largest pod note or transcript a member may save, in KB of UTF-8 text
(issue #1922).

- Type: integer, 1-2048. Invalid or out-of-range values (for example `abc`,
  `0` or `4096`) fall back to `500`.
- Default: `500`.
- Applies to pasted text and to an uploaded file alike. Over the limit the
  form says `Notes can be up to 500 KB. Trim the text or split it into two.`
  (the number follows the setting) and keeps the typed values.

## Sprint pods

Issue #1921 makes a community sprint a second pod activity. One helper,
`pods/services/activity.py` (`PodActivity`), answers for either activity who
is a participant, who may view, whether member pages are on, the member URLs,
the display label and the noun used in copy (`cohort` or `sprint`). Cohort pod
URLs and copy are unchanged.

### Enablement

- `Sprint.pods_enabled` (Studio sprint form: `Show pods to enrolled members`;
  API: `pods_enabled` on `/api/sprints` and `/api/sprints/<slug>`). Default off.
- It is a per-sprint data field, not an IntegrationSetting, because sprints are
  Studio-sourced records (same reasoning as `Sprint.min_tier_level`).
- Off: every `/sprints/<slug>/pods...` URL returns 404 for everyone, staff
  included (staff use Studio), the sprint page has no `Pods` section, and the
  first-meeting nudge and dashboard pod rows skip the sprint's pods.

### URLs

All under the sprint, named `sprint_<course route name>`:

| URL | Purpose |
|---|---|
| `/sprints/<slug>/pods` | Pods list |
| `/sprints/<slug>/pods/new` | Start a pod |
| `/sprints/<slug>/pods/availability` | Availability editor (the same single profile as cohort pods) |
| `/sprints/<slug>/pods/<id>` | Pod page |
| `/sprints/<slug>/pods/<id>/...` | `notes/new`, `notes/<id>`, `notes/<id>/delete` (issue #1922), `request`, `withdraw`, `leave`, `edit`, `requests/<id>/approve`, `requests/<id>/decline`, `slack`, `messages`, `call-link`, `meetings/new`, `meetings/<id>/respond`, `meetings/<id>/move`, `meetings/<id>/cancel`, `meetings/<id>/held` |

Sprint pod pages show a sprint header (`Sprint` eyebrow, the sprint name
linking to `/sprints/<slug>`) instead of the course tab strip.

### Access

- Anonymous: login redirect with `next`.
- Draft or cancelled sprint: 404 for non-staff.
- Logged in without a `SprintEnrollment`: 404.
- Enrolled member: full access. No extra tier check; staff can enroll a member
  below `min_tier_level`. The Slack hand-off keeps its Main-or-above rule.
- Staff not enrolled: operator view (read, no `Start a pod`, no request form).
- A pod id from another sprint or from a cohort: 404.
- Ended sprints keep their pods usable.

### Rules that differ only by activity

- `PODS_MAX_OPEN_REQUESTS_PER_MEMBER` and `PODS_MAX_CREATED_PER_MEMBER` count
  per sprint, and the messages say `in this sprint`. Every other `PODS_*`
  default and limit applies unchanged.
- Unenrollment: deleting a `SprintEnrollment` (`sprint_leave` or Studio
  unenroll; the API has no enrollment delete) removes the user from that
  sprint's pods with the normal leave rules and cancels their open requests
  there. Cohort pods and other sprints' pods are untouched.
- Bell notifications, dashboard `Your week` rows and the first-meeting nudge
  link to `/sprints/<slug>/pods/<id>`.
- The daily staff Slack alert names a sprint pod `Pod name (Sprint name)`.
- Accountability partners are separate: pods never read, create or replace
  partner pairs.

### Studio and API

- `/studio/pods/` lists cohort and sprint pods; `?sprint=<id>` filters.
- `/studio/pods/new?sprint=<id>` preselects the sprint in the `Activity`
  select; owner and member emails must be enrolled in the sprint.
- Studio sprint detail has a `Pods` card with the pod count and `Create pod`.
- `GET /api/sprints/<slug>/members`, `POST /api/pods` with `sprint_slug`,
  `GET /api/pods?sprint=<slug>`; see `_docs/api.md`.

## Pod meetings

How agreed meetings work (issue #1919):

- A member proposes a suggested or custom time. The meeting is agreed
  (`scheduled`) once the members who can make it reach the pod's required
  attendance: 2 in a pod of 2 or 3, all but one in a pod of 4 or more. Staff,
  Studio and API meetings are scheduled directly.
- The rule is visible (issue #1934): the propose page and the Change time
  page for a proposal say `Confirmed once 2 of you can make it.`, and a
  proposal's badge reads `Proposed - 1 of 2 needed`.
- A live proposal shows `Waiting on you and Mike K. since Oct 7.`: the pod
  members with no answer on it (the viewer as `you`, first), counted from its
  last move or else its creation. The line goes once everyone has answered.
  The helper is `members_without_answer` in `pods/services/meeting_rules.py`.
- Dates on the `Waiting on` and `Moved by` lines are relative in the viewer's
  timezone (issue #1937): the viewer's local day reads `since today.` /
  `Moved by Anna K. today`, the previous local day `since yesterday.` /
  `Moved by Anna K. yesterday`, older days `since Oct 7.` /
  `Moved by Anna K. on Oct 7`.
- A live proposal can no longer be confirmed (issue #1937) when the pod's
  members minus those who answered `Can't make it` on it are fewer than the
  required attendance (pod of 3 with 2 `Can't make it`, pod of 4 with 2).
  Only current members' answers count. It is derived, not stored, and
  nothing is cancelled: a member switching back to `I can make it`, or a move
  (which clears answers), brings the normal proposed state back. The helpers
  are `proposal_can_be_confirmed` / `proposal_unconfirmable` in
  `pods/services/meeting_rules.py`. Such a proposal:
  - shows the badge `Can't be confirmed` and the line `Too few of you can
    make this time, so it can't be confirmed. Propose another time.` instead
    of `Proposed - N of M needed` and the `Waiting on` line;
  - makes `Propose <time> instead` (the next best time) the row's one primary
    action, and relabels `Change time` to `Propose another time` (primary
    only when there is no next best time);
  - reads `Proposed time can't be confirmed - propose another time` on the
    `Study groups` tab (when no confirmed meeting is upcoming) and is left out of the
    dashboard `Your week` list;
  - still blocks proposing from Suggested times (no `Propose this time`
    links, no custom propose link), but the blocked line reads `The proposed
    time can't be confirmed. Propose another time from the meeting above.`
    (issue #1940) instead of `Confirm or cancel the proposed time before
    proposing another.`, which stays for a proposal that can still be
    confirmed. The same sentence is the error flash when a member opens
    `/meetings/new` directly. The pod page reads it off the meeting rows; the
    confirm page uses `open_proposal_unconfirmable` in
    `pods/services/meeting_presentation.py`.
- A proposal row's meta lines read top to bottom (issue #1940): `Proposed
  by`, `Moved by`, then `Waiting on ...` or the can't-be-confirmed line (never
  both), then `Can't make it: ...`, the `Clock change` lines, and the next
  best time (or the no-offer line). Who proposed and who moved it, and when,
  stay together.
- The `Can't make it` answer that makes a proposal unconfirmable notifies
  every other member (proposer included) in the bell with `Raj S. can't make
  Pod A meeting 2, so this time can't be confirmed. Propose another time.`,
  linking to the pod's `#meetings`, and the responder sees `Marked that you
  can't make it. This time can't be confirmed, so propose another time.`
  Every other `Can't make it` keeps the plain `Raj S. can't make Pod A
  meeting 2` notification. No email or Slack.
- The dashboard `Your week` list shows a live proposal starting in the next 7
  days that the member has not answered as `<pod> - Proposed time` with a
  `Needs your answer` badge, linking to the pod's `#meetings`. Answering
  removes it; once agreed it comes back as `<pod> - Meeting N of M`.
- A pod has at most one open proposal. A proposal whose start passes before
  agreement is `Not confirmed`, does not count and does not block a new one.
- Scheduled, held and live proposed meetings count toward `meeting_count`;
  lowering `meeting_count` below that number is rejected.
- From meeting 2 the propose page offers `Repeat weekly for the remaining N
  meetings`. Repeats keep the same local wall-clock time in the proposer's
  timezone per date, so a clock change never moves the meeting for them; a
  member in a zone that changes clocks on another date sees a `Clock change`
  line. A shift that lasts to the end of the series is shown once, on the
  first shifted meeting: `Clock change: 22:30 for Raj S. from this week (was
  21:30).` A shift that reverts later in the series (or only affects the last
  meeting) reads `this week (usually 12:00)` on each shifted meeting. A nonexistent local time (spring-forward gap) moves
  forward by the gap; an ambiguous one (fall-back) uses its first occurrence.
- Any member or staff can move, cancel or record a meeting. Both proposal
  and confirmed rows offer `Cancel meeting`: a proposal cancels in one tap, a
  confirmed meeting asks first (`Yes, cancel meeting`). A move clears
  `Can't make it` answers and notifies the others; no re-agreement.
- `Propose <time> instead` on a proposal row cancels the proposal and opens
  the confirm page for that time with `replaces=<cancelled head id>` (issue
  #1940), carried in a hidden `replaces` input. Submitting it sets
  `PodMeeting.replaced_by` to the new proposal's head on every meeting of the
  cancelled proposal (a weekly proposal's whole series), and the old row
  keeps its `Cancelled` badge with the line `Replaced by a new proposed time.`
  under it. A `replaces` value that is not a number, not a meeting of the
  pod, not cancelled or already linked is ignored; the proposal is still
  made. `Cancel meeting`, `Didn't happen`, and leaving the confirm page
  without proposing link nothing, so those rows stay a plain `Cancelled`.
  Proposals cancelled before #1940 are not backfilled. `replaced_by` is a
  member-page hint only: the staff API, the OpenAPI schema and Studio do not
  show it.
- After `Can't make it` the row offers the next best time: a start within 7
  days either side of the meeting (and 12 hours to 6 months from now) that
  the required attendance can make. It is never within 60 minutes of the
  meeting, never overlaps a live meeting and never lands at or before the
  previous or at or after the next live pod meeting, so meeting numbers stay
  put. The same Monday-Sunday week (in the meeting's timezone) wins, then the
  fewest days away, then the best fit, then the closest start. With none:
  `No other time within a week of this meeting fits at least 2 of you.`
- A meeting is marked held only after its start, by members, in Studio and
  through the staff API (`This meeting has not started yet.`). Cancelling a
  future meeting is always allowed.
- One optional call link per pod (`Pod.meeting_url`, https only), set with
  the `Add a call link` / `Change link` button under the Meetings heading,
  where it is shown once as `Call link:`. A scheduled meeting row shows
  `Join call` from 10 minutes before the start until the end.

## Pod chat

A small plain-text chat on the pod page (issue #1920). It is a starting
point; the pod page recommends Slack for ongoing conversation.

- Who: current pod members read and post, on any tier (the Slack tier gate
  does not apply). Staff who are not members read it on the member page but
  cannot post (`You can read this chat. Only pod members can post.`).
  Non-members, requesters, waitlisted members and former members see no chat
  section and get `403` on POST.
- Where: the `Chat` section (`#chat`) is the last section on the pod page.
  Archived pods are 404 for members; staff still read their chat. Closed
  pods keep their chat.
- Notice, always shown above the message box and not dismissible:
  `Admins can read this chat. Do not share private details. Slack is better
  for ongoing conversation.`
- With a Slack channel link saved the section also says `This pod has moved
  to Slack.` with `Open in Slack`. The message box stays, because members
  below Main cannot join Slack.
- Messages: plain text, 1-1000 characters after trimming, escaped, line breaks
  kept, URLs become `rel="nofollow"` links. No markdown, no editing, no
  deleting. Authors show as `Anna K.` (never the email), the viewer's own as
  `(you)`, with the time in the viewer's timezone. A former member's
  messages stay under their name.
- The page shows the latest 50 visible messages, oldest first
  (`Showing the latest 50 messages.` when there are more). No live updates:
  `Refresh` reloads the section.
- Errors keep the draft: `Write a message first.`, `Messages can be up to
  1000 characters.`, and the hourly limit message
  (`PODS_CHAT_MESSAGES_PER_HOUR`).
- Unread: opening the pod page as a member sets `PodMembership.chat_read_at`.
  The member's own rows on the `Study groups` tab show `N new messages` for visible
  messages by others after that time (or after `joined_at` when never read).
  The `Study groups` tab badge still counts only pending requests. There are no bell,
  email or Slack notifications per message.
- Moderation: staff hide or restore a message in Studio (the pod's `Chat`
  section shows the latest 200 messages including hidden ones, with author
  name and email) or with `POST /api/pods/<id>/messages/<message_id>/hide`
  and `/restore`. Hidden messages disappear from the member page and never
  count as unread. `GET /api/pods/<id>/messages` returns the full transcript.

## Pod notes and transcripts

After a meeting, pod members add the meeting notes or the call transcript as
a pod artifact (issue #1922). Plain text only: nothing goes to file storage.

- Who: current pod members add and read. Staff who are not members read them
  (on the pod page and the artifact page) but do not add them (`403`). Non-members,
  requesters, waitlisted and former members see nothing and get 404 on every
  notes URL. Archived pods stay 404 for members. The course must be in
  `PODS_COURSE_SLUGS`.
- Meeting rows: the next upcoming `scheduled` meeting that has not started
  (only that one row, never a proposal) shows `Tip: record the call and add
  the transcript or notes here afterwards.` From start to end a scheduled
  meeting is unchanged (`Join call`, `Mark as held`, `Didn't happen`). After
  its scheduled end, `Add notes` is the row's primary action with `Mark as
  held` and `Didn't happen` as secondary; a `held` meeting shows a secondary
  `Add notes`. Cancelled meetings and proposals never offer it. Notes are
  never required for a meeting to count. Each artifact attached to a meeting
  shows on its row as `Notes: <title>` or `Transcript: <title>`, linking to
  the artifact page.
- Section: `Notes and transcripts` (`#notes`) sits directly after Meetings,
  subtitle `Pod members and admins can read these.`, with `Add notes` below
  the heading for members. Rows are newest first: title link, `Notes` or
  `Transcript` badge, and `Meeting 2 · Added by Anna K. on Oct 14` (viewer
  timezone, `(you)` for the viewer's own, no meeting part when none, display
  names only). Empty: `No notes yet`.
- Form (`/courses/<slug>/home/pods/<pod_id>/notes/new`, optional
  `?meeting=<meeting_id>`): `Meeting` (this pod's meetings that are `held` or
  `scheduled` with a past start, labelled `Meeting 2 - Oct 14`, plus `Not
  tied to a meeting`), `Kind` (`Notes` / `Transcript`), `Title` (prefilled
  `Meeting 2 - Oct 14`, or `Notes - <today>`), `Text`, and `Or upload a file`
  (`.txt`, `.md` or `.vtt`, read as UTF-8). The privacy line `Pod members and
  admins can read this. Remove anything private before uploading.` is always
  shown above `Save notes`.
- Validation (typed values stay, a chosen file does not): `Paste the notes or
  choose a file.`, `Upload a .txt, .md or .vtt file.`, `This file is not
  plain text. Paste the text instead.`, the `PODS_ARTIFACT_MAX_KB` message,
  `Add a title.`, `Titles can be up to 200 characters.` and `Choose a meeting
  that has already happened.` (another pod's, cancelled, proposed or
  not-started meeting). Pasted text wins when both text and a file are sent.
- Saving notes for a `scheduled` meeting past its start marks it `held` in
  the same transaction (`status_changed_by` = the member, same as `Mark as
  held`) and says `Notes saved and meeting marked as held.`; otherwise
  `Notes saved.`
- Artifact page (`.../notes/<artifact_id>`): `Back to pod`, title, kind
  badge, the meta line, then the body as escaped plain text with line breaks
  kept. No markdown. The author sees `Remove` behind a confirm (`Remove these
  notes? This can't be undone.` / `Yes, remove`): a hard delete with `Notes
  removed.`; anyone else gets `403` on the POST. Removing never changes the
  meeting status.
- Studio: `/studio/pods/<id>/` has `Notes and transcripts (N)` after
  Meetings with title, kind, meeting, author name and email, date and size,
  plus `View` (the member artifact page) and `Delete` (`Notes deleted.`).
- Staff API: `GET /api/pods/<id>/artifacts` (metadata, no body; `kind`,
  `meeting_id`, `limit`, `offset`) and `GET /api/pods/<id>/artifacts/<artifact_id>`
  (with `body`); `GET /api/pods/<id>` has `artifact_count`. No API create or
  delete. Both work whatever `PODS_ARTIFACTS_ENABLED` and `PODS_COURSE_SLUGS`
  say.
- No notifications (bell, email or Slack) when notes are added.
