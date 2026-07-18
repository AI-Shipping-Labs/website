#!/usr/bin/env bash
# CI-only image-level rollback/forward-recovery rehearsal for issue #1298.
# It uses an ephemeral PostgreSQL 16 container and the standard ECR login
# already established by Deploy Dev. It never contacts a live database.

set -euo pipefail

if [ "$#" -ne 2 ]; then
    echo "usage: $0 <candidate-image> <exact-r1-image>" >&2
    exit 2
fi

CANDIDATE_IMAGE=$1
R1_IMAGE=$2
POSTGRES_CONTAINER="aisl-r1-recovery-${GITHUB_RUN_ID:-local}-$$"
DATABASE_URL="postgresql://postgres:postgres@127.0.0.1:55432/aisl_r1_recovery"

cleanup() {
    docker rm -f "${POSTGRES_CONTAINER}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

# This gate self-retires when a separately reviewed artifact leaves R1. The
# workflow may remove the step and R1 tag at that point, but future main
# deploys are not coupled to retaining the old ECR image in the meantime.
candidate_phase=$(docker run --rm \
    --entrypoint /app/.venv/bin/python \
    "${CANDIDATE_IMAGE}" \
    -c 'from website.release_phase import R1_EXPAND_COMPATIBILITY; print("r1" if R1_EXPAND_COMPATIBILITY else "post-r1")')
if [ "${candidate_phase}" != "r1" ]; then
    echo "Exact R1 image rehearsal skipped: candidate is no longer in R1."
    exit 0
fi

if ! docker image inspect "${R1_IMAGE}" >/dev/null 2>&1; then
    docker pull "${R1_IMAGE}"
fi
docker run --detach --rm \
    --name "${POSTGRES_CONTAINER}" \
    -e POSTGRES_USER=postgres \
    -e POSTGRES_PASSWORD=postgres \
    -e POSTGRES_DB=aisl_r1_recovery \
    -p 55432:5432 \
    postgres:16 >/dev/null

ready_matches=0
for _attempt in $(seq 1 30); do
    if docker exec "${POSTGRES_CONTAINER}" \
        pg_isready -U postgres -d aisl_r1_recovery >/dev/null 2>&1; then
        ready_matches=$((ready_matches + 1))
        if [ "${ready_matches}" -ge 3 ]; then
            break
        fi
    else
        ready_matches=0
    fi
    sleep 1
done
if [ "${ready_matches}" -lt 3 ]; then
    echo "ephemeral PostgreSQL did not become stably ready" >&2
    exit 1
fi

manage() {
    local image=$1
    shift
    docker run --rm --network host \
        --entrypoint /app/.venv/bin/python \
        -e DATABASE_URL="${DATABASE_URL}" \
        -e SECRET_KEY=ci-r1-image-recovery-only \
        -e DEBUG=False \
        -e SES_ENABLED=true \
        "${image}" /app/manage.py "$@"
}

# Build the database with the exact production artifact, then roll forward.
manage "${R1_IMAGE}" migrate --noinput
manage "${R1_IMAGE}" shell -c '
from accounts.models import User
from content.models import Workshop
from email_app.models import EmailCampaign, EmailLog
u = User.objects.create(email="image-floor@example.com", password="!")
Workshop.objects.create(slug="image-floor-workshop", title="Image floor", date="2026-07-18")
c = EmailCampaign.objects.create(subject="Image floor campaign", body="Body")
EmailLog.objects.create(user=u, campaign=c, email_type="campaign")
'
manage "${CANDIDATE_IMAGE}" migrate --noinput
manage "${CANDIDATE_IMAGE}" reconcile_r1_expand
manage "${CANDIDATE_IMAGE}" reconcile_r1_expand
manage "${CANDIDATE_IMAGE}" shell -c '
from accounts.models import User
from content.models import Workshop
from email_app.models import EmailLog
u = User.objects.create(email="candidate-era@example.com", password="!")
Workshop.objects.create(slug="candidate-era-workshop", title="Candidate era", date="2026-07-18")
EmailLog.objects.create(user=u, email_type="candidate_notice", subject="Candidate subject")
'

# Image-only rollback: exact R1 writes against the expanded schema. No
# migration is reversed. The old image also reads/updates candidate-era data.
manage "${R1_IMAGE}" shell -c '
from accounts.models import User
from content.models import Workshop
from email_app.models import EmailCampaign, EmailLog
assert Workshop.objects.filter(slug__in=["image-floor-workshop", "candidate-era-workshop"]).count() == 2
candidate_workshop = Workshop.objects.get(slug="candidate-era-workshop")
candidate_workshop.title = "Candidate era read by R1"
candidate_workshop.save(update_fields=["title", "updated_at"])
candidate_log = EmailLog.objects.get(email_type="candidate_notice")
candidate_log.email_type = "candidate_notice_read_by_r1"
candidate_log.save(update_fields=["email_type"])
u = User.objects.create(email="rollback-era@example.com", password="!")
Workshop.objects.create(slug="rollback-era-a", title="Rollback A", date="2026-07-18")
Workshop.objects.create(slug="rollback-era-b", title="Rollback B", date="2026-07-18")
c = EmailCampaign.objects.create(subject="Rollback campaign", body="Body")
EmailLog.objects.create(user=u, campaign=c, email_type="campaign")
log = EmailLog.objects.create(user=u, email_type="event_reminder")
log.email_type = "event_reminder_updated"
log.save(update_fields=["email_type"])
from django.db.migrations.recorder import MigrationRecorder
recorder = MigrationRecorder.Migration.objects
assert recorder.filter(app="content", name="0056_reconcile_workshop_preview_tokens").exists()
assert recorder.filter(app="email_app", name="0021_reconcile_emaillog_subject_default").exists()
'

# Forward recovery is the exact candidate plus idempotent reconciliation.
manage "${CANDIDATE_IMAGE}" migrate --noinput
manage "${CANDIDATE_IMAGE}" reconcile_r1_expand
manage "${CANDIDATE_IMAGE}" reconcile_r1_expand
manage "${CANDIDATE_IMAGE}" shell -c '
from content.models import Workshop
from email_app.models import EmailLog
rows = list(Workshop.objects.filter(slug__in=["image-floor-workshop", "candidate-era-workshop", "rollback-era-a", "rollback-era-b"]))
assert len(rows) == 4
assert all(row.preview_token is not None for row in rows)
assert len({row.preview_token for row in rows}) == 4
assert Workshop.objects.get(slug="candidate-era-workshop").title == "Candidate era read by R1"
campaign_subjects = list(EmailLog.objects.filter(campaign__isnull=False).values_list("subject", flat=True))
assert sorted(campaign_subjects) == ["Image floor campaign", "Rollback campaign"]
transactional = EmailLog.objects.get(email_type="event_reminder_updated")
assert transactional.subject == ""
candidate_log = EmailLog.objects.get(email_type="candidate_notice_read_by_r1")
assert candidate_log.subject == "Candidate subject"
assert EmailLog.objects.count() == 4
'

echo "Exact R1 image rollback and candidate forward recovery passed."
