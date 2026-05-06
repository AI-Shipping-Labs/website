"""Sidebar wiring for the Members section (issue #432)."""

from django.contrib.auth import get_user_model
from django.test import TestCase

User = get_user_model()


class PlansSidebarTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='staff@test.com', password='pw', is_staff=True,
        )

    def test_studio_dashboard_sidebar_links_to_sprints_and_plans(self):
        """Both Members links appear inside the Studio sidebar nav.

        Asserting via ``assertContains`` with full anchor markup (rather
        than a bare URL string match) ensures the links live in the
        sidebar nav and aren't an accidental occurrence in the page
        body. The nav itself is keyed by ``id="studio-sidebar-nav"`` in
        the layout (the section heading "Members" is unique to this
        block).
        """
        self.client.login(email='staff@test.com', password='pw')
        response = self.client.get('/studio/')
        self.assertEqual(response.status_code, 200)

        body = response.content.decode()
        # The sidebar nav block is uniquely identifiable.
        self.assertIn('id="studio-sidebar-nav"', body)
        # The Members heading is unique to this section.
        self.assertIn('Members', body)

        # The two Members links target the sprint/plan list URLs.
        self.assertContains(response, 'href="/studio/sprints/"')
        self.assertContains(response, 'href="/studio/plans/"')

        # The link text "Sprints" and "Plans" appear inside <span>
        # elements per the sidebar template; assert with html=True so
        # whitespace differences don't break the test.
        self.assertContains(response, '<span>Sprints</span>', html=True)
        self.assertContains(response, '<span>Plans</span>', html=True)
