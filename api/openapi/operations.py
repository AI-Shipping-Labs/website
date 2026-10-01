"""Translate endpoint decorator metadata into OpenAPI operations."""


def _query_parameters(query_spec):
    """Translate decorator query metadata into OpenAPI parameters."""
    parameters = []
    for name, schema_meta in query_spec.items():
        required = False
        if isinstance(schema_meta, dict):
            required = schema_meta.pop("required", False)
        parameters.append({
            "name": name,
            "in": "query",
            "required": required,
            "schema": dict(schema_meta),
        })
    return parameters


def _header_parameters(header_spec):
    """Translate decorator header metadata into OpenAPI parameters."""
    parameters = []
    for name, schema_meta in header_spec.items():
        meta = dict(schema_meta)
        required = meta.pop("required", False)
        description = meta.pop("description", None)
        parameter = {
            "name": name,
            "in": "header",
            "required": required,
            "schema": meta,
        }
        if description:
            parameter["description"] = description
        parameters.append(parameter)
    return parameters


def _build_request_body(body_spec):
    """Translate request-body metadata into an OpenAPI request body."""
    schema = {"type": "object"}
    if "required" in body_spec:
        schema["required"] = list(body_spec["required"])
    if "properties" in body_spec:
        schema["properties"] = dict(body_spec["properties"])
    content = {"schema": schema}
    if "example" in body_spec:
        content["example"] = body_spec["example"]
    return {
        "required": body_spec.get("body_required", True),
        "content": {"application/json": content},
    }


def _build_responses(responses_spec):
    """Translate decorator response metadata into OpenAPI responses."""
    responses = {}
    for status, meta in responses_spec.items():
        node = {"description": meta.get("description", "")}
        if "example" in meta:
            node["content"] = {
                "application/json": {"example": meta["example"]},
            }
        if "schema" in meta:
            content = node.setdefault("content", {}).setdefault(
                "application/json", {},
            )
            content["schema"] = meta["schema"]
        responses[str(status)] = node
    return responses


def operation_from_method_spec(method_meta, default_summary, tag):
    """Assemble one OpenAPI operation from per-method decorator data."""
    operation = {
        "tags": [tag],
        "summary": method_meta.get("summary") or default_summary or "",
    }
    if "description" in method_meta:
        operation["description"] = method_meta["description"]
    parameters = []
    if "query" in method_meta:
        parameters.extend(_query_parameters(method_meta["query"]))
    if "headers" in method_meta:
        parameters.extend(_header_parameters(method_meta["headers"]))
    if parameters:
        operation["parameters"] = parameters
    if "request_body" in method_meta:
        operation["requestBody"] = _build_request_body(method_meta["request_body"])
    if "responses" in method_meta:
        operation["responses"] = _build_responses(method_meta["responses"])
    else:
        operation["responses"] = {"200": {"description": "Success"}}
    if "security" in method_meta and method_meta["security"] is not None:
        operation["security"] = method_meta["security"]
    return operation
