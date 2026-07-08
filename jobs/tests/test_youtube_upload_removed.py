"""Guard tests for the removed YouTube upload path (Phase C of #1134).

The YouTube upload MECHANISM was removed once S3 serving + the in-app
player became the default watch path. These tests ensure the removed
symbols and module stay gone (no dead import creeps back).
"""

import importlib

from django.test import TestCase, tag


@tag('core')
class YouTubeUploadRemovedTest(TestCase):
    def test_jobs_tasks_has_no_youtube_upload_task(self):
        import jobs.tasks as tasks

        self.assertFalse(hasattr(tasks, 'upload_recording_to_youtube'))
        self.assertNotIn('upload_recording_to_youtube', tasks.__all__)

    def test_youtube_upload_module_is_gone(self):
        with self.assertRaises(ModuleNotFoundError):
            importlib.import_module('jobs.tasks.youtube_upload')

    def test_youtube_service_module_is_gone(self):
        with self.assertRaises(ModuleNotFoundError):
            importlib.import_module('integrations.services.youtube')

    def test_download_recording_to_temp_file_is_gone(self):
        import jobs.tasks.recordings_s3 as recordings_s3

        self.assertFalse(hasattr(recordings_s3, 'download_recording_to_temp_file'))
