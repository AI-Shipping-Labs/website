"""Immutable initial ceilings for visible Python source-inspection syntax.

Only the live manifest may shrink as anchors are rewritten. These tuples and
their reviewed golden digests remain fixed at the #1453 origin baseline.
"""

INSPECT_API_REFERENCE_CEILING: tuple[str, ...] = (
    "api/tests/test_campaigns.py::CampaignsSendPathSentinelTest.test_module_source_does_not_mention_send_campaign::visible-python-source-inspection::4bac986bba7c28d6ec2b7465f2455b3b90b441450eda6caa921b46b1a42a6985::1",
    "comments/tests/test_threads.py::ThreadOwnerRegistryTest.test_notification_resolver_model_set_matches_registry::visible-python-source-inspection::4bac986bba7c28d6ec2b7465f2455b3b90b441450eda6caa921b46b1a42a6985::1",
    "crm/tests/test_plan_sprint_parse.py::ParsePlanSprintImportIsolationTest.test_module_source_imports_no_django_or_app_models::visible-python-source-inspection::4bac986bba7c28d6ec2b7465f2455b3b90b441450eda6caa921b46b1a42a6985::1",
    "email_app/tests/test_email_markdown_parity.py::NoParallelMarkdownEntryPointTest.test_email_modules_do_not_call_markdown_library_directly::visible-python-source-inspection::4bac986bba7c28d6ec2b7465f2455b3b90b441450eda6caa921b46b1a42a6985::1",
    "integrations/tests/test_feedback_synthesis.py::FeedbackSynthesisImportIsolationTest.test_module_source_imports_no_django_or_app_models::visible-python-source-inspection::4bac986bba7c28d6ec2b7465f2455b3b90b441450eda6caa921b46b1a42a6985::1",
    "plans/tests/test_first_sprint_draft.py::FirstSprintDraftCallableTest.test_module_stays_django_independent::visible-python-source-inspection::4bac986bba7c28d6ec2b7465f2455b3b90b441450eda6caa921b46b1a42a6985::1",
    "plans/tests/test_next_sprint_draft.py::DraftImportIsolationTest.test_module_source_imports_no_django_or_app_models::visible-python-source-inspection::4bac986bba7c28d6ec2b7465f2455b3b90b441450eda6caa921b46b1a42a6985::1",
    "questionnaires/tests/test_onboarding_ai_core.py::OnboardingAiImportIsolationTest.test_module_source_imports_no_django_or_app_models::visible-python-source-inspection::4bac986bba7c28d6ec2b7465f2455b3b90b441450eda6caa921b46b1a42a6985::1",
    "studio/tests/test_assistant.py::AssistantImportIsolationTest.test_no_direct_anthropic_import_or_hardcoded_model::visible-python-source-inspection::4bac986bba7c28d6ec2b7465f2455b3b90b441450eda6caa921b46b1a42a6985::1",
)
INSPECT_API_IMPORT_CEILING: tuple[str, ...] = ()
UNKNOWN_DYNAMIC_INSPECT_REFERENCE_CEILING: tuple[str, ...] = ()
DIRECT_PY_OPEN_CEILING: tuple[str, ...] = ()
DIRECT_PY_PATH_READ_CEILING: tuple[str, ...] = ()
UNKNOWN_VISIBLE_PY_PATH_READ_CEILING: tuple[str, ...] = (
    "content/tests/test_design_system_lint.py::DesignSystemWorkshopMediaContractTest.setUpClass::visible-python-source-inspection::21178897c66f264a210bfc3a68f763fae33e72fe91cf050a5817b4f5a5ed3a60::1",
    "content/tests/test_design_system_lint.py::DesignSystemWorkshopMediaContractTest.setUpClass::visible-python-source-inspection::33b05ddee9b5fbeab9c39286f0ffe542d7751095615d93fcbf19a2627eddeeff::1",
    "tests/test_tailwind_build.py::TailwindSourceContractTest.test_runtime_token_is_refactored_without_a_safelist::visible-python-source-inspection::77e8906a61a95671d485f1e312e0af7d894c16955155323b53516fed651c6dc3::1",
)

INSPECT_API_REFERENCE_GOLDEN_SHA256 = "1bb0c3f04f5d2aa45559b998479c31a671dbcfd87b19ba031afa0921da51b96d"
INSPECT_API_IMPORT_GOLDEN_SHA256 = "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"
UNKNOWN_DYNAMIC_INSPECT_REFERENCE_GOLDEN_SHA256 = (
    "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"
)
DIRECT_PY_OPEN_GOLDEN_SHA256 = "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"
DIRECT_PY_PATH_READ_GOLDEN_SHA256 = "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"
UNKNOWN_VISIBLE_PY_PATH_READ_GOLDEN_SHA256 = "eae2abd83d4097f252b6287ad9a3e925701d03987ff81e0bd3753e947eb9bdbe"

EXPECTED_CEILING_COUNTS = {
    "INSPECT_API_REFERENCE": 9,
    "INSPECT_API_IMPORT": 0,
    "UNKNOWN_DYNAMIC_INSPECT_REFERENCE": 0,
    "DIRECT_PY_OPEN": 0,
    "DIRECT_PY_PATH_READ": 0,
    "UNKNOWN_VISIBLE_PY_PATH_READ": 3,
}
