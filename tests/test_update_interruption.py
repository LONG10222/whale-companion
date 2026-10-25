"""A lost updater must not look busy forever or permit another overwrite."""
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch
import psutil
import self_update as update


class InterruptedUpdateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.folder = self.root/'data/setup'
        update.atomic(self.root/'configs/release.json', {'version':'0.3.0'})
        update.atomic(self.folder/'installation.local.json', {'completed':True})
        self.service = update.UpdateService(self.root, clock=lambda:100000)

    def tearDown(self):
        self.tmp.cleanup()

    def progress(self, phase='switching', at=1000):
        update.atomic(self.service.progress, {'phase':phase, 'at':at, 'operation':'fixture'})

    def test_dead_stale_worker_reports_recovery_and_keeps_maintenance_and_backups(self):
        self.progress()
        lock = self.folder/'update-request.lock';lock.write_text('owned fixture')
        backup = self.root/'data/update-backups/fixture/record';backup.parent.mkdir(parents=True)
        backup.write_text('preserve this recovery record')
        with patch.object(update, 'updater_is_alive', return_value=False), patch.object(update.subprocess, 'run') as run:
            state = self.service.state()
            self.assertTrue(state['needs_recovery'])
            self.assertFalse(state['can_upgrade'])
            self.assertEqual(state['progress']['interrupted_phase'], 'switching')
            with self.assertRaisesRegex(ValueError, '需要恢复'):
                self.service.apply()
            run.assert_not_called()
        self.assertEqual(update.read(self.service.progress)['phase'], 'switching')
        self.assertEqual(lock.read_text(), 'owned fixture')
        self.assertEqual(backup.read_text(), 'preserve this recovery record')

    def test_slow_live_worker_is_not_reported_as_abandoned(self):
        self.progress()
        with patch.object(update, 'updater_is_alive', return_value=True), patch.object(update.subprocess, 'run') as run:
            state = self.service.apply()
            self.assertFalse(state['needs_recovery'])
            self.assertEqual(state['progress']['phase'], 'switching')
            run.assert_not_called()

    def test_unreadable_worker_identity_remains_conservative(self):
        self.progress()
        with patch.object(update, 'updater_is_alive', return_value=None):
            self.assertEqual(self.service.state()['progress']['phase'], 'switching')

    def test_newly_scheduled_worker_has_startup_grace(self):
        self.progress('preparing', 99990)
        with patch.object(update, 'updater_is_alive') as probe:
            self.assertFalse(self.service.state()['needs_recovery'])
            probe.assert_not_called()

    def test_legacy_record_does_not_call_an_unreadable_process_dead(self):
        self.progress()
        process = SimpleNamespace(name=Mock(side_effect=psutil.AccessDenied(100)))
        with patch('psutil.process_iter', return_value=[process]):
            self.assertIsNone(update.updater_is_alive(self.root))
            self.assertEqual(self.service.state()['progress']['phase'], 'switching')

    def test_failed_rollback_cannot_be_overwritten_by_another_upgrade(self):
        self.progress('recovery_required')
        with patch.object(update.subprocess, 'run') as run:
            with self.assertRaisesRegex(ValueError, '需要恢复'):
                self.service.apply()
            run.assert_not_called()

    def test_pid_birth_time_and_installation_arguments_define_worker_ownership(self):
        identity = {'pid':100, 'created_at':123.5}
        process = SimpleNamespace(create_time=lambda:123.5, cwd=lambda:str(self.root),
            cmdline=lambda:['python.exe',str(self.root/'scripts/update_worker.py'),'--root',str(self.root)])
        with patch('psutil.Process', return_value=process):
            self.assertTrue(update.updater_is_alive(self.root, identity))
            self.assertFalse(update.updater_is_alive(self.root, {**identity,'created_at':10}))
            self.assertFalse(update.updater_is_alive(self.root/'other', identity))
        with patch('psutil.Process', side_effect=psutil.NoSuchProcess(100)):
            self.assertFalse(update.updater_is_alive(self.root, identity))
        with patch('psutil.Process', side_effect=psutil.AccessDenied(100)):
            self.assertIsNone(update.updater_is_alive(self.root, identity))
