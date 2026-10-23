"""Transport identities, persistent contact keys and capability ceilings."""
from pathlib import Path
from contextlib import contextmanager
import json,re,sqlite3,time

ADMIN='900100001'
CHAT_TOOLS={'use_sticker','use_qq_face','speak_text','recall_memory','sing_song','draw_picture','lookup_web','read_group_images','end_chat_turn','set_my_name'}
QUERY_POLICY={'window_seconds':900,'max_queries':2,'timeout_seconds':300,'model':'gpt-6-luna','service_tier':'default','daily_limit':None}

def qq_id(value):
    value=str(value)
    if not re.fullmatch(r'[1-9]\d{4,11}',value):raise ValueError('无效的QQ号。')
    return value

class Contacts:
    def __init__(self,root):
        self.root=Path(root);self.path=self.root/'data/qq/contacts.sqlite3';self.path.parent.mkdir(parents=True,exist_ok=True)
        # Only the owner's local configuration can select the administrator; chat never can.
        file=self.root/'qq.local.json'
        cfg=json.loads(file.read_text(encoding='utf-8')) if file.exists() else {}
        self._admin=qq_id((cfg.get('test_contact') or {}).get('user_id',ADMIN))
        with self.db() as db:
            db.executescript('''PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS contacts(qq TEXT PRIMARY KEY,nickname TEXT,remark TEXT,first_seen REAL,last_seen REAL);
            CREATE TABLE IF NOT EXISTS memberships(qq TEXT,group_id TEXT,card TEXT,last_seen REAL,PRIMARY KEY(qq,group_id));''')
            db.executescript('''CREATE TABLE IF NOT EXISTS plus_users(qq TEXT PRIMARY KEY,granted_at REAL);
            CREATE TABLE IF NOT EXISTS plus_audit(id INTEGER PRIMARY KEY,qq TEXT,enabled INTEGER,at REAL,origin TEXT);''')
            db.executescript('''CREATE TABLE IF NOT EXISTS plus_requests(bot TEXT,mid TEXT,request_hash TEXT,group_id TEXT,result TEXT,at REAL,PRIMARY KEY(bot,mid));''')
            if 'preferred_name' not in {r[1] for r in db.execute('PRAGMA table_info(contacts)')}:db.execute("ALTER TABLE contacts ADD COLUMN preferred_name TEXT NOT NULL DEFAULT ''")
            db.executescript('''CREATE TABLE IF NOT EXISTS contact_name_audit(id INTEGER PRIMARY KEY,qq TEXT,old_name TEXT,new_name TEXT,at REAL,origin TEXT,bot TEXT,mid TEXT);
                CREATE UNIQUE INDEX IF NOT EXISTS contact_name_origin ON contact_name_audit(bot,mid) WHERE mid!='';''')
        self.observe(self.admin,remark='long')
    @property
    def admin(self):
        file=self.root/'qq.local.json'
        cfg=json.loads(file.read_text(encoding='utf-8')) if file.exists() else {}
        return qq_id((cfg.get('test_contact') or {}).get('user_id',self._admin))
    @contextmanager
    def db(self):
        db=sqlite3.connect(self.path,timeout=10);db.row_factory=sqlite3.Row
        try:
            with db:yield db
        finally:db.close()
    def observe(self,user_id,nickname='',remark='',group_id='',card=''):
        who=qq_id(user_id);now=time.time()
        clean=lambda s:re.sub(r'[\x00-\x1f\x7f]', ' ',str(s or ''))[:64]
        with self.db() as db:
            db.execute('''INSERT INTO contacts(qq,nickname,remark,first_seen,last_seen) VALUES(?,?,?,?,?) ON CONFLICT(qq) DO UPDATE SET
                nickname=CASE WHEN excluded.nickname!='' THEN excluded.nickname ELSE contacts.nickname END,
                remark=CASE WHEN excluded.remark!='' THEN excluded.remark ELSE contacts.remark END,last_seen=excluded.last_seen''',
                (who,clean(nickname),clean(remark),now,now))
            if group_id:db.execute('INSERT OR REPLACE INTO memberships VALUES(?,?,?,?)',(who,qq_id(group_id),clean(card),now))
        return self.get(who)
    def role(self,peer):
        from qq_scope import actor
        peer=actor(peer)
        if str(peer)==self.admin:return 'admin'
        with self.db() as db:plus=db.execute('SELECT 1 FROM plus_users WHERE qq=?',(str(peer),)).fetchone()
        return 'plus' if plus else 'user'
    def set_plus(self,peer,enabled):
        """Called by the authenticated owner route; never an LLM tool."""
        peer=qq_id(peer)
        if peer==self.admin:raise ValueError('long已经是管理员，不需要设为Plus。')
        if type(enabled) is not bool:raise ValueError('Plus设置须为开或关。')
        self.observe(peer)
        with self.db() as db:
            if enabled:db.execute('INSERT OR REPLACE INTO plus_users VALUES(?,?)',(peer,time.time()))
            else:db.execute('DELETE FROM plus_users WHERE qq=?',(peer,))
            db.execute('INSERT INTO plus_audit(qq,enabled,at,origin) VALUES(?,?,?,?)',(peer,int(enabled),time.time(),'local_owner'))
        return self.get(peer)
    def set_plus_many(self,peers,enabled,*,origin,bot,source_mid,group_id='',request_hash=''):
        """Internal gateway for a verified administrator's explicit private command."""
        if origin!='qq_admin_private' or not bot or not source_mid or type(enabled) is not bool:raise ValueError('批量权限修改缺少已核对的管理员指令。')
        peers=sorted({qq_id(p) for p in peers}-{self.admin,qq_id(bot)});now=time.time()
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            old=db.execute('SELECT request_hash,result FROM plus_requests WHERE bot=? AND mid=?',(bot,source_mid)).fetchone()
            if old:
                if old['request_hash']!=request_hash:raise ValueError('同一条指令不能替换成新的权限操作。')
                return json.loads(old['result'])
            changed=0
            for peer in peers:
                previous=bool(db.execute('SELECT 1 FROM plus_users WHERE qq=?',(peer,)).fetchone())
                if previous==enabled:continue
                db.execute('INSERT OR IGNORE INTO contacts(qq,nickname,remark,first_seen,last_seen) VALUES(?,?,?,?,?)',(peer,'','',now,now))
                if enabled:db.execute('INSERT INTO plus_users VALUES(?,?)',(peer,now))
                else:db.execute('DELETE FROM plus_users WHERE qq=?',(peer,))
                db.execute('INSERT INTO plus_audit(qq,enabled,at,origin) VALUES(?,?,?,?)',(peer,int(enabled),now,origin));changed+=1
            result={'changed':changed,'total':len(peers),'enabled':enabled}
            db.execute('INSERT INTO plus_requests VALUES(?,?,?,?,?,?)',(bot,source_mid,request_hash,group_id,json.dumps(result),now))
        return result
    def get(self,peer):
        from qq_scope import actor
        peer=actor(peer)
        with self.db() as db:r=db.execute('SELECT * FROM contacts WHERE qq=?',(str(peer),)).fetchone()
        result=(dict(r) if r else {'qq':str(peer),'nickname':'','remark':'','preferred_name':''})|{'role':self.role(peer)}
        result['display_name']=result.get('preferred_name') or result.get('nickname') or 'QQ '+str(peer)
        result['name_source']='custom' if result.get('preferred_name') else 'qq_nickname' if result.get('nickname') else 'unresolved'
        return result
    def all(self):
        with self.db() as db:ids=[r[0] for r in db.execute('SELECT qq FROM contacts ORDER BY last_seen DESC LIMIT 500')]
        return [self.get(peer) for peer in ids]
    def label(self,peer):
        return self.get(peer)['display_name']
    def set_name(self,peer,name,*,origin='local_owner',bot='',mid=''):
        """A display preference never rewrites the identity key or role tables."""
        peer=qq_id(peer)
        if not isinstance(name,str) or len(name)>64 or re.search(r'[\x00-\x1f\x7f]',name):raise ValueError('称呼最多64个字，不能包含换行或控制字符。')
        name=name.strip()
        if origin not in {'local_owner','qq_self'}:raise ValueError('称呼修改来源不正确。')
        if origin=='qq_self' and (not bot or not mid):raise ValueError('缺少本人来信记录。')
        self.observe(peer)
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if mid:
                old=db.execute('SELECT qq,new_name FROM contact_name_audit WHERE bot=? AND mid=?',(bot,mid)).fetchone()
                if old:
                    if old['qq']!=peer or old['new_name']!=name:raise ValueError('同一条来信不能替换成另一个称呼操作。')
                    return self.get(peer)
            old=db.execute('SELECT preferred_name FROM contacts WHERE qq=?',(peer,)).fetchone()[0]
            db.execute('UPDATE contacts SET preferred_name=? WHERE qq=?',(name,peer))
            db.execute('INSERT INTO contact_name_audit(qq,old_name,new_name,at,origin,bot,mid) VALUES(?,?,?,?,?,?,?)',(peer,old,name,time.time(),origin,bot,mid))
        return self.get(peer)
    def command(self,peer,text):
        from qq_scope import actor
        peer=actor(peer)
        if re.search(r'(?:记录|记住|存|保存).{0,10}(?:我(?:的)?|当前好友的)?[Qq]{2}号|(?:我(?:的)?[Qq]{2}号).{0,8}(?:多少|记得|知道)',text):
            self.observe(peer)
            return '记着呢，你的QQ号是'+str(peer)+'。我按这个号码认人，改昵称也不会混。'
        return None

def group_trigger(obj,bot,allowed_groups=(),enabled=False,reply_is_bot=False):
    """Preparation only. The live bridge intentionally keeps groups disabled."""
    if not enabled or str(obj.get('group_id','')) not in {str(g) for g in allowed_groups}:return False
    return any(s.get('type')=='at' and str(s.get('data',{}).get('qq',''))==str(bot) for s in obj.get('message',[])) or bool(reply_is_bot)

def choose(role,kind,model=None,effort=None,*,image_workflow=False):
    from codex_rpc import validate_choice
    if image_workflow:
        if kind!='draw_picture':raise ValueError('修图模型权限不能用于其它工作。')
        from image_workflow import image_choice
        return image_choice(role,model,effort)
    if role!='admin':
        if kind not in {'draw_picture','sing_song','lookup_web'}:raise ValueError('电脑里的文件我不能替你动，不过公开资料可以帮你查。')
        if model and model not in {'gpt-6-luna','gpt-5.6-luna'}:raise ValueError('普通用户只允许Luna模型。')
        model=model or 'gpt-6-luna'
    else:model=model or ('gpt-6-luna' if kind=='draw_picture' else 'gpt-6-sol')
    effort=effort or 'medium';validate_choice(model,effort)
    return model,effort
