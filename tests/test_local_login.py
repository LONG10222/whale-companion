from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import tempfile,time,unittest
from fastapi import FastAPI,Depends,Request,Header
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient
from local_session import register_local_sessions,MARKER,COOKIE
from qq_login import LoginService

class LocalSessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);(self.root/'local_session.js').write_text('// local ui')
        self.app=FastAPI();self.service=register_local_sessions(self.app,self.root,'secret-not-in-page');self.changed=[]
        def auth(request:Request,authorization:str=Header(default='')):self.service.authorize(request,authorization)
        @self.app.get('/qq',response_class=HTMLResponse)
        def page():return '<html><body>login</body></html>'
        @self.app.get('/private',dependencies=[Depends(auth)])
        def read():return {'ok':True}
        @self.app.post('/private',dependencies=[Depends(auth)])
        def write():self.changed.append(True);return {'ok':True}
        self.client=TestClient(self.app,base_url='http://127.0.0.1:18766',client=('127.0.0.1',50001));self.headers={'X-Local-Client':MARKER}
    def tearDown(self):self.tmp.cleanup()
    def test_automatic_session_protects_routes_without_disclosing_api_key(self):
        self.assertEqual(self.client.get('/private').status_code,401)
        r=self.client.post('/local/session',headers=self.headers);self.assertEqual(r.status_code,200)
        self.assertIn('HttpOnly',r.headers['set-cookie']);self.assertIn('SameSite=strict',r.headers['set-cookie'])
        self.assertNotIn('secret-not-in-page',r.text);self.assertEqual(self.client.get('/private').status_code,200)
        self.assertEqual(self.client.post('/private',headers=self.headers).status_code,200)
        html=self.client.get('/qq');self.assertIn('/local/session.js',html.text);self.assertNotIn('secret-not-in-page',html.text);self.assertEqual(html.headers['x-frame-options'],'DENY')
    def test_foreign_origin_bad_host_remote_client_and_simple_forms_cannot_bootstrap(self):
        for headers in [{},{**self.headers,'Origin':'https://evil.example'},{**self.headers,'Host':'evil.example'},{**self.headers,'Sec-Fetch-Site':'cross-site'}]:self.assertEqual(self.client.post('/local/session',headers=headers).status_code,403)
        remote=TestClient(self.app,base_url='http://127.0.0.1:18766',client=('192.168.1.10',50000));self.assertEqual(remote.post('/local/session',headers=self.headers).status_code,403)
    def test_cookie_mutations_need_custom_header_and_same_origin(self):
        self.client.post('/local/session',headers=self.headers)
        self.assertEqual(self.client.post('/private').status_code,401)
        self.assertEqual(self.client.post('/private',headers={**self.headers,'Origin':'https://evil.example'}).status_code,401)
        self.assertFalse(self.changed)
        self.assertEqual(self.client.get('/private',headers={'Authorization':'Bearer secret-not-in-page'}).status_code,200)
    def test_expired_or_restarted_sessions_need_bootstrap_again(self):
        self.client.post('/local/session',headers=self.headers)
        with self.service.lock:self.service.sessions.clear()
        self.assertEqual(self.client.get('/private').status_code,401)
        self.client.post('/local/session',headers=self.headers);self.assertEqual(self.client.get('/private').status_code,200)

class LoginAutomationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.image=self.root/'cache/qrcode.png';self.image.parent.mkdir()
        self.now=time.time();self.state={'isLogin':False,'loginPhase':'waiting_qrcode'};self.calls=[]
        def call(path):
            self.calls.append(path)
            if path=='QQLogin/CheckLoginStatus':return self.state
            if path=='QQLogin/GetQQLoginInfo':return {'uin':'12345678'}
            if path=='QQLogin/RefreshQRcode':self.image.write_bytes(b'QR');return {}
            raise AssertionError(path)
        self.admin=SimpleNamespace(call=call,config=lambda:{'napcat_root':str(self.root)},credential='private')
        self.bridge=SimpleNamespace(cfg={'bot_id':'12345678','locked_bot_id':'12345678','enabled':False})
        self.service=LoginService(self.root,self.bridge,self.admin,lambda:self.now)
    def tearDown(self):self.tmp.cleanup()
    def test_missing_qr_is_generated_and_repeated_tabs_do_not_reset_it(self):
        result=self.service.ensure();self.assertTrue(result['qr_ready'])
        for _ in range(3):self.now+=1;self.service.ensure()
        self.assertEqual(self.calls.count('QQLogin/RefreshQRcode'),1)
    def test_scanned_initializing_and_reconnecting_are_never_refreshed(self):
        for phase in ['qrcode_scanned','initializing','captcha','new_device','reconnecting']:
            self.state={'isLogin':False,'loginPhase':phase};self.now+=4
            self.assertTrue(self.service.ensure(force=True)['pending_confirmation'])
        self.assertNotIn('QQLogin/RefreshQRcode',self.calls)
    def test_expired_qr_refreshes_and_pause_or_account_lock_remains(self):
        self.service.ensure();self.now+=100;self.service.ensure();self.assertEqual(self.calls.count('QQLogin/RefreshQRcode'),2)
        self.state={'isLogin':True};self.now+=4;s=self.service.ensure(force=True)
        self.assertTrue(s['account_matches']);self.assertFalse(self.bridge.cfg['enabled'])
        self.bridge.cfg['locked_bot_id']='99999999';self.now+=4;self.assertFalse(self.service.ensure()['account_matches'])
