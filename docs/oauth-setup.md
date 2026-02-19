# OAuth Setup: Google and GitHub Login

This guide covers configuring Google and GitHub OAuth for the AI Shipping Labs platform.
The app uses [django-allauth](https://docs.allauth.org/) for social authentication.

**Related:** [GitHub Issue #67 — User Registration, Login, and Authentication](https://github.com/AI-Shipping-Labs/website/issues/67)

---

## Prerequisites

- Django project running with `django-allauth` installed (already in `INSTALLED_APPS`)
- A Django superuser: `uv run python manage.py createsuperuser`
- The dev server running: `uv run python manage.py runserver`

---

## 1. Google OAuth

### 1A. Create Google OAuth Credentials

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project (or select an existing one)
3. Navigate to **APIs & Services > OAuth consent screen**
   - Choose **External** user type
   - Fill in app name: `AI Shipping Labs`
   - Add your email as support email and developer contact
   - Click **Save and Continue** through the scopes and test users steps
4. Navigate to **APIs & Services > Credentials**
5. Click **+ CREATE CREDENTIALS > OAuth client ID**
   - Application type: **Web application**
   - Name: `AI Shipping Labs (local)` (or `production` for remote)
   - Add **Authorized redirect URIs** (see table below)
   - Click **Create**
6. Copy the **Client ID** and **Client Secret**

### 1B. Authorized Redirect URIs

| Environment | Redirect URI |
|-------------|-------------|
| Local | `http://127.0.0.1:8000/accounts/google/login/callback/` |
| Production | `https://aishippinglabs.com/accounts/google/login/callback/` |

You can add both URIs to the same OAuth client, or create separate clients per environment.

### 1C. Configure in Django

**Option A — Via `settings.py` (current setup, good for local dev):**

Edit `website/settings.py`, find the `SOCIALACCOUNT_PROVIDERS` dict and fill in:

```python
SOCIALACCOUNT_PROVIDERS = {
    'google': {
        'SCOPE': ['profile', 'email'],
        'AUTH_PARAMS': {'access_type': 'online'},
        'APP': {
            'client_id': '<your-google-client-id>',
            'secret': '<your-google-client-secret>',
        },
    },
    # ...
}
```

**Option B — Via environment variables (recommended for production):**

Update `settings.py` to read from env vars:

```python
'google': {
    'SCOPE': ['profile', 'email'],
    'AUTH_PARAMS': {'access_type': 'online'},
    'APP': {
        'client_id': os.environ.get('GOOGLE_OAUTH_CLIENT_ID', ''),
        'secret': os.environ.get('GOOGLE_OAUTH_CLIENT_SECRET', ''),
    },
},
```

Then set the env vars:

```bash
export GOOGLE_OAUTH_CLIENT_ID="your-client-id-here"
export GOOGLE_OAUTH_CLIENT_SECRET="your-client-secret-here"
```

**Option C — Via Django Admin (alternative):**

1. Go to `http://127.0.0.1:8000/admin/`
2. Navigate to **Social applications > Add social application**
3. Provider: **Google**
4. Name: `Google`
5. Client ID: paste your client ID
6. Secret key: paste your client secret
7. Sites: move `example.com` (or your site) to **Chosen sites**
8. Save

> **Note:** If using Option C, remove the `'APP'` key from `SOCIALACCOUNT_PROVIDERS['google']` in settings — allauth checks settings first, then the database.

---

## 2. GitHub OAuth

### 2A. Create GitHub OAuth App

1. Go to [GitHub Developer Settings](https://github.com/settings/developers)
2. Click **OAuth Apps > New OAuth App**
3. Fill in:
   - Application name: `AI Shipping Labs (local)` (or `production`)
   - Homepage URL: see table below
   - Authorization callback URL: see table below
4. Click **Register application**
5. Copy the **Client ID**
6. Click **Generate a new client secret** and copy it immediately

### 2B. URLs by Environment

| Environment | Homepage URL | Callback URL |
|-------------|-------------|-------------|
| Local | `http://127.0.0.1:8000` | `http://127.0.0.1:8000/accounts/github/login/callback/` |
| Production | `https://aishippinglabs.com` | `https://aishippinglabs.com/accounts/github/login/callback/` |

> **Important:** GitHub OAuth Apps only allow one callback URL per app. Create separate OAuth Apps for local and production.

### 2C. Configure in Django

**Option A — Via `settings.py` (current setup, good for local dev):**

```python
SOCIALACCOUNT_PROVIDERS = {
    # ...
    'github': {
        'SCOPE': ['user:email'],
        'APP': {
            'client_id': '<your-github-client-id>',
            'secret': '<your-github-client-secret>',
        },
    },
}
```

**Option B — Via environment variables (recommended for production):**

```python
'github': {
    'SCOPE': ['user:email'],
    'APP': {
        'client_id': os.environ.get('GITHUB_OAUTH_CLIENT_ID', ''),
        'secret': os.environ.get('GITHUB_OAUTH_CLIENT_SECRET', ''),
    },
},
```

Then set the env vars:

```bash
export GITHUB_OAUTH_CLIENT_ID="your-client-id-here"
export GITHUB_OAUTH_CLIENT_SECRET="your-client-secret-here"
```

**Option C — Via Django Admin:**

Same as Google — add a **Social application** with provider **GitHub**.

---

## 3. Verify the Django Site Configuration

django-allauth requires the Sites framework. Make sure `SITE_ID = 1` is in settings (already configured), and verify the site domain matches your environment:

```bash
uv run python manage.py shell -c "
from django.contrib.sites.models import Site
site = Site.objects.get(id=1)
print(f'Domain: {site.domain}, Name: {site.name}')
"
```

**For local development**, update if needed:

```bash
uv run python manage.py shell -c "
from django.contrib.sites.models import Site
Site.objects.update_or_create(id=1, defaults={'domain': '127.0.0.1:8000', 'name': 'AI Shipping Labs (local)'})
"
```

**For production:**

```bash
uv run python manage.py shell -c "
from django.contrib.sites.models import Site
Site.objects.update_or_create(id=1, defaults={'domain': 'aishippinglabs.com', 'name': 'AI Shipping Labs'})
"
```

---

## 4. Test the Flow

### Local

1. Start the server: `uv run python manage.py runserver`
2. Open `http://127.0.0.1:8000/accounts/login/`
3. Click **Sign in with Google** — you should be redirected to Google, then back to the homepage as a logged-in user
4. Log out, then click **Sign in with GitHub** — same flow
5. Check Django admin > Users to confirm the user was created with `email_verified = True`

### Production

1. Ensure env vars are set for the production OAuth credentials
2. Ensure the Site domain is `aishippinglabs.com`
3. Visit `https://aishippinglabs.com/accounts/login/`
4. Test both Google and GitHub login flows
5. Verify users appear in Django admin with correct data

---

## 5. Troubleshooting

| Problem | Solution |
|---------|----------|
| `SocialApp matching query does not exist` | Either add credentials via Django Admin (Option C) or ensure `APP` dict is present in `SOCIALACCOUNT_PROVIDERS` in settings |
| `redirect_uri_mismatch` (Google) | The callback URL in Google Console doesn't match. Check for trailing slashes and `http` vs `https` |
| `The redirect_uri MUST match the registered callback URL` (GitHub) | GitHub only allows one callback URL per app. Create separate apps for local/production |
| Login works but user not created | Check `SOCIALACCOUNT_AUTO_SIGNUP = True` in settings |
| `Site matching query does not exist` | Run the Site update command from step 3 above |
| Google shows "Access blocked: This app's request is invalid" | Make sure the OAuth consent screen is configured and the redirect URI is listed exactly |
| Google shows "App not verified" warning | Expected during development. Click **Continue** (only works for test users). For production, submit for verification |

---

## 6. Environment Variables Summary

For production, set these environment variables:

```bash
# Google OAuth
GOOGLE_OAUTH_CLIENT_ID=...
GOOGLE_OAUTH_CLIENT_SECRET=...

# GitHub OAuth
GITHUB_OAUTH_CLIENT_ID=...
GITHUB_OAUTH_CLIENT_SECRET=...
```

> **Note:** The current `settings.py` uses hardcoded empty strings in `SOCIALACCOUNT_PROVIDERS['google']['APP']` and `['github']['APP']`. For production, update those to read from `os.environ.get(...)` as shown in Option B above.
