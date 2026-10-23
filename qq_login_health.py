"""Bounded, read-only native-login diagnostics; never exports raw log lines."""
from pathlib import Path
from datetime import datetime,timedelta,timezone
import json,re,threading,uuid

ZONE=timezone(timedelta(hours=8))
REASONS={
    'password_changed':'QQ服务端提示密码已修改，旧会话被撤销。请核对机器人小号的新密码，或改用扫码恢复。',
    'session_revoked':'QQ服务端撤销了当前登录凭据，需要恢复登录；单独重连聊天接口无法恢复这个会话。',
    'other_login':'QQ服务端提示账号在其他设备登录或被挤下线，请核对机器人小号是否还登录在另一台电脑。',
    'server_offline':'QQ服务端要求账号下线，需要重新确认登录。',
}

class LoginHealth:
    def __init__(self,root):self.root=Path(root);self.signature=None;self.result={};self.lock=threading.RLock()
    def snapshot(self):
        with self.lock:return self._snapshot()
    def _snapshot(self):
        path=self.root/'logs/qq/napcat.out.log'
        file=self.root/'configs/qq-login-health.local.json'
        try:
            cfg=json.loads((self.root/'qq.local.json').read_text(encoding='utf-8'));bot=str(cfg.get('locked_bot_id') or cfg.get('bot_id',''))
        except (OSError,ValueError):bot=''
        try:
            old=json.loads(file.read_text(encoding='utf-8'))
            history=old.get('recent_disconnects',[]) if old.get('bot','')==bot else []
            history=[{'at':float(r['at']),'reason':r['reason'],'message':REASONS[r['reason']]} for r in history if r.get('reason') in REASONS][-6:]
        except (OSError,ValueError,TypeError,KeyError):history=[]
        try:
            stat=path.stat();signature=(stat.st_mtime_ns,stat.st_size)
            if signature==self.signature:return dict(self.result)
            with path.open('rb') as source:
                source.seek(max(0,stat.st_size-1024*1024));text=source.read(1024*1024).decode('utf-8',errors='replace')
            events=list(history);now=datetime.now(ZONE)
            for line in text.splitlines():
                if '[KickedOffLine]' not in line:continue
                stamp=re.search(r'(\d{2}-\d{2} \d{2}:\d{2}:\d{2})',line)
                if not stamp:continue
                try:
                    at=datetime.strptime(str(now.year)+'-'+stamp[1],'%Y-%m-%d %H:%M:%S').replace(tzinfo=ZONE)
                    if at>now+timedelta(days=1):at=at.replace(year=now.year-1)
                except ValueError:continue
                reason='password_changed' if re.search('密码.{0,8}(修改|更改|变更)',line) else 'other_login' if re.search('其他设备|别处|挤|顶号|另一台',line) else 'session_revoked' if re.search('登录.{0,8}失效|凭据|过期',line) else 'server_offline'
                event={'at':at.timestamp(),'reason':reason,'message':REASONS[reason]}
                if event not in events:events.append(event)
            events=sorted(events,key=lambda r:r['at'])[-6:]
            self.result={'recent_disconnects':events[-6:],'source':'native_qq_server_notice','automatic_recovery_cannot_override_server_revocation':True}
            if events!=history:
                file.parent.mkdir(parents=True,exist_ok=True);stage=file.with_name('qq-health-'+uuid.uuid4().hex+'.tmp')
                try:stage.write_text(json.dumps({'bot':bot,'recent_disconnects':events},ensure_ascii=False,indent=2),encoding='utf-8');stage.replace(file)
                except OSError:stage.unlink(missing_ok=True)
            self.signature=signature;return dict(self.result)
        except OSError:return {'recent_disconnects':history,'source':'saved_native_notices' if history else 'native_log_unavailable'}
