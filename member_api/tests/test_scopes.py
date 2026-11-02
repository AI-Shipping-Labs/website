from types import SimpleNamespace

from django.test import SimpleTestCase

from member_api.scopes import require_scope


class RequireScopeTest(SimpleTestCase):
    def test_matching_scope_allows_request(self):
        request = SimpleNamespace(member_api_scopes={"books:read"})

        self.assertIsNone(require_scope(request, "books:read"))

    def test_missing_scope_returns_contract_error(self):
        response = require_scope(SimpleNamespace(), "plans:write")

        self.assertEqual(response.status_code, 401)
        self.assertJSONEqual(
            response.content,
            {
                "error": "Member API key is missing the required scope",
                "code": "insufficient_scope",
                "details": {"required_scope": "plans:write"},
            },
        )
