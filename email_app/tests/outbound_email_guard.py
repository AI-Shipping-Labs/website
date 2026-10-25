"""Static guard against reintroducing local or direct outbound email."""

import ast

FORBIDDEN_MODULE_PARTS = (
    "email_app.services.email_service",
    "email_app.services.email_rendering",
    "community_base.mail.backends.ses_local",
)
FORBIDDEN_SETTINGS = frozenset(
    {
        "SES_FROM_EMAIL", "SES_TRANSACTIONAL_FROM_EMAIL",
        "SES_PROMOTIONAL_FROM_EMAIL", "SES_WELCOME_FROM_EMAIL",
        "SES_WELCOME_REPLY_TO_EMAIL", "SES_UNSUBSCRIBE_EMAIL",
        "SES_ENABLED", "AWS_SES_REGION", "SES_CONFIGURATION_SET_NAME",
    }
)
FORBIDDEN_SENDS = frozenset({"send_email", "send_raw_email", "send_ses_email"})


def source_violations(source, filename):
    tree = ast.parse(source, filename=filename)
    violations = []
    for node in ast.walk(tree):
        reason = _node_violation(node)
        if reason:
            violations.append(f"{filename}:{node.lineno}: {reason}")
    return violations


def _node_violation(node):
    if isinstance(node, ast.ImportFrom) and node.module:
        if any(part in node.module for part in FORBIDDEN_MODULE_PARTS):
            return "removed outbound email import"
    if isinstance(node, ast.Call) and _call_name(node.func) in FORBIDDEN_SENDS:
        return "direct outbound email call"
    if isinstance(node, ast.Call) and _is_ses_client(node):
        return "direct SES client construction"
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        if node.value in FORBIDDEN_SETTINGS:
            return "send-only SES setting"
        if node.value in {"ses_local", "django.core.mail.backends.smtp.EmailBackend"}:
            return "forbidden outbound backend"
    return ""


def _call_name(node):
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return ""


def _is_ses_client(node):
    if _call_name(node.func) != "client":
        return False
    values = [argument.value for argument in node.args if isinstance(argument, ast.Constant)]
    values.extend(
        keyword.value.value
        for keyword in node.keywords
        if keyword.arg == "service_name" and isinstance(keyword.value, ast.Constant)
    )
    return any(value in {"ses", "sesv2"} for value in values)
