from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import json
import shutil
import subprocess
import tempfile
import threading
import unittest
from fastapi import FastAPI, Header, Request
from fastapi.testclient import TestClient
from deployment_mode import DeploymentGate, register_deployment_mode
from local_session import register_local_sessions, MARKER
from scripts import deploy_windows as deploy


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='whale space ')
        self.root = Path(self.tmp.name) / '鲸娅 space'
        self.root.mkdir()
        (self.root/'requirements.txt').write_text('fastapi>=0.116,<1\n')
    def tearDown(self): self.tmp.cleanup()
    def env(self):
        (self.root/'.venv/Scripts').mkdir(parents=True)
        deploy.python_path(self.root).touch()
        (self.root/'.venv/pyvenv.cfg').write_text('home = test\ninclude-system-site-packages = false')
    def test_configuration_never_reads_values(self):
        self.assertEqual(deploy.config_state(self.root), 'not configured')
        (self.root/'settings.local.json').write_text('not JSON - private value')
        self.assertIn('partial', deploy.config_state(self.root))
        with self.assertRaises(RuntimeError): deploy.configure(self.root, True)
        self.assertEqual((self.root/'settings.local.json').read_text(), 'not JSON - private value')
    def test_install_requires_explicit_download_approval(self):
        run=Mock()
        with self.assertRaises(RuntimeError): deploy.install(self.root, False, run)
        run.assert_not_called()
    def test_rejects_unrecognized_venv_without_deleting(self):
        (self.root/'.venv').mkdir()
        with self.assertRaises(RuntimeError): deploy.install(self.root, True, Mock())
        self.assertTrue((self.root/'.venv').exists())
    def test_url_requirements_rejected_before_pip(self):
        (self.root/'requirements.txt').write_text('package @ https://example.invalid/a.whl')
        run=Mock()
        with self.assertRaises(RuntimeError): deploy.install(self.root, True, run)
        run.assert_not_called()
    def test_success_record_only_after_verification(self):
        self.env(); run=Mock(); versions={'fastapi':'0.116.1'}
        with patch.object(deploy,'verify_dependencies',return_value=versions): deploy.install(self.root,True,run)
        calls=[x.args[0] for x in run.call_args_list]
        pip=next(c for c in calls if 'install' in c)
        self.assertEqual(pip[0],str(deploy.python_path(self.root)))
        self.assertIn('--only-binary=:all:',pip); self.assertIn('--isolated',pip)
        self.assertNotIn('--upgrade',pip)
        call=next(c for c in run.call_args_list if 'install' in c.args[0])
        self.assertEqual(call.kwargs['env']['PIP_CONFIG_FILE'],deploy.os.devnull)
        self.assertEqual(json.loads((self.root/'.venv'/deploy.MARKER).read_text())['versions'],versions)
        run.reset_mock()
        with patch.object(deploy,'verify_dependencies',return_value=versions): deploy.install(self.root,False,run)
        run.assert_not_called()
    def test_system_site_packages_venv_rejected(self):
        self.env();(self.root/'.venv/pyvenv.cfg').write_text('include-system-site-packages = true')
        run=Mock()
        with self.assertRaises(RuntimeError): deploy.install(self.root,True,run)
        run.assert_not_called()
    def test_failure_does_not_mark_ready(self):
        self.env()
        with patch.object(deploy,'verify_dependencies',return_value=None):
            with self.assertRaises(RuntimeError): deploy.install(self.root,True,Mock())
        self.assertFalse((self.root/'.venv'/deploy.MARKER).exists())
    def test_lock_reentry_and_cleanup(self):
        with deploy.deployment_lock(self.root):
            with self.assertRaises(RuntimeError):
                with deploy.deployment_lock(self.root): pass
        self.assertFalse((self.root/'.whale-deploy.lock').exists())
    def test_configuration_credential_creation_requires_confirmation(self):
        run=Mock()
        with self.assertRaises(RuntimeError): deploy.configure(self.root,False,run)
        run.assert_not_called()
    def test_complete_existing_configuration_preserved(self):
        for name in ('settings.local.json','qq.local.json','agent.local.json'):(self.root/name).write_text('{}')
        run=Mock()
        with self.assertRaises(RuntimeError): deploy.configure(self.root,True,run)
        run.assert_not_called(); self.assertEqual(deploy.target(self.root)[0],18766)
    def test_occupied_port_never_spawns_or_kills(self):
        spawn=Mock()
        with self.assertRaises(RuntimeError): deploy.start(self.root,False,spawn,lambda r:'occupied')
        spawn.assert_not_called()
    def test_running_console_reused(self):
        spawn=Mock(); deploy.start(self.root,False,spawn,lambda r:'setup console running'); spawn.assert_not_called()
    def test_real_app_launch_is_loopback_safe_mode(self):
        for name in ('settings.local.json','qq.local.json','agent.local.json'):(self.root/name).write_text('{}')
        spawn=Mock(); status=Mock(side_effect=['stopped','setup console running'])
        with patch.object(deploy,'installed',return_value=True),patch.object(deploy.time,'sleep'):
            deploy.start(self.root,False,spawn,status)
        args=spawn.call_args.args[0]; kw=spawn.call_args.kwargs
        self.assertIn('phone_server:app',args); self.assertIn('127.0.0.1',args)
        self.assertEqual(kw['env']['WHALE_SAFE_START'],'1')
        self.assertEqual(kw['cwd'],self.root)


class DeploymentDOMTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node required for DOM mocks')
    def test_native_fetch_and_confirmation_flow(self):
        subprocess.run(['node', str(Path(__file__).with_name('deployment_ui_test.js'))], check=True, timeout=30)


class GateTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        for file in ['deployment.html','deployment.js','local_session.js']:
            (self.root/file).write_text('<html>safe</html>' if file.endswith('html') else '//js')
        self.bridge=SimpleNamespace(lock=threading.RLock(),cfg={'enabled':True,'bot_id':'123456','allowed_private_ids':['654321']},start=Mock(),login_manager=SimpleNamespace(start=Mock(),switcher=None))
        self.app=FastAPI();sessions=register_local_sessions(self.app,self.root,'unit-test-only')
        def auth(request:Request,authorization:str=Header(default='')):return sessions.authorize(request,authorization)
        self.gate=register_deployment_mode(self.app,self.root,self.bridge,auth,sessions)
        @self.app.post('/qq/login/ensure')
        def ensure():return {'unexpected':True}
        self.client=TestClient(self.app,base_url='http://127.0.0.1:18766',client=('127.0.0.1',40000))
        self.headers={'X-Local-Client':MARKER}
    def tearDown(self):self.tmp.cleanup()
    def login(self):self.assertEqual(self.client.post('/local/session',headers=self.headers).status_code,200)
    def body(self):
        self.login();r=self.client.post('/deployment/prepare',headers=self.headers);self.assertEqual(r.status_code,200)
        data=r.json();return {'nonce':data['nonce'],'preview':data['preview'],'confirmed':True}
    def test_start_is_paused_and_old_polling_blocked(self):
        self.assertFalse(self.bridge.cfg['enabled']); self.bridge.start.assert_not_called()
        self.assertEqual(self.client.post('/qq/login/ensure').status_code,423)
        self.assertEqual(self.client.get('/qq').status_code,423)
    def test_session_and_csrf_required(self):
        self.assertEqual(self.client.post('/deployment/prepare').status_code,401)
        self.login();self.assertEqual(self.client.post('/deployment/prepare').status_code,401)
        self.assertEqual(self.client.post('/deployment/prepare',headers={**self.headers,'Origin':'https://example.invalid'}).status_code,401)
    def test_explicit_exact_confirmation_once(self):
        body=self.body();result=self.client.post('/deployment/connect',headers=self.headers,json=body)
        self.assertEqual(result.status_code,200);self.assertFalse(self.bridge.cfg['enabled'])
        self.bridge.start.assert_called_once();self.bridge.login_manager.start.assert_called_once()
        self.assertEqual(self.client.post('/deployment/connect',headers=self.headers,json=body).status_code,409)
        self.bridge.start.assert_called_once()
    def test_valid_bearer_ignores_unverified_cookie_for_principal(self):
        headers={'Authorization':'Bearer unit-test-only','Cookie':'whale_local_session=unverified-a'}
        r=self.client.post('/deployment/prepare',headers=headers);self.assertEqual(r.status_code,200)
        body={k:r.json()[k] for k in ('nonce','preview')};body['confirmed']=True
        headers['Cookie']='whale_local_session=unverified-b'
        self.assertEqual(self.client.post('/deployment/connect',headers=headers,json=body).status_code,200)
        self.bridge.start.assert_called_once()
    def test_new_session_invalidates_previous_nonce(self):
        body=self.body();self.login()
        self.assertEqual(self.client.post('/deployment/connect',headers=self.headers,json=body).status_code,409)
        self.bridge.start.assert_not_called()
    def test_expired_or_mismatched_confirmation_never_connects(self):
        body=self.body();body['preview']['bot_id']='999999'
        self.assertEqual(self.client.post('/deployment/connect',headers=self.headers,json=body).status_code,409)
        body=self.body();self.gate.expires=0
        self.assertEqual(self.client.post('/deployment/connect',headers=self.headers,json=body).status_code,409)
        self.bridge.start.assert_not_called()
    def test_pending_switch_cannot_auto_resume_replies(self):
        self.bridge.login_manager.switcher=SimpleNamespace(load=lambda:{'phase':'checking_friend'})
        self.login();self.assertEqual(self.client.post('/deployment/prepare',headers=self.headers).status_code,409)
        self.bridge.start.assert_not_called()
    def test_switch_or_account_changes_after_prepare_rejected(self):
        body=self.body();self.bridge.cfg['bot_id']='888888'
        self.assertEqual(self.client.post('/deployment/connect',headers=self.headers,json=body).status_code,409)
        self.bridge.start.assert_not_called()
    def test_uncertain_connect_never_retried(self):
        body=self.body();self.bridge.login_manager.start.side_effect=RuntimeError('private internal detail')
        result=self.client.post('/deployment/connect',headers=self.headers,json=body)
        self.assertEqual(result.status_code,503);self.assertNotIn('private internal',result.text)
        self.assertEqual(self.client.post('/deployment/connect',headers=self.headers,json=body).status_code,409)
        self.bridge.start.assert_called_once();self.assertFalse(self.gate.opened())
    def test_no_automatic_session_retry_script_on_deployment_page(self):
        result=self.client.get('/deployment')
        self.assertEqual(result.status_code,200);self.assertNotIn('/local/session.js',result.text)
        self.assertEqual(result.headers['x-frame-options'],'DENY')
    def test_health_has_no_account_or_paths(self):
        result=self.client.get('/deployment/health')
        self.assertEqual(result.status_code,200);self.assertNotIn(str(self.root),result.text)
        self.assertNotIn('123456',result.text)

if __name__=='__main__':unittest.main()
