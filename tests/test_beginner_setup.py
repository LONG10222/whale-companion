import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import httpx

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('beginner_setup', ROOT / 'scripts/beginner_setup.py')
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


class PreparationTests(unittest.TestCase):
    def make_root(self, folder):
        root = Path(folder)
        (root / '.runtime/qq').mkdir(parents=True)
        (root / '.runtime/qq/node.exe').write_bytes(b'fixture')
        return root

    def test_existing_personal_settings_are_never_adopted(self):
        with tempfile.TemporaryDirectory() as folder:
            root = self.make_root(folder)
            target = root / 'settings.local.json'
            target.write_text('{"keep":"mine"}')
            with self.assertRaisesRegex(ValueError, '已有个人配置'):
                setup.prepare(root)
            self.assertEqual(target.read_text(), '{"keep":"mine"}')

    def test_repeat_prepare_preserves_tokens_and_accounts(self):
        with tempfile.TemporaryDirectory() as folder:
            root = self.make_root(folder)
            first = setup.prepare(root)
            before = (root / 'qq.local.json').read_bytes()
            again = setup.prepare(root)
            self.assertEqual(first['instance'], again['instance'])
            self.assertEqual(before, (root / 'qq.local.json').read_bytes())
            config = setup.load_json(root / 'qq.local.json')
            self.assertFalse(config['enabled'])
            self.assertEqual(config['allowed_private_ids'], [])
            self.assertGreater(len(config['onebot_token']), 24)

    def test_interrupted_preparation_recovers_without_changing_created_token(self):
        with tempfile.TemporaryDirectory() as folder:
            root = self.make_root(folder)
            real = setup.write_json
            def interrupted(path, data, **kwargs):
                if Path(path).name == 'agent.local.json':
                    raise OSError('simulated interruption')
                return real(path, data, **kwargs)
            with patch.object(setup, 'write_json', side_effect=interrupted):
                with self.assertRaises(OSError):
                    setup.prepare(root)
            previous = setup.load_json(root / 'qq.local.json')['onebot_token']
            setup.prepare(root)
            self.assertEqual(previous, setup.load_json(root / 'qq.local.json')['onebot_token'])
            self.assertEqual(setup.load_json(root / 'data/setup/installation.local.json')['phase'], 'prepared')

    def test_roots_and_private_paths_cannot_escape(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with self.assertRaises(ValueError):
                setup.child_path(root / '../not-this-install', root)
            self.assertEqual(setup.child_path(root / 'data/own-file', root), root.resolve() / 'data/own-file')

    def test_identity_is_selected_from_current_friends_not_first_incoming_message(self):
        with tempfile.TemporaryDirectory() as folder:
            root = self.make_root(folder)
            setup.prepare(root)
            setup.write_json(root / 'agent.local.json', {'api_key': 'test-fixture-only'})
            engine = setup.SetupEngine(root)
            engine.onebot = lambda action: {'user_id': 123450001} if action == 'get_login_info' else [
                {'user_id': 123450002, 'nickname': 'Owner fixture'}]
            with self.assertRaisesRegex(ValueError, '管理账号必须'):
                engine.finish('123450003')
            self.assertFalse(setup.load_json(root / 'qq.local.json')['enabled'])
            engine.finish('123450002')
            config = setup.load_json(root / 'qq.local.json')
            self.assertEqual(config['test_contact']['user_id'], '123450002')
            self.assertEqual(config['allowed_private_ids'], ['123450002'])
            self.assertEqual(config['locked_bot_id'], '123450001')
            engine.finish('123450003')  # Completed setup cannot reassign ownership.
            self.assertEqual(setup.load_json(root / 'qq.local.json')['test_contact']['user_id'], '123450002')

    def test_setup_does_not_expose_any_send_or_write_onebot_action(self):
        with tempfile.TemporaryDirectory() as folder:
            root = self.make_root(folder)
            setup.prepare(root)
            engine = setup.SetupEngine(root)
            with self.assertRaisesRegex(ValueError, '只允许读取'):
                engine.onebot('send_private_msg')


class AuthenticationTests(unittest.TestCase):
    def setUp(self):
        self.engine = Mock()
        self.engine.install = {'instance': 'synthetic-installation'}
        self.engine.status.return_value = {'completed': False}
        self.server = setup.SetupHTTPServer(('127.0.0.1', 0), self.engine)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.client = httpx.Client(base_url=f'http://127.0.0.1:{self.server.server_port}', trust_env=False, timeout=3)
        self.headers = {'X-Whale-Setup': setup.MARKER}

    def tearDown(self):
        self.client.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)

    def connect(self):
        return self.client.post('/session', json={'token': self.server.policy.token}, headers=self.headers)

    def test_no_token_or_cookie_cannot_read_state(self):
        self.assertEqual(self.client.get('/state', headers=self.headers).status_code, 401)
        self.assertEqual(self.client.post('/session', json={'token': 'incorrect'}, headers=self.headers).status_code, 401)
        self.engine.status.assert_not_called()

    def test_fragment_handshake_yields_httponly_same_site_cookie(self):
        response = self.connect()
        self.assertEqual(response.status_code, 200)
        self.assertIn('HttpOnly', response.headers['set-cookie'])
        self.assertIn('SameSite=Strict', response.headers['set-cookie'])
        state = self.client.get('/state', headers=self.headers)
        self.assertEqual(state.status_code, 200)
        self.assertNotIn(self.server.policy.token, state.text)

    def test_other_site_and_foreign_host_cannot_use_authenticated_browser(self):
        self.connect()
        bad = {**self.headers, 'Origin': 'https://unrelated.example', 'Sec-Fetch-Site': 'cross-site'}
        self.assertEqual(self.client.post('/finish', json={'administrator': '123450002'}, headers=bad).status_code, 403)
        self.assertEqual(self.client.get('/state', headers={**self.headers, 'Host': 'unrelated.example'}).status_code, 403)
        self.engine.finish.assert_not_called()

    def test_missing_ui_marker_blocks_mutation_even_with_cookie(self):
        self.connect()
        self.assertEqual(self.client.post('/finish', json={'administrator': '123450002'}).status_code, 403)
        self.engine.finish.assert_not_called()

    def test_body_limit_and_no_secrets_in_public_pages(self):
        self.connect()
        response = self.client.post('/key', content=b'x' * (setup.MAX_BODY + 1),
                                    headers={**self.headers, 'Content-Type': 'application/json'})
        self.assertEqual(response.status_code, 400)
        self.engine.save_key.assert_not_called()
        page = self.client.get('/')
        self.assertNotIn(self.server.policy.token, page.text)
        self.assertNotIn(self.server.policy.cookie, page.text)
        self.assertIn("frame-ancestors 'none'", page.headers['content-security-policy'])

    def test_setup_upgrade_preserves_active_browser_cookie_but_rotates_launch_token(self):
        with tempfile.TemporaryDirectory() as folder:
            engine = Mock(root=Path(folder))
            engine.install = {'instance': 'synthetic-installation'}
            first = setup.SetupHTTPServer(('127.0.0.1', 0), engine)
            cookie, token = first.policy.cookie, first.policy.token
            first.server_close()
            second = setup.SetupHTTPServer(('127.0.0.1', 0), engine)
            try:
                self.assertEqual(cookie, second.policy.cookie)
                self.assertNotEqual(token, second.policy.token)
            finally:
                second.server_close()


class ProcessOwnershipTests(unittest.TestCase):
    def test_busy_foreign_port_is_never_terminated_or_reused(self):
        with tempfile.TemporaryDirectory() as folder:
            process = setup.ManagedProcess(Path(folder), 'qq', ['C:/Example/node.exe', 'C:/Example/own.cjs'],
                                           Path(folder), [3001])
            connection = Mock(status='LISTEN', laddr=Mock(port=3001))
            with patch('psutil.process_iter', return_value=[]), patch('psutil.net_connections', return_value=[connection]), patch.object(setup.subprocess, 'Popen') as spawn:
                self.assertEqual(process.tick(), 'port_conflict')
                spawn.assert_not_called()
                self.assertIn('未关闭任何其他程序', process.error)

    def test_same_named_qq_in_another_directory_is_not_owned(self):
        with tempfile.TemporaryDirectory() as folder:
            managed = setup.ManagedProcess(Path(folder), 'qq', [str(Path(folder) / 'node.exe'), 'own.cjs'], Path(folder), [3001])
            foreign = Mock()
            foreign.exe.return_value = 'C:/OtherProgram/node.exe'
            foreign.cwd.return_value = str(Path(folder))
            foreign.cmdline.return_value = managed.argv
            self.assertFalse(managed.owns(foreign))


if __name__ == '__main__':
    unittest.main()
