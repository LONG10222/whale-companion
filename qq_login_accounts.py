"""Owner-only login orchestration. Message tools never receive this object."""
from pathlib import Path
import hashlib,io,threading,time,re,secrets
from urllib.parse import urlparse,parse_qs
from qq_credentials import LoginVault
from qq_identity import qq_id

PENDING={'qrcode_scanned','initializing','captcha','new_device','reconnecting','logging_in','password_login','quick_login'}

class AccountLogin:
    def __init__(self,root,bridge,login,vault=None,clock=time.time):
        self.root=Path(root);self.bridge=bridge;self.login=login;self.admin=login.admin;self.vault=vault or LoginVault(root);self.clock=clock
        self.lock=threading.RLock();self.exit=threading.Event();self.thread=None;self.switcher=None
        self.last_attempt=-1e12;self.next_poll=0;self.pending=None;self.session_secret='';self.session_uin='';self.last_persisted='';self.phase='idle';self.message='';self.attempted=False;self.password_fallback=False;self.recovered=False
    def target(self):
        saved=self.vault.load();expected=str(self.bridge.cfg.get('locked_bot_id') or self.bridge.cfg.get('bot_id') or '')
        # An unfinished legacy QR switch must not trigger restoration of the old account.
        switch=self.switcher.load() if self.switcher else {'phase':'idle'}
        if switch['phase'] in {'preparing','awaiting_login','awaiting_bind'}:return saved.get('switch_target','')
        return expected
    def status(self):
        with self.lock:
            d=self.vault.public();d.update(phase=self.phase,message=self.message,challenge=bool(self.pending),expected_bot=str(self.bridge.cfg.get('locked_bot_id') or self.bridge.cfg.get('bot_id','')))
            if self.pending:
                d['phase']=self.pending['kind'];d['expires_in']=max(0,int(self.pending['expires']-self.clock()));d['challenge_id']=self.pending['id']
                if self.pending['kind']=='captcha':d.update(captcha=self.pending['captcha'])
                if self.pending['kind']=='new_device':d['device_qr_ready']=bool(self.pending.get('png'))
            return d
    def configure(self,uin,password='',label='',remember=True):
        uin=qq_id(uin)
        admin=str((self.bridge.cfg.get('test_contact') or {}).get('user_id',''))
        if uin==admin:raise ValueError('请选择机器人小号，不能把owner的日常账号设为机器人。')
        if len(password)>256:raise ValueError('密码长度不正确。')
        digest=hashlib.md5(password.encode()).hexdigest() if password else None
        with self.lock:
            self.vault.save(uin,digest,label,remember);self.session_secret=digest or '';self.session_uin=uin;self.pending=None;self.attempted=False;self.password_fallback=False
            self.vault.set_pending(None);self.recovered=True
            expected=str(self.bridge.cfg.get('locked_bot_id') or self.bridge.cfg.get('bot_id',''))
            if uin!=expected:
                if not self.switcher:raise ValueError('换号服务尚未就绪。')
                state=self.switcher.load()
                if state['phase'] not in {'awaiting_login','awaiting_bind'}:self.switcher.begin()
                self.vault.update(lambda d:d.update(switch_target=uin));self.phase='restarting';self.message='正在切换独立QQ客户端，原账号回复已暂停。'
                self.last_attempt=self.clock()-90;return self.status()
            return self.attempt(manual=True)
    def _call(self,path,body=None):
        try:return self.admin.call(path,body) if body is not None else self.admin.call(path)
        except Exception as exc:
            import httpx
            if isinstance(exc,httpx.HTTPStatusError) and exc.response.status_code==401:
                self.admin.credential='';return self.admin.call(path,body) if body is not None else self.admin.call(path)
            raise
    def attempt(self,manual=False):
        with self.lock:
            now=self.clock();uin=self.target();saved=self.vault.load()
            if not uin:return self.status()
            if not manual and (not saved.get('automatic',True) or self.attempted or now-self.last_attempt<90):return self.status()
            if self.pending and now<self.pending['expires']:return self.status()
            if saved['accounts'].get(uin,{}).get('blocked') and not manual:return self.status()
            state=self._call('QQLogin/CheckLoginStatus') or {}
            if state.get('isLogin'):
                self._logged_in();return self.status()
            if state.get('qrLoginAccepted') or state.get('loginPhase') in PENDING:
                self.message='QQ正在登录或等待验证，请按手机或页面提示完成。';return self.status()
            self.last_attempt=now;self.attempted=True
            try:
                available=self._call('QQLogin/GetQuickLoginListNew') or []
                quick=any(str(x.get('uin','') if isinstance(x,dict) else x)==uin for x in available)
                if not quick:
                    available=self._call('QQLogin/GetQuickLoginList') or []
                    quick=any(str(x.get('uin','') if isinstance(x,dict) else x)==uin for x in available)
                if quick:
                    try:
                        self._call('QQLogin/SetQuickLogin',{'uin':uin});self.phase='logging_in';self.message='正在用此设备保存的登录状态恢复账号。';return self.status()
                    except Exception:pass
                secret=(self.session_secret if self.session_uin==uin else '') or self.vault.secret(uin)
                if not secret:self.phase='needs_password';self.message='请填写一次机器人小号密码并保存；也可以使用扫码登录。';return self.status()
                result=self._call('QQLogin/PasswordLogin',{'uin':uin,'passwordMd5':secret})
                self._result(uin,secret,result);return self.status()
            except Exception as exc:
                import httpx
                if isinstance(exc,(httpx.ConnectError,httpx.TimeoutException)):
                    self.attempted=False;self.phase='unavailable';self.message='独立QQ正在启动或重连，稍后自动恢复。';return self.status()
                self._blocked(uin);return self.status()
    def _blocked(self,uin):
        self.pending=None;self.phase='needs_attention';self.message='QQ未接受这次登录。请检查密码或完成验证后点击一键登录；程序已停止自动重试。'
        self.vault.update(lambda d:d['accounts'].setdefault(uin,{}).update(blocked=True))
        self.vault.set_pending(None)
    def _result(self,uin,secret,result):
        now=self.clock();result=result or {};self.password_fallback=True
        if result.get('needCaptcha'):
            u=urlparse(result.get('proofWaterUrl',''))
            if u.scheme!='https' or not (u.hostname or '').endswith('.qq.com'):raise ValueError('QQ验证地址未通过校验。')
            p=parse_qs(u.query);self.pending={'id':secrets.token_hex(8),'kind':'captcha','uin':uin,'secret':secret,'expires':now+300,'captcha':{'appid':p.get('aid',['2081081773'])[0],'sid':p.get('sid',[''])[0],'uin':uin,'login_appid':p.get('login_appid',[''])[0]}}
            self.phase='captcha';self.message='请完成页面上的QQ滑块验证，之后会自动继续登录。'
        elif result.get('needNewDevice'):
            u=urlparse(result.get('jumpUrl',''))
            if u.scheme!='https' or not ((u.hostname or '').endswith('.qq.com') or u.hostname=='qq.com'):raise ValueError('QQ设备确认地址未通过校验。')
            self.pending={'id':secrets.token_hex(8),'kind':'new_device','uin':uin,'secret':secret,'sig':str(result.get('newDevicePullQrCodeSig','')),'expires':now+300,'png':None}
            qr=self._call('QQLogin/GetNewDeviceQRCode',{'uin':uin,'jumpUrl':result['jumpUrl']}) or {}
            if not qr.get('str_url'):raise ValueError('QQ未返回设备确认二维码。')
            import qrcode
            output=io.BytesIO();qrcode.make(qr['str_url']).save(output,format='PNG');self.pending.update(png=output.getvalue(),token=qr.get('bytes_token',''))
            self.phase='new_device';self.message='QQ要求确认这台设备，请用手机QQ扫描设备确认码；确认后会自动继续。'
        else:self.pending=None;self.phase='logging_in';self.message='QQ已接受登录，正在恢复连接。'
        saved=self.vault.load()['accounts'].get(uin,{})
        self.vault.set_pending(self.pending if saved.get('protected') else None)
    def captcha(self,ticket,randstr):
        with self.lock:
            p=self.pending
            if not p or p['kind']!='captcha' or self.clock()>=p['expires']:raise ValueError('QQ验证已过期，请重新点击一键登录。')
            if not ticket or not randstr or len(ticket)>4096 or len(randstr)>1024:raise ValueError('QQ验证没有完成。')
            try:
                result=self._call('QQLogin/CaptchaLogin',{'uin':p['uin'],'passwordMd5':p['secret'],'ticket':ticket,'randstr':randstr,'sid':p['captcha']['sid']})
                self._result(p['uin'],p['secret'],result)
            except Exception:self._blocked(p['uin'])
            return self.status()
    def device_qr(self):
        with self.lock:
            if not self.pending or self.pending['kind']!='new_device' or self.clock()>=self.pending['expires'] or not self.pending.get('png'):raise ValueError('设备确认码已过期。')
            return self.pending['png']
    def _logged_in(self):
        info=self._call('QQLogin/GetQQLoginInfo') or {};uin=str(info.get('uin') or '')
        expected=self.target()
        if not uin or not expected or uin!=expected:
            self.phase='wrong_account';self.message='当前登录账号与所选机器人小号不一致，自动回复保持原有账号限制。';return
        switch=self.switcher.load() if self.switcher else {'phase':'idle'}
        if switch['phase'] in {'awaiting_login','awaiting_bind'}:
            self.switcher.bind(uin);self.session_secret='';self.pending=None;self.vault.set_pending(None);self.attempted=False;self.phase='restarting';self.message='新账号已确认，正在自动接通。';return
        if switch['phase']=='checking_friend':
            try:self.switcher.finish()
            except ValueError:self.phase='checking_friend';self.message='新账号已登录。请让owner添加这个小号为好友，程序会自动检查并恢复。';return
        if self.last_persisted!=uin:
            self._call('QQLogin/SetQuickLoginQQ',{'uin':uin});self.last_persisted=uin
            self.vault.update(lambda d:(d.update(default=uin,switch_target=''),d['accounts'].setdefault(uin,{}).update(blocked=False)))
            self.vault.set_pending(None)
        self.pending=None;self.session_secret='';self.session_uin='';self.password_fallback=False;self.phase='online';self.message='机器人已登录，已保存此设备的快速登录选择。'
    def tick(self):
        with self.lock:
            if not self.recovered:
                self.recovered=True
                try:
                    p=self.vault.pending()
                    if p and p.get('uin')==self.target():
                        if p.get('expires',0)>self.clock():self.pending=p;self.phase=p['kind'];self.message='已恢复上次QQ验证，请继续完成。'
                        else:self._blocked(p['uin'])
                except ValueError:self.phase='needs_attention';self.message='上次QQ验证未恢复，请重新点击一键登录。';self.attempted=True
            state=self._call('QQLogin/CheckLoginStatus') or {}
            if state.get('isLogin'):self._logged_in();self.attempted=False;return
            if self.pending:
                if self.clock()>=self.pending['expires']:self._blocked(self.pending['uin']);return
                if self.pending['kind']=='new_device' and self.clock()>=self.next_poll:
                    self.next_poll=self.clock()+3;p=self.pending
                    result=self._call('QQLogin/PollNewDeviceQR',{'uin':p['uin'],'bytesToken':p['token']}) or {}
                    if result.get('uint32_guarantee_status')==1:
                        result=self._call('QQLogin/NewDeviceLogin',{'uin':p['uin'],'passwordMd5':p['secret'],'newDevicePullQrCodeSig':result.get('str_nt_succ_token') or p['sig']})
                        self._result(p['uin'],p['secret'],result)
                return
            if state.get('qrLoginAccepted') or state.get('loginPhase') in PENDING:return
            if self.phase=='logging_in' and self.clock()-self.last_attempt>=30:
                # A stale saved session can silently fail; try the saved password once.
                uin=self.target();secret=(self.session_secret if self.session_uin==uin else '') or self.vault.secret(uin)
                if secret and not getattr(self,'password_fallback',False):
                    self.password_fallback=True
                    try:self._result(uin,secret,self._call('QQLogin/PasswordLogin',{'uin':uin,'passwordMd5':secret}))
                    except Exception:self._blocked(uin)
                return
            self.attempt()
    def start(self):
        if self.thread and self.thread.is_alive():return
        def run():
            while not self.exit.is_set():
                try:self.tick()
                except Exception:pass
                self.exit.wait(3)
        self.thread=threading.Thread(target=run,name='qq-local-login',daemon=True);self.thread.start()
    def close(self):self.exit.set();self.pending=None;self.session_secret=''
