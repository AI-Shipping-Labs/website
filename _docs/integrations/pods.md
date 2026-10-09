# Pods

Pods are small groups of members inside a community activity (issue #1918).
Phase 1 attaches pods to dated course cohorts: enrolled cohort members see a
`Pods` tab on course Home, start a pod of one, send join requests, join a
waiting list when a pod is full, add weekly availability in their own
timezone, and see suggested meeting times. Staff manage pods in Studio
(`Planning -> Pods`) and through the staff API (`_docs/api.md`, `Pods API`).

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
