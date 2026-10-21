"""Owner-only, staged bot account replacement. Conversation tools cannot call this service."""
from pathlib import Path
from contextlib import closing
import hashlib,json,re,shutil,sqlite3,threading,time,uuid
import httpx

class NapcatAdmin:
    def __init__(self,root):self.root=Path(root);self.credential=''
    def config(self):return json.loads((self.root/'configs/qq-client.local.json').read_text(encoding='utf-8'))
    def call(self,path,body=None):
        cfg=self.config();ui=json.loads((Path(cfg['napcat_root'])/'config/webui.json').read_text(encoding='utf-8'))
        with httpx.Client(trust_env=False,timeout=12) as c:
            if not self.credential:
                r=c.post('http://127.0.0.1:6099/api/auth/login',json={'hash':hashlib.sha256((ui['token']+'.napcat').encode()).hexdigest()});r.raise_for_status()
                self.credential=r.json().get('data',{}).get('Credential','')
                if not self.credential:raise ValueError('独立QQ管理服务未就绪。')
            r=c.post('http://127.0.0.1:6099/api/'+path,json=body or {},headers={'Authorization':'Bearer '+self.credential});r.raise_for_status();data=r.json()
            if data.get('code')!=0:raise ValueError('独立QQ管理服务暂未完成操作，请稍后重试。')
            return data.get('data')
    def login(self):
        state=self.call('QQLogin/CheckLoginStatus') or {}
        if not state.get('isLogin'):return {'logged_in':False}
        info=self.call('QQLogin/GetQQLoginInfo') or {};uin=str(info.get('uin') or info.get('qq') or info.get('user_id') or '')
        return {'logged_in':bool(re.fullmatch(r'[1-9]\d{4,11}',uin)),'uin':uin,'nickname':str(info.get('nick') or info.get('nickName') or info.get('nickname') or '')[:60]}

class AccountSwitch:
    def __init__(self,root,bridge,proactive=None,admin=None):
        self.root=Path(root);self.bridge=bridge;self.proactive=proactive;self.admin=admin or NapcatAdmin(root);self.lock=threading.Lock()
        self.path=self.root/'configs/qq-account-switch.local.json'
    def load(self):return json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {'phase':'idle'}
    def save(self,state):
        self.path.parent.mkdir(parents=True,exist_ok=True);tmp=self.path.with_suffix('.tmp');tmp.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8');tmp.replace(self.path)
    def scope(self):
        cfg=self.bridge.cfg;who=str((cfg.get('test_contact') or {}).get('user_id',''))
        if (cfg.get('test_contact') or {}).get('name')!='owner' or cfg['allowed_private_ids']!=[who]:raise ValueError('换号前必须保持测试联系人仅为long。')
        return who
    def begin(self):
        with self.lock:
            who=self.scope();old=self.load()
            if old['phase'] in {'awaiting_login','awaiting_bind','checking_friend'}:return self.state()
            self.bridge.configure(False,self.bridge.cfg['bot_id'],[who],self.bridge.cfg['voice_enabled'])
            with self.bridge.chat.memory.db() as db:
                if db.execute("SELECT count(*) FROM events WHERE state IN ('accepted','sending') AND at>?",(time.time()-1800,)).fetchone()[0]:raise ValueError('旧号还有正在收尾的消息，请稍后再换。')
            tasks=getattr(self.bridge.chat,'tasks',None)
            if tasks:
                with tasks.db() as db:
                    if db.execute("SELECT count(*) FROM tasks WHERE state IN ('accepted','running')").fetchone()[0]:raise ValueError('请先停止或等待当前手机任务完成。')
            creative=getattr(self.bridge.chat,'creative',None)
            if creative:
                with creative.db() as db:
                    if db.execute("SELECT count(*) FROM jobs WHERE state IN ('accepted','running')").fetchone()[0]:raise ValueError('请先等待创作或Codex任务结束，再更换账号。')
            folder=self.root/'data/qq/account-backups'/uuid.uuid4().hex;folder.mkdir(parents=True)
            shutil.copy2(self.bridge.file,folder/'qq.local.json')
            for name in ['qq-memory.sqlite3','persona-learning.sqlite3','companion-life.sqlite3','action-consent.sqlite3']:
                src=self.root/'data/qq'/name
                if src.exists():
                    with closing(sqlite3.connect(src)) as source,closing(sqlite3.connect(folder/name)) as dest:source.backup(dest)
            shutil.copy2(self.root/'configs/persona.local.json',folder/'persona.local.json')
            state={'phase':'preparing','old_bot':self.bridge.cfg['bot_id'],'backup':str(folder),'started':time.time()};self.save(state)
            # Clears only this portable client's quick-login choice; no profile or history is deleted.
            self.admin.call('QQLogin/SetQuickLoginQQ',{'uin':''})
            self.admin.call('QQLogin/RestartNapCat');self.admin.credential=''
            state['phase']='awaiting_login';self.save(state)
            return {'phase':state['phase'],'message':'独立QQ已切到扫码准备，旧号回复保持暂停。'}
    def state(self):
        s=self.load();out={'phase':s['phase'],'old_bot':s.get('old_bot'),'new_bot':s.get('new_bot'),'reply_enabled':self.bridge.cfg['enabled'],'contact':'owner','memory_preserved':True}
        try:out['login']=self.admin.login()
        except Exception:out['login']={'logged_in':False,'message':'独立QQ正在重启或等待扫码。'}
        if s['phase']=='awaiting_login' and out['login'].get('logged_in') and out['login'].get('uin')!=s['old_bot']:out['phase']='awaiting_bind'
        return out
    def bind(self,expected):
        with self.lock:
            who=self.scope();s=self.load();login=self.admin.login();uin=login.get('uin','')
            if s['phase'] not in {'awaiting_login','awaiting_bind'}:raise ValueError('请先开始换号。')
            if not login.get('logged_in') or uin!=expected or uin in {s['old_bot'],who}:raise ValueError('请核对扫码登录的是新机器人小号，不能是long或原小号。')
            cfg=self.admin.config();nc=Path(cfg['napcat_root'])/'config';source=nc/('onebot11_'+s['old_bot']+'.json')
            if not source.is_file():raise ValueError('找不到原有本机QQ连接配置，未切换。')
            data=json.loads(source.read_text(encoding='utf-8'));servers=data.get('network',{}).get('websocketServers',[])
            if not any(str(v.get('port'))=='3001' and v.get('host')=='127.0.0.1' for v in servers):raise ValueError('原连接不是已核对的本机端口配置，未切换。')
            target=nc/('onebot11_'+uin+'.json')
            if target.exists():shutil.copy2(target,Path(s['backup'])/target.name)
            target.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
            # Preserve the exact friend and local credential. Only the owner-confirmed bot identity changes.
            with self.bridge.lock:
                self.bridge.cfg.update(bot_id=uin,locked_bot_id=uin,enabled=False)
                stage=self.bridge.file.with_suffix('.staging.json');stage.write_text(json.dumps(self.bridge.cfg,ensure_ascii=False,indent=2),encoding='utf-8');stage.replace(self.bridge.file)
            from chat_permissions import ChatPermissions
            ChatPermissions(self.root).revoke_bot(s['old_bot'])
            if self.proactive:
                with self.proactive.db() as db:db.execute("UPDATE drafts SET state='stale' WHERE peer=? AND state='draft'",(who,))
            s.update(phase='checking_friend',new_bot=uin);self.save(s)
            self.admin.call('QQLogin/SetQuickLoginQQ',{'uin':uin});self.admin.call('QQLogin/RestartNapCat');self.admin.credential=''
            return {'phase':'checking_friend','message':'已绑定新小号。等待连接恢复，并用long添加新小号为好友。'}
    def finish(self):
        with self.lock:
            who=self.scope();s=self.load();snapshot=self.bridge.snapshot()
            if s['phase']!='checking_friend' or snapshot['login_id']!=s.get('new_bot') or not snapshot['account_matches']:raise ValueError('新小号尚未接通，请稍后检查。')
            friends=self.bridge.call('get_friend_list')
            if not isinstance(friends,list) or not any(str(f.get('user_id'))==who for f in friends):raise ValueError('请先用long添加新小号为好友，再点击检查。')
            self.bridge.configure(True,s['new_bot'],[who],self.bridge.cfg['voice_enabled']);s['phase']='complete';self.save(s)
            return {'complete':True,'message':'新小号已启用，仅回复long；人格和记忆已保留。'}
