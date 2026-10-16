"""Recurring schedules for site and shared coursework review pools."""


def content_schedule_definitions(definition):
    return (
        definition(
            'self-paced-peer-review-batches',
            'content.tasks.peer_reviews.form_self_paced_review_batches',
            '*/15 * * * *',
            description='every 15 min',
        ),
        definition(
            'coursework-form-pooled-batches',
            'jobs.tasks.community_base_jobs.form_pooled_batches',
            '*/15 * * * *',
            description='every 15 min',
        ),
        definition(
            'coursework-expire-pooled-reviews',
            'jobs.tasks.community_base_jobs.expire_pooled_reviews',
            '*/15 * * * *',
            description='every 15 min',
        ),
    )
