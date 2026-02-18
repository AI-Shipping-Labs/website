# 094 - Email + Password Authentication

**Status:** pending
**Tags:** `auth`, `email`, `frontend`
**GitHub Issue:** [#94](https://github.com/AI-Shipping-Labs/website/issues/94)
**Specs:** 01 (user model), 10 (email verification)
**Depends on:** [067-user-auth](067-user-auth.md), [085-email-ses](085-email-ses.md)
**Blocks:** —

## Scope

- Email + password registration (in addition to existing OAuth)
- Password reset flow via email link
- Email verification flow: send tokenized link on registration, clicking sets email_verified = true
- Token is JWT with user_id, expires in 24 hours

## Acceptance Criteria

- [ ] `POST /api/register` with email + password creates user with email_verified = false and tier = free
- [ ] Registration sends verification email via EmailService with JWT token (user_id, 24h expiry)
- [ ] `GET /api/verify-email?token={jwt}` sets email_verified = true
- [ ] `POST /api/login` with email + password authenticates user; returns 401 if credentials invalid
- [ ] `POST /api/password-reset-request` sends password reset email with JWT token (user_id, 1h expiry)
- [ ] `GET /api/password-reset?token={jwt}` renders password reset form; `POST` with token + new_password updates password
- [ ] Unverified users have same access as free tier (not blocked, just limited)
- [ ] Works alongside existing Google/GitHub OAuth — same User model, accounts linked by email
- [ ] `[HUMAN]` All auth pages (register, login, password reset) styled consistently with site design
