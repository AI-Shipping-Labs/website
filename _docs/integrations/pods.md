# Pods

Pods are small groups of members inside a community activity (issue #1918).
Phase 1 attaches pods to dated course cohorts: enrolled cohort members see a
`Pods` tab on course Home, start a pod of one, send join requests, join a
waiting list when a pod is full, add weekly availability in their own
timezone, and see suggested meeting times. Since issue #1919 they turn a
suggested time into an agreed pod meeting (propose, confirm, `Can't make it`
with the next best time, move, cancel, held, a weekly series and one pod call
link), with bell notifications and a reminder before each meeting. Staff
manage pods in Studio (`Planning -> Pods`) and through the staff API
(`_docs/api.md`, `Pods API`).

Every key below is set in Studio (`Operations -> Settings`, `Pods` group). An
environment variable with the same name is an optional fallback.

## PODS_COURSE_SLUGS

Comma-separated course slugs whose dated-cohort members see Pods, for example
`ai-buildcamp`.

- Type: string.
- Default: empty.
- When empty, or when a course is not listed: every member pod URL for that
  course returns 404 and course Home renders no `Pods` tab.
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
(issue #1935).

- Type: integer, 1-336. Invalid or out-of-range values fall back to `48`.
- Default: `48`.
- Stuck proposal: `proposed`, start still in the future and not expired, pod
  not archived, and more than this many hours since it was proposed or last
  moved (`moved_at`, else `created_at`; a move resets the answers, so the
  clock restarts). A weekly proposal counts once, on its first meeting, where
  the answers live.
- Waiting on: pod members with no answer on that meeting, the same rule as
  the member-side `Waiting on` line.
- Studio: the pod's Meetings table shows `No answer: Mike K.` under a live
  proposal's status and a `Stuck` badge on a stuck one; `/studio/pods/` shows
  a `Stuck proposal` badge on pods with a stuck proposal.
- Staff API: each meeting has `waiting_on` (emails), `stuck` and
  `stuck_alerted_at`.

## PODS_STUCK_PROPOSAL_ALERT_ENABLED

Kill switch for the stuck proposals section of the daily pods staff Slack
message.

- Type: boolean.
- Default: `true`.
- Job: the same `pods-stale-request-alert` (daily at 09:00 UTC). One message
  per run covers stale requests and stuck proposals; it posts only when at
  least one of them is new.
- Section: `*3 pod proposals have had no answer for 48 hours*`, then up to 10
  lines, oldest first, then `and N more`:
  `Pod name (Course, Cohort) - Tue Oct 20, 18:00 UTC, proposed by Anna K. 3
  days ago - waiting on Mike K. (mike@example.com, last sign-in 12 days ago)`.
  A moved proposal reads `moved by`. Members who answered `Can't make it`
  follow as `- can't make it: Raj P.`; a member who never signed in reads
  `never signed in`. Already announced proposals still stuck are counted in
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
- Any member or staff can move, cancel or record a meeting. A move clears
  `Can't make it` answers and notifies the others; no re-agreement.
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
  the `Add a call link` / `Change link` button under the Meetings heading.
  Only the soonest upcoming meeting row repeats it as `Call link:`. A
  scheduled meeting row shows `Join call` from 10 minutes before the start
  until the end.
