"""Course navigation contracts for the Study groups tab (#1970)."""

from django.test import tag

from pods.tests.fixtures import COURSE_SLUG
from pods.tests.test_member_views import ENABLED, PodViewFixture


@tag('core')
@ENABLED
class StudyGroupsTabTest(PodViewFixture):
    def _course_nav(self, response):
        page = response.content.decode()
        return page.split('aria-label="Course pages"')[1].split('</nav>')[0]

    def test_member_sees_tab_between_syllabus_and_live_sessions(self):
        self.client.force_login(self.anna)
        home = self.client.get(f'/courses/{COURSE_SLUG}/home')
        nav = self._course_nav(home)
        tab = nav.index('data-testid="course-tab-pods"')

        self.assertLess(nav.index('>Syllabus</a>'), tab)
        self.assertLess(tab, nav.index('>Live sessions</a>'))
        self.assertIn('>Study groups', nav)
        self.assertIn(f'href="/courses/{COURSE_SLUG}/home/pods?cohort=4"', nav)

    def test_staff_preview_sees_active_study_groups_tab(self):
        self.client.force_login(self.staff)
        home = self.client.get(f'/courses/{COURSE_SLUG}/home?cohort=4')
        self.assertContains(home, '>Study groups<')

        destination = self.client.get(self.pods_url('?cohort=4'))
        nav = self._course_nav(destination)
        before_testid = nav.split('data-testid="course-tab-pods"')[0]
        study_groups = before_testid.rsplit('<a ', 1)[1]
        self.assertIn('aria-current="page"', study_groups)
