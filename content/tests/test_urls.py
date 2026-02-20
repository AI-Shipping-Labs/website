from django.test import TestCase
from django.urls import reverse, resolve
from content.views.home import home
from content.views.pages import (
    about, activities, blog_list, blog_detail,
    recordings_list, recording_detail,
    projects_list, project_detail,
    collection_list,
    tutorials_list, tutorial_detail,
)


class URLResolutionTest(TestCase):
    def test_home_url_resolves(self):
        url = reverse('home')
        self.assertEqual(url, '/')
        self.assertEqual(resolve(url).func, home)

    def test_about_url_resolves(self):
        url = reverse('about')
        self.assertEqual(url, '/about')
        self.assertEqual(resolve(url).func, about)

    def test_activities_url_resolves(self):
        url = reverse('activities')
        self.assertEqual(url, '/activities')
        self.assertEqual(resolve(url).func, activities)

    def test_blog_list_url_resolves(self):
        url = reverse('blog_list')
        self.assertEqual(url, '/blog')
        self.assertEqual(resolve(url).func, blog_list)

    def test_blog_detail_url_resolves(self):
        url = reverse('blog_detail', args=['test-slug'])
        self.assertEqual(url, '/blog/test-slug')
        self.assertEqual(resolve(url).func, blog_detail)

    def test_recordings_list_url_resolves(self):
        url = reverse('recordings_list')
        self.assertEqual(url, '/event-recordings')
        self.assertEqual(resolve(url).func, recordings_list)

    def test_recording_detail_url_resolves(self):
        url = reverse('recording_detail', args=['test-slug'])
        self.assertEqual(url, '/event-recordings/test-slug')
        self.assertEqual(resolve(url).func, recording_detail)

    def test_projects_list_url_resolves(self):
        url = reverse('projects_list')
        self.assertEqual(url, '/projects')
        self.assertEqual(resolve(url).func, projects_list)

    def test_project_detail_url_resolves(self):
        url = reverse('project_detail', args=['test-slug'])
        self.assertEqual(url, '/projects/test-slug')
        self.assertEqual(resolve(url).func, project_detail)

    def test_collection_list_url_resolves(self):
        url = reverse('collection_list')
        self.assertEqual(url, '/resources')
        self.assertEqual(resolve(url).func, collection_list)

    def test_collection_backward_compat_url_resolves(self):
        self.assertEqual(resolve('/collection').func, collection_list)

    def test_tutorials_list_url_resolves(self):
        url = reverse('tutorials_list')
        self.assertEqual(url, '/tutorials')
        self.assertEqual(resolve(url).func, tutorials_list)

    def test_tutorial_detail_url_resolves(self):
        url = reverse('tutorial_detail', args=['test-slug'])
        self.assertEqual(url, '/tutorials/test-slug')
        self.assertEqual(resolve(url).func, tutorial_detail)
