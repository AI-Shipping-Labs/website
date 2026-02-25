# OAuth Setup

This document has been consolidated into [setup.md](setup.md).

See the Google OAuth and GitHub OAuth sections there for current instructions.

## Troubleshooting

- `redirect_uri_mismatch` (Google) — Add both `localhost` and `127.0.0.1` callback URIs in Google Console. allauth uses the host from the browser request.
- `The redirect_uri MUST match the registered callback URL` (GitHub) — GitHub only allows one callback URL per app. Create separate apps for local/production.
- `User has no field named 'username'` — Ensure `ACCOUNT_USER_MODEL_USERNAME_FIELD = None` is in settings.py.
- `SocialApp matching query does not exist` — Ensure `APP` dict with credentials is in `SOCIALACCOUNT_PROVIDERS` in settings.py.
- `Site matching query does not exist` — Run: `uv run python manage.py shell -c "from django.contrib.sites.models import Site; Site.objects.update_or_create(id=1, defaults={'domain': 'localhost:8000', 'name': 'AI Shipping Labs'})"`
- Google shows "App not verified" warning — Expected during development. Click Continue. For production, submit for Google verification.
- Login works but accounts not consolidated — Ensure `SOCIALACCOUNT_EMAIL_AUTHENTICATION = True` and `SOCIALACCOUNT_EMAIL_AUTHENTICATION_AUTO_CONNECT = True` in settings.
