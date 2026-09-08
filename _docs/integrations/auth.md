# Auth integration setup

This page documents every setting registered in
`integrations/settings_registry.py` under the `auth` group. Each
section follows the same template — Purpose, Without it, Where to
find it, Prereqs, Rotation, Test vs live.

Unlike most integration groups, the `auth` group has no external
service behind it. The settings here tune the platform's own
authentication-related behaviour — how long unverified email signups
live before the daily purge job removes them, and the shared throttle
on public login, register, password-reset request, and newsletter
subscribe JSON endpoints.

External OAuth providers (Google, GitHub) are configured separately
via Django's `SocialApp` admin, not through this group. See
`_docs/configuration.md` for the operator setup.

## UNVERIFIED_USER_TTL_DAYS

Purpose: Number of days an email-signup account stays alive without
verifying its email before the daily purge job hard-deletes it. Read
by `accounts/services/verification.py:resolve_unverified_ttl_days`.

Email-signup users get a `verification_expires_at` of
`now + UNVERIFIED_USER_TTL_DAYS` (see
`accounts/models/user.py:120`). A cron / scheduled task runs daily
and removes any account that:

- Was created via email signup (not OAuth).
- Has not verified the address.
- Has an expired verification window.

Default: 7 (one week). The default lives as the constant
`DEFAULT_UNVERIFIED_USER_TTL_DAYS` in
`accounts/services/verification.py`; if this setting is missing or
the value cannot be parsed as an int, the code uses 7.

Without it (or unparseable): Falls back to 7 days. Existing
unverified accounts retain their pre-existing expiry — only newly
created accounts pick up the new TTL.

Where to find it: This is operator intent — a number of days as an
integer (e.g. `3`, `7`, `30`). There is no external dashboard.

Lower the TTL (e.g. `3`) during spam waves to reduce the window in
which fake accounts can sit on a verified-looking signup. Raise it
(e.g. `30`) during slow launches where users may take longer to
verify after registering.

Prereqs:
- The daily purge job must be scheduled and running. Check it via:

  ```
  uv run python manage.py shell -c "from django_q.tasks import Schedule; print(Schedule.objects.all())"
  ```

  The schedule should include the `accounts.tasks.purge_unverified_users.purge_unverified_users`
  task.

Rotation: n/a. Adjust the value as the operational situation
demands. The next purge run uses the new value.

Test vs live: n/a. Tighten in production during spam waves, leave
relaxed in dev. There is no test-vs-live distinction at the
platform level.

## PURGE_UNVERIFIED_BATCH_SIZE

Purpose: Number of field-safe candidate ids each pass inspects in one
primary-key window. The purge checks every non-ignored reverse relation once
for the whole window, then hard-deletes only ids with no blocking rows.

Default: 500. A missing, non-integer, or non-positive value falls back to 500.

Without it (or unparseable): Each pass uses 500 candidates per batch. The query
uses `pk__gt` and never uses `OFFSET`, so the worker does not materialize the
whole backlog.

Where to find it: This is an operator-controlled positive integer. Raise it
during a spam wave when the database has enough headroom; lower it when smaller
transactions are preferable.

Prereqs: The `purge-unverified-users` daily schedule must be running.

Rotation: n/a. The next run reads the current Studio value.

Test vs live: n/a. Tests use small overrides to exercise batch continuation.

## PURGE_UNVERIFIED_MAX_BATCHES

Purpose: Maximum number of primary-key windows processed by each purge pass in
one daily run.

Default: 50, or at most 25,000 candidates per pass with the default batch size.
A missing, non-integer, or non-positive value falls back to 50.

Without it (or unparseable): Each pass stops after 50 batches. The whole task
also stops after a fixed 240-second wall-clock budget, leaving 60 seconds below
the django-q worker timeout. Remaining candidates wait for the next daily
08:00 UTC run; the task returns normally and does not enqueue itself.

Where to find it: This is an operator-controlled positive integer. Raise it
temporarily during a spam wave only when normal run duration stays below the
wall-clock budget.

Prereqs: The `purge-unverified-users` daily schedule must be running.

Rotation: n/a. The next run reads the current Studio value.

Test vs live: n/a. Tests use small overrides rather than creating a production
sized backlog.

## AUTH_THROTTLE_LOGIN_IP_LIMIT

Purpose: Maximum `POST /api/login` attempts from one client IP inside
the login window. Read by `accounts/services/auth_throttle.py`. The
IP comes from `website.request_ip.client_ip_from_request` and is
SHA-256 hashed before it becomes a cache key.

Default: 20. The default lives as `DEFAULT_LOGIN_IP_LIMIT` in
`accounts/services/auth_throttle.py`; if this setting is missing or
the value cannot be parsed as a positive int, the code uses 20.

Without it (or unparseable): Falls back to 20. Existing counters keep
their current window; only new increments use a newly saved limit.

Where to find it: This is operator intent — a positive integer (e.g.
`10`, `20`, `50`). There is no external dashboard.

Lower the limit (e.g. `5`) during credential-stuffing waves. Raise it
if a shared NAT is blocking legitimate retries.

Prereqs:
- None. Counters live in the shared `django_q` cache, which already
  exists for IntegrationSetting stamps.

Rotation: n/a. Adjust as the operational situation demands. The next
login POST uses the new limit after config-cache invalidation.

Test vs live: n/a. Tests lower this via IntegrationSetting rather
than looping against the production default. There is no test-vs-live
distinction at the platform level.

## AUTH_THROTTLE_LOGIN_EMAIL_LIMIT

Purpose: Maximum `POST /api/login` attempts for one normalized email
inside the login window, even when each attempt uses a different IP.
Read by `accounts/services/auth_throttle.py`. The email is normalized
then SHA-256 hashed before it becomes a cache key.

Default: 10. The default lives as `DEFAULT_LOGIN_EMAIL_LIMIT` in
`accounts/services/auth_throttle.py`; if this setting is missing or
the value cannot be parsed as a positive int, the code uses 10.

Without it (or unparseable): Falls back to 10. Existing counters keep
their current window; only new increments use a newly saved limit.

Where to find it: This is operator intent — a positive integer (e.g.
`5`, `10`, `20`). There is no external dashboard.

Lower the limit to cap password guessing against a single inbox.
Raise it if members behind flaky networks retry more often than 10
times in 15 minutes.

Prereqs:
- None. Counters live in the shared `django_q` cache.

Rotation: n/a. The next login POST uses the new limit after
config-cache invalidation.

Test vs live: n/a. Tests lower this via IntegrationSetting rather
than looping against the production default.

## AUTH_THROTTLE_LOGIN_WINDOW_SECONDS

Purpose: Sliding window in seconds for the login IP and email
buckets. Read by `accounts/services/auth_throttle.py`. Cache TTLs
match this window.

Default: 900 (15 minutes). The default lives as
`DEFAULT_LOGIN_WINDOW_SECONDS` in
`accounts/services/auth_throttle.py`; if this setting is missing or
the value cannot be parsed as a positive int, the code uses 900.

Without it (or unparseable): Falls back to 900. Counters already in
cache keep the TTL they were created with.

Where to find it: This is operator intent — a positive integer number
of seconds (e.g. `300`, `900`, `1800`). There is no external
dashboard.

Shorten the window during an active stuffing attack so blocked IPs
recover faster after the wave. Lengthen it to keep a slow brute-force
from resetting.

Prereqs:
- None. Counters live in the shared `django_q` cache.

Rotation: n/a. New buckets use the new window; in-flight buckets
expire on their original TTL.

Test vs live: n/a. Tests use a short override (for example 1 second)
to prove expiry without waiting 15 minutes.

## AUTH_THROTTLE_MAIL_IP_LIMIT

Purpose: Maximum `POST /api/register`,
`POST /api/password-reset-request`, and `POST /api/subscribe`
attempts from one client IP inside the mail window. Each endpoint
has its own cache prefix, so exhausting register does not lock
subscribe. Read by `accounts/services/auth_throttle.py`.

Default: 8. The default lives as `DEFAULT_MAIL_IP_LIMIT` in
`accounts/services/auth_throttle.py`; if this setting is missing or
the value cannot be parsed as a positive int, the code uses 8.

Without it (or unparseable): Falls back to 8. Existing counters keep
their current window; only new increments use a newly saved limit.

Where to find it: This is operator intent — a positive integer (e.g.
`3`, `8`, `20`). There is no external dashboard.

Lower the limit during signup or inbox-harassment waves to cap SES
spend and unverified user creation. Raise it if a workshop or office
NAT is hitting false positives.

Prereqs:
- None. Counters live in the shared `django_q` cache.

Rotation: n/a. The next matching POST uses the new limit after
config-cache invalidation.

Test vs live: n/a. Tests lower this via IntegrationSetting rather
than looping against the production default.

## AUTH_THROTTLE_MAIL_EMAIL_LIMIT

Purpose: Maximum `POST /api/register`,
`POST /api/password-reset-request`, and `POST /api/subscribe`
attempts for one normalized email inside the mail window. Read by
`accounts/services/auth_throttle.py`.

Default: 3. The default lives as `DEFAULT_MAIL_EMAIL_LIMIT` in
`accounts/services/auth_throttle.py`; if this setting is missing or
the value cannot be parsed as a positive int, the code uses 3.

Without it (or unparseable): Falls back to 3. Existing counters keep
their current window; only new increments use a newly saved limit.

Where to find it: This is operator intent — a positive integer (e.g.
`1`, `3`, `10`). There is no external dashboard.

Lower the limit to stop one address from harvesting verification or
reset mail. A different email from the same IP can still proceed
until the IP bucket trips.

Prereqs:
- None. Counters live in the shared `django_q` cache.

Rotation: n/a. The next matching POST uses the new limit after
config-cache invalidation.

Test vs live: n/a. Tests lower this via IntegrationSetting rather
than looping against the production default.

## AUTH_THROTTLE_MAIL_WINDOW_SECONDS

Purpose: Sliding window in seconds for register, password-reset
request, and subscribe IP and email buckets. Read by
`accounts/services/auth_throttle.py`. Cache TTLs match this window.

Default: 3600 (1 hour). The default lives as
`DEFAULT_MAIL_WINDOW_SECONDS` in
`accounts/services/auth_throttle.py`; if this setting is missing or
the value cannot be parsed as a positive int, the code uses 3600.

Without it (or unparseable): Falls back to 3600. Counters already in
cache keep the TTL they were created with.

Where to find it: This is operator intent — a positive integer number
of seconds (e.g. `600`, `3600`, `7200`). There is no external
dashboard.

Shorten the window so a blocked workshop NAT recovers sooner. Lengthen
it to keep a slow SES-burning loop from resetting every few minutes.

Prereqs:
- None. Counters live in the shared `django_q` cache.

Rotation: n/a. New buckets use the new window; in-flight buckets
expire on their original TTL.

Test vs live: n/a. Tests use a short override (for example 1 second)
to prove expiry without waiting an hour.
