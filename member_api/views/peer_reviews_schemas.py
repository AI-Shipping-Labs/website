"""OpenAPI schemas for member peer-review endpoints."""

_ERROR_RESPONSE = {
    "description": "Error response.",
    "content": {
        "application/json": {
            "schema": {"$ref": "#/components/schemas/ErrorResponse"},
        },
    },
}

_COURSE_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "integer"},
        "slug": {"type": "string"},
        "title": {"type": "string"},
    },
    "required": ["id", "slug", "title"],
}

_PROJECT_SCHEMA = {
    "type": ["object", "null"],
    "properties": {
        "id": {"type": "integer"},
        "slug": {"type": "string"},
        "title": {"type": "string"},
        "state": {"type": "string", "enum": ["CL", "CS", "PR", "CO"]},
        "state_label": {"type": "string"},
        "review_due_at": {"type": ["string", "null"], "format": "date-time"},
        "is_self_paced": {"type": "boolean"},
        "review_open": {"type": "boolean"},
    },
    "required": [
        "id",
        "slug",
        "title",
        "state",
        "state_label",
        "review_due_at",
        "is_self_paced",
        "review_open",
    ],
}

_SUBMISSION_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "integer"},
        "author_label": {"type": "string"},
        "project_url": {"type": "string"},
        "commit_id": {"type": "string"},
        "description": {"type": "string"},
        "learning_in_public_links": {
            "type": "array",
            "items": {"type": "string"},
        },
        "time_spent": {"type": ["number", "null"]},
        "submitted_at": {"type": "string", "format": "date-time"},
        "review_deadline": {"type": ["string", "null"], "format": "date-time"},
    },
    "required": [
        "id",
        "author_label",
        "project_url",
        "commit_id",
        "description",
        "learning_in_public_links",
        "time_spent",
        "submitted_at",
        "review_deadline",
    ],
}

_CRITERION_OPTION_SCHEMA = {
    "type": "object",
    "properties": {
        "index": {"type": "integer", "minimum": 0},
        "points": {"type": "integer", "minimum": 0},
        "text": {"type": "string"},
    },
    "required": ["index", "points", "text"],
}

_CRITERION_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "title": {"type": "string"},
        "description": {"type": "string"},
        "type": {"type": "string", "enum": ["scale", "checklist"]},
        "max_points": {"type": "integer", "minimum": 0},
        "options": {"type": "array", "items": _CRITERION_OPTION_SCHEMA},
    },
    "required": ["id", "title", "description", "type", "max_points", "options"],
}

_SUMMARY_PROPERTIES = {
    "id": {"type": "integer"},
    "submission_id": {"type": "integer"},
    "course": _COURSE_SCHEMA,
    "project": _PROJECT_SCHEMA,
    "submission": _SUBMISSION_SCHEMA,
    "is_complete": {"type": "boolean"},
    "assigned_at": {"type": "string", "format": "date-time"},
    "completed_at": {"type": ["string", "null"], "format": "date-time"},
    "feedback": {"type": "string"},
    "score": {"type": ["integer", "null"], "minimum": 1, "maximum": 5},
    "criteria_scores": {"type": "object"},
    "total_points": {"type": "integer", "minimum": 0},
    "has_criteria": {"type": "boolean"},
}

PEER_REVIEW_SUMMARY_SCHEMA = {
    "type": "object",
    "properties": _SUMMARY_PROPERTIES,
    "required": list(_SUMMARY_PROPERTIES),
}

_DETAIL_PROPERTIES = {
    **_SUMMARY_PROPERTIES,
    "criteria": {"type": "array", "items": _CRITERION_SCHEMA},
    "criteria_max_points": {
        "type": "object",
        "properties": {
            "core": {"type": "integer", "minimum": 0},
            "bonus": {"type": "integer", "minimum": 0},
        },
        "required": ["core", "bonus"],
    },
    "passing_score": {"type": ["integer", "null"], "minimum": 0},
}

PEER_REVIEW_DETAIL_SCHEMA = {
    "type": "object",
    "properties": _DETAIL_PROPERTIES,
    "required": list(_DETAIL_PROPERTIES),
}

_PAGINATION_SCHEMA = {
    "type": "object",
    "properties": {
        "page": {"type": "integer", "minimum": 1},
        "page_size": {"type": "integer", "const": 20},
        "total": {"type": "integer", "minimum": 0},
        "total_pages": {"type": "integer", "minimum": 0},
    },
    "required": ["page", "page_size", "total", "total_pages"],
}

PEER_REVIEWS_COLLECTION_OPENAPI = {
    "get": {
        "summary": "List peer reviews assigned to the authenticated member",
        "parameters": [
            {
                "name": "course",
                "in": "query",
                "required": False,
                "schema": {"type": "string"},
                "description": "Filter by course slug.",
            },
            {
                "name": "project",
                "in": "query",
                "required": False,
                "schema": {"type": "string"},
                "description": "Filter by project attempt slug.",
            },
            {
                "name": "status",
                "in": "query",
                "required": False,
                "schema": {
                    "type": "string",
                    "enum": ["all", "pending", "completed"],
                    "default": "all",
                },
                "description": "Filter by review completion status.",
            },
            {
                "name": "page",
                "in": "query",
                "required": False,
                "schema": {"type": "integer", "minimum": 1, "default": 1},
            },
        ],
        "responses": {
            "200": {
                "description": "Paginated list of assigned peer reviews.",
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "properties": {
                                "peer_reviews": {
                                    "type": "array",
                                    "items": PEER_REVIEW_SUMMARY_SCHEMA,
                                },
                                "pagination": _PAGINATION_SCHEMA,
                            },
                            "required": ["peer_reviews", "pagination"],
                        },
                    },
                },
            },
            "401": _ERROR_RESPONSE,
            "422": _ERROR_RESPONSE,
        },
    },
}

_REVIEW_ID_PARAM = {
    "name": "review_id",
    "in": "path",
    "required": True,
    "schema": {"type": "integer"},
}

_UPDATE_REQUEST_BODY = {
    "required": True,
    "content": {
        "application/json": {
            "schema": {
                "type": "object",
                "properties": {
                    "criteria_scores": {
                        "type": "object",
                        "description": (
                            "Map of criterion ID to option index (integer for "
                            "scale criteria, list of integers for checklist)."
                        ),
                    },
                    "feedback": {"type": "string"},
                    "score": {
                        "type": ["integer", "null"],
                        "minimum": 1,
                        "maximum": 5,
                        "description": "Legacy 1-5 score when no criteria exist.",
                    },
                    "draft": {
                        "type": "boolean",
                        "default": False,
                        "description": "Save a draft without marking complete.",
                    },
                },
            },
        },
    },
}

_WRITE_RESPONSES = {
    "200": {
        "description": "Updated peer review detail.",
        "content": {
            "application/json": {"schema": PEER_REVIEW_DETAIL_SCHEMA},
        },
    },
    "400": _ERROR_RESPONSE,
    "401": _ERROR_RESPONSE,
    "404": _ERROR_RESPONSE,
    "409": _ERROR_RESPONSE,
    "422": _ERROR_RESPONSE,
}

PEER_REVIEW_DETAIL_OPENAPI = {
    "get": {
        "summary": "Get an assigned peer review, submission details, and criteria",
        "parameters": [_REVIEW_ID_PARAM],
        "responses": {
            "200": {
                "description": "Peer review detail with evaluation criteria.",
                "content": {
                    "application/json": {"schema": PEER_REVIEW_DETAIL_SCHEMA},
                },
            },
            "401": _ERROR_RESPONSE,
            "404": _ERROR_RESPONSE,
        },
    },
    "put": {
        "summary": "Submit or replace an assigned peer review",
        "parameters": [_REVIEW_ID_PARAM],
        "requestBody": _UPDATE_REQUEST_BODY,
        "responses": _WRITE_RESPONSES,
    },
    "patch": {
        "summary": "Submit or partially update an assigned peer review",
        "parameters": [_REVIEW_ID_PARAM],
        "requestBody": _UPDATE_REQUEST_BODY,
        "responses": _WRITE_RESPONSES,
    },
}
