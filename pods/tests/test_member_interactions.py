"""Slack hand-off and availability editor member view tests."""

from django.test import tag

from pods.tests.test_member_views import ENABLED, PodViewFixture, _pod, texts


@tag("core")
@ENABLED
class SlackHandOffTest(PodViewFixture):
    def setUp(self):
        for user, slack_id in ((self.anna, "UANNA"), (self.raj, "URAJ")):
            user.slack_member = True
            user.slack_user_id = slack_id
            user.save(update_fields=["slack_member", "slack_user_id"])

    def test_main_members_get_callout_and_message_links(self):
        pod = _pod(self.cohort, "Evening builders", members=[self.anna, self.raj])
        self.client.force_login(self.anna)
        response = self.client.get(self.pod_url(pod))
        self.assertEqual(texts(response, "pod-slack-suggested-name"), [f"#pod-{pod.pk}-evening-builders"])
        self.assertContains(response, 'href="https://app.slack.com/client/T0TEAM/URAJ"')
        self.assertNotContains(response, "https://app.slack.com/client/T0TEAM/UANNA")
        self.assertNotContains(response, 'data-testid="pod-slack-tier-warning"')

    def test_basic_member_sees_gate_and_main_member_sees_warning(self):
        pod = _pod(self.cohort, "Evening builders", members=[self.anna, self.basic])
        self.basic.slack_member = True
        self.basic.slack_user_id = "UBASIC"
        self.basic.save(update_fields=["slack_member", "slack_user_id"])
        self.client.force_login(self.basic)
        response = self.client.get(self.pod_url(pod))
        self.assertEqual(texts(response, "pod-slack-gate"), ["Slack is available to Main and Premium members."])
        self.assertContains(response, 'href="/membership"')
        self.assertNotContains(response, 'data-testid="pod-slack-input"')
        self.assertNotContains(response, 'data-testid="pod-member-slack"')
        self.client.force_login(self.anna)
        response = self.client.get(self.pod_url(pod))
        self.assertEqual(len(texts(response, "pod-slack-tier-warning")), 1)
        self.assertNotContains(response, "UBASIC")

    def test_link_validation_and_clearing(self):
        pod = _pod(self.cohort, "Evening builders", members=[self.anna, self.raj])
        self.client.force_login(self.anna)
        for bad in (
            "http://aishippinglabs.slack.com/archives/C1",
            "https://example.com/chan",
            "https://slack.com.evil.io/x",
        ):
            response = self.client.post(self.pod_url(pod, "/slack"), {"slack_channel_url": bad}, follow=True)
            self.assertEqual(
                texts(response, "pod-slack-error"),
                ["Paste a Slack channel link, for example https://yourworkspace.slack.com/archives/C0123456789."],
            )
        pod.refresh_from_db()
        self.assertEqual(pod.slack_channel_url, "")
        for good in ("https://aishippinglabs.slack.com/archives/C0123456789", "https://app.slack.com/client/T1/C2"):
            self.client.post(self.pod_url(pod, "/slack"), {"slack_channel_url": good})
            pod.refresh_from_db()
            self.assertEqual(pod.slack_channel_url, good)
        response = self.client.get(self.pod_url(pod))
        self.assertEqual(texts(response, "pod-slack-channel"), ["Slack channel - Open in Slack"])
        self.client.post(self.pod_url(pod, "/slack"), {"slack_channel_url": ""})
        pod.refresh_from_db()
        self.assertEqual(pod.slack_channel_url, "")

    def test_basic_member_cannot_set_link(self):
        pod = _pod(self.cohort, "Evening builders", members=[self.anna, self.basic])
        self.client.force_login(self.basic)
        response = self.client.post(
            self.pod_url(pod, "/slack"), {"slack_channel_url": "https://a.slack.com/archives/C1"}
        )
        self.assertEqual(response.status_code, 403)
        pod.refresh_from_db()
        self.assertEqual(pod.slack_channel_url, "")


@tag("core")
@ENABLED
class AvailabilityEditorTest(PodViewFixture):
    def _preference_for_day(self, day):
        if day == 4:
            return "if_needed"
        return "preferred"

    def test_save_and_overlap_error(self):
        self.client.force_login(self.mike)
        data = {"timezone": "America/New_York", "cohort": "4"}
        for day in range(5):
            data.update(
                {
                    f"window-{day}-weekday": str(day),
                    f"window-{day}-start": "18:00",
                    f"window-{day}-end": "21:00",
                    f"window-{day}-preference": self._preference_for_day(day),
                }
            )
        response = self.client.post(self.pods_url("/availability"), data, follow=True)
        days = texts(response, "availability-summary-day")
        self.assertEqual(days[0], "Monday 18:00-21:00")
        self.assertEqual(days[4], "Friday 18:00-21:00 (If needed)")
        data.update(
            {
                "window-9-weekday": "1",
                "window-9-start": "20:00",
                "window-9-end": "22:00",
                "window-9-preference": "preferred",
            }
        )
        response = self.client.post(self.pods_url("/availability"), data)
        self.assertEqual(
            texts(response, "availability-error"), ["Windows on Tuesday overlap. Merge them into one window."]
        )
        self.assertEqual(self.mike.availability_profile.windows.count(), 5)
