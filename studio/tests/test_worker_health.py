"""Tests for ``studio.worker_health`` — django-q liveness detection helpers.

These tests mock ``django_q.status.Stat.get_all`` because the real call
requires a running broker. The helper's contract is:

- empty list → alive=False
- non-empty list → alive=True, last_heartbeat_age derived from timestamp
- broker exception → alive=False with ``error`` populated
- busy if any cluster has queued/completed tasks in its internal queue
"""

import os
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase
from django.utils import timezone

from studio.worker_health import (
    expect_worker,
    get_worker_status,
    worker_is_alive,
)


def _fake_cluster(
    cluster_id='cid',
    host='h1',
    pid=1,
    workers=(10,),
    status='Idle',
    heartbeat_age_seconds=2.0,
    tob_seconds_ago=60.0,
    task_q_size=0,
    done_q_size=0,
):
    now = timezone.now()
    c = SimpleNamespace(
        cluster_id=cluster_id,
        host=host,
        pid=pid,
        workers=list(workers),
        status=status,
        timestamp=now - timedelta(seconds=heartbeat_age_seconds),
        tob=(now - timedelta(seconds=tob_seconds_ago)) if tob_seconds_ago else None,
        task_q_size=task_q_size,
        done_q_size=done_q_size,
    )
    c.uptime = lambda self=c: (timezone.now() - self.tob).total_seconds()
    return c


class GetWorkerStatusTest(SimpleTestCase):
    def test_empty_cluster_list_means_not_alive(self):
        with patch('studio.worker_health.Stat.get_all', return_value=[]):
            info = get_worker_status()
        self.assertFalse(info['alive'])
        self.assertEqual(info['cluster_count'], 0)
        self.assertIsNone(info['last_heartbeat_age'])
        self.assertEqual(info['clusters'], [])
        self.assertIsNone(info['error'])

    def test_single_cluster_marks_alive_and_reports_heartbeat(self):
        cluster = _fake_cluster(heartbeat_age_seconds=7.0)
        with patch('studio.worker_health.Stat.get_all', return_value=[cluster]):
            info = get_worker_status()
        self.assertTrue(info['alive'])
        self.assertEqual(info['cluster_count'], 1)
        # Heartbeat age should be roughly 7s
        self.assertAlmostEqual(info['last_heartbeat_age'], 7.0, delta=1.0)

    def test_multi_cluster_reports_freshest_heartbeat(self):
        stale = _fake_cluster(cluster_id='stale', heartbeat_age_seconds=12.0)
        fresh = _fake_cluster(cluster_id='fresh', heartbeat_age_seconds=1.0)
        with patch('studio.worker_health.Stat.get_all', return_value=[stale, fresh]):
            info = get_worker_status()
        self.assertEqual(info['cluster_count'], 2)
        # min age → fresh
        self.assertAlmostEqual(info['last_heartbeat_age'], 1.0, delta=1.0)

    def test_idle_when_no_tasks_in_queues(self):
        cluster = _fake_cluster(task_q_size=0, done_q_size=0)
        with patch('studio.worker_health.Stat.get_all', return_value=[cluster]):
            info = get_worker_status()
        self.assertTrue(info['idle'])

    def test_busy_when_task_queue_has_items(self):
        cluster = _fake_cluster(task_q_size=5, done_q_size=0)
        with patch('studio.worker_health.Stat.get_all', return_value=[cluster]):
            info = get_worker_status()
        self.assertFalse(info['idle'])

    def test_busy_when_done_queue_has_items(self):
        cluster = _fake_cluster(task_q_size=0, done_q_size=2)
        with patch('studio.worker_health.Stat.get_all', return_value=[cluster]):
            info = get_worker_status()
        self.assertFalse(info['idle'])

    def test_broker_error_surfaces_as_not_alive(self):
        with patch(
            'studio.worker_health.Stat.get_all',
            side_effect=RuntimeError('redis down'),
        ), self.assertLogs('studio.worker_health', level='WARNING') as logs:
            info = get_worker_status()
        self.assertFalse(info['alive'])
        self.assertEqual(info['error'], 'redis down')
        self.assertIn(
            'Failed to query django-q cluster status: redis down',
            logs.output[0],
        )

    def test_cluster_summary_fields(self):
        cluster = _fake_cluster(
            cluster_id='abc',
            host='w1',
            workers=(1, 2, 3),
            status='Idle',
            heartbeat_age_seconds=2.0,
            tob_seconds_ago=30.0,
        )
        with patch('studio.worker_health.Stat.get_all', return_value=[cluster]):
            info = get_worker_status()
        c = info['clusters'][0]
        self.assertEqual(c['cluster_id'], 'abc')
        self.assertEqual(c['host'], 'w1')
        self.assertEqual(c['worker_count'], 3)
        self.assertEqual(c['status'], 'Idle')
        self.assertAlmostEqual(c['heartbeat_age'], 2.0, delta=1.0)
        self.assertAlmostEqual(c['uptime'], 30.0, delta=1.0)


class WorkerIsAliveTest(SimpleTestCase):
    def test_returns_false_when_no_cluster(self):
        with patch('studio.worker_health.Stat.get_all', return_value=[]):
            self.assertFalse(worker_is_alive())

    def test_returns_true_when_cluster_present(self):
        with patch('studio.worker_health.Stat.get_all', return_value=[_fake_cluster()]):
            self.assertTrue(worker_is_alive())


class ExpectWorkerEnvTest(SimpleTestCase):
    def test_default_is_true(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('EXPECT_WORKER', None)
            self.assertTrue(expect_worker())

    def test_false_lowercase_disables(self):
        with patch.dict(os.environ, {'EXPECT_WORKER': 'false'}):
            self.assertFalse(expect_worker())

    def test_false_uppercase_disables(self):
        with patch.dict(os.environ, {'EXPECT_WORKER': 'FALSE'}):
            self.assertFalse(expect_worker())

    def test_any_other_value_is_true(self):
        with patch.dict(os.environ, {'EXPECT_WORKER': 'true'}):
            self.assertTrue(expect_worker())
        with patch.dict(os.environ, {'EXPECT_WORKER': '1'}):
            self.assertTrue(expect_worker())
        with patch.dict(os.environ, {'EXPECT_WORKER': ''}):
            self.assertTrue(expect_worker())

    def test_status_includes_expect_worker_flag(self):
        with patch.dict(os.environ, {'EXPECT_WORKER': 'false'}), \
             patch('studio.worker_health.Stat.get_all', return_value=[]):
            info = get_worker_status()
        self.assertFalse(info['expect_worker'])
