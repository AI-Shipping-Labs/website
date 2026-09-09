"""CRM persona-label resolution shared across API and Studio services."""


def resolve_crm_persona(record):
    """Prefer a structured persona label, then stripped free text."""
    if record.persona_ref_id is not None and record.persona_ref is not None:
        return record.persona_ref.display_label
    return (record.persona or '').strip()
