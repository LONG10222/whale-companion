"""Owner-authorized groups, transport-bound grants, cooldowns and sent receipts."""
from pathlib import Path
from contextlib import contextmanager
from datetime import datetime,timedelta,timezone
import json,re,sqlite3,threading,time
from qq_scope import actor,group

class QQGroups:
    def __init__(self,root,bridge,clock=time.time):
        self.root=Path(root);self.bridge=bridge;self.clock=clock;self.path=self.root/'data/qq/groups.sqlite3';self.path.parent.mkdir(parents=True,exist_ok=True)
        self.lock=threading.RLock();self.cooldowns={}
        with self.db() as db:db.executescript('''PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS grants(bot TEXT,gid TEXT,name TEXT,enabled INTEGER,revision INTEGER,expires REAL,origin TEXT,at REAL,PRIMARY KEY(bot,gid));
            CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY,bot TEXT,gid TEXT,enabled INTEGER,origin TEXT,source_mid TEXT,at REAL);
            CREATE TABLE IF NOT EXISTS receipts(bot TEXT,gid TEXT,mid TEXT,peer TEXT,local_id INTEGER,at REAL,PRIMARY KEY(bot,gid,mid));
            CREATE TABLE IF NOT EXISTS directory(bot TEXT,gid TEXT,name TEXT,members INTEGER,at REAL,PRIMARY KEY(bot,gid));''')
    @contextmanager
    def db(self):
        db=sqlite3.connect(self.path,timeout=10);db.row_factory=sqlite3.Row
        try:
            with db:yield db
        finally:db.close()
    def bot(self):return str(self.bridge.cfg.get('bot_id',''))
    def refresh(self):
        bot=self.bot()
        if not self.bridge.connected or self.bridge.login_id!=bot:raise ValueError('机器人账号尚未连接，暂时不能读取群列表。')
        rows=self.bridge.call('get_group_list')
        if self.bot()!=bot or self.bridge.login_id!=bot:raise ValueError('读取期间机器人账号已改变，未保存旧账号群列表。')
        if not isinstance(rows,list):raise ValueError('QQ未返回有效群列表。')
        if not rows:return {**self.state(),'directory_empty':True,'cached_directory_retained':True}
        with self.db() as db:
            db.execute('DELETE FROM directory WHERE bot=?',(bot,))
            for row in rows:
                gid=str(row.get('group_id',''))
                if re.fullmatch(r'[1-9]\d{4,11}',gid):db.execute('INSERT INTO directory VALUES(?,?,?,?,?)',(bot,gid,str(row.get('group_name',''))[:100],int(row.get('member_count',0)),self.clock()))
            missing=db.execute('SELECT gid FROM grants WHERE bot=? AND enabled=1 AND gid NOT IN (SELECT gid FROM directory WHERE bot=?)',(bot,bot)).fetchall()
            for row in missing:
                db.execute('UPDATE grants SET enabled=0,revision=revision+1 WHERE bot=? AND gid=?',(bot,row['gid']))
                db.execute('INSERT INTO audit(bot,gid,enabled,origin,source_mid,at) VALUES(?,?,0,?,?,?)',(bot,row['gid'],'membership_absent','',self.clock()))
        return self.state()
    def state(self):
        with self.db() as db:
            rows=[dict(r) for r in db.execute('SELECT d.*,g.enabled,g.revision,g.expires FROM directory d LEFT JOIN grants g ON g.bot=d.bot AND g.gid=d.gid WHERE d.bot=?',(self.bot(),))]
        for row in rows:row['enabled']=bool(row.get('enabled') and (not row.get('expires') or row['expires']>self.clock()))
        return {'groups':rows,'trigger':'mention_or_reply','automatic_join':False,'group_min_interval_seconds':5,'user_min_interval_seconds':10,'private_memory_shared':False,
            'reply_service_enabled':bool(self.bridge.cfg.get('enabled')),'qq_connected':bool(self.bridge.connected),'account_matches':self.bridge.login_id==self.bot()}
    def grant(self,gid):
        with self.db() as db:r=db.execute('SELECT * FROM grants WHERE bot=? AND gid=?',(self.bot(),str(gid))).fetchone()
        return dict(r) if r else {}
    def allowed(self,gid,revision=None):
        r=self.grant(gid)
        return bool(r.get('enabled') and (not r['expires'] or r['expires']>self.clock()) and (revision is None or r['revision']==revision))
    def configure(self,gid,enabled,origin='local_owner',source_mid='',expires=0):
        if type(enabled) is not bool or origin not in {'local_owner','qq_admin_private'}:raise ValueError('群授权来源无效。')
        gid=str(gid);bot=self.bot()
        if enabled:self.refresh()
        if self.bot()!=bot:raise ValueError('机器人账号已变化，未应用旧账号授权。')
        with self.db() as db:
            row=db.execute('SELECT * FROM directory WHERE bot=? AND gid=?',(bot,gid)).fetchone()
            if not row:raise ValueError('机器人尚未加入这个群，未启用。')
            old=db.execute('SELECT revision FROM grants WHERE bot=? AND gid=?',(bot,gid)).fetchone();revision=(old[0] if old else 0)+1
            db.execute('INSERT OR REPLACE INTO grants VALUES(?,?,?,?,?,?,?,?)',(bot,gid,row['name'],int(enabled),revision,expires,origin,self.clock()))
            db.execute('INSERT INTO audit(bot,gid,enabled,origin,source_mid,at) VALUES(?,?,?,?,?,?)',(bot,gid,int(enabled),origin,str(source_mid),self.clock()))
        self.bridge.batcher.wake()
        from usage_ledger import Ledger
        Ledger(self.root).record('permission_changed',peer=self.bridge.contacts.admin,state='enabled' if enabled else 'disabled',details={'group_id':gid,'origin':origin,'source_mid':str(source_mid),'revision':revision,'expires':expires})
        return {'group_id':gid,'name':row['name'],'enabled':enabled,'revision':revision,'expires':expires,'trigger':'mention_or_reply'}
    def reply_is_bot(self,gid,mid):
        with self.db() as db:return bool(db.execute('SELECT 1 FROM receipts WHERE bot=? AND gid=? AND mid=?',(self.bot(),str(gid),str(mid))).fetchone())
    def accepted(self,event):
        gid=str(event.get('group_id',''));segments=event.get('message')
        if not isinstance(segments,list) or not self.allowed(gid,event.get('_group_revision')):return False
        mentioned=any(s.get('type')=='at' and str(s.get('data',{}).get('qq',''))==self.bot() for s in segments)
        reply=next((s.get('data',{}).get('id') for s in segments if s.get('type')=='reply'),None)
        return mentioned or reply is not None and self.reply_is_bot(gid,reply)
    def reserve(self,gid,uid):
        now=time.monotonic()
        with self.lock:
            keys=[('group',str(gid)),('user',str(gid),str(uid))]
            if any(now-self.cooldowns.get(key,-10000)<seconds for key,seconds in zip(keys,[5,10])):return False
            for key in keys:self.cooldowns[key]=now
            if len(self.cooldowns)>1000:self.cooldowns={k:v for k,v in self.cooldowns.items() if now-v<60}
            return True
    def receipt(self,peer,remote_id,local_id):
        gid=group(peer)
        if gid:
            with self.db() as db:db.execute('INSERT OR REPLACE INTO receipts VALUES(?,?,?,?,?,?)',(self.bot(),gid,str(remote_id),peer,local_id,self.clock()))
    def quote(self,gid,mid):
        with self.db() as db:r=db.execute('SELECT peer,local_id FROM receipts WHERE bot=? AND gid=? AND mid=?',(self.bot(),str(gid),str(mid))).fetchone()
        if not r:return {'missing':True}
        with self.bridge.chat.memory.db() as db:m=db.execute('SELECT content FROM messages WHERE peer=? AND id=?',(r['peer'],r['local_id'])).fetchone()
        return {'role':'assistant','text':m[0].split('\n[本轮')[0][:3500]} if m else {'missing':True}
    def command(self,peer,text,context):
        # Every control action is bound to an actual private administrator event.
        if group(peer) or not re.search('群',text):return None
        if re.search('转发|引用|别人说|他说|她说|你(?:刚才|昨天|之前)?说|例如|假如|怎么|如何|为什么|为何|是否|[“「]',text):return None
        inspect=bool(re.search('群列表|加入了哪些群|加入哪些群|在哪些群|群聊状态|看.{0,3}群|群.{0,4}(?:看见|看得到)|(?:邀请你|拉你|加入了).{0,8}群',text))
        disable=bool(re.search('暂停|停止|关闭|停用|撤销|取消|别再|不要再|不要在|别在',text) and re.search('群.{0,8}(?:聊|回|消息|授权)|群聊',text))
        if re.search(r'(?:不要|别|不许|不用).{0,3}(?:暂停|停止|关闭|停用|撤销|取消)',text):return None
        if re.search(r'^(?:我(?:刚才|今天|昨天|正在|已经)?在|我(?:看到|看见|发现)你)|(?:没有|没|不|未)允许|(?:没有|未)授权',text):return None
        negative=bool(re.search('不要|别|不许|禁止|不用|不需要|没让|没有让',text))
        negative_reply=bool(re.search(r'(?:不要|别|不许|禁止|不用|不需要).{0,18}群(?:里面|里)?(?:聊天|聊聊|回复|发消息|聊[。！!啊呀吧\s]*$)',text))
        # Mentioning one's own group activity or reported speech cannot authorize the bot.
        if negative:
            disable=negative_reply or bool(re.search('暂停|停止|关闭|停用|撤销|取消',text))
            if not disable:return None
        enable=bool(not disable and (re.search(r'(?:开启|启用|允许|授权).{0,25}群',text) or re.search(r'(?:^(?:今天|今晚|现在|直接)|你|鲸娅|请|帮我).{0,6}(?:去|到|在).{0,18}群.{0,10}(?:聊天|聊聊|回复|发消息|聊[啊呀吧。！!\s]*$)',text) or re.fullmatch(r'(?:去|到).{0,18}群(?:里面|里)?(?:聊天|聊聊|回复|发消息|聊)[啊呀吧。！!\s]*',text)))
        if not (inspect or enable or disable):return None
        if actor(peer)!=self.bridge.contacts.admin:return '群聊范围需要 long 在私聊里设置。'
        from chat_permissions import ChatPermissions
        if not ChatPermissions(self.root).origin(peer,context):return '这项设置需要来自当前管理员的真实私聊。'
        state=self.refresh();rows=state['groups']
        if inspect and not (enable or disable):
            return '我目前加入了：'+('；'.join(r['name']+'（'+r['gid']+'，'+('群聊已启用' if r['enabled'] else '尚未启用回复')+'）' for r in rows) if rows else '暂时没有群。')
        matches=[r for r in rows if r['gid'] in text or r['name'] and r['name'] in text]
        if not matches and len(rows)==1:matches=rows
        if len(matches)!=1:return '需要确定是哪一个群。目前有：'+'、'.join(r['name']+'（'+r['gid']+'）' for r in rows)
        expires=0
        if '今天' in text:expires=(datetime.fromtimestamp(self.clock(),timezone(timedelta(hours=8)))+timedelta(days=1)).replace(hour=0,minute=0,second=0,microsecond=0).timestamp()
        result=self.configure(matches[0]['gid'],not disable,'qq_admin_private',context['mid'],expires)
        return ('已暂停在「'+result['name']+'」里的回复。' if disable else '已接通「'+result['name']+'」，有人 @我或回复我时才接话。'+('这项授权今天有效。' if expires else '你说暂停就会停。'))
    def discussion(self,gid):
        if not self.allowed(gid):return []
        with self.bridge.chat.memory.db() as db:rows=db.execute("SELECT peer,role,content FROM messages WHERE peer LIKE ? ORDER BY id DESC LIMIT 8",('group:'+str(gid)+':%',)).fetchall()[::-1]
        return [{'speaker_qq':actor(peer),'role':role,'text':text.split('\n[本条QQ')[0].split('\n[本轮')[0][:400]} for peer,role,text in rows]
    def probe(self,gid):
        gid=str(gid);state=self.bridge.snapshot()
        if not self.allowed(gid):raise ValueError('这个群的回复授权尚未启用。')
        if not state['enabled']:raise ValueError('QQ回复总开关已暂停。')
        if not state['connected']:raise ValueError('独立QQ尚未连接，请先完成QQ登录或验证码。')
        if not state['account_matches']:raise ValueError('当前QQ账号与配置的机器人小号不一致。')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT 1 FROM audit WHERE bot=? AND gid=? AND origin='local_probe' AND at>?",(self.bot(),gid,self.clock()-30)).fetchone():raise ValueError('刚做过连接测试，稍后再试。')
            db.execute('INSERT INTO audit(bot,gid,enabled,origin,source_mid,at) VALUES(?,?,1,?,?,?)',(self.bot(),gid,'local_probe','',self.clock()))
        text='群聊已接通。我会在被 @或被回复时接话，文字、表情、图片和语音都可以继续测试。'
        receipt=self.bridge.call('send_group_msg',{'group_id':int(gid),'message':[{'type':'text','data':{'text':text}}]})
        if not isinstance(receipt,dict) or not receipt.get('message_id'):raise RuntimeError('QQ没有返回消息编号，不会自动重发。')
        peer='group:'+gid+':'+self.bridge.contacts.admin;mid=self.bridge.chat.memory.add(peer,'assistant',text,origin='task')
        self.bridge.chat.memory.learning.delivery(peer,receipt['message_id'],mid);self.receipt(peer,receipt['message_id'],mid)
        from usage_ledger import Ledger
        Ledger(self.root).record('group_connection_test',peer=self.bridge.contacts.admin,state='receipt_received',details={'group_id':gid,'message_id':str(receipt['message_id'])})
        return {'sent':True,'receipt_received':True,'message_id':str(receipt['message_id']),'message':'连接器已返回发送回执，最终显示以QQ群为准。'}
