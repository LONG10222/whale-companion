from pathlib import Path
from types import SimpleNamespace
import base64,json,tempfile,unittest
from tests import test_qq_account_login as fixture
from tests import test_local_login as local_fixture
from qq_login_accounts import AccountLogin
from qq_login_health import LoginHealth

class StabilityTests(unittest.TestCase):
    def setUp(self):self.case=fixture.LoginTests();self.case.setUp()
    def tearDown(self):self.case.tearDown()
    def device(self):
        c=self.case;c.result={'needNewDevice':True,'jumpUrl':'https://accounts.qq.com/a?sig=unit&uin-token=unit','newDevicePullQrCodeSig':'private-sig'}
    def test_missing_direct_device_token_uses_current_client_url_fallback(self):
        c=self.case;original=c.admin.call
        def call(path,body=None):
            if path=='QQLogin/GetNewDeviceQRCode':return {'str_url':'https://accounts.qq.com/verify?str_url=artificial-poll-token'}
            return original(path,body)
        c.admin.call=call;self.device();c.login.configure(c.uin,'unit-password')
        self.assertEqual(c.login.pending['token'],base64.b64encode(b'artificial-poll-token').decode())
        c.result=None;c.login.tick();self.assertIn('QQLogin/NewDeviceLogin',c.names())
    def test_no_usable_device_token_stops_instead_of_silently_polling_empty(self):
        c=self.case;original=c.admin.call;c.admin.call=lambda path,body=None:({'str_url':'https://accounts.qq.com/verify'} if path=='QQLogin/GetNewDeviceQRCode' else original(path,body))
        self.device();state=c.login.configure(c.uin,'unit-password');self.assertFalse(state['challenge']);self.assertIn('扫码恢复',state['message']);c.login.tick();self.assertNotIn('QQLogin/PollNewDeviceQR',c.names())
    def test_device_poll_failures_back_off_and_keep_specific_reason(self):
        c=self.case;self.device();c.login.configure(c.uin,'unit-password');original=c.admin.call
        def call(path,body=None):
            if path=='QQLogin/PollNewDeviceQR':raise ValueError('sensitive upstream content')
            return original(path,body)
        c.admin.call=call
        for _ in range(4):c.now+=31;c.login.tick()
        self.assertFalse(c.login.pending);self.assertIn('暂时无法查询',c.login.message);self.assertNotIn('sensitive',str(c.login.status()));self.assertEqual(c.names().count('QQLogin/PasswordLogin'),1)
    def test_device_expiry_is_not_mislabeled_as_picture_captcha(self):
        c=self.case;self.device();c.login.configure(c.uin,'unit-password');c.now+=301;c.login.tick();self.assertIn('设备确认',c.login.message)
    def test_confirmed_without_server_token_does_not_reuse_old_signature(self):
        c=self.case;self.device();c.login.configure(c.uin,'unit-password');original=c.admin.call;c.admin.call=lambda path,body=None:({'uint32_guarantee_status':1} if path=='QQLogin/PollNewDeviceQR' else original(path,body))
        c.login.tick();self.assertNotIn('QQLogin/NewDeviceLogin',c.names());self.assertFalse(c.login.pending)
    def test_qr_recovery_preserves_secret_and_survives_restart_without_password_retry(self):
        c=self.case;self.device();c.login.configure(c.uin,'unit-password');secret=c.vault.secret(c.uin);c.login.use_qr();self.assertFalse(c.vault.pending());self.assertEqual(c.vault.secret(c.uin),secret)
        other=AccountLogin(c.root,c.bridge,SimpleNamespace(admin=c.admin),c.vault,lambda:c.now)
        try:
            other.tick();self.assertEqual(other.status()['recovery_mode'],'qr');self.assertEqual(c.names().count('QQLogin/PasswordLogin'),1)
            c.state['isLogin']=True;other.tick();self.assertEqual(other.phase,'online');self.assertFalse(c.vault.load().get('qr_recovery'))
        finally:other.close()
    def test_qr_preference_clears_even_when_same_account_was_online_before(self):
        c=self.case;c.vault.save(c.uin,'a'*32);c.state['isLogin']=True;c.login.tick();c.state['isLogin']=False;c.login.use_qr();c.state['isLogin']=True;c.login.tick();self.assertFalse(c.vault.load().get('qr_recovery'))
    def test_explicit_qr_recovery_can_leave_stale_native_password_challenge(self):
        case=local_fixture.LoginAutomationTests();case.setUp()
        try:
            case.state={'isLogin':False,'loginPhase':'new_device'};case.service.accounts=SimpleNamespace(status=lambda:{'recovery_mode':'qr','phase':'waiting_qrcode'})
            result=case.service.ensure(force=True);self.assertTrue(result['qr_ready']);self.assertIn('QQLogin/RefreshQRcode',case.calls)
        finally:case.tearDown()

class HealthTests(unittest.TestCase):
    def test_native_log_exports_only_classification_and_time(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);p=root/'logs/qq/napcat.out.log';p.parent.mkdir(parents=True)
            p.write_text('10-22 16:40:06 [error] robot | [KickedOffLine] [下线通知] 你的账号当前登录已失效，请重新登录。\n10-22 17:21:04 [error] robot | [KickedOffLine] [下线通知] 你的QQ密码已修改，请重新登录。\npassword=private-sentinel https://example.com/token\n',encoding='utf-8')
            health=LoginHealth(root);result=health.snapshot();self.assertEqual([r['reason'] for r in result['recent_disconnects']],['session_revoked','password_changed']);self.assertNotIn('private-sentinel',json.dumps(result));self.assertNotIn('example.com',json.dumps(result));self.assertEqual(health.snapshot(),result)
            p.write_text('new startup without account notices',encoding='utf-8');restored=LoginHealth(root).snapshot();self.assertEqual(restored['recent_disconnects'],result['recent_disconnects'])
            (root/'qq.local.json').write_text(json.dumps({'bot_id':'different-unit-account'}),encoding='utf-8');self.assertFalse(LoginHealth(root).snapshot()['recent_disconnects'])

if __name__=='__main__':unittest.main()
