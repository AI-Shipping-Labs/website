from integrations.services.calendly_delivery import retry_failed_calendly_deliveries


def retry_calendly_webhooks():
    return retry_failed_calendly_deliveries(limit=100)
