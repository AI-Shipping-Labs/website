"""Static guard against reintroducing local or direct outbound email."""

import ast

FORBIDDEN_MODULE_PARTS = (
    "email_app.services.email_service",
    "email_app.services.email_rendering",
    "email_app.services.email_errors",
    "email_app.services.relay_transport",
    "community_base.mail.backends.ses_local",
)
FORBIDDEN_SETTINGS = frozenset({
    "SES_FROM_EMAIL", "SES_TRANSACTIONAL_FROM_EMAIL",
    "SES_PROMOTIONAL_FROM_EMAIL", "SES_WELCOME_FROM_EMAIL",
    "SES_WELCOME_REPLY_TO_EMAIL", "SES_UNSUBSCRIBE_EMAIL",
    "SES_ENABLED", "AWS_SES_REGION", "SES_CONFIGURATION_SET_NAME",
})
FORBIDDEN_SENDS = frozenset({
    "send_email", "send_raw_email", "send_ses_email",
})
FORBIDDEN_CLIENTS = frozenset({
    "django.core.mail.backends.smtp.EmailBackend",
    "smtplib.SMTP",
    "smtplib.SMTP_SSL",
})
FORBIDDEN_BACKENDS = frozenset({
    "ses_local",
    "django.core.mail.backends.smtp.EmailBackend",
    "django_ses.SESBackend",
})


def source_violations(source, filename):
    tree = ast.parse(source, filename=filename)
    bindings = _bindings(tree)
    violations = []
    for node in ast.walk(tree):
        reason = _node_violation(node, bindings)
        if reason:
            violations.append(f"{filename}:{node.lineno}: {reason}")
    return violations


def _node_violation(node, bindings):
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        imported = _imported_names(node)
        if any(
            any(part in name for part in FORBIDDEN_MODULE_PARTS)
            for name in imported
        ):
            return "removed outbound email import"
        if imported & (FORBIDDEN_CLIENTS | {"django_ses.SESBackend"}):
            return "direct outbound email import"
    if isinstance(node, ast.Call):
        return _call_violation(node, bindings)
    if isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_SETTINGS:
        return "send-only SES setting"
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        if node.value in FORBIDDEN_SETTINGS:
            return "send-only SES setting"
        if node.value in FORBIDDEN_BACKENDS:
            return "forbidden outbound backend"
    return ""


def _imported_names(node):
    if isinstance(node, ast.Import):
        return {item.name for item in node.names}
    module = node.module or ""
    return {f"{module}.{item.name}" for item in node.names}


def _bindings(tree):
    bindings = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for item in node.names:
                bindings.setdefault(item.asname or item.name, []).append(item.name)
        elif isinstance(node, ast.ImportFrom):
            for item in node.names:
                name = item.asname or item.name
                bindings.setdefault(name, []).append(f"{node.module}.{item.name}")
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    bindings.setdefault(target.id, []).append(node.value)
    return bindings


def _resolve(node, bindings, seen=frozenset()):
    if isinstance(node, ast.Name):
        if node.id in seen:
            return set()
        values = bindings.get(node.id)
        if values is None:
            return {node.id}
        resolved = set()
        for value in values:
            if isinstance(value, str):
                resolved.add(value)
            else:
                resolved.update(_resolve(value, bindings, seen | {node.id}))
        return resolved
    if isinstance(node, ast.Attribute):
        return {f"{value}.{node.attr}" for value in _resolve(node.value, bindings, seen)}
    if isinstance(node, ast.Call):
        return _resolve(node.func, bindings, seen)
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {node.value}
    return set()


def _call_violation(node, bindings):
    names = _resolve(node.func, bindings)
    if any(name.rsplit(".", 1)[-1] in FORBIDDEN_SENDS for name in names):
        return "direct outbound email call"
    if names & {"django_ses.SESBackend"}:
        return "direct SES backend construction"
    if names & {"django.core.mail.backends.smtp.EmailBackend"}:
        return "direct SMTP backend construction"
    if names & FORBIDDEN_CLIENTS:
        return "direct SMTP client construction"
    if any(name.startswith("boto3.") and name.endswith(".client") for name in names):
        if _service_names(node, bindings) & {"ses", "sesv2"}:
            return "direct SES client construction"
    return ""


def _service_names(node, bindings):
    if node.args:
        return _resolve(node.args[0], bindings)
    for keyword in node.keywords:
        if keyword.arg == "service_name":
            return _resolve(keyword.value, bindings)
    return set()
