from contextlib import closing
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from fastapi import FastAPI, Depends, Header, HTTPException, Request
from fastapi.testclient import TestClient
from PIL import Image

from workspace_policy import prepare_inputs, permission_config
from safe_input_snapshots import Snapshot
from qq_images import QQImages
from local_session import register_local_sessions, MARKER
from agent_routes import register_agent_routes
from music_routes import register_music_routes
import threading


def link_directory(link, target):
    if os.name == 'nt':
        result = subprocess.run(['cmd.exe', '/d', '/c', 'mklink', '/J', str(link), str(target)],
                                capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode:
            raise unittest.SkipTest('Directory junctions unavailable in this test account')
    else:
        link.symlink_to(target, target_is_directory=True)


class InputSnapshotBoundaryTests(unittest.TestCase):
    def test_task_input_link_never_redirects_the_host_database_write(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            task = root / 'task'
            task.mkdir()
            outside = root / 'outside-task'
            outside.mkdir()
            marker = outside / 'qq-memory.sqlite3'
            marker.write_bytes(b'untouched fixture')
            database = root / 'data/qq/qq-memory.sqlite3'
            database.parent.mkdir(parents=True)
            with closing(sqlite3.connect(database)) as db, db:
                db.execute('CREATE TABLE sample(value TEXT)')
            link_directory(task / 'inputs', outside)
            with self.assertRaises(ValueError):
                prepare_inputs(root, root, task, '读取聊天记录数据库', ['聊天记录'])
            self.assertEqual(marker.read_bytes(), b'untouched fixture')

    def test_source_directory_link_does_not_import_outside_document(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            task = root / 'task'
            task.mkdir()
            sources = root / 'sources'
            sources.mkdir()
            outside = root / 'outside-sources'
            outside.mkdir()
            (outside / 'private.txt').write_text('synthetic outside marker')
            link_directory(sources / 'linked', outside)
            with self.assertRaises(ValueError):
                prepare_inputs(root, root, task, '读取项目资料', ['项目资料'])

    def test_parent_directory_gets_filtered_copy_instead_of_broad_read_permission(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            task = root / 'task'
            task.mkdir()
            provided = root / 'provided'
            provided.mkdir()
            (provided / 'notes.txt').write_text('safe project note')
            (provided / 'agent.local.json').write_text('{"api_key":"synthetic-secret"}')
            (provided / 'innocent-name.txt').write_text('sk-' + 'synthetic-credential-only-for-test')
            result = prepare_inputs(root, root, task, '只读查看 ' + str(provided), paths=[str(provided)])
            self.assertNotIn(str(provided), result['read_only_paths'])
            exported = Path(result['input_copies'][0])
            self.assertTrue((exported / 'notes.txt').exists())
            self.assertFalse((exported / 'agent.local.json').exists())
            self.assertFalse((exported / 'innocent-name.txt').exists())
            self.assertFalse(exported.is_relative_to(task))
            permissions = permission_config(task, result['read_only_paths'])['permissions']['qq_task']['filesystem']
            self.assertEqual(permissions[result['read_only_paths'][0]], 'read')
            self.assertEqual(permissions[str(task.resolve())], 'write')

    def test_checked_handle_cannot_read_a_path_resolved_outside_approved_root(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            task = root / 'task'
            task.mkdir()
            supplied = root / 'provided'
            supplied.mkdir()
            source = supplied / 'safe.txt'
            source.write_text('safe fixture')
            snapshot = Snapshot(root, task)
            with patch('safe_input_snapshots.final_path', return_value=root / 'outside.txt'):
                with self.assertRaises(ValueError):
                    snapshot.copy(source, supplied, 'safe.txt')

    def test_hardlinked_input_is_rejected_and_negative_path_with_comma_remains_negative(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            task = root / 'task'
            task.mkdir()
            source = root / 'source,notes.txt'
            source.write_text('fixture')
            with self.assertRaises(ValueError):
                prepare_inputs(root, root, task, '不要读取 ' + str(source), paths=[str(source)])
            try:
                os.link(source, root / 'alias.txt')
            except OSError:
                return
            with self.assertRaises(ValueError):
                prepare_inputs(root, root, task, '读取 ' + str(source), paths=[str(source)])


class AnimationBudgetTests(unittest.TestCase):
    def test_normal_small_animation_keeps_its_frames(self):
        with tempfile.TemporaryDirectory() as folder:
            frames = [Image.new('RGB', (40, 40), c) for c in ('red', 'green', 'blue')]
            data = io.BytesIO()
            frames[0].save(data, format='GIF', save_all=True, append_images=frames[1:], duration=80)
            result = QQImages(Path(folder)).prepare(data.getvalue())
            self.assertEqual(result['frames'], 3)
            self.assertEqual(result['sampled_frames'], 3)
            self.assertFalse(result['animation_truncated'])

    def test_many_frames_get_a_bounded_prefix_without_decoding_the_full_animation(self):
        with tempfile.TemporaryDirectory() as folder:
            frames = [Image.new('RGB', (32, 32), (i % 255, (i * 3) % 255, 20)) for i in range(150)]
            data = io.BytesIO()
            frames[0].save(data, format='GIF', save_all=True, append_images=frames[1:], duration=20)
            start = time.monotonic()
            result = QQImages(Path(folder)).prepare(data.getvalue())
            self.assertTrue(result['animation_truncated'])
            self.assertEqual(result['decoded_frame_limit'], 120)
            self.assertLess(time.monotonic() - start, 6)


class PrivateAudioSessionTests(unittest.TestCase):
    def test_actual_trace_and_artifact_routes_require_authentication(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'local_session.js').write_text('// fixture')
            app = FastAPI()
            sessions = register_local_sessions(app, root, 'fixture-only', port=18765)
            def auth(request: Request, authorization: str = Header(default='')):
                sessions.authorize(request, authorization)
            register_agent_routes(app, root=root, authorize=auth)
            music = register_music_routes(app, root=root, authorize=auth, lock=threading.Lock(),
                                          unload=lambda: None, synthesize=lambda text: (b'', {}))
            aid = 'a' * 32
            (music.artifacts / (aid + '.wav')).write_bytes(b'RIFF-fixture-only')
            with patch('agent_routes.deepseek_client.ready', return_value=False), patch(
                    'agent_routes.deepseek_client.configuration', return_value={'model': 'fixture'}):
                with TestClient(app, base_url='http://127.0.0.1:18765', client=('127.0.0.1', 50000)) as client:
                    self.assertEqual(client.get('/agent/status').status_code, 401)
                    self.assertEqual(client.get('/artifacts/' + aid).status_code, 401)
                    client.post('/local/session', headers={'X-Local-Client': MARKER})
                    self.assertEqual(client.get('/agent/status').status_code, 200)
                    self.assertEqual(client.get('/artifacts/' + aid).content, b'RIFF-fixture-only')

    def test_installed_bundle_needs_private_setup_session_before_auto_connect(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'data/setup').mkdir(parents=True)
            (root / 'local_session.js').write_text('// fixture')
            (root / 'data/setup/installation.local.json').write_text('{"completed":true}')
            (root / 'data/setup/browser-session.local.json').write_text(json.dumps({
                'cookie': 'synthetic-local-bootstrap', 'expires': 2000}))
            app = FastAPI()
            sessions = register_local_sessions(app, root, 'fixture-only', port=18765)
            sessions.clock = lambda: 1000
            def auth(request: Request, authorization: str = Header(default='')):
                sessions.authorize(request, authorization)
            @app.get('/private', dependencies=[Depends(auth)])
            def private():return {'ok': True}
            with TestClient(app, base_url='http://127.0.0.1:18765', client=('127.0.0.1', 50000)) as client:
                self.assertEqual(client.post('/local/session', headers={'X-Local-Client': MARKER}).status_code, 401)
                self.assertEqual(client.get('/private').status_code, 401)
                client.cookies.set('whale_setup_session', 'synthetic-local-bootstrap')
                self.assertEqual(client.post('/local/session', headers={'X-Local-Client': MARKER}).status_code, 200)
                self.assertEqual(client.get('/private').status_code, 200)
                self.assertEqual(client.get('/private', headers={'Origin': 'https://unrelated.example'}).status_code, 401)

    def test_audio_and_trace_need_auth_but_same_origin_audio_cookie_can_play(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'local_session.js').write_text('// fixture')
            app = FastAPI()
            sessions = register_local_sessions(app, root, 'audio-fixture-key', port=18765)
            def auth(request: Request, authorization: str = Header(default='')):
                sessions.authorize(request, authorization)
            @app.get('/artifacts/example', dependencies=[Depends(auth)])
            def audio(): return {'playable': True}
            with TestClient(app, base_url='http://127.0.0.1:18765', client=('127.0.0.1', 50000)) as client:
                self.assertEqual(client.get('/artifacts/example').status_code, 401)
                response = client.post('/local/session', headers={'X-Local-Client': MARKER})
                self.assertIn('whale_local_session_18765=', response.headers['set-cookie'])
                self.assertEqual(client.get('/artifacts/example').status_code, 200)
