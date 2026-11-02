"""Ownership and domain-contract coverage for issue #1535."""

import ast
import importlib.util
import json
from datetime import date, datetime
from types import SimpleNamespace

from django.test import SimpleTestCase, TestCase

from api.actors import actor_label
from api.request_parsing import (
    coerce_datetime,
    normalize_emails,
    parse_account_lifecycle,
    parse_iso_date,
    parse_limit,
    parse_offset,
    parse_since,
)
from questionnaires.models import Persona, Questionnaire
from questionnaires.services import (
    persona_map_by_questionnaire,
    resolve_persona_for_questionnaire,
)


def _module_tree(module_name):
    spec = importlib.util.find_spec(module_name)
    if spec is None or spec.origin is None:
        raise AssertionError(f'Cannot locate {module_name}')
    with open(spec.origin, encoding='utf-8') as source:
        return ast.parse(source.read(), filename=spec.origin)


class SharedHelperImportGraphTest(SimpleTestCase):
    view_modules = (
        'api.views.crm_export',
        'api.views.questionnaire_responses',
        'api.views.onboarding',
        'api.views.email_log',
        'api.views.ses_events_list',
    )
    public_owner_modules = (
        'api.actors',
        'api.course_lookup',
        'api.permissions',
        'api.request_parsing',
        'api.user_lookup',
        'api.serializers.crm',
        'api.serializers.enrollments',
        'questionnaires.services',
        'crm.services.persona',
    )

    def test_named_views_do_not_import_private_names_from_other_views(self):
        violations = []
        for module_name in self.view_modules:
            for node in ast.walk(_module_tree(module_name)):
                if not (
                    isinstance(node, ast.ImportFrom)
                    and node.module
                    and node.module.startswith('api.views.')
                ):
                    continue
                for imported in node.names:
                    if imported.name.startswith('_'):
                        violations.append(
                            f'{module_name}: {node.module}.{imported.name}',
                        )
        self.assertEqual(violations, [])

    def test_public_owner_modules_do_not_depend_on_api_views(self):
        violations = []
        for module_name in self.public_owner_modules:
            for node in ast.walk(_module_tree(module_name)):
                if (
                    isinstance(node, ast.ImportFrom)
                    and node.module
                    and node.module.startswith('api.views')
                ):
                    violations.append(f'{module_name}: {node.module}')
                elif isinstance(node, ast.Import):
                    for imported in node.names:
                        if imported.name.startswith('api.views'):
                            violations.append(
                                f'{module_name}: {imported.name}',
                            )
        self.assertEqual(violations, [])

    def test_old_view_implementations_are_deleted(self):
        forbidden_definitions = {
            'api.views.enrollments': {
                '_serialize_enrollment',
                '_normalize_emails',
            },
            'api.views.course_enrollments': {
                '_serialize_enrollment',
                '_normalize_emails',
                '_get_published_course',
            },
            'api.views.crm_export': {'serialize_crm_record_full'},
            'api.views.onboarding': {
                '_parse_offset',
                '_persona_map',
                '_resolve_persona',
            },
            'api.views.users': {
                '_find_user',
                '_parse_account_lifecycle',
                '_parse_limit',
                '_parse_since',
                '_user_not_found_response',
                'resolve_crm_persona',
                'serialize_crm_record_for_operator',
                'serialize_crm_record_summary',
                '_actor_label',
            },
            'api.views.aliases': {'_actor_label'},
            'api.views.submissions_on_behalf': {'_actor_label'},
            'api.views.payment_mismatches': {'_actor_label'},
            'api.views.user_merge': {'_actor_label'},
            'api.views.tier_overrides': {'_actor_label'},
            'api.views.campaigns': {'_read_only_field_response'},
            'api.views.events': {'_read_only_field_response'},
            'api.views.marketing_pages': {'_read_only_field_response'},
            'api.views.course_certificates': {'_get_published_course'},
            'api.views.weeks': {
                '_load_plan_for_write',
                '_load_week_for_write',
            },
            'api.views.checkpoints': {
                '_coerce_datetime',
                '_load_week_for_write',
            },
            'api.views.plan_items': {
                '_coerce_datetime',
                '_load_plan_for_write',
            },
            'api.views.plans': {'_coerce_datetime'},
            'api.views.books': {'_parse_iso_date'},
            'api.views.sprints': {'_parse_iso_date'},
            'studio.views.ses_events': {'_parse_iso_date'},
            'api.views.plan_sprints_ingest': {'_parse_limit'},
            'api.views.worker': {'_parse_limit', '_parse_since'},
            'api.views.stripe_webhooks': {'_dt'},
            'crm.services.member_profile': {'_persona_label'},
        }
        violations = []
        for module_name, forbidden in forbidden_definitions.items():
            definitions = {
                node.name
                for node in ast.walk(_module_tree(module_name))
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
            for name in sorted(definitions & forbidden):
                violations.append(f'{module_name}.{name}')
        self.assertEqual(violations, [])


class SharedRequestParsingContractTest(SimpleTestCase):
    def assert_validation_error(self, response, *, field, value):
        self.assertEqual(response.status_code, 422)
        payload = json.loads(response.content)
        self.assertEqual(payload['code'], 'validation_error')
        self.assertEqual(payload['details']['field'], field)
        self.assertEqual(payload['details']['value'], value)

    def test_limit_and_offset_keep_existing_bounds_and_error_fields(self):
        self.assertEqual(parse_limit(None), (50, None))
        self.assertEqual(parse_limit('250'), (200, None))
        self.assertEqual(parse_offset('12'), (12, None))

        _, limit_error = parse_limit('zero')
        self.assert_validation_error(limit_error, field='limit', value='zero')
        _, offset_error = parse_offset('-1')
        self.assert_validation_error(offset_error, field='offset', value='-1')

    def test_since_and_account_lifecycle_keep_existing_contract(self):
        parsed, error = parse_since('2026-09-09T10:30:00Z')
        self.assertIsNone(error)
        self.assertEqual(parsed.isoformat(), '2026-09-09T10:30:00+00:00')

        _, since_error = parse_since('yesterday')
        self.assert_validation_error(
            since_error,
            field='since',
            value='yesterday',
        )
        _, lifecycle_error = parse_account_lifecycle('unknown')
        self.assert_validation_error(
            lifecycle_error,
            field='account_lifecycle',
            value='unknown',
        )
        self.assertIn('allowed', json.loads(lifecycle_error.content)['details'])

    def test_shared_normalizers_keep_existing_behavior(self):
        self.assertEqual(
            normalize_emails([
                ' Alice@Example.com ',
                'alice@example.com',
                None,
                'BOB@example.com',
                '',
            ]),
            ['alice@example.com', 'bob@example.com'],
        )
        self.assertEqual(parse_iso_date('2026-10-30'), date(2026, 10, 30))
        self.assertIsNone(parse_iso_date('not-a-date'))
        self.assertIsNone(parse_iso_date(None))
        original = datetime(2026, 10, 30, 12, 0)
        self.assertIs(coerce_datetime(original), original)
        self.assertIsNone(coerce_datetime(''))
        self.assertEqual(
            coerce_datetime('2026-10-30T12:00:00+00:00'),
            datetime.fromisoformat('2026-10-30T12:00:00+00:00'),
        )

    def test_actor_label_prefers_name_then_masked_prefix(self):
        named = SimpleNamespace(name='Plan sync', key_prefix='tok_abcd')
        unnamed = SimpleNamespace(name='', key_prefix='tok_efgh')
        self.assertEqual(
            actor_label(SimpleNamespace(auth_token=named)),
            'Plan sync',
        )
        self.assertEqual(
            actor_label(SimpleNamespace(auth_token=unnamed)),
            'tok_efgh',
        )
        self.assertEqual(actor_label(SimpleNamespace()), 'unknown')


class QuestionnairePersonaServiceTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.questionnaire = Questionnaire.objects.create(
            title='Persona owner questionnaire',
            slug='persona-owner-1535',
            purpose='onboarding',
        )
        cls.generic = Questionnaire.objects.create(
            title='Generic questionnaire',
            slug='generic-owner-1535',
            purpose='onboarding',
        )
        Persona.objects.create(
            name='Inactive first',
            archetype='Inactive',
            slug='inactive-first-1535',
            default_questionnaire=cls.questionnaire,
            is_active=False,
            order=0,
        )
        cls.second = Persona.objects.create(
            name='Zeta',
            archetype='Second',
            slug='zeta-1535',
            default_questionnaire=cls.questionnaire,
            order=1,
        )
        cls.first = Persona.objects.create(
            name='Alpha',
            archetype='First',
            slug='alpha-1535',
            default_questionnaire=cls.questionnaire,
            order=1,
        )

    def test_resolution_and_bulk_map_choose_first_active_by_order_and_name(self):
        self.assertEqual(
            resolve_persona_for_questionnaire(self.questionnaire),
            self.first,
        )
        mapping = persona_map_by_questionnaire()
        self.assertEqual(mapping[self.questionnaire.pk], self.first)
        self.assertNotIn(self.generic.pk, mapping)

    def test_generic_and_precomputed_resolution_do_not_query(self):
        with self.assertNumQueries(1):
            self.assertIsNone(resolve_persona_for_questionnaire(self.generic))

        mapping = {self.questionnaire.pk: self.second}
        with self.assertNumQueries(0):
            resolved = resolve_persona_for_questionnaire(
                self.questionnaire,
                personas_by_questionnaire=mapping,
            )
        self.assertEqual(resolved, self.second)
