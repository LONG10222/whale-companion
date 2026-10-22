"""Use the portable client's own authenticated WebUI API for QR refresh."""
from pathlib import Path
import hashlib,json,time,subprocess,threading
import httpx

def refresh_qr(root):
    root=Path(root);config=json.loads((root/'configs/qq-client.local.json').read_text(encoding='utf-8'))
    ui=json.loads((Path(config['napcat_root'])/'config/webui.json').read_text(encoding='utf-8'))
    with httpx.Client(trust_env=False,timeout=12) as c:
        base='http://127.0.0.1:6099'
        response=c.post(base+'/api/auth/login',json={'hash':hashlib.sha256((ui['token']+'.napcat').encode()).hexdigest()});response.raise_for_status()
        credential=response.json().get('data',{}).get('Credential')
        if not credential:raise RuntimeError('无法连接独立QQ登录服务。')
        headers={'Authorization':'Bearer '+credential}
        status=c.post(base+'/api/QQLogin/CheckLoginStatus',json={},headers=headers).json()
        if status.get('data',{}).get('isLogin'):return {'logged_in':True}
        image=Path(config['napcat_root'])/'cache/qrcode.png';old=image.stat().st_mtime_ns if image.exists() else 0
        result=c.post(base+'/api/QQLogin/RefreshQRcode',json={},headers=headers).json()
        if result.get('code')!=0:raise RuntimeError('QQ正在重连或等待手机确认，请稍后刷新。')
        for _ in range(20):
            if image.exists() and image.stat().st_mtime_ns!=old:return {'logged_in':False,'refreshed':True}
            time.sleep(.1)
        return {'logged_in':False,'refreshed':False}

class LoginService:
    def __init__(self,root,bridge,admin=None,clock=time.time):
        from qq_account import NapcatAdmin
        self.root=Path(root);self.bridge=bridge;self.admin=admin or NapcatAdmin(root);self.clock=clock;self.lock=threading.Lock();self.cached={};self.checked=0;self.last_refresh=0;self.last_start=0
    def ensure(self,force=False):
        with self.lock:
            now=self.clock()
            if not force and self.cached and now-self.checked<3:return self.cached
            try:
                try:state=self.admin.call('QQLogin/CheckLoginStatus') or {}
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code!=401:raise
                    self.admin.credential='';state=self.admin.call('QQLogin/CheckLoginStatus') or {}
                image=Path(self.admin.config()['napcat_root'])/'cache/qrcode.png'
                expected=self.bridge.cfg.get('locked_bot_id') or self.bridge.cfg.get('bot_id','')
                phase=state.get('loginPhase','');pending=bool(state.get('qrLoginAccepted') or phase in {'qrcode_scanned','initializing','captcha','new_device','reconnecting'})
                out={'logged_in':bool(state.get('isLogin')),'phase':phase,'pending_confirmation':pending,'expected_bot':expected,'qr_ready':False,'message':''}
                owned=self.accounts.status() if getattr(self,'accounts',None) else {}
                if out['logged_in']:
                    info=self.admin.call('QQLogin/GetQQLoginInfo') or {};uin=str(info.get('uin') or '')
                    out.update(login_id=uin,account_matches=uin==expected,message='机器人已登录，正在自动接通。' if uin==expected else '登录账号与机器人小号不一致，请使用指定小号。')
                elif owned.get('phase') in {'captcha','new_device','logging_in','restarting'}:
                    out.update(phase=owned['phase'],pending_confirmation=True,message=owned.get('message','正在恢复账号登录。'))
                elif pending:
                    out['message']='已扫码，请在手机确认或完成必要验证。' if phase!='reconnecting' else 'QQ正在自动重连，暂不刷新二维码。'
                else:
                    age=max(0,now-image.stat().st_mtime) if image.exists() else 9999
                    if (not image.exists() or age>=95 or force and now-self.last_refresh>=10) and now-self.last_refresh>=10:
                        self.admin.call('QQLogin/RefreshQRcode');self.last_refresh=now
                    if image.exists():out.update(qr_ready=True,qr_version=str(image.stat().st_mtime_ns),expires_in=max(0,95-int(now-image.stat().st_mtime)),message='用机器人小号扫码并在手机确认，二维码会自动更新。')
                    else:out['message']='正在准备二维码，页面会自动重试。'
                    if state.get('loginError'):out['message']='QQ需要验证或重新扫码，请按手机提示完成。'
                self.cached=out;self.checked=now;return out
            except (httpx.ConnectError,httpx.ConnectTimeout):
                if now-self.last_start>=60:
                    script=self.root/'start-qq.ps1'
                    if script.is_file():subprocess.Popen(['powershell.exe','-NoProfile','-WindowStyle','Hidden','-File',str(script)],cwd=str(self.root),stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                    self.last_start=now
                return {'logged_in':False,'qr_ready':False,'message':'独立QQ客户端正在启动，页面会自动连接。'}
            except Exception:return {'logged_in':False,'qr_ready':False,'message':'独立QQ暂未就绪，页面会自动重试；手机若有验证请自行完成。'}
