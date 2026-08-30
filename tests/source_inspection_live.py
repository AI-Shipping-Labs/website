"""Shrink-only live manifest for visible Python source-inspection syntax."""

INSPECT_REFERENCE_REWRITE_REASON = (
    "Replace the visible inspect API reference with a behavioral test against the owned public outcome."
)
UNKNOWN_PATH_REWRITE_REASON = (
    "Replace the unsupported visible Python path read with a behavioral test; opaque runtime paths are outside v1."
)

SOURCE_INSPECTION_LIVE = {
    "schema_version": 1,
    "INSPECT_API_REFERENCE": {
        "api/tests/test_campaigns.py::CampaignsSendPathSentinelTest.test_module_source_does_not_mention_send_campaign::visible-python-source-inspection::4bac986bba7c28d6ec2b7465f2455b3b90b441450eda6caa921b46b1a42a6985::1": INSPECT_REFERENCE_REWRITE_REASON,
        "comments/tests/test_threads.py::ThreadOwnerRegistryTest.test_notification_resolver_model_set_matches_registry::visible-python-source-inspection::4bac986bba7c28d6ec2b7465f2455b3b90b441450eda6caa921b46b1a42a6985::1": INSPECT_REFERENCE_REWRITE_REASON,
        "crm/tests/test_plan_sprint_parse.py::ParsePlanSprintImportIsolationTest.test_module_source_imports_no_django_or_app_models::visible-python-source-inspection::4bac986bba7c28d6ec2b7465f2455b3b90b441450eda6caa921b46b1a42a6985::1": INSPECT_REFERENCE_REWRITE_REASON,
        "email_app/tests/test_email_markdown_parity.py::NoParallelMarkdownEntryPointTest.test_email_modules_do_not_call_markdown_library_directly::visible-python-source-inspection::4bac986bba7c28d6ec2b7465f2455b3b90b441450eda6caa921b46b1a42a6985::1": INSPECT_REFERENCE_REWRITE_REASON,
        "integrations/tests/test_feedback_synthesis.py::FeedbackSynthesisImportIsolationTest.test_module_source_imports_no_django_or_app_models::visible-python-source-inspection::4bac986bba7c28d6ec2b7465f2455b3b90b441450eda6caa921b46b1a42a6985::1": INSPECT_REFERENCE_REWRITE_REASON,
        "plans/tests/test_first_sprint_draft.py::FirstSprintDraftCallableTest.test_module_stays_django_independent::visible-python-source-inspection::4bac986bba7c28d6ec2b7465f2455b3b90b441450eda6caa921b46b1a42a6985::1": INSPECT_REFERENCE_REWRITE_REASON,
        "plans/tests/test_next_sprint_draft.py::DraftImportIsolationTest.test_module_source_imports_no_django_or_app_models::visible-python-source-inspection::4bac986bba7c28d6ec2b7465f2455b3b90b441450eda6caa921b46b1a42a6985::1": INSPECT_REFERENCE_REWRITE_REASON,
        "questionnaires/tests/test_onboarding_ai_core.py::OnboardingAiImportIsolationTest.test_module_source_imports_no_django_or_app_models::visible-python-source-inspection::4bac986bba7c28d6ec2b7465f2455b3b90b441450eda6caa921b46b1a42a6985::1": INSPECT_REFERENCE_REWRITE_REASON,
        "studio/tests/test_assistant.py::AssistantImportIsolationTest.test_no_direct_anthropic_import_or_hardcoded_model::visible-python-source-inspection::4bac986bba7c28d6ec2b7465f2455b3b90b441450eda6caa921b46b1a42a6985::1": INSPECT_REFERENCE_REWRITE_REASON,
    },
    "INSPECT_API_IMPORT": {},
    "UNKNOWN_DYNAMIC_INSPECT_REFERENCE": {},
    "DIRECT_PY_OPEN": {},
    "DIRECT_PY_PATH_READ": {},
    "UNKNOWN_VISIBLE_PY_PATH_READ": {
        "content/tests/test_design_system_lint.py::DesignSystemWorkshopMediaContractTest.setUpClass::visible-python-source-inspection::21178897c66f264a210bfc3a68f763fae33e72fe91cf050a5817b4f5a5ed3a60::1": UNKNOWN_PATH_REWRITE_REASON,
        "content/tests/test_design_system_lint.py::DesignSystemWorkshopMediaContractTest.setUpClass::visible-python-source-inspection::33b05ddee9b5fbeab9c39286f0ffe542d7751095615d93fcbf19a2627eddeeff::1": UNKNOWN_PATH_REWRITE_REASON,
        "tests/test_tailwind_build.py::TailwindSourceContractTest.test_runtime_token_is_refactored_without_a_safelist::visible-python-source-inspection::77e8906a61a95671d485f1e312e0af7d894c16955155323b53516fed651c6dc3::1": UNKNOWN_PATH_REWRITE_REASON,
    },
}
