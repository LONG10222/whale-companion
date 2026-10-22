from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import hashlib,json,os,tempfile,time,unittest
from qq_credentials import LoginVault,dpapi
from qq_login_accounts import AccountLogin

def vault(root):return LoginVault(root,encrypt=lambda b:b'UNIT'+b[::-1],decrypt=lambda b:b[4:][::-1])

class VaultTests(unittest.TestCase):
    def test_password_equivalent_is_never_saved_or_returned_in_plaintext(self):
        with tempfile.TemporaryDirectory() as td:
            v=vault(td);digest=hashlib.md5(b'unit-password').hexdigest();v.save('12345678',digest,'unit')
            raw=v.path.read_text(encoding='utf-8');self.assertNotIn(digest,raw);self.assertNotIn('unit-password',raw);self.assertEqual(v.secret('12345678'),digest)
            self.assertNotIn(digest,str(v.public()));self.assertNotIn('protected',str(v.public()))
            v.forget('12345678');self.assertFalse(v.secret('12345678'))
    @unittest.skipUnless(os.name=='nt','Windows DPAPI')
    def test_actual_windows_encryption_roundtrip(self):
        secret=b'artificial-unit-secret';encrypted=dpapi(secret);self.assertNotIn(secret,encrypted);self.assertEqual(dpapi(encrypted,True),secret)
    def test_do_not_remember_removes_only_this_accounts_secret(self):
        with tempfile.TemporaryDirectory() as td:
            v=vault(td);v.save('12345678','a'*32);v.save('99999999','b'*32);v.save('12345678',remember=False)
            self.assertFalse(v.secret('12345678'));self.assertEqual(v.secret('99999999'),'b'*32)

class LoginTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.now=1000.;self.calls=[];self.state={'isLogin':False,'loginPhase':'waiting_qrcode'};self.result=None;self.quick=[];self.uin='12345678';self.raise_password=False
        def call(path,body=None):
            self.calls.append((path,body))
            if path=='QQLogin/CheckLoginStatus':return self.state
            if path=='QQLogin/GetQQLoginInfo':return {'uin':self.uin}
            if path in {'QQLogin/GetQuickLoginList','QQLogin/GetQuickLoginListNew'}:return self.quick
            if path in {'QQLogin/PasswordLogin','QQLogin/CaptchaLogin','QQLogin/NewDeviceLogin'}:
                if self.raise_password:raise ValueError('NEVER_ECHO_RAW_SECRET')
                return self.result
            if path in {'QQLogin/SetQuickLogin','QQLogin/SetQuickLoginQQ'}:return None
            if path=='QQLogin/GetNewDeviceQRCode':return {'str_url':'https://accounts.qq.com/device-confirm?unit=1','bytes_token':'private-token'}
            if path=='QQLogin/PollNewDeviceQR':return {'uint32_guarantee_status':1,'str_nt_succ_token':'confirmed-unit-token'}
            raise AssertionError(path)
        self.admin=SimpleNamespace(call=call,credential='');self.bridge=SimpleNamespace(cfg={'bot_id':self.uin,'locked_bot_id':self.uin,'test_contact':{'user_id':'87654321'},'enabled':False})
        self.vault=vault(self.root);self.login=AccountLogin(self.root,self.bridge,SimpleNamespace(admin=self.admin),self.vault,lambda:self.now)
    def tearDown(self):self.login.close();self.tmp.cleanup()
    def names(self):return [p for p,_ in self.calls]
    def test_explicit_password_login_and_save_never_changes_reply_scope(self):
        result=self.login.configure(self.uin,'unit-password');self.assertEqual(result['phase'],'logging_in')
        self.assertIn('QQLogin/PasswordLogin',self.names());self.assertNotIn('unit-password',str(result));self.assertNotIn(hashlib.md5(b'unit-password').hexdigest(),str(result))
        self.assertFalse(self.bridge.cfg['enabled'])
    def test_saved_session_is_preferred_and_stale_session_falls_back_only_once(self):
        self.vault.save(self.uin,'a'*32);self.quick=[{'uin':self.uin}];self.login.attempt();self.assertNotIn('QQLogin/PasswordLogin',self.names())
        self.now+=31;self.login.tick();self.assertEqual(self.names().count('QQLogin/PasswordLogin'),1)
        self.now+=91;self.login.tick();self.assertEqual(self.names().count('QQLogin/PasswordLogin'),1)
    def test_wrong_password_is_blocked_even_across_restart(self):
        self.raise_password=True;self.login.configure(self.uin,'unit-password');self.assertEqual(self.login.phase,'needs_attention')
        for _ in range(5):self.now+=1000;self.login.tick()
        other=AccountLogin(self.root,self.bridge,SimpleNamespace(admin=self.admin),self.vault,lambda:self.now);other.attempt()
        self.assertEqual(self.names().count('QQLogin/PasswordLogin'),1);self.assertNotIn('NEVER_ECHO',str(self.login.status()))
    def test_scanned_and_reconnecting_are_never_interrupted(self):
        self.vault.save(self.uin,'a'*32)
        for phase in ['qrcode_scanned','initializing','reconnecting']:
            self.state['loginPhase']=phase;self.now+=100;self.login.tick()
        self.assertNotIn('QQLogin/SetQuickLogin',self.names());self.assertNotIn('QQLogin/PasswordLogin',self.names())
    def test_captcha_uses_same_accounts_secret_and_no_fabricated_ticket(self):
        self.result={'needCaptcha':True,'proofWaterUrl':'https://ssl.captcha.qq.com/a?aid=2081081773&sid=unit-sid'}
        state=self.login.configure(self.uin,'unit-password');self.assertEqual(state['phase'],'captcha');self.assertNotIn('secret',str(state))
        self.login.tick();self.assertEqual(self.names().count('QQLogin/PasswordLogin'),1)
        self.result=None;self.login.captcha('genuine-unit-ticket','genuine-unit-rand')
        body=next(b for p,b in self.calls if p=='QQLogin/CaptchaLogin');self.assertEqual(body['sid'],'unit-sid');self.assertEqual(body['uin'],self.uin)
    def test_new_device_qr_poll_and_continue(self):
        self.result={'needNewDevice':True,'jumpUrl':'https://accounts.qq.com/a?sig=unit&uin-token=unit','newDevicePullQrCodeSig':'private-sig'}
        state=self.login.configure(self.uin,'unit-password');self.assertTrue(state['device_qr_ready']);self.assertTrue(self.login.device_qr().startswith(b'\x89PNG'))
        self.assertNotIn('private-sig',str(state));self.assertNotIn('private-token',str(state));self.result=None;self.login.tick()
        body=next(b for p,b in self.calls if p=='QQLogin/NewDeviceLogin');self.assertEqual(body['newDevicePullQrCodeSig'],'confirmed-unit-token')
    def test_device_confirmation_expiry_does_not_retry_the_password(self):
        self.result={'needCaptcha':True,'proofWaterUrl':'https://ssl.captcha.qq.com/a'};self.login.configure(self.uin,'unit-password');self.now+=301;self.login.tick()
        self.assertFalse(self.login.pending);self.assertEqual(self.names().count('QQLogin/PasswordLogin'),1)
    def test_pending_captcha_survives_companion_restart_encrypted(self):
        self.result={'needCaptcha':True,'proofWaterUrl':'https://ssl.captcha.qq.com/a?sid=unit'};self.login.configure(self.uin,'unit-password')
        self.assertNotIn(hashlib.md5(b'unit-password').hexdigest(),self.vault.path.read_text(encoding='utf-8'))
        other=AccountLogin(self.root,self.bridge,SimpleNamespace(admin=self.admin),self.vault,lambda:self.now);other.tick()
        self.assertEqual(other.status()['phase'],'captcha');self.assertEqual(self.names().count('QQLogin/PasswordLogin'),1)
        self.result=None;other.captcha('unit-ticket','unit-rand');self.assertFalse(self.vault.pending());other.close()
    def test_do_not_remember_keeps_challenge_credential_in_memory_only(self):
        self.result={'needCaptcha':True,'proofWaterUrl':'https://ssl.captcha.qq.com/a?sid=unit'}
        self.login.configure(self.uin,'unit-password',remember=False)
        self.assertTrue(self.login.pending);self.assertFalse(self.vault.pending());self.assertFalse(self.vault.secret(self.uin))
    def test_active_logged_account_is_not_assumed_to_be_selected_bot(self):
        self.state['isLogin']=True;self.uin='99999999';self.login.tick();self.assertEqual(self.login.phase,'wrong_account');self.assertNotIn('QQLogin/SetQuickLoginQQ',self.names())
    def test_real_success_persists_client_quick_choice_without_enabling_paused_replies(self):
        self.state['isLogin']=True;self.login.tick();self.login.tick()
        self.assertEqual(self.names().count('QQLogin/SetQuickLoginQQ'),1);self.assertFalse(self.bridge.cfg['enabled'])
    def test_owner_personal_account_cannot_become_bot(self):
        with self.assertRaises(ValueError):self.login.configure('87654321','unit-password')
        self.assertFalse(self.calls)
    def test_legacy_manual_switch_does_not_restore_old_account(self):
        self.login.switcher=SimpleNamespace(load=lambda:{'phase':'awaiting_login'})
        self.vault.save(self.uin,'a'*32);self.login.attempt();self.assertNotIn('QQLogin/PasswordLogin',self.names())
    def test_failed_switch_cannot_apply_new_accounts_password_to_old_bot(self):
        self.vault.save(self.uin,'a'*32)
        def begin():raise ValueError('Existing work must finish first')
        self.login.switcher=SimpleNamespace(load=lambda:{'phase':'complete'},begin=begin)
        with self.assertRaises(ValueError):self.login.configure('99999999','new-unit-password')
        self.login.attempt();body=next(b for p,b in self.calls if p=='QQLogin/PasswordLogin');self.assertEqual(body['passwordMd5'],'a'*32)
    def test_new_account_switch_is_staged_and_friend_checked_before_resume(self):
        phase={'phase':'complete'};actions=[]
        def begin():actions.append('begin');phase['phase']='awaiting_login';self.bridge.cfg['enabled']=False
        def bind(uin):actions.append('bind');self.bridge.cfg.update(bot_id=uin,locked_bot_id=uin);phase['phase']='checking_friend';self.state['isLogin']=False
        def finish():actions.append('finish');phase['phase']='complete';self.bridge.cfg['enabled']=True
        self.login.switcher=SimpleNamespace(load=lambda:phase,begin=begin,bind=bind,finish=finish)
        self.login.configure('99999999','unit-password');self.assertEqual(actions,['begin']);self.assertFalse(self.bridge.cfg['enabled'])
        self.now+=100;self.login.tick();self.uin='99999999';self.state['isLogin']=True;self.login.tick();self.assertEqual(actions,['begin','bind'])
        self.state['isLogin']=True;self.login.tick();self.assertEqual(actions,['begin','bind','finish']);self.assertTrue(self.bridge.cfg['enabled'])

class RouteTests(unittest.TestCase):
    def test_credentials_and_account_list_are_owner_only(self):
        from fastapi import FastAPI,Header,HTTPException
        from fastapi.testclient import TestClient
        from qq_routes import register_qq_routes
        with tempfile.TemporaryDirectory() as td:
            app=FastAPI()
            def auth(authorization:str=Header(default='')):
                if authorization!='Bearer owner':raise HTTPException(401)
            bridge=register_qq_routes(app,authorize=auth,root=Path(td));client=TestClient(app)
            try:
                for method,path,kw in [('get','/qq/login/accounts',{}),('post','/qq/login/account',{'json':{'uin':'12345678','password':'unit-secret'}}),('get','/qq/login/device-qr',{}),('post','/qq/login/captcha',{'json':{'ticket':'x','randstr':'y'}}),('delete','/qq/login/accounts/12345678',{})]:
                    self.assertEqual(getattr(client,method)(path,**kw).status_code,401)
                response=client.get('/qq/login/accounts',headers={'Authorization':'Bearer owner'});self.assertEqual(response.status_code,200);self.assertNotIn('protected',response.text)
            finally:bridge.close()

if __name__=='__main__':unittest.main()
