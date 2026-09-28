"""Server-side contract for the collapsible Studio sidebar rail.

Playwright (``playwright_tests/test_studio_sidebar_rail.py``) covers the
collapse/expand behaviour, persistence, and centring; these tests pin the
markup the behaviour depends on.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase

User = get_user_model()


class StudioSidebarRailMarkupTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            email='rail-staff@test.com', password='pw', is_staff=True,
        )

    def setUp(self):
        self.client.force_login(self.staff)

    def test_collapse_toggle_renders_on_studio_pages(self):
        for path in ('/studio/', '/studio/courses/'):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertContains(
                    response,
                    'data-testid="studio-sidebar-collapse-toggle"',
                    count=1,
                )
                self.assertContains(response, 'aria-label="Collapse Studio sidebar"')
                self.assertContains(response, 'data-lucide="panel-left-close"')
                self.assertContains(response, 'data-lucide="panel-left-open"')

    def test_prepaint_script_runs_before_the_sidebar(self):
        body = self.client.get('/studio/').content.decode()
        prepaint = body.index("localStorage.getItem('studio-sidebar-collapsed')")
        self.assertLess(prepaint, body.index('id="studio-sidebar"'))

    def test_rail_search_button_renders(self):
        response = self.client.get('/studio/')
        self.assertContains(response, 'data-testid="studio-rail-search"', count=1)
