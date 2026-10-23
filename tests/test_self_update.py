import hashlib
from contextlib import closing
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import zipfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
import self_update as update
import update_worker
from fastapi import FastAPI, Depends, HTTPException, Header
from fastapi.testclient import TestClient
from maintenance_guard import register_maintenance_guard


def artifact(version='0.3.0', **changes):
    tag = 'v' + version
    name = 'WhaleCompanion-' + tag + '-windows-x64.zip'
    data = {'tag_name': tag, 'draft': False, 'prerelease': False,
            'html_url': f'https://github.com/{update.REPOSITORY}/releases/tag/{tag}',
            'assets': [{'name': name, 'size': 123, 'digest': 'sha256:' + 'a' * 64,
                        'browser_download_url': f'https://github.com/{update.REPOSITORY}/releases/download/{tag}/{name}'}]}
    data.update(changes)
    return data


def build_archive(path, extra=None, corrupt=False):
    names = ['phone_server.py', 'scripts/beginner_setup.py', 'scripts/bootstrap.ps1',
             'scripts/update_worker.py', 'self_update.py', 'configs/distribution.json',
             'configs/release.json', 'requirements-bootstrap.lock', 'maintenance_guard.py', 'feature_profiles.py']
    files = {n: ('fixture for ' + n).encode() for n in names}
    files.update(extra or {})
    manifest = {'schema_version': 1, 'version': '0.3.0', 'files': {
        n: {'bytes': len(body), 'sha256': hashlib.sha256(body).hexdigest()} for n, body in files.items()}}
    with zipfile.ZipFile(path, 'w') as bundle:
        for n, body in files.items():
            bundle.writestr(n, body + b'changed' if corrupt and n == 'phone_server.py' else body)
        bundle.writestr('release-manifest.json', json.dumps(manifest))
    return manifest


class ReleaseValidationTests(unittest.TestCase):
    def test_numeric_semver_and_only_newer_stable_release(self):
        self.assertGreater(update.version('v0.10.0'), update.version('0.9.9'))
        self.assertIsNone(update.select_release(artifact('0.2.0'), '0.2.0'))
        self.assertIsNone(update.select_release(artifact(prerelease=True), '0.2.0'))
        self.assertEqual(update.select_release(artifact(), '0.2.0')['version'], '0.3.0')

    def test_foreign_repository_and_missing_digest_never_produce_upgrade(self):
        data = artifact()
        data['assets'][0]['browser_download_url'] = 'https://unrelated.example/program.zip'
        with self.assertRaises(ValueError):
            update.select_release(data, '0.2.0')
        data = artifact()
        data['assets'][0]['digest'] = ''
        with self.assertRaises(ValueError):
            update.select_release(data, '0.2.0')

    def test_valid_package_has_exact_file_manifest(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            expected = build_archive(root / 'test.zip')
            result = update.validate_archive(root / 'test.zip', root / 'staged', '0.3.0')
            self.assertEqual(result, expected)
            self.assertTrue((root / 'staged/phone_server.py').is_file())

    def test_changed_bytes_are_rejected_before_application_switch(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            build_archive(root / 'test.zip', corrupt=True)
            with self.assertRaisesRegex(ValueError, '文件校验失败'):
                update.validate_archive(root / 'test.zip', root / 'staged', '0.3.0')

    def test_package_cannot_include_personal_data_or_escape_paths(self):
        for name in ['../outside.py', 'settings.local.json', 'data/qq/contacts.sqlite3',
                     '.runtime/qq/node.exe', 'configs/agent.local.json', 'C:/Users/example/file.py']:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                build_archive(root / 'test.zip', extra={name: b'fixture'})
                with self.assertRaises(ValueError):
                    update.validate_archive(root / 'test.zip', root / 'staged', '0.3.0')
                self.assertFalse((root.parent / 'outside.py').exists())

    def test_new_version_cannot_masquerade_as_old_manifest(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            build_archive(root / 'test.zip')
            with self.assertRaisesRegex(ValueError, '版本和发布说明不一致'):
                update.validate_archive(root / 'test.zip', root / 'staged', '0.4.0')

    def test_request_cannot_start_upgrader_for_existing_unmanaged_instance(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            service = update.UpdateService(root)
            with patch.object(update.subprocess, 'run') as process:
                with self.assertRaisesRegex(ValueError, '初次设置'):
                    service.apply()
                process.assert_not_called()

    def test_offline_check_keeps_verified_notice_without_installing(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            factory = Mock(side_effect=OSError('offline'))
            service = update.UpdateService(root, client_factory=factory, clock=lambda: 50000)
            update.atomic(service.cache, {'at': 1, 'release': update.select_release(artifact(), '0.2.0')})
            state = service.check()
            self.assertTrue(state['available'])
            self.assertIn('未执行升级', state['check_message'])
            self.assertFalse(state['automatic_install'])


class TransactionTests(unittest.TestCase):
    def fixture(self, folder):
        root = Path(folder)
        (root / 'configs').mkdir()
        (root / '.runtime').mkdir()
        (root / 'data/qq').mkdir(parents=True)
        (root / 'data/setup').mkdir()
        settings = {'api_token': 'local-test-only', 'python': str(root / '.venv/Scripts/python.exe')}
        update.atomic(root / 'settings.local.json', settings)
        update.atomic(root / 'qq.local.json', {'enabled': True, 'bot_id': '123450001', 'allowed_private_ids': ['123450002']})
        update.atomic(root / 'configs/release.json', {'version': '0.2.0'})
        update.atomic(root / 'data/setup/update-request.local.json', {'root': str(root), 'requested': 1,
            'release': {'tag': 'v0.3.0', 'version': '0.3.0'}})
        (root / 'phone_server.py').write_text('old program', encoding='utf-8')
        (root / 'requirements-bootstrap.lock').write_text('old lock', encoding='utf-8')
        (root / '.runtime/dependencies.sha256').write_text('OLD-STAMP')
        with closing(sqlite3.connect(root / 'data/qq/memory.sqlite3')) as db, db:
            db.execute('CREATE TABLE memories(text TEXT)')
            db.execute('INSERT INTO memories VALUES(?)', ('original memory',))
        stage = root / 'new-version'
        (stage / 'configs').mkdir(parents=True)
        (stage / 'phone_server.py').write_text('new program', encoding='utf-8')
        (stage / 'requirements-bootstrap.lock').write_text('new lock', encoding='utf-8')
        update.atomic(stage / 'configs/release.json', {'version': '0.3.0'})
        records = {n: {} for n in ['phone_server.py', 'requirements-bootstrap.lock', 'configs/release.json']}
        manifest = {'files': records, 'version': '0.3.0'}
        update.atomic(stage / 'release-manifest.json', manifest)
        job = update_worker.Upgrade(root)
        job.download = Mock(return_value=(stage, manifest))
        job.prepare_environment = Mock(return_value=str(root / '.runtime/envs/new/Scripts/python.exe'))
        job.stop_owned = Mock()
        job.set_replies = Mock()
        def drain():
            job.was_enabled = True
            job.backup.mkdir(parents=True)
            job.snapshot_private()
            job.snapshot_databases()
            job.stopped = True
        job.drain = Mock(side_effect=drain)
        return root, job

    def test_success_preserves_original_memory_and_private_settings(self):
        with tempfile.TemporaryDirectory() as folder:
            root, job = self.fixture(folder)
            job.start = Mock()
            job.run()
            self.assertEqual((root / 'phone_server.py').read_text(), 'new program')
            self.assertEqual(update.read(job.progress_file)['phase'], 'succeeded')
            self.assertEqual(update.read(root / 'settings.local.json')['api_token'], 'local-test-only')
            with closing(sqlite3.connect(root / 'data/qq/memory.sqlite3')) as db, db:
                self.assertEqual(db.execute('SELECT text FROM memories').fetchone()[0], 'original memory')
            self.assertTrue((job.backup / 'private/qq.local.json').exists())

    def test_failed_start_restores_code_configuration_and_migrated_database(self):
        with tempfile.TemporaryDirectory() as folder:
            root, job = self.fixture(folder)
            def first_start():
                if job.start.call_count == 1:
                    with closing(sqlite3.connect(root / 'data/qq/memory.sqlite3')) as db, db:
                        db.execute('UPDATE memories SET text=?', ('new incompatible state',))
                    raise ValueError('simulated failed startup')
            job.start = Mock(side_effect=first_start)
            job.run()
            self.assertEqual(update.read(job.progress_file)['phase'], 'rolled_back')
            self.assertEqual((root / 'phone_server.py').read_text(), 'old program')
            self.assertEqual(update.read(root / 'configs/release.json')['version'], '0.2.0')
            self.assertEqual((root / '.runtime/dependencies.sha256').read_text(), 'OLD-STAMP')
            self.assertFalse((root / 'data/setup/python-environment.local.json').exists())
            with closing(sqlite3.connect(root / 'data/qq/memory.sqlite3')) as db, db:
                self.assertEqual(db.execute('SELECT text FROM memories').fetchone()[0], 'original memory')
            self.assertEqual(job.start.call_count, 2)

    def test_busy_tasks_are_resumed_without_forced_termination(self):
        with tempfile.TemporaryDirectory() as folder:
            root, job = self.fixture(folder)
            job.drain = update_worker.Upgrade.drain.__get__(job)
            job.set_replies = Mock()
            with patch.object(update_worker, 'active_tasks', return_value=1), patch.object(update_worker.time, 'monotonic', side_effect=[0, 301]):
                with self.assertRaisesRegex(ValueError, '本次升级已推迟'):
                    job.drain()
            self.assertEqual([c.args[0] for c in job.set_replies.call_args_list], [False, True])
            job.stop_owned.assert_not_called()

    def test_backup_covers_extension_database_catalog_and_browser_session(self):
        with tempfile.TemporaryDirectory() as folder:
            root, job = self.fixture(folder)
            (root/'data/bilibili').mkdir()
            with closing(sqlite3.connect(root/'data/bilibili/patrol.sqlite3')) as db, db:
                db.execute('CREATE TABLE runs(state TEXT)')
                db.execute("INSERT INTO runs VALUES('completed')")
            update.atomic(root/'assets/stickers/catalog.json', {'packs': []})
            update.atomic(root/'data/setup/browser-session.local.json', {'value': 'session-fixture'})
            job.snapshot_private()
            job.snapshot_databases()
            self.assertTrue((job.backup/'private/assets/stickers/catalog.json').is_file())
            self.assertTrue((job.backup/'private/data/setup/browser-session.local.json').is_file())
            with closing(sqlite3.connect(job.backup/'databases/data/bilibili/patrol.sqlite3')) as db:
                self.assertEqual(db.execute('SELECT state FROM runs').fetchone()[0], 'completed')
            self.assertFalse((job.backup/'databases/data/update-backups').exists())


class MaintenanceTests(unittest.TestCase):
    def test_new_work_paused_and_updater_exception_still_requires_authentication(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            update.atomic(root / 'data/setup/update-progress.local.json', {'phase': 'validating'})
            update.atomic(root / 'data/setup/update-request.local.json', {'maintenance_token': 'nonce-fixture'})
            app = FastAPI()
            def authorize(authorization: str = Header(default='')):
                if authorization != 'Bearer fixture-only':
                    raise HTTPException(401)
            @app.post('/qq/config', dependencies=[Depends(authorize)])
            def config(): return {'ok': True}
            @app.post('/synthesize', dependencies=[Depends(authorize)])
            def synthesize(): return {'ok': True}
            @app.post('/local/session')
            def session(): return {'allowed': True}
            register_maintenance_guard(app, root)
            with TestClient(app) as client:
                self.assertEqual(client.post('/synthesize', headers={'Authorization': 'Bearer fixture-only'}).status_code, 503)
                self.assertEqual(client.post('/qq/config', headers={'Authorization': 'Bearer fixture-only'}).status_code, 503)
                self.assertEqual(client.post('/qq/config', headers={'X-Whale-Updater': 'nonce-fixture'}).status_code, 401)
                self.assertEqual(client.post('/qq/config', headers={'Authorization': 'Bearer fixture-only', 'X-Whale-Updater': 'nonce-fixture'}).status_code, 200)
                self.assertEqual(client.post('/local/session').status_code, 200)


if __name__ == '__main__':
    unittest.main()
