from pathlib import Path
from unittest.mock import patch
import httpx,json,tempfile,unittest
from qq_account import NapcatAdmin,AdminOperationError

class AdminAuthenticationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);(self.root/'config').mkdir();(self.root/'config/webui.json').write_text(json.dumps({'token':'artificial-unit-token'}))
        self.admin=NapcatAdmin(self.root);self.admin.config=lambda:{'napcat_root':str(self.root)};self.admin.credential='expired-unit-credential';self.paths=[];self.original=httpx.Client
    def tearDown(self):self.tmp.cleanup()
    def client(self,handler):
        transport=httpx.MockTransport(handler)
        return patch('qq_account.httpx.Client',side_effect=lambda **kw:self.original(transport=transport,**kw))
    def test_http_200_unauthorized_refreshes_webui_credential_once(self):
        def handler(request):
            self.paths.append(request.url.path)
            if request.url.path.endswith('/auth/login'):return httpx.Response(200,json={'code':0,'data':{'Credential':'new-unit-credential'}})
            if request.headers['authorization'].endswith('expired-unit-credential'):return httpx.Response(200,json={'code':-1,'message':'Unauthorized'})
            return httpx.Response(200,json={'code':0,'data':{'isLogin':False}})
        with self.client(handler):self.assertEqual(self.admin.call('QQLogin/CheckLoginStatus'),{'isLogin':False})
        self.assertEqual(len(self.paths),3);self.assertEqual(self.paths.count('/api/auth/login'),1)
    def test_actual_qq_login_failure_is_not_retried_as_authentication_failure(self):
        def handler(request):self.paths.append(request.url.path);return httpx.Response(200,json={'code':-1,'message':'QQ login failed'})
        with self.client(handler),self.assertRaises(ValueError):self.admin.call('QQLogin/PasswordLogin',{'passwordMd5':'artificial-digest'})
        self.assertEqual(self.paths,['/api/QQLogin/PasswordLogin'])
    def test_repeated_unauthorized_does_not_loop(self):
        def handler(request):
            self.paths.append(request.url.path)
            return httpx.Response(200,json={'code':0,'data':{'Credential':'new-unit-credential'}}) if request.url.path.endswith('/auth/login') else httpx.Response(200,json={'code':-1,'message':'Unauthorized'})
        with self.client(handler),self.assertRaises(ValueError):self.admin.call('QQLogin/CheckLoginStatus')
        self.assertEqual(len(self.paths),3)
    def test_password_rejection_is_classified_without_echoing_raw_credential(self):
        def handler(request):return httpx.Response(200,json={'code':-1,'message':'账号或密码不正确 artificial-sensitive-string'})
        with self.client(handler),self.assertRaises(AdminOperationError) as caught:self.admin.call('QQLogin/PasswordLogin')
        self.assertEqual(caught.exception.reason,'password_rejected');self.assertNotIn('artificial-sensitive',str(caught.exception))
    def test_captcha_rejection_is_distinct_from_password_rejection(self):
        def handler(request):return httpx.Response(200,json={'code':-1,'message':'验证码校验失败 artificial-sensitive-string'})
        with self.client(handler),self.assertRaises(AdminOperationError) as caught:self.admin.call('QQLogin/CaptchaLogin')
        self.assertEqual(caught.exception.reason,'verification_rejected');self.assertNotIn('artificial-sensitive',str(caught.exception))

if __name__=='__main__':unittest.main()
