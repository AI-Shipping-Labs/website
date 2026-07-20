"""Pure Markdown renderer for staff CRM record archives."""

from __future__ import annotations

import json
import re
from urllib.parse import quote, unquote_plus, urlsplit, urlunsplit

SECTION_TITLES = (
    "Profile & account",
    "CRM snapshot",
    "Onboarding",
    "Activity history",
    "Plans & sprints",
    "Sprint enrollments",
    "Course enrollments",
    "Booked calls",
    "Member notes",
)

_MARKDOWN_CHARS = re.compile(r"([\\`*_{}\[\]()#+\-.!|])")
_SECRET_KEY_COMPONENTS = frozenset({
    "auth",
    "key",
    "password",
    "secret",
    "signature",
    "token",
})
_SECRET_COMPOUND_KEYS = frozenset({
    "accesskey",
    "accesstoken",
    "apikey",
    "authkey",
    "authtoken",
    "clientsecret",
    "clienttoken",
    "passwordtoken",
    "privatekey",
    "secretkey",
    "signaturekey",
})
_VALID_PERCENT_ESCAPE = re.compile(r"%(?=[0-9A-Fa-f]{2})")
_QUERY_KEY_CHARS = re.compile(r"^[A-Za-z0-9._-]+$")


class _TrustedMarkdown(str):
    """Renderer-owned Markdown that must not be escaped a second time."""


def markdown_filename_for_crm_record(crm_id):
    return f"crm-record-{int(crm_id)}.md"


def render_crm_record_markdown(aggregate):
    """Render ``aggregate`` as deterministic, structure-safe Markdown."""
    metadata = aggregate["export_metadata"]
    title = aggregate.get("display_name") or aggregate.get("email")
    lines = [
        f"# CRM record: {_escape(title)}",
        "",
        _bullet("Studio CRM URL", _safe_link("Open CRM record", metadata.get("studio_url"))),
        _bullet("CRM record ID", metadata.get("crm_record_id")),
        _bullet("Exported at", metadata.get("exported_at")),
    ]

    lines.extend(["", "## Profile & account"])
    lines.extend(_profile_lines(aggregate))
    lines.extend(["", "## CRM snapshot"])
    lines.extend(_crm_snapshot_lines(aggregate.get("crm_record")))
    lines.extend(["", "## Onboarding"])
    lines.extend(_onboarding_lines(aggregate.get("onboarding_responses") or []))
    lines.extend(["", "## Activity history"])
    lines.extend(_activity_lines(aggregate.get("activities") or []))
    lines.extend(["", "## Plans & sprints"])
    lines.extend(_plan_lines(aggregate.get("plans") or []))
    lines.extend(["", "## Sprint enrollments"])
    lines.extend(_sprint_enrollment_lines(aggregate.get("sprint_enrollments") or []))
    lines.extend(["", "## Course enrollments"])
    lines.extend(_course_enrollment_lines(aggregate.get("course_enrollments") or []))
    lines.extend(["", "## Booked calls"])
    lines.extend(_booked_call_lines(aggregate.get("booked_calls") or []))
    lines.extend(["", "## Member notes"])
    lines.extend(_member_note_lines(aggregate.get("notes") or []))
    return "\n".join(lines).rstrip("\n") + "\n"


def _profile_lines(data):
    tier = data.get("tier") or {}
    base_tier = data.get("base_tier") or {}
    override = data.get("tier_override")
    subscription = data.get("subscription")
    lines = [
        _bullet("Email", data.get("email")),
        _bullet("First name", data.get("first_name")),
        _bullet("Last name", data.get("last_name")),
        _bullet("Display name", data.get("display_name")),
        _bullet("Aliases", data.get("aliases")),
        _bullet("Joined at", data.get("date_joined")),
        _bullet("Last login", data.get("last_login")),
        _bullet("Effective tier", tier.get("slug")),
        _bullet("Effective tier level", tier.get("level")),
        _bullet("Effective tier source", tier.get("source")),
        _bullet("Paid/base tier", base_tier.get("slug")),
        _bullet("Paid/base tier level", base_tier.get("level")),
        _bullet("Tier override active", data.get("tier_override_active")),
    ]
    if override:
        lines.extend([
            _bullet("Override tier", override.get("tier_slug")),
            _bullet("Override level", override.get("level")),
            _bullet("Override expires at", override.get("expires_at")),
            _bullet("Override granted by", override.get("granted_by")),
        ])
    else:
        lines.append(_bullet("Tier override", None))
    lines.extend([
        _bullet("Tags", data.get("tags")),
        _bullet("Email verified", data.get("email_verified")),
        _bullet("Newsletter unsubscribed", data.get("unsubscribed")),
        _bullet("Bounce state", data.get("bounce_state")),
        _bullet("Soft bounce count", data.get("soft_bounce_count")),
        _bullet("Slack member", data.get("slack_member")),
        _bullet("Slack user ID", data.get("slack_user_id")),
        _bullet("Stripe customer ID", data.get("stripe_customer_id")),
        _bullet("Subscription ID", data.get("subscription_id")),
        _bullet("Signup source", data.get("signup_source")),
        _bullet("Account activated", data.get("account_activated")),
        _bullet("Account lifecycle", data.get("account_lifecycle")),
        "",
        "### Email preferences",
        _json_block(data.get("email_preferences") or {}),
        "",
        "### Import metadata",
        _json_block(data.get("import_metadata") or {}),
        "",
        "### Subscription summary",
        _json_block(subscription or {}),
    ])
    return lines


def _crm_snapshot_lines(record):
    if not record:
        return ["_Not specified._"]
    return [
        _bullet("Status", record.get("status")),
        _bullet("Persona", record.get("persona")),
        _bullet("Summary", record.get("summary")),
        _bullet("Next steps", record.get("next_steps")),
        _bullet("Created at", record.get("created_at")),
        _bullet("Updated at", record.get("updated_at")),
    ]


def _onboarding_lines(responses):
    if not responses:
        return ["_No onboarding responses._"]
    lines = []
    for index, response in enumerate(responses):
        if index:
            lines.append("")
        lines.extend([
            f"### Response {response.get('id')}: {_escape(response.get('questionnaire_slug'))}",
            _bullet("Status", response.get("status")),
            _bullet("Submitted at", response.get("submitted_at")),
            _bullet("Resolved persona", _persona_label(response.get("persona"))),
        ])
        questions = response.get("questions") or []
        if not questions:
            lines.extend(["", "_No snapshotted questions._"])
            continue
        for question in questions:
            lines.extend([
                "",
                f"#### Question {question.get('order')}: {_escape(question.get('prompt'))}",
                _bullet("Type", question.get("question_type")),
                _bullet("Order", question.get("order")),
                _bullet("Answer", question.get("answer")),
            ])
            if "answer_options" in question:
                lines.extend([
                    "- Answer options:",
                    _json_block(question.get("answer_options") or []),
                ])
    return lines


def _activity_lines(activities):
    if not activities:
        return ["_No recorded activity._"]
    lines = []
    for index, activity in enumerate(activities):
        if index:
            lines.append("")
        lines.extend([
            f"### {_escape(activity.get('occurred_at'))} — {_escape(activity.get('label'))}",
            _bullet("Category", activity.get("category")),
            _bullet("Type", activity.get("type_label")),
            _bullet("Object type", activity.get("object_type")),
            _bullet("Object ID", activity.get("object_id")),
            _bullet("Paid-upgrade marker", activity.get("is_upgrade_marker")),
        ])
        target = _safe_link("Open activity target", activity.get("target_url"), same_site=True)
        lines.append(_bullet("Target", target))
    return lines


def _plan_lines(plans):
    if not plans:
        return ["_No sprint plans._"]
    lines = []
    for index, plan in enumerate(plans):
        if index:
            lines.append("")
        lines.extend([
            f"### {_escape(plan.get('title'))}",
            _bullet("Plan ID", plan.get("id")),
            _bullet("Sprint", plan.get("sprint")),
            _bullet("Visibility", plan.get("visibility")),
            _bullet("Shared at", plan.get("shared_at")),
            _bullet("Created at", plan.get("created_at")),
            _bullet("Updated at", plan.get("updated_at")),
            _bullet("Goal", plan.get("goal")),
            "",
            "#### Summary and focus",
        ])
        summary = plan.get("summary") or {}
        lines.extend([
            _bullet("Current situation", summary.get("current_situation")),
            _bullet("Summary goal", summary.get("goal")),
            _bullet("Main gap", summary.get("main_gap")),
            _bullet("Weekly hours", summary.get("weekly_hours")),
            _bullet("Why this plan", summary.get("why_this_plan")),
            _bullet("Main focus", (plan.get("focus") or {}).get("main")),
            _bullet("Supporting focus", (plan.get("focus") or {}).get("supporting")),
            _bullet("Accountability", plan.get("accountability")),
            "",
            "#### Weeks",
        ])
        weeks = plan.get("weeks") or []
        if not weeks:
            lines.append("_No weeks._")
        for week in weeks:
            lines.extend([
                "",
                f"**Week {week.get('week_number')}: {_escape(week.get('theme'))}**",
            ])
            checkpoints = week.get("checkpoints") or []
            lines.extend(_checkbox_rows(checkpoints, "description", "done_at", "No checkpoints."))
            note = week.get("note")
            lines.append(_bullet("Participant week note", note.get("body") if note else None))
            if note:
                lines.extend([
                    _bullet("Week note author", note.get("author_email")),
                    _bullet("Week note created at", note.get("created_at")),
                    _bullet("Week note updated at", note.get("updated_at")),
                ])
        lines.extend(["", "#### Resources"])
        resources = plan.get("resources") or []
        if not resources:
            lines.append("_No resources._")
        for resource in resources:
            label = resource.get("title") or "Resource"
            rendered = _safe_link(label, resource.get("url")) or _escape(label)
            note = _value(resource.get("note"))
            lines.append(f"- {rendered}" + (f" — {note}" if note != "_Not specified._" else ""))
        lines.extend(["", "#### Deliverables"])
        lines.extend(_checkbox_rows(plan.get("deliverables") or [], "description", "done_at", "No deliverables."))
        next_steps = plan.get("next_steps") or []
        lines.extend(["", "#### Pre-sprint items"])
        lines.extend(_checkbox_rows(
            [item for item in next_steps if item.get("kind") == "pre_sprint"],
            "description", "done_at", "No pre-sprint items.",
        ))
        lines.extend(["", "#### Next-step items"])
        lines.extend(_checkbox_rows(
            [item for item in next_steps if item.get("kind") != "pre_sprint"],
            "description", "done_at", "No next-step items.",
        ))
    return lines


def _sprint_enrollment_lines(enrollments):
    if not enrollments:
        return ["_No sprint enrollments._"]
    lines = []
    for index, enrollment in enumerate(enrollments):
        if index:
            lines.append("")
        enrolled_by = enrollment.get("enrolled_by")
        lines.extend([
            f"### {_escape(enrollment.get('sprint_slug'))}",
            _bullet("Enrolled at", enrollment.get("enrolled_at")),
            _bullet("Enrolled by", enrolled_by),
            _bullet("Self-enrolled", enrolled_by is None),
        ])
    return lines


def _course_enrollment_lines(enrollments):
    if not enrollments:
        return ["_No course enrollments._"]
    lines = []
    for index, enrollment in enumerate(enrollments):
        if index:
            lines.append("")
        lines.extend([
            f"### {_escape(enrollment.get('course_slug'))}",
            _bullet("Enrolled at", enrollment.get("enrolled_at")),
            _bullet("Unenrolled at", enrollment.get("unenrolled_at")),
            _bullet("Source", enrollment.get("source")),
        ])
    return lines


def _booked_call_lines(calls):
    if not calls:
        return ["_No booked calls._"]
    lines = []
    for index, call in enumerate(calls):
        if index:
            lines.append("")
        lines.extend([
            f"### Call {call.get('id')} with {_escape(call.get('host'))}",
            _bullet("Host", call.get("host")),
            _bullet("Host slug", call.get("host_slug")),
            _bullet("Scheduled at", call.get("scheduled_at")),
            _bullet("Invitee name", call.get("invitee_name")),
            _bullet("Invitee email", call.get("invitee_email")),
            _bullet(
                "Calendly event URI",
                _safe_link(
                    "Open Calendly event",
                    call.get("calendly_event_uri"),
                ),
            ),
            _bullet(
                "Calendly invitee URI",
                _safe_link(
                    "Open Calendly invitee",
                    call.get("calendly_invitee_uri"),
                ),
            ),
            _bullet(
                "Reschedule URL",
                _safe_link("Reschedule call", call.get("reschedule_url")),
            ),
            _bullet(
                "Cancellation URL",
                _safe_link("Cancel call", call.get("cancel_url")),
            ),
            _bullet("Created at", call.get("created_at")),
            _bullet("Updated at", call.get("updated_at")),
        ])
    return lines


def _member_note_lines(notes):
    if not notes:
        return ["_No member notes._"]
    lines = []
    for index, note in enumerate(notes):
        if index:
            lines.append("")
        lines.extend([
            f"### Note {note.get('id')}",
            _bullet("Plan ID", note.get("plan_id")),
            _bullet("Visibility", note.get("visibility")),
            _bullet("Kind", note.get("kind")),
            _bullet("Body", note.get("body")),
            _bullet("Tags", note.get("tags")),
            _bullet("Source type", note.get("source_type")),
            _bullet("Created by", note.get("created_by_email")),
            _bullet("Created at", note.get("created_at")),
            _bullet("Updated at", note.get("updated_at")),
            "",
            "#### Source metadata",
            _json_block(note.get("source_metadata") or {}),
        ])
    return lines


def _persona_label(persona):
    if not persona:
        return None
    return " — ".join(
        str(value) for value in (persona.get("name"), persona.get("archetype"))
        if value
    ) or persona.get("slug")


def _checkbox_rows(items, text_key, done_key, empty):
    if not items:
        return [f"- _{empty}_"]
    lines = []
    for item in items:
        marker = "x" if item.get(done_key) else " "
        text = _escape(item.get(text_key) or "Untitled item")
        lines.append(f"- [{marker}] {_indent(text)}")
        if item.get(done_key):
            lines.append(f"  - Completed at: {_value(item.get(done_key))}")
    return lines


def _bullet(label, value):
    return f"- {label}: {_indent(_value(value))}"


def _value(value):
    if isinstance(value, _TrustedMarkdown):
        return value
    if value is None or value == "" or value == []:
        return "_Not specified._"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (list, tuple)):
        return ", ".join(_escape(item) for item in value) or "_Not specified._"
    return _escape(value)


def _escape(value):
    if value is None:
        return "_Not specified._"
    text = str(value).replace("\r\n", "\n").replace("\r", "\n")
    text = _MARKDOWN_CHARS.sub(r"\\\1", text)
    return text.replace("<", r"\<").replace(">", r"\>")


def _indent(value):
    return str(value).replace("\n", "\n  ")


def _json_block(value):
    serialized = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
    # Keep the fenced payload valid JSON while ensuring an HTML renderer never
    # sees literal tag delimiters, even when its Markdown configuration allows
    # raw HTML inside code blocks.
    serialized = serialized.replace("<", r"\u003c").replace(">", r"\u003e")
    longest = max((len(run) for run in re.findall(r"`+", serialized)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}json\n{serialized}\n{fence}"


def _safe_link(label, destination, *, same_site=False):
    if not destination:
        return None
    raw = str(destination).strip()
    if any(ord(character) < 32 for character in raw):
        return None
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return None
    if same_site:
        if not raw.startswith("/") or raw.startswith("//"):
            return None
        if parsed.query or parsed.fragment:
            return None
    else:
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return None
        if parsed.username or parsed.password:
            return None
        if not _query_keys_are_safe(parsed.query):
            return None
    encoded = urlunsplit((
        parsed.scheme,
        parsed.netloc,
        quote(parsed.path, safe="/%:@~!$&'+,;=-._"),
        quote(parsed.query, safe="=&%:@~!$'+,;/?-._"),
        "",
    ))
    return _TrustedMarkdown(f"[{_escape(label)}]({encoded})")


def _query_keys_are_safe(query):
    """Fail closed unless every query key is unambiguous and non-secret.

    Values are deliberately never decoded or inspected: once any key is
    unsafe the entire URL is discarded, so its values cannot reach output.
    Keys are decoded exactly once after strict percent-shape validation. A
    residual percent sign indicates double encoding and is treated as
    ambiguous rather than decoded a second time.
    """
    if not query:
        return True
    # Semicolons have historically been interpreted as alternate query
    # separators by frameworks and proxies. Reject that ambiguity rather than
    # letting two parsers disagree about which key guards a value.
    if ";" in query:
        return False

    seen = set()
    for pair in query.split("&"):
        if not pair:
            return False
        raw_key = pair.split("=", 1)[0]
        if not raw_key or _has_malformed_percent_escape(raw_key):
            return False
        try:
            decoded = unquote_plus(raw_key, encoding="utf-8", errors="strict")
        except (UnicodeDecodeError, ValueError):
            return False
        if "%" in decoded or not _QUERY_KEY_CHARS.fullmatch(decoded):
            return False

        canonical = decoded.casefold()
        if canonical in seen:
            return False
        seen.add(canonical)

        # Split camelCase before case-folding, then normalize every supported
        # separator. This makes accessToken, access-token, and access_token
        # equivalent for classification.
        camel_split = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", decoded)
        components = {
            component.casefold()
            for component in re.split(r"[._-]+", camel_split)
            if component
        }
        collapsed = "".join(
            component.casefold()
            for component in re.split(r"[._-]+", camel_split)
            if component
        )
        if components & _SECRET_KEY_COMPONENTS:
            return False
        # Catch only recognized separator-free compounds. Arbitrary substring
        # matching would incorrectly reject benign keys such as ``author``,
        # ``keyboard``, and ``monkey``.
        if collapsed in _SECRET_COMPOUND_KEYS:
            return False
    return True


def _has_malformed_percent_escape(value):
    stripped = _VALID_PERCENT_ESCAPE.sub("", value)
    return "%" in stripped
