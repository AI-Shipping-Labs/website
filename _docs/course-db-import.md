# DataTalks Course Database Import

Use `manage.py import_users course_db --csv path/to/alumni.csv --dry-run` before
running a live import. The CSV is operator-curated and must not contain real
alumni data in fixtures or docs.

Required columns:

- `email`
- `name`
- `course_slug`

Optional columns:

- `enrollment_date`
- `course_db_user_id`

Additional columns are ignored and are not stored in user metadata. The adapter
aggregates multiple rows with the same normalized email into one imported user,
preserving course slugs in first-seen CSV order. Each imported alumnus receives
`course:<course_slug>` tags and `User.import_metadata["course_db"]` provenance
with `course_slugs`, optional `enrollment_dates_by_course`, and optional
`course_db_user_ids`.

The import requests permanent Main access through the tier override pipeline.
It does not mutate the user's stored Stripe/free tier, does not subscribe users
to newsletters, and does not mark email addresses verified.

Consent decision: this import uses opt-out consent because DataTalks alumni
already paid for or completed DataTalks courses and receive no-cost Main access
as a continuity bridge. The welcome email explains the DataTalks course-history
reason, lists imported course slugs, links to password setup and sign-in, keeps
the unsubscribe link, and says recipients can reply to request deletion if the
account was unexpected.
