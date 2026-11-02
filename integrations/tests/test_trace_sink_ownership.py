from django.test import SimpleTestCase

from crm.services.plan_sprint_parse import TraceSink as PlanSprintTraceSink
from integrations.services.ai_eval.trace import FileTraceSink
from integrations.services.feedback_synthesis import (
    TraceSink as FeedbackTraceSink,
)
from integrations.services.llm import TraceSink
from plans.services.first_sprint_draft import TraceSink as FirstDraftTraceSink
from plans.services.next_sprint_draft import TraceSink as NextDraftTraceSink
from questionnaires.onboarding_ai import TraceSink as OnboardingTraceSink


class SharedTraceSinkTest(SimpleTestCase):
    def test_ai_callables_reexport_the_shared_noop_sink(self):
        for trace_sink in (
            PlanSprintTraceSink,
            FeedbackTraceSink,
            FirstDraftTraceSink,
            NextDraftTraceSink,
            OnboardingTraceSink,
        ):
            with self.subTest(trace_sink=trace_sink):
                self.assertIs(trace_sink, TraceSink)
        self.assertTrue(issubclass(FileTraceSink, TraceSink))

    def test_shared_sink_hooks_remain_noops(self):
        sink = TraceSink()

        self.assertIsNone(sink.on_request(system="s", messages=[], tool=None))
        self.assertIsNone(sink.on_result(result=None, latency_seconds=0))
        self.assertIsNone(sink.on_parsed(parsed=None))
        self.assertIsNone(sink.on_error(error=None))
