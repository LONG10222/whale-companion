"""Offline management-page/API tests. No QQ account or outbound API calls."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import shutil
import subprocess
import tempfile
import threading
import unittest
from fastapi import FastAPI, Header, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient
from local_session import register_local_sessions, MARKER
from qq_social import QQSocial
from qq_social_routes import register_social_routes

ROOT = Path(__file__).resolve().parents[1]

class SocialUITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.bridge = SimpleNamespace(root=Path(self.tmp.name), lock=threading.RLock(),
            connected=True, login_id='12345678', epoch='test-epoch',
            cfg={'enabled':True,'bot_id':'12345678'}, call=Mock(return_value={'tid':'mock-tid'}))
        self.bridge.social = QQSocial(self.bridge)
        self.app = FastAPI()
        self.sessions = register_local_sessions(self.app, ROOT, 'mock-owner-token')
        def auth(request: Request, authorization: str = Header(default='')):
            return self.sessions.authorize(request, authorization)
        register_social_routes(self.app, authorize=auth, bridge=self.bridge)
        @self.app.get('/qq/status', dependencies=[Depends(auth)])
        def status():
            return {'connected':True,'account_matches':True,'enabled':True,'login_id':'12345678'}
        @self.app.get('/other', response_class=HTMLResponse)
        def other(): return '<html><body>Other page</body></html>'
        self.client = TestClient(self.app, base_url='http://127.0.0.1:18766',client=('127.0.0.1',10000))
        self.headers = {'X-Local-Client':MARKER}
    def tearDown(self):
        self.client.close()
        self.tmp.cleanup()
    def test_static_shell_no_retry_wrapper_but_keeps_security_headers(self):
        response=self.client.get('/qq/social')
        self.assertEqual(response.status_code,200)
        self.assertNotIn('/local/session.js',response.text)
        self.assertIn('/qq/social/controller.js',response.text)
        self.assertIn('无默认受众',response.text)
        self.assertNotIn('12345678',response.text)
        self.assertEqual(response.headers['cache-control'],'no-store')
        self.assertEqual(response.headers['x-frame-options'],'DENY')
        self.assertEqual(response.headers['x-content-type-options'],'nosniff')
        self.assertIn('/local/session.js',self.client.get('/other').text)
        self.bridge.call.assert_not_called()
    def test_assets_and_protected_data(self):
        for path in ['controller.js','app.js','style.css']:
            response=self.client.get('/qq/social/'+path)
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.headers['cache-control'],'no-store')
        for path in ['status','requests']:
            self.assertEqual(self.client.get('/qq/social/'+path).status_code,401)
        self.assertEqual(self.client.post('/qq/social/config',json={'enabled':True}).status_code,401)
        self.bridge.call.assert_not_called()
    def test_manual_session_then_exact_preview_confirm_once(self):
        c=self.client;h=self.headers
        self.assertEqual(c.post('/local/session',headers=h,json={}).status_code,200)
        self.assertFalse(c.get('/qq/social/status').json()['enabled'])
        self.assertEqual(c.post('/qq/social/config',headers=h,json={'enabled':True}).status_code,200)
        op={'kind':'qzone_text','content':'offline mock only','audience':'self'}
        draft=c.post('/qq/social/prepare',headers=h,json={'operation':op}).json()
        self.bridge.call.assert_not_called()
        self.assertEqual(draft['preview']['content'],op['content'])
        payload={'nonce':draft['nonce'],'preview':draft['preview'],'confirmed':True}
        response=c.post('/qq/social/confirm',headers=h,json=payload)
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.headers['cache-control'],'no-store')
        self.assertEqual(c.post('/qq/social/confirm',headers=h,json=payload).status_code,409)
        self.assertEqual(self.bridge.call.call_count,1)
    def test_new_session_invalidates_confirmation_identity(self):
        c=self.client;h=self.headers
        c.post('/local/session',headers=h,json={})
        c.post('/qq/social/config',headers=h,json={'enabled':True})
        draft=c.post('/qq/social/prepare',headers=h,json={'operation':{
            'kind':'qzone_text','content':'not sent','audience':'self'}}).json()
        c.post('/local/session',headers=h,json={})
        response=c.post('/qq/social/confirm',headers=h,json={
            'nonce':draft['nonce'],'preview':draft['preview'],'confirmed':True})
        self.assertEqual(response.status_code,409)
        self.bridge.call.assert_not_called()
    @unittest.skipUnless(shutil.which('node'), 'Node.js is required for the offline JS/DOM harness')
    def test_offline_javascript_and_dom_harness(self):
        result=subprocess.run([shutil.which('node'),'tests/qq_social_ui_test.js'],
            cwd=ROOT,capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
        self.assertIn('DOM offline safety assertions passed',result.stdout)

if __name__ == '__main__': unittest.main()
