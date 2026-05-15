# Auth integration setup

This page documents every setting registered in
`integrations/settings_registry.py` under the `auth` group. Each
section follows the same template — Purpose, Without it, Where to
find it, Prereqs, Rotation, Test vs live.

Unlike most integration groups, the `auth` group has no external
service behind it. The settings here tune the platform's own
authentication-related behaviour — currently, how long unverified
email signups live before the daily purge job removes them.

External OAuth providers (Google, GitHub) are configured separately
via Django's `SocialApp` admin, not through this group. See
`_docs/configuration.md` for the operator setup.

## UNVERIFIED_USER_TTL_DAYS

Purpose: Number of days an email-signup account stays alive without
verifying its email before the daily purge job hard-deletes it. Read
by `accounts/services/verification.py:get_unverified_user_ttl_days`.

Email-signup users get a `verification_token_expires_at` of
`now + UNVERIFIED_USER_TTL_DAYS` (see
`accounts/models/user.py:73`). A cron / scheduled task runs daily
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

  The schedule should include the `accounts.services.verification.purge_unverified_users`
  task (or whatever name the codebase wires up).

Rotation: n/a. Adjust the value as the operational situation
demands. The next purge run uses the new value.

Test vs live: n/a. Tighten in production during spam waves, leave
relaxed in dev. There is no test-vs-live distinction at the
platform level.
