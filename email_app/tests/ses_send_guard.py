"""Static SES bypass detector used by the Relay regression tests."""

import ast

FORBIDDEN_SENDS = {"send_email", "send_raw_email", "send_ses_email"}
FORBIDDEN_BACKENDS = {
    "ses_local", "django.core.mail.backends.smtp.EmailBackend", "django_ses.SESBackend",
}


def _resolve(node, bindings, seen=frozenset()):
    if isinstance(node, ast.Name):
        return _name_values(node.id, bindings, seen)
    if isinstance(node, ast.Attribute):
        return {f"{value}.{node.attr}" for value in _resolve(node.value, bindings, seen)}
    if isinstance(node, ast.Call):
        return _resolve(node.func, bindings, seen)
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {node.value}
    return set()


def _name_values(name, bindings, seen):
    if name in seen:
        return set()
    if name not in bindings:
        return {name}
    values = set()
    for binding in bindings[name]:
        if isinstance(binding, str):
            values.add(binding)
        else:
            values.update(_resolve(binding, bindings, seen | {name}))
    return values


def _bindings(tree):
    # Keep every possible binding: later reassignment or a different local
    # scope must never erase evidence of an earlier SES client construction.
    bindings = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for imported in node.names:
                bindings.setdefault(imported.asname or imported.name, []).append(imported.name)
        if isinstance(node, ast.ImportFrom):
            for imported in node.names:
                name = imported.asname or imported.name
                bindings.setdefault(name, []).append(f"{node.module}.{imported.name}")
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    bindings.setdefault(target.id, []).append(node.value)
    return bindings


def _service_names(node, bindings):
    if node.args:
        return _resolve(node.args[0], bindings)
    for keyword in node.keywords:
        if keyword.arg == "service_name":
            return _resolve(keyword.value, bindings)
    return set()


def _call_violation(node, bindings):
    for name in _resolve(node.func, bindings):
        if name.split(".")[-1] in FORBIDDEN_SENDS:
            return "direct SES send call"
        if name.startswith("boto3.") and name.endswith(".client"):
            if _service_names(node, bindings).intersection({"ses", "sesv2"}):
                return "boto3 SES client construction"
    return None


def source_violations(source, filename):
    tree = ast.parse(source, filename=filename)
    bindings = _bindings(tree)
    violations = []
    for node in ast.walk(tree):
        reason = None
        if isinstance(node, ast.Call):
            reason = _call_violation(node, bindings)
        if isinstance(node, ast.ImportFrom) and node.module:
            if "ses_transport" in node.module:
                reason = "deleted SES transport import"
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if filename != "email_app/checks.py" and node.value in FORBIDDEN_BACKENDS:
                reason = "forbidden mail backend"
        if reason:
            violations.append(f"{filename}:{node.lineno}: {reason}")
    return violations
